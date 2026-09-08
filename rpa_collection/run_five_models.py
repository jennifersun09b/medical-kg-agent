"""
run_five_models.py — 五模型真实平台评测采集

链路（文档 §1）：
    本脚本 -> GEO 后端 -> 统一模型网关 / RPA 平台 -> 真实模型

设计要点：
  1. 题在外层、模型在内层 —— 同一题的 5 个模型在同一时间窗内被问到。
     千问/DeepSeek/豆包会实时联网，先跑完一个模型再跑下一个会引入时间漂移。
  2. 网关 3 个模型并行，RPA 2 个模型全局串行（RPA 在真开浏览器，并发会抢登录态）。
  3. 默认不自动重试 —— 文档 §4.3：失败请求也计入真实调用次数，重试会再计一次。
     失败的题记进结果文件，之后用 --resume 补跑。
  4. 逐条 append 落盘，进程被 Ctrl-C 或崩溃都不丢已采到的数据。
  5. 每条结果都记录上游实际模型标识，用于确认没有串模型（文档 §14.5）。

常用：
    python run_five_models.py --smoke              # 先用 1 题打通 5 个模型
    python run_five_models.py                      # 正式跑 50 题 x 5 模型
    python run_five_models.py --resume             # 补跑失败/未完成的题
    python run_five_models.py --models kimi        # 只跑某几个模型
"""
import argparse
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).parent
DEFAULT_QUESTIONS = HERE.parent / "model_response" / "question_list_all_new.jsonl"
RESULTS_DIR = HERE / "results"

# GEO 后端地址。本机没起后端就在 .env 里写成已部署的域名。
BASE_URL = os.getenv("GEO_BASE_URL", "http://127.0.0.1:6011").rstrip("/")

SAMPLE_SIZE = 50
TIMEOUT_S = 920          # 文档 §14.3
GATEWAY_CONCURRENCY = 3  # 网关模型并行度；RPA 恒为 1

# ---------------------------------------------------------------------------
# 五模型规格表：所有模型间差异集中在这里，下面的正文逻辑只有一套
# 字段依据文档 §5 / §6 / §7 / §8 / §9
# ---------------------------------------------------------------------------
MODELS = {
    "qwen": {
        "name": "通义千问",
        "endpoint": "/api/llm/qwen/ask",
        "lane": "gateway",
        "extra": {"model": "qwen3.7-max"},   # §5.2 固定联网搜索
        "send_task_id": True,
        "identity_field": "providerModel",   # §14.5
        "identity_expect": "qwen",
    },
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "/api/llm/deepseek/ask",
        "lane": "gateway",
        "extra": {"model": "deepseek-v4-flash", "enableWebSearch": True},  # §6.2
        "send_task_id": True,
        "identity_field": "providerModel",
        "identity_expect": "deepseek",
    },
    "doubao": {
        "name": "豆包",
        "endpoint": "/api/llm/doubao/ask",
        "lane": "gateway",
        "extra": {"model": "doubao-seed-2-1-pro-260628", "feature": "web_search"},  # §7.2
        "send_task_id": False,               # §7.2 请求字段表里没有 taskId
        "identity_field": "model",           # §14.5 豆包看 data.model
        "identity_expect": "doubao",
    },
    "kimi": {
        "name": "Kimi",
        "endpoint": "/api/llm/kimi/ask",
        "lane": "rpa",
        "extra": {},                         # §8.2 只要 question
        "send_task_id": False,
        "identity_field": "provider",        # §14.5
        "identity_expect": "kimi",
    },
    "yuanbao": {
        "name": "腾讯元宝(混元)",
        "endpoint": "/api/llm/yuanbao/ask",
        "lane": "rpa",
        "extra": {},                         # §9.2 只要 question
        "send_task_id": False,
        "identity_field": "provider",
        "identity_expect": "yuanbao",
    },
}

_write_lock = threading.Lock()
_rpa_lock = threading.Lock()   # 保证全局同时只有一个 RPA 请求在飞


# ---------------------------------------------------------------------------
# 题库
# ---------------------------------------------------------------------------
def question_text(q: dict) -> str:
    """题库字段名兼容。"""
    for k in ("question", "question_text", "query", "content", "text"):
        v = q.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise KeyError(f"找不到问题文本，该条字段为: {sorted(q.keys())}")


def load_questions(path: Path, n: int, seed: int) -> list:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    random.seed(seed)
    picked = random.sample(rows, min(n, len(rows)))
    # 落一份抽样清单，保证这批题以后可复现、可追溯
    RESULTS_DIR.mkdir(exist_ok=True)
    manifest = RESULTS_DIR / "_sample_manifest.json"
    manifest.write_text(json.dumps({
        "source": str(path),
        "total_in_pool": len(rows),
        "sample_size": len(picked),
        "seed": seed,
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "question_ids": [q.get("question_id") for q in picked],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return picked


# ---------------------------------------------------------------------------
# 结果账本
# ---------------------------------------------------------------------------
def out_path(model_key: str) -> Path:
    return RESULTS_DIR / f"{model_key}_results.jsonl"


def done_ids(model_key: str) -> set:
    """只有 error 为空且 response 非空才算成功；失败的会被重跑。"""
    p = out_path(model_key)
    if not p.exists():
        return set()
    ids = set()
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("error") and row.get("response"):
            ids.add(row.get("question_id"))
    return ids


def write_row(model_key: str, row: dict):
    RESULTS_DIR.mkdir(exist_ok=True)
    with _write_lock:
        with open(out_path(model_key), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 单次调用
# ---------------------------------------------------------------------------
def call_model(model_key: str, question: str, task_id: str, timeout: int) -> dict:
    """发一次请求，返回 GEO 的 data。HTTP 码和 JSON code 必须同时为 200（§4.2）。"""
    spec = MODELS[model_key]
    payload = {"question": question, **spec["extra"]}
    if spec["send_task_id"]:
        payload["taskId"] = task_id

    try:
        resp = requests.post(BASE_URL + spec["endpoint"], json=payload, timeout=timeout)
    except requests.Timeout:
        raise RuntimeError(f"超时（>{timeout}s）")
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP 请求失败: {e}")

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError(f"响应不是 JSON，HTTP {resp.status_code}: {resp.text[:200]}")

    if not resp.ok or result.get("code") != 200:
        raise RuntimeError(result.get("msg") or f"HTTP {resp.status_code}")

    return result.get("data") or {}


def check_identity(model_key: str, data: dict):
    """确认没串模型（§14.5）。返回 (是否匹配, 上游实际标识)。"""
    spec = MODELS[model_key]
    actual = data.get(spec["identity_field"])
    if actual is None:
        return None, None   # 上游没返回该字段，无法判定
    return spec["identity_expect"].lower() in str(actual).lower(), actual


def collect_one(model_key: str, q: dict, timeout: int) -> dict:
    """跑一题一模型，落盘并返回结果行。RPA 模型全局串行。"""
    spec = MODELS[model_key]
    qid = q.get("question_id")
    qtext = ""
    t0 = time.monotonic()

    try:
        qtext = question_text(q)
        task_id = f"EVAL-{model_key}-{qid}"
        if spec["lane"] == "rpa":
            with _rpa_lock:          # RPA 同时只允许一个
                data = call_model(model_key, qtext, task_id, timeout)
        else:
            data = call_model(model_key, qtext, task_id, timeout)

        elapsed = round(time.monotonic() - t0, 1)
        answer = data.get("answer") or ""
        id_ok, id_actual = check_identity(model_key, data)

        row = {
            "question_id": qid,
            "cancer_type": q.get("cancer_type"),
            "question_type": q.get("question_type"),
            "question": qtext,
            "model": model_key,
            "model_name": spec["name"],
            "lane": spec["lane"],
            "response": answer,
            "reasoning": data.get("reasoning"),                  # RPA 两家通常为空（§14.4）
            "references": data.get("references"),
            "citations": data.get("citations"),                  # 豆包
            "tool_steps": data.get("toolSteps"),                 # DeepSeek
            "candidate_sources": data.get("candidateSources"),   # DeepSeek
            "provider_model": id_actual,
            "identity_ok": id_ok,
            "provider_response_id": data.get("providerResponseId"),
            "rpa_task_id": data.get("taskId") if spec["lane"] == "rpa" else None,
            "error": None,
            "elapsed_s": elapsed,
            "asked_at": datetime.now(timezone.utc).isoformat(),
            "raw_data": data,
        }
        if id_ok is False:
            print(f"    ⚠️  {spec['name']} 疑似串模型：期望含 "
                  f"'{spec['identity_expect']}'，实际 '{id_actual}'")

    except Exception as exc:
        elapsed = round(time.monotonic() - t0, 1)
        row = {
            "question_id": qid,
            "cancer_type": q.get("cancer_type"),
            "question_type": q.get("question_type"),
            "question": qtext or q.get("question"),
            "model": model_key,
            "model_name": spec["name"],
            "lane": spec["lane"],
            "response": None,
            "error": str(exc),
            "elapsed_s": elapsed,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }

    write_row(model_key, row)
    return row


# ---------------------------------------------------------------------------
# 收尾核对（§13）
# ---------------------------------------------------------------------------
def verify_usage():
    try:
        resp = requests.post(
            f"{BASE_URL}/api/toolbox/model-usage/report",
            json={"granularity": "DAY",
                  "anchorDate": datetime.now().strftime("%Y-%m-%d")},
            timeout=30,
        )
        data = (resp.json() or {}).get("data") or {}
        print(f"\n后端统计：今日调用总数 {data.get('todayTotal')}，"
              f"本月 {data.get('currentMonthTotal')}")
        for m in (data.get("models") or []):
            print(f"  {m}")
    except Exception as exc:
        print(f"\n（调用统计核对失败，不影响已采数据：{exc}）")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    ap.add_argument("--questions-file", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--sample", type=int, default=SAMPLE_SIZE)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="跳过已成功的 (模型, 题) 组合")
    ap.add_argument("--smoke", action="store_true", help="只用第 1 题打通全部模型后退出")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S)
    ap.add_argument("--concurrency", type=int, default=GATEWAY_CONCURRENCY,
                    help="网关模型并行度；RPA 恒为串行")
    ap.add_argument("--sleep", type=float, default=1.0, help="题间间隔秒")
    args = ap.parse_args()

    # 后端探活，避免白跑
    try:
        requests.post(f"{BASE_URL}/api/llm/deepseek/info", timeout=10)
        print(f"✓ GEO 后端可达: {BASE_URL}")
    except requests.RequestException as e:
        print(f"✗ GEO 后端不可达 {BASE_URL}: {e}")
        print("  先启动 geo_server，或在 rpa_chat/.env 里设 GEO_BASE_URL=<已部署域名>")
        raise SystemExit(1)

    questions = load_questions(args.questions_file, args.sample, args.seed)
    if args.smoke:
        questions = questions[:1]
        print("\n[冒烟测试] 用 1 题打通 5 个模型（文档 §15 建议先跑通再批量）")

    gateway = [m for m in args.models if MODELS[m]["lane"] == "gateway"]
    rpa = [m for m in args.models if MODELS[m]["lane"] == "rpa"]
    done = {m: (done_ids(m) if args.resume and not args.smoke else set())
            for m in args.models}

    total_todo = sum(len([q for q in questions if q.get("question_id") not in done[m]])
                     for m in args.models)
    print(f"\n题目 {len(questions)} 道 (seed={args.seed}) | 模型 {len(args.models)} 个 "
          f"| 待采 {total_todo} 次真实调用")
    if rpa and not args.smoke:
        print(f"提示：RPA 模型({'/'.join(MODELS[m]['name'] for m in rpa)})串行执行，"
              f"单题按 2-5 分钟估，整轮可能需要数小时。")

    stats = {m: {"ok": 0, "fail": 0, "skip": 0} for m in args.models}
    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for idx, q in enumerate(questions, 1):
            qid = q.get("question_id")
            print(f"\n[{idx}/{len(questions)}] {qid}")

            # 网关模型并行提交；RPA 在主线程串行 —— 两条道同时推进
            futures = {}
            for m in gateway:
                if qid in done[m]:
                    stats[m]["skip"] += 1
                    continue
                futures[m] = pool.submit(collect_one, m, q, args.timeout)

            for m in rpa:
                if qid in done[m]:
                    stats[m]["skip"] += 1
                    continue
                row = collect_one(m, q, args.timeout)
                failed = bool(row.get("error"))
                stats[m]["fail" if failed else "ok"] += 1
                detail = (f"FAIL {row['error'][:60]}" if failed
                          else f"len={len(row.get('response') or '')}")
                print(f"    {MODELS[m]['name']:<14} {row['elapsed_s']:>6}s  {detail}")

            for m, fut in futures.items():
                row = fut.result()
                failed = bool(row.get("error"))
                stats[m]["fail" if failed else "ok"] += 1
                detail = (f"FAIL {row['error'][:60]}" if failed
                          else f"len={len(row.get('response') or '')}")
                print(f"    {MODELS[m]['name']:<14} {row['elapsed_s']:>6}s  {detail}")

            if idx < len(questions):
                time.sleep(args.sleep)

    mins = round((time.monotonic() - t_start) / 60, 1)
    print(f"\n{'='*52}\n耗时 {mins} 分钟")
    for m in args.models:
        s = stats[m]
        print(f"  {MODELS[m]['name']:<14} 成功 {s['ok']:>3}  失败 {s['fail']:>3}  "
              f"跳过 {s['skip']:>3}  → {out_path(m).name}")
    if any(stats[m]["fail"] for m in args.models):
        print("\n有失败题目。用 --resume 补跑，只会重问失败的那些。")
    verify_usage()


if __name__ == "__main__":
    main()
