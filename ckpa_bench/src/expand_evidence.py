"""Reconstruct a FULLER open-book evidence bundle from the original source files.

The current CKPA source_provided mode only feeds source_a_quote[:500] / source_b_quote[:500]
(the snippets minted at construction time). This module rebuilds a richer bundle from the
files the item points to via provenance, so we can test whether low open-book accuracy is
evidence insufficiency vs. true arbitration failure.

Minimal, additive — does not touch pipeline.py / evaluator.py.
"""
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

from config import PathConfig
from content_extractor import extract_sections
from data_loader import extract_drug_label_fields

P = PathConfig()
_dec = None
_gl = None


def decision_index() -> Dict[str, dict]:
    global _dec
    if _dec is None:
        _dec = {}
        with open(P.dxy_decisions, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(r.get("src_id", ""))
                if sid:
                    _dec[sid] = r
    return _dec


def guideline_index() -> Dict[str, dict]:
    global _gl
    if _gl is None:
        _gl = {}
        with open(P.dxy_guidelines, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(r.get("src_id", ""))
                if sid:
                    _gl[sid] = r
    return _gl


def drug_rows(names: List[str]) -> Dict[str, dict]:
    """Stream the 850MB CSV once, collect full rows for the requested drug names."""
    want = [n for n in names if n]
    if not want:
        return {}
    found: Dict[str, dict] = {}
    with open(P.drug_csv, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            nm = (row.get("规范名称（中文）", "") or "").strip()
            if not nm:
                continue
            for w in want:
                if w and w not in found and (w in nm or nm in w):
                    found[w] = row
            if len(found) >= len(want):
                break
    return found


def _amboss_text(pdf_name: str, around: str = "", limit: int = 2500) -> str:
    if not pdf_name:
        return ""
    base = P.project_root / "专业知识/四高语料/指南/AMBOSS指南"
    path = base / pdf_name
    if not path.exists():
        cand = list(base.glob(pdf_name[:20] + "*")) or list(base.glob("*" + pdf_name[-20:]))
        if cand:
            path = cand[0]
    if not path.exists():
        return ""
    try:
        from pdf_extractor import extract_pdf_text
        txt = extract_pdf_text(path)
    except Exception:
        return ""
    if around and around[:30] in txt:
        i = txt.find(around[:30])
        return txt[max(0, i - 400): i + limit]
    return txt[:limit]


def _decision_section_text(src_id: str, section_hint: str = "") -> str:
    rec = decision_index().get(str(src_id))
    if not rec:
        return ""
    secs = extract_sections(rec)
    if section_hint:
        for k, v in secs.items():
            if section_hint in k:
                return v[:3000]
    # else concat treatment/diagnosis sections
    picked = [v for k, v in secs.items() if any(w in k for w in ["治疗", "诊断", "控制", "用药"])]
    return (" \n".join(picked) or " ".join(secs.values()))[:3000]


def _dxy_pdf_text(src_id: str, limit: int = 2600) -> str:
    # Prefer the already-downloaded cache: output/dxy_pdf_cache/{src_id}_*.pdf
    cache = list((P.output_dir / "dxy_pdf_cache").glob(f"{src_id}_*.pdf"))
    if cache:
        try:
            from pdf_extractor import extract_pdf_text
            txt = extract_pdf_text(cache[0])
            if txt:
                return txt[:limit]
        except Exception:
            pass
    rec = guideline_index().get(str(src_id))
    if not rec:
        return ""
    try:
        from pdf_extractor import extract_dxy_pdf_text
        return (extract_dxy_pdf_text(str(src_id), rec) or "")[:limit]
    except Exception:
        return ""


DRUG_LABEL_KEYS = ["contraindications", "precautions", "interactions", "dosage",
                   "special_populations", "pediatric", "geriatric", "pregnancy", "adverse_reactions"]
DRUG_LABEL_CN = {"contraindications": "禁忌", "precautions": "慎用/注意事项", "interactions": "药物相互作用",
                 "dosage": "用法用量", "special_populations": "特殊人群用药", "pediatric": "儿童用药",
                 "geriatric": "老年人用药", "pregnancy": "孕妇用药", "adverse_reactions": "不良反应"}


def _drug_full_label(row: dict) -> str:
    f = extract_drug_label_fields(row)
    parts = [f"【{f['drug_name']}】"]
    for k in DRUG_LABEL_KEYS:
        v = (f.get(k) or "").strip()
        if v and v not in ("尚不明确", "无", "暂无"):
            parts.append(f"[{DRUG_LABEL_CN[k]}] {v[:600]}")
    return "\n".join(parts)


def build_expanded_context(item: dict) -> str:
    """Return a fuller evidence bundle rebuilt from original files. Falls back to
    the stored quotes if reconstruction yields nothing."""
    raw_layer = (item.get("_layer") or item.get("layer") or "").upper()
    if raw_layer.startswith("CORE"):
        layer = "CORE"
    elif raw_layer.startswith(("TMP", "TEMP")):
        layer = "TEMPORAL"
    elif raw_layer.startswith(("DRUG", "DRUG-LABEL", "DRUG_LABEL", "DL")):
        layer = "DRUG-LABEL"
    elif raw_layer.startswith(("CMB", "COMB")):
        layer = "COMBINATION"
    else:
        layer = raw_layer
    prov = item.get("provenance", {})
    if isinstance(prov, str):
        try:
            prov = json.loads(prov)
        except Exception:
            prov = {}
    blocks: List[str] = []

    if layer == "CORE":
        a = _decision_section_text(prov.get("src_id_dxy", ""), prov.get("section_dxy", ""))
        b = _amboss_text(prov.get("amboss_pdf", ""), prov.get("source_b_quote", ""))
        if a: blocks.append("来源A（中国DXY临床决策 · 完整章节）:\n" + a)
        if b: blocks.append("来源B（国际AMBOSS指南 · 扩展原文）:\n" + b)

    elif layer == "TEMPORAL":
        old = _dxy_pdf_text(prov.get("old_src_id", ""))
        new = _dxy_pdf_text(prov.get("new_src_id", ""))
        if old: blocks.append(f"旧版指南（src {prov.get('old_src_id')} · {prov.get('old_quote','')}）:\n" + old)
        if new: blocks.append(f"新版指南（src {prov.get('new_src_id')} · {prov.get('new_quote','')}）:\n" + new)

    elif layer in ("DRUG-LABEL", "DRUG_LABEL", "DRUGLABEL"):
        name = prov.get("drug_name") or prov.get("drug") or prov.get("规范名称") or ""
        rows = drug_rows([name]) if name else {}
        if rows:
            blocks.append("来源A（药品说明书 · 完整安全字段）:\n" + _drug_full_label(list(rows.values())[0]))
        b = _decision_section_text(prov.get("src_id_dxy", ""))
        if b: blocks.append("来源B（DXY临床决策 · 相关章节）:\n" + b)

    elif layer == "COMBINATION":
        da = prov.get("drug_a", ""); db = prov.get("drug_b", "")
        rows = drug_rows([da, db])
        for key, lab in [(da, "药品A"), (db, "药品B")]:
            if key in rows:
                blocks.append(f"{lab}（{key} · 完整说明书）:\n" + _drug_full_label(rows[key]))

    if not blocks:  # fallback to stored quotes
        for k, lab in [("source_a_quote", "来源A"), ("source_b_quote", "来源B")]:
            q = prov.get(k, "")
            if q:
                blocks.append(f"{lab}:\n{q}")
    return "\n\n".join(blocks)
