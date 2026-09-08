"""
probe_gateway.py — 花 1 次调用，问清楚一件事：
    直连统一模型网关，到底能不能拿到引用（references / citations）？

这决定了「GEO 后端没起来时能不能先跑 3 个 API 模型」。
拿不到引用，那批数据对 GEO 研究就没用，只能等后端。

用法：
    # Key 从环境变量读，不写进代码
    export GATEWAY_API_KEY='<文档第64行那把>'
    python probe_gateway.py
    python probe_gateway.py --model deepseek-v4-flash

完整原始响应会存到 gateway_probe/ 下，可以自己翻。
"""
import argparse
import json
import os
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BASE_URL = os.getenv("GATEWAY_BASE_URL", "https://<your-model-gateway>/v1").rstrip("/")
API_KEY = os.getenv("GATEWAY_API_KEY", "")
OUT_DIR = Path(__file__).parent / "gateway_probe"

# 故意挑一个必须联网才答得好、且天然会引用权威来源的医学问题
DEFAULT_Q = "骨转移患者使用双膦酸盐类药物，国内指南推荐的用法和注意事项是什么？请给出信息来源。"

# 引用信息可能藏在这些键名下（各家命名不一样）
CITE_KEYS = {
    "references", "citations", "citation", "sources", "source",
    "search_results", "web_search_results", "candidatesources", "candidate_sources",
    "tool_steps", "toolsteps", "annotations", "url_citation", "reference",
}


def find_citations(obj, path="$"):
    """递归找返回体里所有像引用的字段。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower().replace("-", "_") in CITE_KEYS:
                hits.append((f"{path}.{k}", v))
            hits.extend(find_citations(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:8]):
            hits.extend(find_citations(v, f"{path}[{i}]"))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.7-max",
                    help="qwen3.7-max / deepseek-v4-flash / doubao-seed-2-1-pro-260628")
    ap.add_argument("--question", default=DEFAULT_Q)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    if not API_KEY:
        raise SystemExit(
            "缺少 GATEWAY_API_KEY。\n"
            "  export GATEWAY_API_KEY='<文档第64行那把Key>'\n"
            "或写进 rpa_chat/.env（.gitignore 已经挡住它了）")

    payload = {"model": args.model,
               "messages": [{"role": "user", "content": args.question}]}

    print(f"模型: {args.model}")
    print(f"问题: {args.question[:40]}...")
    print("发一次请求...\n")

    resp = requests.post(f"{BASE_URL}/chat/completions", json=payload,
                         headers={"Authorization": f"Bearer {API_KEY}"},
                         timeout=args.timeout)

    try:
        raw = resp.json()
    except ValueError:
        raise SystemExit(f"响应不是 JSON，HTTP {resp.status_code}:\n{resp.text[:500]}")

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"raw_{args.model.replace('.', '_')}.json"
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"HTTP {resp.status_code}")
    if not resp.ok:
        print(json.dumps(raw, ensure_ascii=False, indent=2)[:800])
        raise SystemExit(1)

    msg = ((raw.get("choices") or [{}])[0].get("message") or {})
    answer = msg.get("content") or ""

    print(f"顶层字段: {sorted(raw.keys())}")
    print(f"message 字段: {sorted(msg.keys())}")
    print(f"回答长度: {len(answer)} 字")
    print(f"\n回答开头:\n  {answer[:180]}...\n")

    hits = find_citations(raw)
    print("=" * 56)
    if hits:
        print("✅ 有结构化引用字段 —— 直连网关可能可用：\n")
        for p, v in hits[:10]:
            s = json.dumps(v, ensure_ascii=False)
            print(f"  {p}\n    {s[:220]}\n")
    else:
        print("❌ 没有任何结构化引用字段。")
        print("   直连网关只能拿到纯文本，拿不到「引用了哪些来源」。")
        print("   → GEO 研究要的引用数据拿不到，这 3 个模型只能等 GEO 后端。")

    # 正文里有没有裸链接，作为退路参考
    import re
    urls = re.findall(r"https?://[^\s)\]，。、】]+", answer)
    if urls:
        print(f"\n补充：回答正文里有 {len(urls)} 个裸链接，"
              f"必要时可以正则抠出来当引用（质量不如结构化字段）：")
        for u in urls[:5]:
            print(f"  {u}")
    else:
        print("\n补充：回答正文里也没有裸链接。")

    print("=" * 56)
    print(f"完整原始响应: {out}")


if __name__ == "__main__":
    main()
