#!/usr/bin/env python3
"""Assemble the final 200-item CKPA pipeline set (50 per layer), fixing the
seq-reset duplicate-item_id bug by reassigning globally-unique ids per layer.

Layers & sources:
  core        -> output/core/ckpa_*_latest.json                 (target 50)
  temporal    -> output/temporal/ckpa_*_latest.json             (trim 58 -> 50)
  drug_label  -> existing43_backup.json + NEW distinct drugs
                 harvested from ckpa_drug_label_latest.json      (top up to 50)
  combination -> output/combination/ckpa_combination_latest.json (target 50)

All layers were generated with gpt-5.4-mini.
Writes data/ckpa_pipeline_200.json and prints per-layer counts + a sample.
"""
import json, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = {"core": "CORE", "temporal": "TEMPORAL", "drug_label": "DRUG", "combination": "COMB"}


def load(path):
    return json.load(open(path, encoding="utf-8"))


def latest(layer):
    fs = glob.glob(str(ROOT / f"output/{layer}/ckpa_*_latest.json"))
    return load(fs[0]) if fs else []


def content_key(it):
    prov = it.get("provenance", {}) or {}
    return (prov.get("drug_name", ""),
            it["visible_prompt"]["question"],
            json.dumps(it.get("hidden_labels", {}).get("key_values_gold"), ensure_ascii=False, sort_keys=True))


def has_quotes(it):
    prov = it.get("provenance", {}) or {}
    return bool(prov.get("source_a_quote")) and bool(prov.get("source_b_quote"))


# ---- core ----
core = latest("core")[:50]

# ---- temporal (trim to 50) ----
temporal = latest("temporal")[:50]

# ---- drug: existing 43 (verbatim) + new distinct drugs from the top-up run ----
existing = load(ROOT / "output/drug_label/existing43_backup.json")
existing_drugs = {(it.get("provenance", {}) or {}).get("drug_name", "") for it in existing}
existing_keys = {content_key(it) for it in existing}
fresh = latest("drug_label")
drug = list(existing)
for it in fresh:
    if len(drug) >= 50:
        break
    dn = (it.get("provenance", {}) or {}).get("drug_name", "")
    k = content_key(it)
    if dn and dn not in existing_drugs and k not in existing_keys:
        drug.append(it)
        existing_drugs.add(dn)
        existing_keys.add(k)
drug = drug[:50]

# ---- combination ----
combination = latest("combination")[:50]

layers = {"core": core, "temporal": temporal, "drug_label": drug, "combination": combination}

# ---- reassign globally-unique item_ids per layer ----
final = []
report = {}
for layer, items in layers.items():
    noq = 0
    for i, it in enumerate(items, 1):
        it["item_id"] = f"CKPA-V2-{PREFIX[layer]}-{i:03d}"
        it["_layer"] = layer
        if not has_quotes(it):
            noq += 1
    report[layer] = {"count": len(items), "missing_quotes": noq}
    final.extend(items)

out = ROOT / "data/ckpa_pipeline_200.json"
out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== CKPA pipeline set assembled ===")
print(f"written: {out}   total items: {len(final)}")
for layer in ["core", "temporal", "drug_label", "combination"]:
    r = report[layer]
    print(f"  {layer:12s} count={r['count']:3d}  missing_quotes={r['missing_quotes']}")
# id uniqueness check
ids = [it["item_id"] for it in final]
print(f"\nunique item_ids: {len(set(ids))} / {len(ids)}  (should be equal)")
# sample one per layer
print("\n=== sample (one per layer) ===")
seen = set()
for it in final:
    if it["_layer"] in seen:
        continue
    seen.add(it["_layer"])
    prov = it.get("provenance", {}) or {}
    print(f"\n[{it['_layer']}] {it['item_id']}  drug={prov.get('drug_name','-')}")
    print("  Q:", it["visible_prompt"]["question"][:90])
    print("  A_quote:", (prov.get("source_a_quote", "") or "")[:70])
    print("  B_quote:", (prov.get("source_b_quote", "") or "")[:70])
    print("  gold:", json.dumps(it.get("hidden_labels", {}).get("key_values_gold"), ensure_ascii=False)[:90])
