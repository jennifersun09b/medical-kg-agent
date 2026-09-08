"""
trigger_geo_rpa.py — 通过 GEO Java 后端调用 Kimi 和混元的 RPA 采集

GEO 后端已经配置好了这两个平台的 RPA 任务，我们只需：
1. 从 question_list_all_new.jsonl 抽取 50 题
2. 逐题调用 /api/llm/kimi/ask 和 /api/llm/yuanbao/ask
3. 保存结果到 results/
"""
import argparse
import json
import random
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

QUESTIONS_FILE = Path(__file__).parent.parent / "model_response" / "question_list_all_new.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

# GEO 后端配置
GEO_BASE_URL = "http://127.0.0.1:6011"

PLATFORMS = {
    "kimi": {
        "endpoint": "/api/llm/kimi/ask",
        "name": "Kimi",
    },
    "hunyuan": {
        "endpoint": "/api/llm/yuanbao/ask",  # 注意：元宝接口对应混元
        "name": "混元",
    },
}

SAMPLE_SIZE = 50


def load_questions(n: int, seed: int) -> list[dict]:
    questions = [json.loads(l) for l in QUESTIONS_FILE.open(encoding="utf-8") if l.strip()]
    random.seed(seed)
    return random.sample(questions, min(n, len(questions)))


def ask_geo_backend(platform_key: str, question: str) -> dict:
    """
    调用 GEO 后端的 RPA 接口

    返回格式:
    {
      "code": 200,
      "msg": "success",
      "data": {
        "provider": "kimi",
        "taskId": "...",
        "question": "...",
        "answer": "...",
        "references": [...],
        "rawResponse": {...}
      }
    }
    """
    config = PLATFORMS[platform_key]
    url = f"{GEO_BASE_URL}{config['endpoint']}"

    payload = {"question": question}

    try:
        resp = requests.post(url, json=payload, timeout=920)  # 文档建议至少 920 秒
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") != 200:
            raise Exception(f"GEO backend error: {result.get('msg')}")

        return result["data"]

    except requests.Timeout:
        raise Exception(f"请求超时（>920s），可能 RPA 执行时间过长")
    except requests.RequestException as e:
        raise Exception(f"HTTP 请求失败: {e}")


def save_result(platform_key: str, row: dict):
    """追加写入单条结果"""
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{platform_key}_results.jsonl"

    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", nargs="+", choices=list(PLATFORMS.keys()),
                        default=list(PLATFORMS.keys()))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resume", action="store_true",
                        help="跳过 results/ 中已存在的平台")
    args = parser.parse_args()

    questions = load_questions(SAMPLE_SIZE, args.seed)
    print(f"抽取 {len(questions)} 题 (seed={args.seed})")

    # 检查已完成的平台
    done = set()
    if args.resume:
        for f in RESULTS_DIR.glob("*_results.jsonl"):
            done.add(f.stem.replace("_results", ""))

    platforms = [p for p in args.platforms if p not in done]
    if not platforms:
        print("所有平台已完成，无需重跑。")
        return

    for platform_key in platforms:
        config = PLATFORMS[platform_key]
        print(f"\n[{config['name']}] 开始采集 {len(questions)} 题...")

        ok_count = 0
        for i, q in enumerate(questions, 1):
            qid = q.get("question_id")
            try:
                t0 = time.monotonic()
                data = ask_geo_backend(platform_key, q["question"])
                elapsed = round(time.monotonic() - t0, 1)

                answer = data.get("answer", "")
                references = data.get("references", [])

                row = {
                    "question_id": qid,
                    "cancer_type": q.get("cancer_type"),
                    "question_type": q.get("question_type"),
                    "question": q["question"],
                    "model": platform_key,
                    "response": answer,
                    "references": references,
                    "error": None,
                    "elapsed_s": elapsed,
                    "channel": "geo_rpa",
                    "raw_data": data,  # 保留完整原始响应
                }

                save_result(platform_key, row)
                ok_count += 1
                print(f"  [{i}/{len(questions)}] {qid} ok {elapsed}s len={len(answer)}")

            except Exception as exc:
                row = {
                    "question_id": qid,
                    "question": q.get("question"),
                    "model": platform_key,
                    "response": None,
                    "error": str(exc),
                    "channel": "geo_rpa",
                }
                save_result(platform_key, row)
                print(f"  [{i}/{len(questions)}] {qid} FAILED: {exc}")

            # 避免过快请求
            if i < len(questions):
                time.sleep(2)

        out_file = RESULTS_DIR / f"{platform_key}_results.jsonl"
        print(f"\n[{config['name']}] 完成 {ok_count}/{len(questions)} 题 → {out_file}")

    print("\n全部完成。")


if __name__ == "__main__":
    main()
