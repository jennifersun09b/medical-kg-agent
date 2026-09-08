#!/usr/bin/env python3
"""Generate target-model answers under 3 evidence conditions. NO judging here —
the evaluation is done separately by Claude (the agent), not by the target model.

Conditions:
  no_knowledge : question only (closed-book prior)
  clean_gold   : question + the single correct Chinese-guideline snippet
  noisy_gold   : question + an over-supplied evidence bundle (gold + distractors +
                 cross-item noise), shuffled — the model must SELECT the applicable
                 Chinese standard from too much / conflicting evidence.
"""
import argparse, json, os, random
from pathlib import Path
from openai import OpenAI

SYS = "你是一名临床决策支持系统。请根据患者问题给出简明、专业的临床回答，必要时给出关键数值、目标或结论。"

def bundle(item, all_items, rng):
    ev = [("正确", item["gold_evidence"])] + [("干扰", d) for d in item.get("distractors", [])]
    # add 2 cross-item distractors (unrelated four-high evidence) as extra over-information
    others = [x for x in all_items if x["id"] != item["id"]]
    for o in rng.sample(others, min(2, len(others))):
        ev.append(("干扰", o["gold_evidence"]))
    rng.shuffle(ev)
    return "\n".join(f"[资料{i+1}] {txt}" for i, (_, txt) in enumerate(ev))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="../output/ex2_gold_items.json")
    ap.add_argument("--api-key", default=os.environ.get("CKPA_EVAL_API_KEY", ""))
    ap.add_argument("--base-url", default=os.environ.get("CKPA_EVAL_BASE_URL", "https://api.openai.com/v1"))
    ap.add_argument("--model", default=os.environ.get("CKPA_EVAL_MODEL", "gpt-4o"))
    ap.add_argument("--output", default="../output/ex2_answers.json")
    args = ap.parse_args()
    if not args.api_key:
        raise SystemExit("Need --api-key or CKPA_EVAL_API_KEY")

    items = json.load(open(args.input, encoding="utf-8"))
    rng = random.Random(42)
    client = OpenAI(api_key=args.api_key, base_url=args.base_url, timeout=60, max_retries=2)

    def ask(q, ctx):
        user = q if not ctx else f"## 参考资料（可能包含多条、甚至互相冲突的标准，请自行判断哪条适用于中国患者）\n{ctx}\n\n## 临床问题\n{q}"
        r = client.chat.completions.create(model=args.model, temperature=0, max_tokens=450,
            messages=[{"role": "system", "content": SYS}, {"role": "user", "content": user}])
        return r.choices[0].message.content or ""

    out = []
    for i, it in enumerate(items):
        conds = {
            "no_knowledge": ask(it["question"], None),
            "clean_gold":   ask(it["question"], it["gold_evidence"]),
            "noisy_gold":   ask(it["question"], bundle(it, items, rng)),
        }
        out.append({"id": it["id"], "category": it["category"], "question": it["question"],
                    "gold": it["gold"], "answers": conds})
        print(f"[{i+1}/{len(items)}] {it['id']} answered (3 conditions)", flush=True)
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved -> {args.output}")

if __name__ == "__main__":
    main()
