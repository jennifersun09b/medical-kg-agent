#!/usr/bin/env python3
"""Oracle-evidence test: give each question GUARANTEED-sufficient gold knowledge and
measure accuracy. Isolates knowledge-access from reasoning.

Conditions:
  no_source   : scenario + question only (model's own knowledge)
  oracle_gold : + an authoritative snippet that states the governing gold answer
                (built from hidden_labels — guaranteed to contain what's needed)

If oracle_gold >> no_source, the bottleneck is knowledge access, not reasoning.
Residual oracle failures = evaluator/matcher/item bugs (the model was handed the answer).
"""
import argparse, json, glob, os
from pathlib import Path
from openai import OpenAI
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT

def oracle_context(item):
    hl = item.get("hidden_labels", {})
    lines = ["## 权威指南知识（已确认完整、正确、适用于本患者）"]
    act = hl.get("clinical_action_canonical", "")
    if act:
        lines.append(f"- 适用的临床结论：{act}")
    for k, v in (hl.get("key_values_gold", {}) or {}).items():
        lines.append(f"- {k} = {v}")
    basis = hl.get("arbitration_basis", [])
    if basis:
        lines.append(f"- 依据：{'、'.join(basis)}（应据此作答）")
    return "\n".join(lines)

def load_items(cap):
    items = []
    for layer, f in [("core","core"),("temporal","temporal"),("drug-label","drug_label"),("combination","combination")]:
        p = glob.glob(f"../output/{f}/*_latest.json")
        if p:
            lst = json.load(open(p[0], encoding="utf-8"))
            if cap: lst = lst[:cap]
            for it in lst:
                it["_layer"] = layer; items.append(it)
    return items

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--layers", default="core,temporal")
    ap.add_argument("--max-per-layer", type=int, default=10)
    ap.add_argument("--out", default="../output/ckpa_oracle_eval.json")
    args = ap.parse_args()
    client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"], base_url=os.environ.get("CKPA_EVAL_BASE_URL","https://www.micuapi.ai/v1"), timeout=90, max_retries=2)
    ev = Evaluator.__new__(Evaluator)
    want = set(args.layers.split(","))
    items = [it for it in load_items(args.max_per_layer) if it["_layer"] in want]
    print(f"items: {len(items)} | model {args.model}", flush=True)

    rows = []
    for i, it in enumerate(items):
        vp = it["visible_prompt"]
        base = EVAL_USER_PROMPT.format(scenario=vp["patient_scenario"], question=vp["question"],
                                       output_format_spec=json.dumps(vp["output_format"], ensure_ascii=False))
        rec = {"item_id": it.get("item_id"), "layer": it["_layer"], "cond": {}}
        for c, ctx in [("no_source", ""), ("oracle_gold", oracle_context(it))]:
            prompt = base if not ctx else base.replace("## 请按以下格式回复", f"## 参考来源\n{ctx}\n\n## 请按以下格式回复")
            try:
                r = client.chat.completions.create(model=args.model, temperature=0, max_tokens=600,
                    messages=[{"role":"system","content":EVAL_SYSTEM_PROMPT},{"role":"user","content":prompt}])
                raw = r.choices[0].message.content or ""; rawj = raw[raw.find("{"):] if "{" in raw else raw
                ans = ev._parse_json(rawj)
            except Exception as e:
                ans = {"_error": str(e)}
            rec["cond"][c] = {"passed": ev._score(it, ans, "")["passed"]}
        rows.append(rec)
        print(f"[{i+1}/{len(items)}] {rec['layer']:11s} {rec['item_id']:16s} ns={rec['cond']['no_source']['passed']} oracle={rec['cond']['oracle_gold']['passed']}", flush=True)
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def rate(layer, c):
        rs = [x for x in rows if x["layer"] == layer]
        return sum(x["cond"][c]["passed"] for x in rs), len(rs)
    print("\n=== no_source -> oracle_gold ===")
    for L in sorted({x["layer"] for x in rows}):
        n1 = rate(L,"no_source"); n2 = rate(L,"oracle_gold")
        print(f"  {L:12s}: no_source {n1[0]}/{n1[1]} ({n1[0]/max(1,n1[1]):.0%}) -> oracle {n2[0]}/{n2[1]} ({n2[0]/max(1,n2[1]):.0%})")

if __name__ == "__main__":
    main()
