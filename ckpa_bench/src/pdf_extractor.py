"""Extract and index AMBOSS international guideline PDFs.

V3 improvements:
- Multi-parser fallback (PyPDF2 → pdfplumber → pymupdf)
- Full PDF reading (no page limit)
- BM25 retrieval for relevant chunk selection
- Multi-PDF candidates per disease
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AMBOSS_DIR = Path(__file__).parent.parent / "专业知识" / "四高语料" / "指南" / "AMBOSS指南"

DISEASE_MAP = {
    "hypertension": {
        "cn": "高血压",
        "en_keywords": ["hypertension", "blood pressure", "hypertensive", "sbp", "dbp",
                        "antihypertensive", "diuretic", "ace inhibitor", "arb"],
        "cn_keywords": ["高血压", "血压", "降压", "心血管", "冠心病", "心衰", "心力衰竭",
                        "心肌梗死", "脑卒中", "脑血管", "肾脏病", "慢性肾病", "蛋白尿",
                        "钙通道阻滞", "血管紧张素", "氨氯地平", "硝苯地平"],
    },
    "diabetes": {
        "cn": "糖尿病",
        "en_keywords": ["diabetes", "diabetic", "glycemic", "glucose", "insulin",
                        "hypoglycemi", "hba1c", "metformin", "sglt2", "glp1"],
        "cn_keywords": ["糖尿病", "血糖", "糖化", "胰岛素", "二甲双胍", "口服降糖",
                        "糖尿病肾病", "糖尿病足", "酮症酸中毒", "高血糖", "低血糖"],
    },
    "dyslipidemia": {
        "cn": "高脂血症",
        "en_keywords": ["lipid", "cholesterol", "dyslipidemia", "triglyceride",
                        "statin", "ldl", "hdl", "lipoprotein", "atorvastatin",
                        "rosuvastatin", "ezetimibe", "pcsk9"],
        "cn_keywords": ["血脂", "高脂血症", "胆固醇", "甘油三酯", "低密度脂蛋白",
                        "他汀", "阿托伐他汀", "瑞舒伐他汀", "降脂", "动脉粥样硬化"],
    },
    "obesity": {
        "cn": "肥胖",
        "en_keywords": ["obesity", "overweight", "weight management", "weight loss",
                        "bariatric", "bmi", "body mass", "waist circumference"],
        "cn_keywords": ["肥胖", "超重", "体重", "减重", "BMI", "腰围", "代谢综合征"],
    },
}


# ---- Extraction ----

def _try_pypdf2(path: Path) -> Optional[str]:
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        return None


def _try_pdfplumber(path: Path) -> Optional[str]:
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return None


def _try_pymupdf(path: Path) -> Optional[str]:
    try:
        import fitz
        doc = fitz.open(str(path))
        return "\n".join(page.get_text() for page in doc)
    except Exception:
        return None


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract full text with multi-parser fallback. No page limit."""
    for parser in [_try_pypdf2, _try_pdfplumber, _try_pymupdf]:
        text = parser(pdf_path)
        if text and len(text.strip()) > 200:
            text = re.sub(r"\s+", " ", text).strip()
            text = text.replace("%20", " ").replace("%3A", ":").replace("%2C", ",")
            text = text.replace("%2F", "/").replace("%28", "(").replace("%29", ")")
            return text
    return ""


# ---- Classification ----

def classify_pdf_disease(filename: str, text_first_500: str) -> Optional[str]:
    combined = (filename + " " + text_first_500).lower()
    for disease_key, info in DISEASE_MAP.items():
        for kw in info["en_keywords"]:
            if kw.lower() in combined:
                return disease_key
    return None


def title_from_filename(fname: str) -> str:
    t = fname.replace(".pdf", "").replace("%20", " ").replace("%3A", ":")
    t = t.replace("%2C", ",").replace("%2F", "/").replace("%28", "(").replace("%29", ")")
    return t


# ---- Indexing ----

def load_amboss_pdfs(amboss_dir: Optional[Path] = None) -> Dict[str, List[Dict]]:
    """Load and index all AMBOSS PDFs. Returns {disease_key: [docs]}"""
    amboss_dir = amboss_dir or AMBOSS_DIR
    if not amboss_dir.exists():
        logger.warning("AMBOSS directory not found: %s", amboss_dir)
        return {}

    indexed: Dict[str, List[Dict]] = {k: [] for k in DISEASE_MAP}
    pdf_files = sorted([f for f in os.listdir(amboss_dir) if f.endswith(".pdf")])

    logger.info("Loading %d AMBOSS PDFs (multi-parser fallback)...", len(pdf_files))
    ok = 0
    for fname in pdf_files:
        path = amboss_dir / fname
        text = extract_pdf_text(path)
        if not text or len(text) < 200:
            continue

        disease = classify_pdf_disease(fname, text[:500])
        if not disease:
            continue

        # Split into paragraphs for later retrieval
        paragraphs = _split_paragraphs(text)

        indexed[disease].append({
            "filename": fname,
            "path": str(path),
            "text": text,
            "paragraphs": paragraphs,
            "title_guess": title_from_filename(fname),
            "char_count": len(text),
            "para_count": len(paragraphs),
        })
        ok += 1

    for k, v in indexed.items():
        logger.info("  %s: %d PDFs", DISEASE_MAP[k]["cn"], len(v))
    logger.info("Total: %d PDFs extracted successfully", ok)
    return indexed


def _split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs, keeping only substantive ones."""
    paras = re.split(r'\n\s*\n|(?<=\.)\s+(?=[A-Z])', text)
    return [p.strip() for p in paras if len(p.strip()) >= 50]


# ---- BM25 Retrieval ----

def _tokenize(text: str) -> List[str]:
    """Simple English tokenizer."""
    return re.findall(r'[a-zA-Z0-9]+', text.lower())


class BM25:
    """Minimal BM25 for paragraph retrieval."""
    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.paragraphs: List[str] = []
        self.doc_lens: List[int] = []
        self.avgdl: float = 0
        self.df: Dict[str, int] = {}
        self.N: int = 0

    def index(self, paragraphs: List[str]):
        self.paragraphs = paragraphs
        self.N = len(paragraphs)
        self.doc_lens = []
        self.df = {}
        for p in paragraphs:
            tokens = _tokenize(p)
            self.doc_lens.append(len(tokens))  # all tokens, not unique
            for t in set(tokens):
                self.df[t] = self.df.get(t, 0) + 1
        self.avgdl = sum(self.doc_lens) / max(1, self.N)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        if self.N == 0:
            return []
        q_tokens = _tokenize(query)
        scores = []
        for i, para in enumerate(self.paragraphs):
            score = 0.0
            doc_tokens = _tokenize(para)
            doc_len = len(doc_tokens)
            tf = {}
            for t in doc_tokens:
                tf[t] = tf.get(t, 0) + 1
            for t in q_tokens:
                if t not in self.df:
                    continue
                idf = max(0, __import__('math').log((self.N - self.df[t] + 0.5) / (self.df[t] + 0.5) + 1))
                f = tf.get(t, 0)
                numerator = f * (self.k1 + 1)
                denominator = f + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                score += idf * numerator / denominator
            scores.append((i, score))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


# ---- Retrieval helpers ----

def build_bm25_for_disease(amboss_index: Dict[str, List[Dict]],
                           disease_key: str) -> Optional[BM25]:
    """Build a BM25 index across all PDFs for a disease."""
    pdfs = amboss_index.get(disease_key, [])
    all_paras = []
    for pdf in pdfs:
        all_paras.extend(pdf.get("paragraphs", []))
    if not all_paras:
        return None
    bm = BM25()
    bm.index(all_paras)
    return bm


def retrieve_relevant_chunks(amboss_index: Dict[str, List[Dict]],
                             disease_key: str,
                             query: str,
                             top_k: int = 5,
                             max_total_chars: int = 3000) -> str:
    """Retrieve the most relevant paragraphs across all PDFs for a disease using BM25."""
    bm = build_bm25_for_disease(amboss_index, disease_key)
    if not bm:
        return ""

    results = bm.search(query, top_k=top_k)
    chunks = []
    total = 0
    for idx, score in results:
        para = bm.paragraphs[idx]
        if total + len(para) > max_total_chars:
            remaining = max_total_chars - total
            if remaining > 100:
                chunks.append(para[:remaining])
            break
        chunks.append(para)
        total += len(para)

    return "\n\n".join(chunks)


def find_specific_recommendation(text: str, disease: str, decision_point: str,
                                 max_chars: int = 2000) -> str:
    """Legacy keyword-based retrieval. Kept for backward compatibility.
    Prefer retrieve_relevant_chunks() for new code."""
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return text[:max_chars]

    keywords = ["recommend", "should", "must", "guideline", "target", "threshold",
                "treatment", "therapy", "manage", "initiate", "dose", "diagnos",
                "screen", "monitor", "indication", "contraindication",
                "mmHg", "mg", "mmol", "HbA1c", "LDL", "BP "]
    scored = []
    dp_lower = decision_point.lower()
    for p in paragraphs:
        score = 0
        p_lower = p.lower()
        for kw in DISEASE_MAP.get(disease, {}).get("en_keywords", []):
            if kw.lower() in p_lower:
                score += 1
        dp_words = set(re.findall(r'\w+', dp_lower))
        r_words = set(re.findall(r'\w+', p_lower))
        score += len(dp_words & r_words) * 2
        if any(kw in p_lower for kw in keywords):
            score += 1
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    top = [r for _, r in scored[:5]]
    return " ".join(top)[:max_chars]

# ---- DXY Guideline PDF download + extraction ----

import requests
import hashlib
import time

DXY_CACHE_DIR = Path(__file__).parent.parent / "output" / "dxy_pdf_cache"


def download_dxy_pdf(pdf_url: str, src_id: str) -> Optional[Path]:
    """Download a DXY guideline PDF, cache locally. Returns cached path or None."""
    if not pdf_url:
        return None

    cache_name = f"{src_id}_{hashlib.md5(pdf_url.encode()).hexdigest()[:8]}.pdf"
    cache_path = DXY_CACHE_DIR / cache_name

    DXY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path

    try:
        logger.info("Downloading DXY PDF %s -> %s", src_id, cache_name)
        r = requests.get(pdf_url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200 and len(r.content) > 1000:
            tmp_path = cache_path.with_suffix('.tmp')
            with open(tmp_path, 'wb') as f:
                f.write(r.content)
            tmp_path.rename(cache_path)  # atomic on same filesystem
            time.sleep(0.5)  # Be polite to CDN
            return cache_path
    except Exception as e:
        logger.warning("Failed to download DXY PDF %s: %s", src_id, e)
    return None


def extract_dxy_pdf_text(src_id: str, raw_guideline: dict, max_pages: int = 30) -> str:
    """Download and extract text from a DXY guideline PDF.
    Returns full text or falls back to summary."""
    extra = raw_guideline.get('extra', {})
    pdf_url = extra.get('pdf_url', '')

    if pdf_url:
        pdf_path = download_dxy_pdf(pdf_url, src_id)
        if pdf_path:
            text = _try_pymupdf(pdf_path) or _try_pypdf2(pdf_path) or _try_pdfplumber(pdf_path)
            if text and len(text.strip()) > 500:
                return text

    # Fallback to summary
    j = raw_guideline.get('json_', {})
    return (j.get('summary') or raw_guideline.get('title', ''))
