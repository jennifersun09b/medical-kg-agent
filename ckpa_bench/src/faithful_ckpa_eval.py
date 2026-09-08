#!/usr/bin/env python3
"""Faithful CKPA evaluation: for each generated item run the target model under
no_source and source_provided (frozen quote replay, as CKPA intends), score with the
pipeline's own evaluator, and emit a full auditable record + failure diagnosis.

Outputs:
  output/faithful_ckpa_fixed_50_items.json   (per-item: scenario/question/gold/quotes/answers/verdict/diagnosis)
  output/faithful_ckpa_fixed_eval.json       (aggregate: arbitration gain, by-layer)
"""
import argparse, json, glob, os
from pathlib import Path
from openai import OpenAI
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT

ROOT = Path(__file__).resolve().parent.parent

def diagnose(item, ns_pass, sp_pass, sp_ctx):
    if ns_pass and sp_pass: return "correct_both"
    if not ns_pass and sp_pass: return "arbitration_gain (evidence fixed a wrong prior)"
    if ns_pass and not sp_pass: return "evidence_hurt (source_provided regressed a correct prior)"
    # both wrong
    if not (sp_ctx or "").strip(): return "evidence_absent (source_provided packet was EMPTY)"
    return "evidence_present_but_wrong (arbitration/selection/reasoning or bad gold)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()
    ev = Evaluator.__new__(Evaluator)
    client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"],
                    base_url=os.environ.get("CKPA_EVAL_BASE_URL","https://www.micuapi.ai/v1"),
                    timeout=90, max_retries=4)

    # collect generated items, dedup by item_id. Order small layers first so a balanced
    # 4-layer set is kept: all Core + Drug + Combination, then Temporal fills to n.
    items, seen = [], set()
    for L in ["core","drug_label","combination","temporal"]:
        for f in glob.glob(str(ROOT/f"output/{L}/ckpa_*_latest.json")):
            for it in json.load(open(f, encoding="utf-8")):
                iid = it.get("item_id")
                if iid and iid not in seen:
                    seen.add(iid); it["_layer"] = L; items.append(it)
    items = items[:args.n]
    print(f"items: {len(items)} | model {args.model}", flush=True)

    def ask(item, mode):
        vp = item["visible_prompt"]
        prompt = EVAL_USER_PROMPT.format(scenario=vp["patient_scenario"], question=vp["question"],
                    output_format_spec=json.dumps(vp.get("output_format", {}), ensure_ascii=False))
        ctx = ev._build_source_context(item) if mode == "source_provided" else ""
        if ctx:
            prompt = prompt.replace("## 请按以下格式回复", f"## 参考来源\n{ctx}\n\n## 请按以下格式回复")
        try:
            r = client.chat.completions.create(model=args.model, temperature=0, max_tokens=600,
                messages=[{"role":"system","content":EVAL_SYSTEM_PROMPT},{"role":"user","content":prompt}])
            raw = r.choices[0].message.content or ""
            ans = ev._parse_json(raw[raw.find("{"):] if "{" in raw else raw)
        except Exception as e:
            raw, ans = str(e), {"_error": str(e)}
        return ans, raw, ctx

    out = []
    for i, it in enumerate(items):
        p = it.get("provenance", {}) or {}
        if isinstance(p, str):
            try: p = json.loads(p)
            except: p = {}
        ns_ans, ns_raw, _ = ask(it, "no_source")
        sp_ans, sp_raw, sp_ctx = ask(it, "source_provided")
        ns_pass = ev._score(it, ns_ans, ns_raw)["passed"]
        sp_pass = ev._score(it, sp_ans, sp_raw)["passed"]
        rec = {
            "item_id": it.get("item_id"), "layer": it.get("_layer"),
            "scenario": it["visible_prompt"]["patient_scenario"],
            "question": it["visible_prompt"]["question"],
            "gold": it.get("hidden_labels", {}).get("key_values_gold"),
            "clinical_action_gold": it.get("hidden_labels", {}).get("clinical_action_canonical"),
            "source_a_quote": p.get("source_a_quote", ""),
            "source_b_quote": p.get("source_b_quote", ""),
            "provenance": p,
            "no_source_answer": ns_ans.get("key_values", ns_ans) if isinstance(ns_ans, dict) else ns_ans,
            "source_provided_answer": sp_ans.get("key_values", sp_ans) if isinstance(sp_ans, dict) else sp_ans,
            "no_source_pass": ns_pass, "source_provided_pass": sp_pass,
            "evaluator_verdict": ("PASS" if sp_pass else "FAIL") + f" (no_source={'PASS' if ns_pass else 'FAIL'})",
            "failure_diagnosis": diagnose(it, ns_pass, sp_pass, sp_ctx),
        }
        out.append(rec)
        print(f"[{i+1}/{len(items)}] {rec['layer']:11s} {rec['item_id']:16s} ns={ns_pass} sp={sp_pass} :: {rec['failure_diagnosis'][:40]}", flush=True)
        Path(ROOT/"output/faithful_ckpa_fixed_50_items.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # aggregate
    def rate(rows, key): return sum(1 for r in rows if r[key]) / max(1, len(rows))
    layers = sorted({r["layer"] for r in out})
    agg = {"model": args.model, "n": len(out),
           "no_source_acc": round(rate(out, "no_source_pass"), 3),
           "source_provided_acc": round(rate(out, "source_provided_pass"), 3),
           "arbitration_gain": round(rate(out, "source_provided_pass") - rate(out, "no_source_pass"), 3),
           "by_layer": {}, "diagnosis_counts": {}}
    from collections import Counter
    agg["diagnosis_counts"] = dict(Counter(r["failure_diagnosis"].split(" (")[0] for r in out))
    for L in layers:
        rs = [r for r in out if r["layer"] == L]
        agg["by_layer"][L] = {"n": len(rs), "no_source": round(rate(rs,"no_source_pass"),3),
                              "source_provided": round(rate(rs,"source_provided_pass"),3),
                              "empty_source_provided": sum(1 for r in rs if not (r["source_a_quote"] or r["source_b_quote"]))}
    Path(ROOT/"output/faithful_ckpa_fixed_eval.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== aggregate ===")
    print(json.dumps(agg, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
