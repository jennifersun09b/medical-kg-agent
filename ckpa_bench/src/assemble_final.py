"""Assemble the final deliverable:
  - 7 per-model JSON logs, each = core-500 + comb-500 + temporal-500 + drug-500
    (2000 items x {NS, SP}); saved to output/final_logs/<model>_2000.json
  - a 4-layer x 7-model pass-rate table (each cell scored on 500 items)
Run after the eval drivers complete (safe to run early — reports actual counts).
"""
import json, os
from collections import defaultdict

D = "/Users/henrychen/medical_agent/four_high_kdd_benchmark"
MODELS = {
    "gpt56": "gpt-5.6", "qwen37": "qwen3.7-max", "minimax": "MiniMax-M3",
    "dsv4pro": "deepseek-v4-pro", "glm52": "glm-5.2",
    "claude": "claude-opus-4-8", "kimi": "kimi-k2.6",
}
# canonical id sets per layer
def load_ids(path, layers):
    items = json.load(open(f"{D}/{path}"))
    s = defaultdict(set)
    for x in items:
        l = x.get("layer", "")
        if l in layers: s[l].add(x["item_id"])
    return s
cc_ids = load_ids("data/core_comb_1000.json", {"core", "combination"})
tp_ids = {"temporal": {x["item_id"] for x in json.load(open(f"{D}/data/temporal_500.json"))}}
dr_ids = {"drug_label": {x["item_id"] for x in json.load(open(f"{D}/data/drug_500.json"))}}
CANON = {**cc_ids, **tp_ids, **dr_ids}   # layer -> set of 500 ids
LAYERS = ["core", "temporal", "drug_label", "combination"]

def read_per_item(path):
    if not os.path.exists(path): return []
    try: d = json.load(open(path))
    except: return []
    return d.get("per_item") or d.get("results") or []

os.makedirs(f"{D}/output/final_logs", exist_ok=True)
table = {}   # model -> layer -> {mode -> (passed,total)}
for tag, name in MODELS.items():
    recs = {}   # (item_id, mode) -> record, canonical only, good preferred
    for src in [f"output/cc1000_{tag}.json", f"output/core_{tag}.json", f"output/comb_{tag}.json",
                f"output/temp500_{tag}.json", f"output/drug_{tag}.json"]:
        for r in read_per_item(f"{D}/{src}"):
            if not isinstance(r, dict): continue
            iid, mode, layer = r.get("item_id"), r.get("mode"), r.get("layer")
            if layer not in CANON or iid not in CANON[layer]: continue
            if mode not in ("no_source", "ns_hint", "source_provided", "sp_hint"): continue
            k = (iid, mode)
            is_err = "_error" in (r.get("model_answer") or {})
            # prefer a good record over an errored one
            if k in recs and "_error" not in (recs[k].get("model_answer") or {}):
                continue
            recs[k] = r
    records = list(recs.values())
    # per-model log
    json.dump({"model": name, "n_records": len(records), "per_item": records},
              open(f"{D}/output/final_logs/{tag}_4modes.json", "w"), ensure_ascii=False, indent=2)
    # table stats
    by = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # layer -> mode -> [passed,total]
    for r in records:
        if "_error" in (r.get("model_answer") or {}): continue
        by[r["layer"]][r["mode"]][0] += 1 if r.get("passed") else 0
        by[r["layer"]][r["mode"]][1] += 1
    table[tag] = by

# print 4-mode x 4-layer x 7-model table
lab = {"core": "Core", "temporal": "Temporal", "drug_label": "Drug", "combination": "Comb"}
MODE_ORDER = [("no_source","NS"), ("ns_hint","NS+H"), ("source_provided","SP"), ("sp_hint","SP+H")]
print(f"\n{'='*100}\nCKPA-Bench FINAL — 4 modes x 4 layers x 7 models (% correct; N shown if <500)\n{'='*100}")
for layer in LAYERS:
    print(f"\n## {lab[layer]}")
    print(f"{'model':16s} " + " ".join(f"{lbl:>10s}" for _,lbl in MODE_ORDER))
    for tag, name in MODELS.items():
        cells=[]
        for mode,_ in MODE_ORDER:
            v = table[tag].get(layer, {}).get(mode, [0,0])
            cells.append(f"{100*v[0]/v[1]:.0f}%" + (f"({v[1]})" if v[1] and v[1]<500 else "") if v[1] else "-")
        print(f"{name:16s} " + " ".join(f"{c:>10s}" for c in cells))
print(f"\nPer-model logs (8000 records each) -> {D}/output/final_logs/<tag>_4modes.json")
