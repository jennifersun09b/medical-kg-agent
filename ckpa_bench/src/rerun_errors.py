#!/usr/bin/env python3
"""Re-run ONLY the errored (item, mode) pairs of a neweval_{TAG}.json result file
at a higher token budget, patch them back in, and recompute mode_rates/by_layer/gaps.

Env:
  TAG                 -> which output/neweval_{TAG}.json to fix
  CKPA_API_*          -> subject model (key/base/model)
  CKPA_JUDGE_*        -> judge model (gpt-5.4-mini)
  CKPA_MAX_TOKENS     -> higher cap for the rerun
  CKPA_WORKERS        -> concurrency
"""
import json, os
from collections import defaultdict
from evaluator import Evaluator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = os.environ["TAG"]
resfile = os.path.join(ROOT, f"output/neweval_{TAG}.json")

d = json.load(open(resfile, encoding="utf-8"))
per = d["per_item"]
items_by_id = {x["item_id"]: x for x in json.load(open(os.path.join(ROOT, "data/ckpa_pipeline_200.json"), encoding="utf-8"))}
modes = d["modes"]

ev = Evaluator()  # subject from CKPA_API_*, judge from CKPA_JUDGE_*

# collect errored positions grouped by mode
err = defaultdict(list)  # mode -> [(index_in_per, item)]
for i, r in enumerate(per):
    if r.get("judge_verdict") == "error":
        err[r["mode"]].append((i, items_by_id[r["item_id"]]))
total = sum(len(v) for v in err.values())
print(f"{TAG}: re-running {total} errored items at max_tokens={os.environ.get('CKPA_MAX_TOKENS')}", flush=True)

for mode, lst in err.items():
    print(f"  [{mode}] {len(lst)} items", flush=True)
    new = ev.evaluate([it for _, it in lst], mode=mode)
    fixed = sum(1 for r in new if r.get("judge_verdict") != "error")
    print(f"  [{mode}] recovered {fixed}/{len(lst)}", flush=True)
    for (i, _), scored in zip(lst, new):
        per[i] = scored

# recompute aggregates
def rate(rs):
    return sum(1 for r in rs if r.get("passed")) / max(1, len(rs))
d["mode_rates"] = {m: rate([r for r in per if r["mode"] == m]) for m in modes}
layers = sorted(set(r["layer"] for r in per))
d["by_layer"] = {L: {m: rate([r for r in per if r["mode"] == m and r["layer"] == L]) for m in modes} for L in layers}
d["gaps"] = {f"{modes[i]} → {modes[i+1]}": d["mode_rates"][modes[i+1]] - d["mode_rates"][modes[i]] for i in range(len(modes)-1)}

json.dump(d, open(resfile, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
newerr = sum(1 for r in per if r.get("judge_verdict") == "error")
print(f"{TAG}: errors {total} -> {newerr}", flush=True)
print("  mode_rates: " + "  ".join(f"{m}={d['mode_rates'][m]:.0%}" for m in modes), flush=True)
