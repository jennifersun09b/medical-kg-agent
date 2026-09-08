"""Load and preprocess raw data sources for CKPA benchmark construction."""

import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from config import PathConfig

logger = logging.getLogger(__name__)


def load_jsonl(path: Path, max_lines: Optional[int] = None) -> List[dict]:
    """Load a JSONL file into a list of dicts."""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            try:
                items.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON at line %d in %s", i, path)
    logger.info("Loaded %d items from %s", len(items), path.name)
    return items


def load_drug_csv(path: Path, max_rows: Optional[int] = None) -> List[dict]:
    """Load drug details CSV with proper encoding handling."""
    items = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if max_rows and i >= max_rows:
                break
            items.append(dict(row))
    logger.info("Loaded %d rows from %s", len(items), path.name)
    return items


def extract_guideline_meta(raw: dict) -> Dict:
    """Extract key metadata from a DXY guideline entry."""
    j = raw.get("json_", {})
    relevant = j.get("relevantGuides") or []
    related_list = []
    for group in relevant:
        for item in group.get("list", []):
            related_list.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "maker": item.get("maker"),
                "publishDate": item.get("publishDate"),
                "readCount": item.get("readCount"),
            })

    return {
        "src_id": raw.get("src_id"),
        "title": j.get("title", raw.get("title", "")),
        "summary": j.get("summary", ""),
        "maker": j.get("maker", ""),
        "year": j.get("year", ""),
        "publishDate": j.get("publishDate", ""),
        "magazine": j.get("magazine", ""),
        "source": j.get("source", ""),
        "guideTag": j.get("guideTag"),
        "office": raw.get("extra", {}).get("office", ""),
        "relevantGuides": related_list,
    }


def extract_drug_label_fields(row: dict) -> Dict:
    """Extract key safety-relevant fields from drug CSV row."""
    return {
        "drug_name": row.get("规范名称（中文）", "").strip(),
        "trade_name": row.get("商品名称", "").strip(),
        "indications": row.get("适应症&功能主治", "").strip(),
        "dosage": row.get("用法用量", "").strip(),
        "adverse_reactions": row.get("不良反应", "").strip(),
        "contraindications": row.get("禁忌", "").strip(),
        "precautions": row.get("注意事项", "").strip(),
        "special_populations": row.get("特殊人群用药", "").strip(),
        "pediatric": row.get("儿童用药", "").strip(),
        "geriatric": row.get("老年人用药", "").strip(),
        "pregnancy": row.get("孕妇用药", "").strip(),
        "interactions": row.get("药物相互作用", "").strip(),
        "high_risk": row.get("高危药物", "").strip(),
        "overdose": row.get("药物过量", "").strip(),
    }


def filter_high_signal_drugs(drugs: List[dict]) -> List[dict]:
    """Keep only drugs with meaningful safety constraint content."""
    filtered = []
    skip_phrases = {"尚不明确", "尚不清楚", "不详", "暂无", "无", ""}
    for d in drugs:
        label = extract_drug_label_fields(d)
        # Must have non-trivial contraindications OR interactions OR special population info
        has_contra = label["contraindications"] not in skip_phrases
        has_interact = label["interactions"] not in skip_phrases
        has_special = any(
            label[f] not in skip_phrases
            for f in ["pediatric", "geriatric", "pregnancy"]
        )
        if has_contra or has_interact or has_special:
            filtered.append(label)
    logger.info(
        "Filtered to %d high-signal drugs (from %d total)",
        len(filtered), len(drugs)
    )
    return filtered


def find_guidelines_by_disease(
    guidelines: List[dict], disease_keywords: List[str]
) -> List[dict]:
    """Find guidelines matching disease keywords in title or summary."""
    results = []
    for g in guidelines:
        meta = extract_guideline_meta(g)
        text = f"{meta['title']} {meta['summary']}".lower()
        if any(kw.lower() in text for kw in disease_keywords):
            results.append(meta)
    logger.info(
        "Found %d guidelines matching %s", len(results), disease_keywords
    )
    return results
