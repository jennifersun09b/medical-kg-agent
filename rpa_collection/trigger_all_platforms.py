"""
trigger_all_platforms.py — 从 question_list_all_new.jsonl 抽样 50 题，
对 6 个平台逐一通过 RPA 平台 API 触发采集，将结果合并写入 results/ 目录。

用法：
    python trigger_all_platforms.py                     # 全部 6 个平台
    python trigger_all_platforms.py --platforms qianwen deepseek
    python trigger_all_platforms.py --seed 42           # 固定随机种子（复现抽样）
    python trigger_all_platforms.py --resume            # 跳过 results/ 中已有的平台

环境变量（或 .env 文件）：
    RPA_PLATFORM_URL    配置平台外网地址，如 https://<your-rpa-platform>
    OPEN_API_TOKEN      对外 API 鉴权 Token
    TASK_IDS            各平台 task_id，JSON 格式，如
                        '{"qianwen":"101","doubao":"102","zhipu":"103","kimi":"104","deepseek":"105","hunyuan":"106"}'
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

QUESTIONS_FILE = Path(__file__).parent.parent / "model_response" / "question_list_all_new.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

ALL_PLATFORMS = ["kimi", "hunyuan"]  # 当前只有这两个平台配置了RPA
SAMPLE_SIZE = 50
SYNC_TIMEOUT_S = 600  # 50 题流式渲染，给足时间


def load_questions(n: int, seed: int) -> list[dict]:
    questions = [json.loads(l) for l in QUESTIONS_FILE.open(encoding="utf-8") if l.strip()]
    random.seed(seed)
    return random.sample(questions, min(n, len(questions)))


def load_existing_platforms() -> set[str]:
    done = set()
    for f in RESULTS_DIR.glob("*_results.jsonl"):
        done.add(f.stem.replace("_results", ""))
    return done


def trigger_platform(base_url: str, token: str, task_id: str, platform: str, questions: list[dict]) -> dict:
    """
    POST /api/v1/tasks/trigger/sync
    custom_input 里带 platform key 和 questions 列表，
    脚本通过 API_CUSTOM_INPUT 或 stdin 的 custom_input 字段读取。
    """
    url = f"{base_url.rstrip('/')}/api/v1/tasks/trigger/sync"
    payload = {
        "task_id": task_id,
        "custom_input": {
            "platform": platform,
            "questions": questions,
            "interval_s": 5,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=SYNC_TIMEOUT_S + 30)
    resp.raise_for_status()
    return resp.json()


def save_results(platform: str, rows: list[dict]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{platform}_results.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", nargs="+", choices=ALL_PLATFORMS, default=ALL_PLATFORMS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    base_url = os.environ.get("RPA_PLATFORM_URL", "https://<your-rpa-platform>")
    token = os.environ.get("OPEN_API_TOKEN", "")
    task_ids_raw = os.environ.get("TASK_IDS", "{}")
    try:
        task_ids: dict = json.loads(task_ids_raw)
    except json.JSONDecodeError:
        print("ERROR: TASK_IDS 环境变量不是合法 JSON", file=sys.stderr)
        sys.exit(1)

    if not token:
        print("ERROR: 缺少 OPEN_API_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)

    questions = load_questions(SAMPLE_SIZE, args.seed)
    print(f"抽取 {len(questions)} 题 (seed={args.seed})")

    done = load_existing_platforms() if args.resume else set()
    platforms = [p for p in args.platforms if p not in done]
    if not platforms:
        print("所有平台已完成，无需重跑。")
        return

    for platform in platforms:
        task_id = task_ids.get(platform)
        if not task_id:
            print(f"[{platform}] 跳过：TASK_IDS 中未配置 task_id")
            continue

        print(f"\n[{platform}] 触发采集…")
        t0 = time.monotonic()
        try:
            resp = trigger_platform(base_url, token, task_id, platform, questions)
        except requests.HTTPError as exc:
            print(f"[{platform}] HTTP 错误: {exc.response.status_code} {exc.response.text[:300]}")
            continue
        except Exception as exc:
            print(f"[{platform}] 请求失败: {exc}")
            continue

        elapsed = round(time.monotonic() - t0, 1)
        data = resp.get("data") or {}
        status = data.get("status")
        summary = data.get("result_summary") or {}
        rows = (data.get("result") or {}).get("rows") or []

        if not resp.get("success") or status != "success":
            print(f"[{platform}] 任务失败 status={status} error={data.get('error_msg')}")
            continue

        out = save_results(platform, rows)
        ok = summary.get("ok", len([r for r in rows if r.get("response")]))
        total = summary.get("total", len(rows))
        print(f"[{platform}] 完成 {ok}/{total} 题，耗时 {elapsed}s → {out}")

    print("\n全部完成。")


if __name__ == "__main__":
    main()
