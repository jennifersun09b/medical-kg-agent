"""
trigger_geo_all.py — 通过 GEO Java 后端采集 5 个模型的回答

链路：本脚本 -> GEO 后端 (127.0.0.1:6011) -> 统一模型网关 / RPA 平台 -> 真实模型

- 千问 / DeepSeek / 豆包：走统一模型网关，模型自己联网搜索
- Kimi / 元宝(混元)：走 GEO 后端预配置的 RPA 任务

本脚本不需要任何上游 API Key —— Key 只保存在 GEO 后端私密配置里。

用法：
    python trigger_geo_all.py                      # 5 个模型全跑
    python trigger_geo_all.py --platforms qwen     # 只跑千问
    python trigger_geo_all.py --resume             # 断点续跑（按题跳过已成功的）
"""
import argparse
import json
import os
import random
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

QUESTIONS_FILE = Path(__file__).parent.parent / "model_response" / "question_list_all_new.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

# GEO 后端地址。本机没跑就在 .env 里改成部署好的域名。
GEO_BASE_URL = os.getenv("GEO_BASE_URL", "http://127.0.0.1:6011").rstrip("/")

# 直连统一模型网关（仅千问/DeepSeek/豆包可用，Kimi 和混元必须走 GEO 后端的 RPA）。
# Key 从环境变量读，绝不写进代码或提交进 Git。
GATEWAY_BASE_URL = os.getenv("GATEWAY_BASE_URL", "https://<your-model-gateway>/v1").rstrip("/")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")

SAMPLE_SIZE = 50
TIMEOUT_S = 920  # 文档 14.3：建议至少 920 秒

PLATFORMS = {
    "qwen": {
        "name": "通义千问",
        "endpoint": "/api/llm/qwen/ask",
        "channel": "geo_gateway",
        "extra": {"model": "qwen3.7-max"},
        "gateway_model": "qwen3.7-max",
    },
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "/api/llm/deepseek/ask",
        "channel": "geo_gateway",
        "extra": {"model": "deepseek-v4-flash", "enableWebSearch": True},
        "gateway_model": "deepseek-v4-flash",
    },
    "doubao": {
        "name": "豆包",
        "endpoint": "/api/llm/doubao/ask",
        "channel": "geo_gateway",
        "extra": {"model": "doubao-seed-2-1-pro-260628", "feature": "web_search"},
        "gateway_model": "doubao-seed-2-1-pro-260628",
    },
    "kimi": {
        "name": "Kimi",
        "endpoint": "/api/llm/kimi/ask",
        "channel": "geo_rpa",
        "extra": {},
        "gateway_model": None,  # 只能走 RPA
    },
    "hunyuan": {
        "name": "腾讯元宝(混元)",
        "endpoint": "/api/llm/yuanbao/ask",
        "channel": "geo_rpa",
        "extra": {},
        "gateway_model": None,  # 只能走 RPA
    },
}


def load_questions(n: int, seed: int) -> list[dict]:
    questions = [json.loads(l) for l in QUESTIONS_FILE.open(encoding="utf-8") if l.strip()]
    random.seed(seed)
    return random.sample(questions, min(n, len(questions)))


def question_text(q: dict) -> str:
    """题库字段名兼容：优先 question，其次几个常见别名。"""
    for k in ("question", "question_text", "query", "content", "text"):
        v = q.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise KeyError(f"这条记录里找不到问题文本，字段有: {sorted(q.keys())}")


def ask_geo(platform_key: str, question: str, task_id: str) -> dict:
    """调用 GEO 后端，返回 data。HTTP 状态码和 JSON code 必须同时判断（文档 4.2）。"""
    cfg = PLATFORMS[platform_key]
    payload = {"question": question, **cfg["extra"]}
    if cfg["channel"] == "geo_gateway" and platform_key != "doubao":
        payload["taskId"] = task_id  # 豆包接口不吃 taskId

    try:
        resp = requests.post(GEO_BASE_URL + cfg["endpoint"], json=payload, timeout=TIMEOUT_S)
    except requests.Timeout:
        raise RuntimeError(f"请求超时（>{TIMEOUT_S}s）")
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP 请求失败: {e}")

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError(f"响应不是 JSON，HTTP {resp.status_code}: {resp.text[:200]}")

    if not resp.ok or result.get("code") != 200:
        raise RuntimeError(result.get("msg") or f"HTTP {resp.status_code}")

    return result.get("data") or {}


def ask_gateway(platform_key: str, question: str) -> dict:
    """
    直连统一模型网关（OpenAI 兼容）。仅在 GEO 后端起不来时作为退路，
    且只对千问/DeepSeek/豆包有效 —— Kimi 和混元没有网关侧的 API。

    注意：这条路走的是网关默认行为，不含 GEO 后端那一层的搜索编排，
    因此 references 通常为空，与走 GEO 后端的结果不完全等价。
    """
    cfg = PLATFORMS[platform_key]
    if not cfg["gateway_model"]:
        raise RuntimeError(f"{cfg['name']} 只能走 GEO 后端的 RPA，无法直连网关")
    if not GATEWAY_API_KEY:
        raise RuntimeError("缺少 GATEWAY_API_KEY，请写进 rpa_chat/.env（不要提交到 Git）")

    payload = {
        "model": cfg["gateway_model"],
        "messages": [{"role": "user", "content": question}],
    }
    try:
        resp = requests.post(
            f"{GATEWAY_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {GATEWAY_API_KEY}"},
            timeout=TIMEOUT_S,
        )
    except requests.Timeout:
        raise RuntimeError(f"请求超时（>{TIMEOUT_S}s）")
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP 请求失败: {e}")

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError(f"响应不是 JSON，HTTP {resp.status_code}: {resp.text[:200]}")

    if not resp.ok:
        raise RuntimeError(str(result.get("error") or f"HTTP {resp.status_code}"))

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"网关未返回 choices: {json.dumps(result, ensure_ascii=False)[:200]}")

    msg = choices[0].get("message") or {}
    return {
        "answer": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content"),
        "providerModel": result.get("model"),
        "providerResponseId": result.get("id"),
        "usage": result.get("usage"),
    }


def out_path(platform_key: str) -> Path:
    return RESULTS_DIR / f"{platform_key}_results.jsonl"


def done_ids(platform_key: str) -> set:
    """已成功采集的 question_id（error 为空的才算成功）。"""
    p = out_path(platform_key)
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


def save_result(platform_key: str, row: dict):
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(out_path(platform_key), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", nargs="+", choices=list(PLATFORMS.keys()),
                        default=list(PLATFORMS.keys()))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--sample", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--resume", action="store_true", help="跳过已成功采集的题目")
    parser.add_argument("--sleep", type=float, default=2.0, help="题间间隔秒数")
    parser.add_argument("--route", choices=["geo", "gateway"], default="geo",
                        help="geo=走 GEO 后端(推荐，5 模型都支持)；"
                             "gateway=直连统一网关(仅千问/DeepSeek/豆包，退路)")
    args = parser.parse_args()

    if args.route == "gateway":
        blocked = [k for k in args.platforms if not PLATFORMS[k]["gateway_model"]]
        if blocked:
            names = "、".join(PLATFORMS[k]["name"] for k in blocked)
            print(f"注意：{names} 无法直连网关，本轮将跳过。它们必须等 GEO 后端可用。")
            args.platforms = [k for k in args.platforms if PLATFORMS[k]["gateway_model"]]
        if not args.platforms:
            print("没有可用平台，退出。")
            return

    questions = load_questions(args.sample, args.seed)
    print(f"抽取 {len(questions)} 题 (seed={args.seed})，"
          f"线路={args.route}，平台: {', '.join(args.platforms)}")

    for platform_key in args.platforms:
        cfg = PLATFORMS[platform_key]
        skip = done_ids(platform_key) if args.resume else set()
        todo = [q for q in questions if q.get("question_id") not in skip]

        if not todo:
            print(f"\n[{cfg['name']}] 已全部完成，跳过。")
            continue

        print(f"\n[{cfg['name']}] 采集 {len(todo)} 题"
              + (f"（跳过已完成 {len(skip)} 题）" if skip else ""))

        ok = 0
        channel = "gateway_direct" if args.route == "gateway" else cfg["channel"]
        for i, q in enumerate(todo, 1):
            qid = q.get("question_id")
            qtext = ""
            t0 = time.monotonic()
            try:
                qtext = question_text(q)
                if args.route == "gateway":
                    data = ask_gateway(platform_key, qtext)
                else:
                    data = ask_geo(platform_key, qtext, f"EVAL-{platform_key}-{qid}")
                elapsed = round(time.monotonic() - t0, 1)
                answer = data.get("answer") or ""
                row = {
                    "question_id": qid,
                    "cancer_type": q.get("cancer_type"),
                    "question_type": q.get("question_type"),
                    "question": qtext,
                    "model": platform_key,
                    "provider_model": data.get("providerModel") or data.get("model")
                                      or data.get("provider"),
                    "response": answer,
                    "reasoning": data.get("reasoning"),
                    "references": data.get("references"),
                    "citations": data.get("citations"),
                    "error": None,
                    "elapsed_s": elapsed,
                    "channel": channel,
                    "raw_data": data,
                }
                ok += 1
                print(f"  [{i}/{len(todo)}] {qid} ok {elapsed}s len={len(answer)}")
            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 1)
                row = {
                    "question_id": qid,
                    "cancer_type": q.get("cancer_type"),
                    "question_type": q.get("question_type"),
                    "question": qtext or q.get("question"),
                    "model": platform_key,
                    "response": None,
                    "error": str(exc),
                    "elapsed_s": elapsed,
                    "channel": channel,
                }
                print(f"  [{i}/{len(todo)}] {qid} FAILED ({elapsed}s): {exc}")

            save_result(platform_key, row)

            if i < len(todo):
                time.sleep(args.sleep)

        print(f"[{cfg['name']}] 完成 {ok}/{len(todo)} → {out_path(platform_key)}")

    print("\n全部完成。失败的题目可用 --resume 重跑。")


if __name__ == "__main__":
    main()
