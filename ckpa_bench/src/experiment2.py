#!/usr/bin/env python3
"""Experiment 2 — 充分证据条件下的模型判断能力测试.

来自 medical_guideline_bias 提案的实验二：
    在同一批 benchmark 题目上，比较模型在三种证据条件下的判断能力：
      1. no_retrieval   无检索（闭卷，只有场景+问题）—— 模型内部先验
      2. rag            普通 RAG（BM25 从语料检索 top-k 段落注入）
      3. full_evidence  充分证据（人工给定完整、正确、适用的指南证据，
                        并允许包含一定超量信息 / distractor，模型需自行选出适用标准）

判读逻辑：
    若 full_evidence 相比 no_retrieval 大幅提升 → 模型具备基本推理能力，
    主要瓶颈在证据访问（支持提案核心假设）。
    若 full_evidence 下仍大量出错 → 需进一步考虑推理 / 指令遵循 / 概念理解。

用法：
    export CKPA_EVAL_PROVIDER=openai        # 或 anthropic / deepseek
    export CKPA_EVAL_API_KEY=sk-...
    python experiment2.py --input ../output/final/ckpa_passed.json --max-items 40
    python experiment2.py --provider anthropic --model claude-sonnet-4-5 --distractors 2
"""

import argparse
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

from config import PathConfig
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT

# The three evidence conditions of Experiment 2, in report order.
CONDITIONS = ["no_retrieval", "rag", "full_evidence"]

# Provider presets — the evaluator talks to any OpenAI-compatible API.
PROVIDERS = {
    "deepseek":  {"base_url": "https://api.deepseek.com",      "model": "deepseek-chat"},
    "openai":    {"base_url": "https://api.openai.com/v1",     "model": "gpt-4o-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1/", "model": "claude-sonnet-4-5"},
}


def resolve_backend(provider: Optional[str], model: Optional[str],
                    base_url: Optional[str], api_key: Optional[str]) -> Dict[str, str]:
    provider = provider or os.environ.get("CKPA_EVAL_PROVIDER", "deepseek")
    preset = PROVIDERS.get(provider, PROVIDERS["deepseek"])
    key = (api_key or os.environ.get("CKPA_EVAL_API_KEY")
           or os.environ.get(f"{provider.upper()}_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY", ""))
    return {
        "provider": provider,
        "base_url": base_url or os.environ.get("CKPA_EVAL_BASE_URL") or preset["base_url"],
        "model":    model or os.environ.get("CKPA_EVAL_MODEL") or preset["model"],
        "api_key":  key,
    }


# ------------------------------------------------------------------
# Chinese-aware BM25 (the repo's BM25 tokenizes English only).
# Char-bigrams for CJK + whole ASCII tokens — good enough for top-k recall.
# ------------------------------------------------------------------
def cjk_tokenize(text: str) -> List[str]:
    text = text.lower()
    toks: List[str] = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[一-鿿]", text)
    toks += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]  # bigrams
    return toks


class BM25:
    def __init__(self, docs: List[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tokens = [cjk_tokenize(d) for d in docs]
        self.doc_len = [len(t) for t in self.tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.freqs = [Counter(t) for t in self.tokens]
        df: Counter = Counter()
        for t in self.tokens:
            df.update(set(t))
        n = max(1, len(docs))
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, top_k: int = 3) -> List[Tuple[int, float]]:
        q = cjk_tokenize(query)
        scored: List[Tuple[int, float]] = []
        for i, freq in enumerate(self.freqs):
            dl = self.doc_len[i] or 1
            s = 0.0
            for w in q:
                tf = freq.get(w)
                if not tf:
                    continue
                idf = self.idf.get(w, 0.0)
                s += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((i, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


def _flatten_strings(obj, out: List[str], budget: int = 3000) -> None:
    if sum(len(x) for x in out) > budget:
        return
    if isinstance(obj, str):
        if len(obj) >= 4:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_strings(v, out, budget)
    elif isinstance(obj, list):
        for v in obj:
            _flatten_strings(v, out, budget)


def build_corpus(paths: PathConfig, max_docs: int = 3000) -> List[str]:
    """Load DXY decisions + guideline summaries as retrievable passages."""
    corpus: List[str] = []
    for src in (paths.dxy_decisions, paths.dxy_guidelines):
        if not src.exists():
            continue
        with open(src, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_docs:
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                strs: List[str] = []
                title = str(r.get("title", ""))
                _flatten_strings(r.get("json_", {}), strs)
                doc = (title + " " + " ".join(strs)).strip()
                if len(doc) >= 20:
                    corpus.append(doc[:1500])
    return corpus


class Experiment2Evaluator(Evaluator):
    """Evaluator wired to a chosen provider + the three evidence conditions."""

    def __init__(self, backend: Dict[str, str], corpus: Optional[List[str]] = None,
                 rag_top_k: int = 3, distractors: int = 2):
        # Skip Evaluator.__init__ (it builds a DeepSeek client) — set fields directly.
        self.api_cfg = None
        self.model = backend["model"]
        self.client = OpenAI(api_key=backend["api_key"], base_url=backend["base_url"])
        self.results: List[dict] = []
        self.corpus = corpus or []
        self.bm25 = BM25(self.corpus) if self.corpus else None
        self.rag_top_k = rag_top_k
        self.distractors = distractors

    # -- context builders per condition --------------------------------
    def _rag_passages(self, item: dict, k: int) -> List[str]:
        if not self.bm25:
            return []
        vp = item["visible_prompt"]
        query = f"{vp['patient_scenario']} {vp['question']}"
        hits = self.bm25.search(query, top_k=k)
        return [self.corpus[i][:500] for i, _ in hits]

    def _context_for(self, item: dict, condition: str) -> str:
        if condition == "no_retrieval":
            return ""
        if condition == "rag":
            passages = self._rag_passages(item, self.rag_top_k)
            return "\n\n".join(f"[检索片段{n+1}]\n{p}" for n, p in enumerate(passages))
        if condition == "full_evidence":
            gold = self._build_source_context(item)  # gold source A/B quotes
            blocks = [gold] if gold else []
            # 超量信息：附带若干 BM25 干扰片段，模型需自行选出适用标准
            if self.distractors:
                for n, p in enumerate(self._rag_passages(item, self.distractors)):
                    blocks.append(f"[补充资料{n+1}]\n{p}")
            return "\n\n".join(blocks)
        return ""

    def run_condition(self, items: List[dict], condition: str) -> List[dict]:
        results = []
        for i, item in enumerate(items):
            vp = item["visible_prompt"]
            prompt = EVAL_USER_PROMPT.format(
                scenario=vp["patient_scenario"],
                question=vp["question"],
                output_format_spec=json.dumps(vp["output_format"], ensure_ascii=False),
            )
            ctx = self._context_for(item, condition)
            if ctx:
                prompt = prompt.replace(
                    "## 请按以下格式回复",
                    f"## 参考来源\n{ctx}\n\n## 请按以下格式回复",
                )
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=600,
                    temperature=0,
                )
                raw = r.choices[0].message.content or ""
                raw_json = raw[raw.find("{"):] if "{" in raw else raw
                answer = self._parse_json(raw_json)
            except Exception as e:  # noqa
                raw, answer = str(e), {"_error": str(e)}

            scored = self._score(item, answer, raw)
            parts = item.get("item_id", "").split("-")
            scored["layer"] = item.get("layer") or (parts[2][:3] if len(parts) >= 3 else "?")
            scored["item_id"] = item.get("item_id", f"item_{i}")
            scored["condition"] = condition
            results.append(scored)
            if (i + 1) % 20 == 0:
                p = sum(1 for x in results if x["passed"])
                print(f"  [{condition}] {i+1}/{len(items)}: {p}/{i+1} ({100*p/(i+1):.0f}%)")
        return results

    def run_all(self, items: List[dict]) -> dict:
        per_cond: Dict[str, List[dict]] = {}
        for cond in CONDITIONS:
            print(f"=== 条件: {cond}  ({len(items)} 题) ===")
            per_cond[cond] = self.run_condition(items, cond)

        def rate(rs):
            return sum(1 for r in rs if r["passed"]) / max(1, len(rs))

        rates = {c: rate(per_cond[c]) for c in CONDITIONS}
        layers = sorted({r["layer"] for r in per_cond["no_retrieval"]})
        by_layer = {
            c: {ly: rate([r for r in per_cond[c] if r["layer"] == ly]) for ly in layers}
            for c in CONDITIONS
        }
        # per-item transition no_retrieval -> full_evidence
        nr = {r["item_id"]: r["passed"] for r in per_cond["no_retrieval"]}
        fe = {r["item_id"]: r["passed"] for r in per_cond["full_evidence"]}
        trans = {"right_right": 0, "right_wrong": 0, "wrong_right": 0, "wrong_wrong": 0}
        for iid, a in nr.items():
            b = fe.get(iid, False)
            trans[("right" if a else "wrong") + "_" + ("right" if b else "wrong")] += 1
        return {
            "backend_model": self.model,
            "n_items": len(items),
            "rates": rates,
            "rag_gain": rates["rag"] - rates["no_retrieval"],
            "evidence_gain": rates["full_evidence"] - rates["no_retrieval"],
            "by_layer": by_layer,
            "transitions_nr_to_fe": trans,
            "results": per_cond,
        }


def interpret(summary: dict) -> str:
    r = summary["rates"]
    eg = summary["evidence_gain"]
    fe = r["full_evidence"]
    lines = []
    if eg >= 0.15 and fe >= 0.6:
        lines.append("→ 充分证据大幅提升且绝对准确率高：支持核心假设——瓶颈在证据访问，而非推理能力。")
    elif eg >= 0.15 and fe < 0.6:
        lines.append("→ 充分证据有明显提升但绝对准确率仍偏低：证据访问是瓶颈之一，但推理/概念理解仍需改进。")
    else:
        lines.append("→ 充分证据提升有限：瓶颈更可能在模型推理、指令遵循或医学概念理解，而非单纯证据访问。")
    if r["rag"] <= r["no_retrieval"] + 0.03:
        lines.append("→ 普通 RAG 几乎无增益：现成检索没能命中关键证据，凸显 evidence-seeking 的必要性。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="CKPA-Bench Experiment 2 runner")
    ap.add_argument("--input", default=None, help="passed items JSON (default: output/final/ckpa_passed.json)")
    ap.add_argument("--provider", default=None, choices=list(PROVIDERS))
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--max-items", type=int, default=0)
    ap.add_argument("--rag-top-k", type=int, default=3)
    ap.add_argument("--distractors", type=int, default=2, help="超量干扰片段数（充分证据条件）")
    ap.add_argument("--corpus-max-docs", type=int, default=3000)
    ap.add_argument("--output", default="../output/experiment2_result.json")
    args = ap.parse_args()

    backend = resolve_backend(args.provider, args.model, args.base_url, args.api_key)
    if not backend["api_key"]:
        raise SystemExit("No API key. Set CKPA_EVAL_API_KEY or pass --api-key.")

    paths = PathConfig()
    input_path = args.input or str(paths.output_dir / "final" / "ckpa_passed.json")
    if not Path(input_path).exists():
        raise SystemExit(f"Benchmark items not found: {input_path}\n"
                         f"先构建题目: python run_forge.py --mode sanity")
    items = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if args.max_items:
        items = items[:args.max_items]

    print(f"Backend: {backend['provider']} · {backend['model']}  |  题目: {len(items)}")
    print("Building Chinese BM25 corpus (DXY decisions + guidelines)...")
    corpus = build_corpus(paths, max_docs=args.corpus_max_docs)
    print(f"Corpus: {len(corpus)} passages\n")

    ev = Experiment2Evaluator(backend, corpus=corpus,
                              rag_top_k=args.rag_top_k, distractors=args.distractors)
    summary = ev.run_all(items)

    print("\n" + "=" * 58)
    print("Experiment 2 — 充分证据条件下的模型判断能力")
    print("=" * 58)
    for c in CONDITIONS:
        print(f"  {c:14s}: {summary['rates'][c]:.0%}")
    print(f"  {'RAG 增益':14s}: {summary['rag_gain']:+.1%}")
    print(f"  {'充分证据增益':10s}: {summary['evidence_gain']:+.1%}")
    print("\n  分层 (no_retrieval / rag / full_evidence):")
    for ly in sorted(next(iter(summary['by_layer'].values())).keys()):
        a = summary['by_layer']['no_retrieval'][ly]
        b = summary['by_layer']['rag'][ly]
        c = summary['by_layer']['full_evidence'][ly]
        print(f"    {ly:6s}  {a:.0%} / {b:.0%} / {c:.0%}")
    t = summary["transitions_nr_to_fe"]
    print(f"\n  无检索→充分证据 转移: R→R {t['right_right']}  W→R {t['wrong_right']}  "
          f"R→W {t['right_wrong']}  W→W {t['wrong_wrong']}")
    print("\n" + interpret(summary))

    Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved → {args.output}")


if __name__ == "__main__":
    main()
