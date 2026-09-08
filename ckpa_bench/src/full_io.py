#!/usr/bin/env python3
"""Produce ONE auditable artifact with the exact INPUT and OUTPUT for all three
conditions (no_source / source_provided / oracle) per item.
Output: output/faithful_ckpa_full_io.json
"""
import json, glob, os
from pathlib import Path
from openai import OpenAI
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT
from oracle_experiment import oracle_context

ROOT = Path(__file__).resolve().parent.parent
ev = Evaluator.__new__(Evaluator)
client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"],
                base_url=os.environ.get("CKPA_EVAL_BASE_URL","https://api.openai.com/v1"),
                timeout=90, max_retries=4)
MODEL = os.environ.get("CKPA_EVAL_MODEL", "gpt-4o")

# the 50 chosen items (order/ids), and the full original items (hidden_labels/provenance) by id
chosen = [r["item_id"] for r in json.loads((ROOT/"output/faithful_ckpa_fixed_50_items.json").read_text(encoding="utf-8"))]
full = {}
for L in ["core","temporal","drug_label","combination"]:
    for f in glob.glob(str(ROOT/f"output/{L}/ckpa_*_latest.json")):
        for it in json.load(open(f, encoding="utf-8")):
            it["_layer"] = L; full[it["item_id"]] = it

def run(item, condition):
    vp = item["visible_prompt"]
    base = EVAL_USER_PROMPT.format(scenario=vp["patient_scenario"], question=vp["question"],
                                   output_format_spec=json.dumps(vp.get("output_format", {}), ensure_ascii=False))
    if condition == "no_source":
        ctx = ""
    elif condition == "source_provided":
        ctx = ev._build_source_context(item)
    else:  # oracle
        ctx = oracle_context(item)
    prompt = base if not ctx else base.replace("## 请按以下格式回复", f"## 参考来源\n{ctx}\n\n## 请按以下格式回复")
    try:
        r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=600,
            messages=[{"role":"system","content":EVAL_SYSTEM_PROMPT},{"role":"user","content":prompt}])
        raw = r.choices[0].message.content or ""
        parsed = ev._parse_json(raw[raw.find("{"):] if "{" in raw else raw)
    except Exception as e:
        raw, parsed = str(e), {"_error": str(e)}
    return {"input_evidence": ctx or "(none — closed book)", "output_text": raw,
            "output_parsed": parsed, "pass": ev._score(item, parsed, raw)["passed"]}

out = []
for i, iid in enumerate(chosen):
    it = full.get(iid)
    if not it:
        continue
    vp = it["visible_prompt"]
    rec = {"item_id": iid, "layer": it["_layer"],
           "shared_input": {"scenario": vp["patient_scenario"], "question": vp["question"]},
           "gold": it.get("hidden_labels", {}).get("key_values_gold"),
           "conditions": {c: run(it, c) for c in ["no_source", "source_provided", "oracle"]}}
    out.append(rec)
    p = {c: rec["conditions"][c]["pass"] for c in ["no_source","source_provided","oracle"]}
    print(f"[{i+1}/{len(chosen)}] {rec['layer']:11s} {iid:16s} ns={p['no_source']} sp={p['source_provided']} oracle={p['oracle']}", flush=True)
    (ROOT/"output/faithful_ckpa_full_io.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

def rate(c): return sum(1 for r in out if r["conditions"][c]["pass"])/max(1,len(out))
print(f"\nOVERALL n={len(out)}  no_source={rate('no_source'):.0%}  source_provided={rate('source_provided'):.0%}  oracle={rate('oracle'):.0%}")
