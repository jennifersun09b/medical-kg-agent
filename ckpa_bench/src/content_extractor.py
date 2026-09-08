"""Extract structured clinical content from DXY clinical decision HTML."""

import re
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup


def strip_html(html: str, max_len: Optional[int] = None) -> str:
    """Strip HTML tags, collapse whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    if max_len:
        text = text[:max_len]
    return text


def extract_sections(raw: dict) -> Dict[str, str]:
    """Extract all sections from a DXY clinical decision entry.
    Returns {section_name: plain_text_content}."""
    j = raw.get("json_", {})
    details = j.get("details", [])
    sections = {}
    for sec in details:
        prop = sec.get("property", "")
        content = sec.get("content", "")
        text = strip_html(content)
        if text and len(text) > 50:
            sections[prop] = text
    return sections


def extract_recommendations(text: str, min_len: int = 30) -> List[str]:
    """Extract recommendation-like sentences from clinical text.

    Looks for patterns like:
    - Numbered lists: '1、... 2、...'
    - Recommendation keywords: 推荐, 建议, 首选, 不宜, 禁用, 慎用, 不推荐
    - Semicolon-separated items
    """
    recs = []

    # Split on numbered markers
    chunks = re.split(r'(?<=\D)(?:\d+|（[一二三四五六七八九十]+）)[\.、．]\s*', text)
    # Also try semicolon splitting
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < min_len:
            continue
        # Does it contain recommendation language?
        keywords = ["推荐", "建议", "首选", "不宜", "禁用", "慎用", "不推荐",
                     "应采用", "可选用", "应", "需", "必须", "禁忌",
                     "一线", "二线", "标准治疗", "剂量", "mg", "μg",
                     "mmHg", "mmol/L", "≥", "≤"]
        if any(kw in chunk for kw in keywords):
            recs.append(chunk)

    # If no keyword-based recommendations found, return top-level sentences
    if not recs:
        sentences = re.split(r'[。；;]\s*', text)
        recs = [s.strip() for s in sentences if len(s.strip()) >= min_len]

    return recs


def extract_clinical_values(text: str) -> Dict[str, str]:
    """Extract structured clinical values: thresholds, drug names, etc."""
    values = {}

    # Blood pressure thresholds
    bp_match = re.search(
        r'(?:血压|收缩压|舒张压).*?(\d{2,3})\s*[-/~]\s*(\d{2,3})\s*mmHg', text
    )
    if bp_match:
        values["bp_threshold"] = f"{bp_match.group(1)}/{bp_match.group(2)}"

    # HbA1c thresholds
    hba1c_match = re.search(
        r'(?:HbA1c|糖化血红蛋白|糖化).*?(\d+\.?\d*)\s*%', text
    )
    if hba1c_match:
        values["hba1c_threshold"] = f"{hba1c_match.group(1)}%"

    # LDL thresholds
    ldl_match = re.search(
        r'(?:LDL|低密度脂蛋白).*?(\d+\.?\d*)\s*mmol/L', text
    )
    if ldl_match:
        values["ldl_threshold"] = f"{ldl_match.group(1)} mmol/L"

    # Age cutoffs
    age_match = re.search(r'(\d{1,3})\s*岁\s*(?:以上|以下|以内)', text)
    if age_match:
        values["age_cutoff"] = age_match.group(0)

    # Drug names (common patterns in Chinese clinical text)
    drug_matches = re.findall(
        r'(?:如|推荐|首选|采用|给予|使用|联合|单用|加用)\s*([\u4e00-\u9fff]{2,8}(?:注射液|片|胶囊|颗粒|口服液)?(?:[，,、\s]*[\u4e00-\u9fff]{2,8}(?:注射液|片|胶囊|颗粒|口服液)?){0,3})',
        text
    )
    if drug_matches:
        values["mentioned_drugs"] = " | ".join(drug_matches[:5])

    return values


def get_disease_name(raw: dict) -> str:
    """Extract disease name from a DXY clinical decision."""
    j = raw.get("json_", {})
    return j.get("fieldName", raw.get("title", ""))
