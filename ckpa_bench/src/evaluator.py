"""CKPA-Bench Evaluator — tests target models on no-source benchmark items."""

import json
import re
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

from config import APIConfig, PathConfig
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT, JUDGE_SYSTEM, JUDGE_USER


def _load_resume(path: Optional[str]) -> Dict[Tuple[str, str], dict]:
    """Load previously-saved GOOD (item_id, mode) scores from an output file.

    Errored entries (model_answer has '_error') are intentionally skipped so
    they get retried on the next run. Returns {} if the file is missing/unreadable.
    """
    import os
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    per = d.get("per_item") or d.get("results") or []
    out: Dict[Tuple[str, str], dict] = {}
    for r in per:
        if not isinstance(r, dict):
            continue
        ma = r.get("model_answer") or {}
        if isinstance(ma, dict) and "_error" in ma:
            continue  # retry errored items
        iid, md = r.get("item_id"), r.get("mode")
        if iid and md:
            out[(iid, md)] = r
    return out


def _atomic_write_per_item(path: Optional[str], per_item: List[dict]) -> None:
    """Atomically write a partial results file (per_item only) for crash-safe resume."""
    import os
    if not path:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"per_item": per_item}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


class Evaluator:
    """Runs CKPA-Bench evaluation on a target model."""

    def __init__(self, api_cfg: Optional[APIConfig] = None):
        import os
        self.api_cfg = api_cfg or APIConfig()
        # per-call timeout so a single hung request can't stall the whole run
        _timeout = float(os.environ.get("CKPA_TIMEOUT", "300"))
        self.client = OpenAI(
            api_key=self.api_cfg.api_key,
            base_url=self.api_cfg.base_url,
            timeout=_timeout,
            max_retries=2,
        )
        # Optional SEPARATE judge endpoint (so the subject doesn't grade itself).
        # Set CKPA_JUDGE_MODEL / CKPA_JUDGE_API_KEY / CKPA_JUDGE_BASE_URL to use it.
        self.judge_model = os.environ.get("CKPA_JUDGE_MODEL")
        if self.judge_model:
            self.judge_client = OpenAI(
                api_key=os.environ.get("CKPA_JUDGE_API_KEY", self.api_cfg.api_key),
                base_url=os.environ.get("CKPA_JUDGE_BASE_URL", self.api_cfg.base_url),
                timeout=_timeout,
                max_retries=2,
            )
        else:
            self.judge_client = self.client
            self.judge_model = self.api_cfg.model
        self.results: List[dict] = []

    def load_items(self, path: Optional[str] = None) -> List[dict]:
        path = path or str(PathConfig().output_dir / "final" / "ckpa_passed.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # Layer-specific awareness hints — tell the model WHAT conflict exists, not WHICH side to pick
    LAYER_HINTS = {
        "core":        "注意：不同国家/地区的临床指南对同一疾病的诊断阈值和治疗目标可能存在差异。该患者就诊于中国大陆医疗机构，请据此选择适用的临床标准。",
        "temporal":    "注意：同一疾病的临床指南可能在不同年份发布过多个版本，新版本可能更新了诊断标准或治疗推荐。请确认你采用的是当前最新版本的推荐。",
        "drug_label":  "注意：药品说明书中的禁忌症和特殊人群限制具有法律约束力。请在回答前核查该药品说明书中是否存在与本患者情况相关的禁忌或限制。",
        "combination": "注意：两种药物联用可能产生药物相互作用，影响药效或增加不良反应风险。请在回答前评估两药联用的安全性。",
    }

    def evaluate(self, items: List[dict], model: Optional[str] = None,
                 mode: str = "no_source") -> List[dict]:
        """Evaluate items on a target model.

        Args:
            items: CKPA benchmark items
            model: target model name (default: deepseek-chat)
            mode: 'no_source'        — scenario + question only
                  'ns_hint'          — scenario + question + layer-specific hint (no source text)
                  'source_provided'  — scenario + question + source excerpts
                  'sp_hint'          — scenario + question + source excerpts + layer-specific hint
        """
        model = model or self.api_cfg.model
        import os, threading
        from concurrent.futures import ThreadPoolExecutor
        workers = int(os.environ.get("CKPA_WORKERS", "8"))
        flush_every = int(os.environ.get("CKPA_FLUSH_EVERY", "25"))
        prescored = getattr(self, "_prescored", None) or {}
        flush_cb = getattr(self, "_flush_cb", None)
        results = [None] * len(items)
        _c = {"done": 0, "passed": 0, "reused": 0}
        _lock = threading.Lock()

        def work(i_item):
            i, item = i_item
            # RESUME: reuse a previously-saved good score for this (item_id, mode)
            _iid = item.get("item_id", f"item_{i}")
            _cached = prescored.get((_iid, mode))
            if _cached is not None:
                results[i] = _cached
                with _lock:
                    _c["done"] += 1
                    _c["reused"] += 1
                    if _cached.get("passed"):
                        _c["passed"] += 1
                return _cached
            vp = item["visible_prompt"]
            layer = item.get("layer", "")

            # Build context and hint based on mode
            context_text = ""
            hint_text = ""
            if mode in ("source_provided", "sp_hint"):
                context_text = self._build_source_context(item)
            if mode in ("ns_hint", "sp_hint"):
                hint_text = self.LAYER_HINTS.get(layer, "")

            prompt = EVAL_USER_PROMPT.format(
                scenario=vp["patient_scenario"],
                question=vp["question"],
            )
            # Insert hint after question
            if hint_text:
                prompt = prompt.replace(
                    "## 请按以下格式回复",
                    f"## 临床指导\n{hint_text}\n\n## 请按以下格式回复"
                )
            # Insert source context before hint/output
            if context_text:
                prompt = prompt.replace(
                    "## 请按以下格式回复",
                    f"## 参考来源\n{context_text}\n\n## 请按以下格式回复"
                )
            try:
                kw = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},  # force pure JSON (no prose preamble)
                }
                # gpt-5 family on native OpenAI needs max_completion_tokens + default temperature
                _tok = int(os.environ.get("CKPA_MAX_TOKENS", "3000"))  # raise for heavy-reasoning models
                if "gpt-5" in model and "api.openai.com" in str(self.client.base_url):
                    kw["max_completion_tokens"] = _tok  # reasoning models need room for reasoning + JSON
                else:
                    kw["max_tokens"] = _tok
                # some models (gpt-5, kimi k2.x reasoning) only allow the default temperature
                if not any(t in model.lower() for t in ("gpt-5", "kimi")):
                    kw["temperature"] = 0
                # robust send: progressively drop params a provider rejects (json mode, then temperature)
                last = None
                for drop in ([], ["response_format"], ["response_format", "temperature"]):
                    try:
                        r = self.client.chat.completions.create(
                            **{k: v for k, v in kw.items() if k not in drop})
                        break
                    except Exception as e:
                        last = e
                else:
                    raise last
                raw = r.choices[0].message.content
                # Extract thinking (text before JSON)
                thinking = ""
                if raw and "{" in raw:
                    json_start = raw.find("{")
                    thinking = raw[:json_start].strip()
                    raw_json = raw[json_start:]
                else:
                    raw_json = raw
                answer = self._parse_json(raw_json)
                # Cap reasoning length so verbose chains don't bloat/break the judge call.
                _rmax = int(os.environ.get("CKPA_REASONING_MAX_CHARS", "120"))
                if isinstance(answer, dict) and "reasoning" in answer:
                    answer["reasoning"] = str(answer["reasoning"])[:_rmax]
            except Exception as e:
                raw = str(e)
                thinking = ""
                answer = {"_error": str(e)}

            scored = self._score(item, answer, raw)
            lid = item.get("layer", "")
            if not lid:
                parts = item.get("item_id", "").split("-")
                lid = parts[2][:3] if len(parts) >= 3 else "?"
            scored["item_id"] = item.get("item_id", f"item_{i}")
            scored["layer"] = lid
            scored["model_answer"] = answer
            scored["model_raw"] = raw
            scored["thinking"] = thinking
            scored["mode"] = mode
            # Source preference (source_provided only)
            scored["source_preference"] = self._infer_source_preference(item, answer)
            # Error classification for failed items
            if not scored["passed"] and "_error" not in answer:
                scored.update(self._classify_error(item, answer, scored))
            results[i] = scored
            with _lock:
                _c["done"] += 1
                if scored.get("passed"):
                    _c["passed"] += 1
                d = _c["done"]
                if d % 20 == 0 or d == len(items):
                    print(f"  [{mode}] {d}/{len(items)}: {_c['passed']}/{d} passed ({100*_c['passed']/d:.0f}%) [reused {_c['reused']}]", flush=True)
                _do_flush = flush_cb is not None and (d % flush_every == 0)
            if _do_flush:
                try: flush_cb(mode, results)
                except Exception: pass
            return scored

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, enumerate(items)))
        if flush_cb is not None:
            try: flush_cb(mode, results)
            except Exception: pass
        self.results = results
        return results

    def evaluate_both(self, items: List[dict], model: Optional[str] = None,
                      output_path: Optional[str] = None) -> dict:
        """Run NS + SP and compute Arbitration Gain (backward compatible)."""
        return self.evaluate_all(items, model=model, modes=["no_source", "source_provided"],
                                 output_path=output_path)

    def evaluate_all(self, items: List[dict], model: Optional[str] = None,
                     modes: Optional[List[str]] = None,
                     output_path: Optional[str] = None) -> dict:
        """Run multiple modes and compute pairwise gains.

        Default modes: NS, NS+Hint, SP, SP+Hint

        If output_path is given, resumes from any good scores already saved
        there and flushes partial per-item results incrementally, so a killed
        run continues instead of restarting.
        """
        import os
        model = model or self.api_cfg.model
        if modes is None:
            modes = ["no_source", "ns_hint", "source_provided", "sp_hint"]

        # RESUME: load previously-saved good (item_id, mode) scores
        self._prescored = _load_resume(output_path) if output_path else {}
        if self._prescored:
            print(f"[resume] loaded {len(self._prescored)} good scores from {output_path}")

        mode_results = {}
        mode_rates = {}
        _completed_modes = []  # scored results from fully/partly finished modes

        def _flush(cur_mode, live_results):
            # Merge seed (prescored) + finished modes + current live so a flush during an
            # early mode never drops records from a later mode that were loaded from disk.
            merged = {}
            for r in self._prescored.values():
                k = (r.get("item_id"), r.get("mode"))
                if k[0] and k[1]:
                    merged[k] = r
            for r in _completed_modes:
                merged[(r.get("item_id"), r.get("mode"))] = r
            for r in live_results:
                if r is not None:
                    merged[(r.get("item_id"), r.get("mode"))] = r
            _atomic_write_per_item(output_path, list(merged.values()))
        self._flush_cb = _flush if output_path else None

        for mode in modes:
            label = {"no_source": "NS", "ns_hint": "NS+Hint",
                     "source_provided": "SP", "sp_hint": "SP+Hint"}.get(mode, mode)
            print(f"\n=== {label} ({len(items)} items) ===")
            results = self.evaluate(items, model=model, mode=mode)
            _completed_modes.extend(results)
            rate = sum(1 for r in results if r["passed"]) / max(1, len(results))
            mode_results[mode] = results
            mode_rates[mode] = rate
        self._flush_cb = None

        self.results = [r for rs in mode_results.values() for r in rs]

        # Per-layer rates for each mode
        by_layer = {}
        all_layers = set(r["layer"] for rs in mode_results.values() for r in rs)
        for layer in all_layers:
            by_layer[layer] = {}
            for mode in modes:
                layer_items = [r for r in mode_results[mode] if r["layer"] == layer]
                by_layer[layer][mode] = sum(1 for r in layer_items if r["passed"]) / max(1, len(layer_items))

        # Compute gaps between consecutive modes
        gaps = {}
        for i in range(len(modes) - 1):
            gap_name = f"{modes[i]} → {modes[i+1]}"
            gaps[gap_name] = mode_rates[modes[i+1]] - mode_rates[modes[i]]

        # Paired transitions for all adjacent mode pairs
        all_transitions = {}
        for i in range(len(modes) - 1):
            m_from, m_to = modes[i], modes[i + 1]
            label = f"{m_from} → {m_to}"
            from_by_id = {r["item_id"]: r["passed"] for r in mode_results[m_from]}
            to_by_id = {r["item_id"]: r["passed"] for r in mode_results[m_to]}
            t = {"right_right": 0, "right_wrong": 0, "wrong_right": 0, "wrong_wrong": 0}
            for iid in from_by_id:
                f, s = from_by_id[iid], to_by_id.get(iid, False)
                if f and s: t["right_right"] += 1
                elif f and not s: t["right_wrong"] += 1
                elif not f and s: t["wrong_right"] += 1
                else: t["wrong_wrong"] += 1
            all_transitions[label] = t

        return {
            "modes": modes,
            "mode_rates": mode_rates,
            "gaps": gaps,
            "by_layer": by_layer,
            "paired_transitions": all_transitions,
        }

    @staticmethod
    def _infer_source_preference(item: dict, answer: dict) -> Optional[str]:
        """Infer which source the model's answer aligns with more."""
        if "_error" in answer:
            return None
        prov = item.get("provenance", {})
        if isinstance(prov, str):
            try: prov = json.loads(prov)
            except: return None
        a_quote = prov.get("source_a_quote", "")
        b_quote = prov.get("source_b_quote", "")
        if not a_quote and not b_quote:
            return None
        a_score = 0
        b_score = 0
        kv = answer.get("key_values", {})
        for val in kv.values():
            vs = str(val)
            if vs and len(vs) > 2:
                if vs in a_quote: a_score += 1
                if vs in b_quote: b_score += 1
        if a_score > b_score: return "A"
        if b_score > a_score: return "B"
        if a_score == b_score and a_score > 0: return "tie"
        return "neither"

    @staticmethod
    def _classify_error(item: dict, answer: dict, scored: dict) -> dict:
        """Classify error type for a failed item."""
        hl = item.get("hidden_labels", {})
        gold_kv = hl.get("key_values_gold", {})
        result = {"error_type": "unknown", "error_detail": ""}

        for key, matched in scored.get("kv_match", {}).items():
            if matched:
                continue
            mv = str(answer.get("key_values", {}).get(key, ""))
            gv = str(gold_kv.get(key, ""))

            if not mv or mv == "None":
                result["error_type"] = "missing_answer"
                result["error_detail"] = f"{key}: expected '{gv}', got empty"
            elif mv == gv:
                result["error_type"] = "scoring_false_negative"
                result["error_detail"] = f"{key}: '{mv}' should match '{gv}'"
            elif Evaluator._extract_number(mv) and Evaluator._extract_number(gv):
                mn = Evaluator._extract_number(mv)
                gn = Evaluator._extract_number(gv)
                ratio = abs(mn - gn) / max(1e-6, abs(gn))
                if ratio > 0.5:
                    result["error_type"] = "large_numeric_deviation"
                else:
                    result["error_type"] = "small_numeric_deviation"
                result["error_detail"] = f"{key}: got {mv} ({mn}), expected {gv} ({gn}), ratio={ratio:.2f}"
            elif gv in mv or mv in gv:
                result["error_type"] = "partial_match_not_exact"
                result["error_detail"] = f"{key}: '{mv}' ≈ '{gv}'"
            else:
                result["error_type"] = "value_mismatch"
                result["error_detail"] = f"{key}: got '{mv}', expected '{gv}'"
            break

        return result

    @staticmethod
    def _build_source_context(item: dict) -> str:
        """Build source excerpt text from item provenance for source-provided mode."""
        prov = item.get("provenance", {})
        if isinstance(prov, str):
            try:
                prov = json.loads(prov)
            except Exception:
                prov = {}

        lines = []
        source_a = prov.get("source_a_quote", "")
        source_b = prov.get("source_b_quote", "")

        # Also check _verification for quotes
        ver = item.get("_verification", {})
        if not source_a:
            source_a = ver.get("source_a_quote", "") or ver.get("source_quote", "")
        if not source_b:
            source_b = ver.get("source_b_quote", "")

        if source_a:
            lines.append(f"来源A:\n{source_a[:500]}")
        if source_b:
            lines.append(f"来源B:\n{source_b[:500]}")

        if not lines:
            return ""  # No source text available — fall back to no-source behavior

        return "\n\n".join(lines)

    def summary(self) -> dict:
        if not self.results:
            return {}
        total = len(self.results)
        passed = sum(1 for r in self.results if r.get("passed", False))
        by_layer = {}
        by_harm = {}
        for r in self.results:
            lid = r.get("layer", "?")
            by_layer.setdefault(lid, {"total": 0, "passed": 0})
            by_layer[lid]["total"] += 1
            by_layer[lid]["passed"] += r.get("passed", False)
            h = r.get("harm_level", "?")
            by_harm.setdefault(h, {"total": 0, "passed": 0})
            by_harm[h]["total"] += 1
            by_harm[h]["passed"] += r.get("passed", False)

        return {
            "total": total, "passed": passed,
            "pass_rate": passed / max(1, total),
            "by_layer": {k: {"pass_rate": v["passed"] / max(1, v["total"])} for k, v in by_layer.items()},
            "by_harm": {k: {"pass_rate": v["passed"] / max(1, v["total"])} for k, v in by_harm.items()},
        }

    # ---------------------------------------------------------------
    # Scoring
    # ---------------------------------------------------------------

    def _score(self, item: dict, answer: dict, raw: str) -> dict:
        """Pure LLM judge scoring — no rule-based matching."""
        hl = item.get("hidden_labels", {})
        gold_kv = hl.get("key_values_gold", {})
        gold_action = hl.get("clinical_action_canonical", "")
        gold = gold_kv if gold_kv else {"clinical_action": gold_action}

        result = {
            "raw": raw,
            "passed": False,
            "harm_level": hl.get("harm_level_if_wrong", "?"),
        }

        if "_error" in answer:
            result["error"] = answer["_error"]
            result["judge_verdict"] = "error"
            return result

        verdict = self._llm_judge(gold, answer)
        result["judge_verdict"] = verdict
        result["passed"] = verdict == "correct"
        return result

    def _llm_judge(self, gold: dict, answer: dict) -> str:
        """LLM judge for rule-fail escalation. Returns 'correct' or 'wrong'."""
        gold_str = json.dumps(gold, ensure_ascii=False)
        model_str = json.dumps(answer, ensure_ascii=False)[:500]
        try:
            kw = {
                "model": self.judge_model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": JUDGE_USER.format(gold=gold_str, model=model_str)},
                ],
            }
            # gpt-5 minis on native OpenAI need max_completion_tokens + default temperature
            if "gpt-5" in self.judge_model and "api.openai.com" in str(self.judge_client.base_url):
                kw["max_completion_tokens"] = 50
            else:
                kw["max_tokens"] = 50
                kw["temperature"] = 0
            r = self.judge_client.chat.completions.create(**kw)
            raw = r.choices[0].message.content
            if '"correct"' in raw:
                return "correct"
            return "wrong"
        except Exception:
            return "error"

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Extract JSON from model output — handles markdown, trailing text, uppercase fences."""
        raw = raw.strip()
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Find JSON object boundaries
        for fence in ["```json", "```JSON", "```"]:
            if fence in raw:
                parts = raw.split(fence, 1)
                if len(parts) > 1:
                    inner = parts[1].split("```", 1)[0].strip()
                    try:
                        return json.loads(inner)
                    except json.JSONDecodeError:
                        pass
        # Find first { ... } as fallback
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse JSON from: {raw[:200]}")

    # Decision keywords: exact one-word answers that must match exactly
    DECISION_EXACT = {"禁用", "不禁用", "是", "否", "可以", "不可以", "推荐", "不推荐",
                      "禁忌", "慎用", "可", "不可", "无", "有"}

    @staticmethod
    def _fuzzy_match(model_val, gold_val) -> bool:
        """Normalize and compare values: ranges first, then numbers, then text."""
        if model_val is None or gold_val is None:
            return False

        ms = str(model_val).strip()
        gs = str(gold_val).strip()

        # Direct match
        if ms == gs:
            return True

        # Exact decision keywords — must match exactly (prevent 禁用≈不禁用)
        if gs in Evaluator.DECISION_EXACT or ms in Evaluator.DECISION_EXACT:
            return ms == gs

        # 1. Range comparison FIRST (before scalar extraction)
        gold_range = Evaluator._extract_range(gs)
        model_range = Evaluator._extract_range(ms)
        if gold_range and model_range:
            g_lo, g_hi = gold_range
            m_lo, m_hi = model_range
            return g_lo == m_lo and g_hi == m_hi
        # One side is a range, other is a scalar — check containment
        if gold_range and model_range is None:
            model_num = Evaluator._extract_number(ms)
            if model_num is not None:
                g_lo, g_hi = gold_range
                return g_lo * 0.85 <= model_num <= g_hi * 1.15
        if model_range and gold_range is None:
            gold_num = Evaluator._extract_number(gs)
            if gold_num is not None:
                m_lo, m_hi = model_range
                return m_lo * 0.85 <= gold_num <= m_hi * 1.15

        # 2. Inequality-aware numeric comparison
        g_ineq, g_num = Evaluator._extract_inequality(gs)
        m_ineq, m_num = Evaluator._extract_inequality(ms)
        if g_num is not None and m_num is not None:
            if m_num == g_num:
                if g_ineq and m_ineq and g_ineq != m_ineq:
                    return False  # "<55" ≠ ">55"
                return True
            return False  # any numeric difference → fail, let judge decide

        # 3. Substring — but NOT for short negation-vulnerable text
        if len(ms) > 3 and len(gs) > 3:
            # Disallow: "禁用" matching inside "不禁用"
            if gs in ms:
                # Check the match isn't part of a negation
                idx = ms.find(gs)
                prefix = ms[max(0, idx-2):idx]
                if prefix in ("不", "非", "无", "未"):
                    return False
                return True
            if ms in gs:
                idx = gs.find(ms)
                prefix = gs[max(0, idx-2):idx]
                if prefix in ("不", "非", "无", "未"):
                    return False
                return True

        # 4. Chinese keyword overlap (last resort)
        if any('\u4e00' <= c <= '\u9fff' for c in ms + gs):
            m_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ms))
            g_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', gs))
            if g_words:
                overlap = len(m_words & g_words) / len(g_words)
                return overlap > 0.6

        return False

    @staticmethod
    def _extract_number(val) -> Optional[float]:
        """Extract numeric value, ignoring inequality signs and units."""
        s = str(val).replace("～", " ").replace("~", " ").replace("−", "-")
        s = re.sub(r'[<>=≤≥]', ' ', s)
        s = re.sub(r'[a-zA-Z%/]+', ' ', s)
        match = re.search(r'[-+]?\d+\.?\d*', s)
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_inequality(val) -> Tuple[Optional[str], Optional[float]]:
        """Extract (inequality_sign, numeric_value). e.g. '<55 mg/dL' -> ('<', 55.0)."""
        s = str(val).strip().replace("～", " ").replace("−", "-")
        ineq = None
        for sign, sym in [("<=", "≤"), (">=", "≥"), ("<", "<"), (">", ">"), ("≤", "≤"), ("≥", "≥")]:
            if s.startswith(sym) or sym in s[:3]:
                ineq = sign
                break
        num = Evaluator._extract_number(s)
        return ineq, num

    @staticmethod
    def _extract_range(val) -> Optional[Tuple[float, float]]:
        """Extract a range like '7-9' or '7.0%~8.0%'. Returns (lo, hi) or None."""
        s = str(val).replace("～", "-").replace("~", "-").replace("%", "").replace(" ", "")
        match = re.match(r'(\d+\.?\d*)\s*[-–—]\s*(\d+\.?\d*)', s)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None

    @staticmethod
    def _action_close(model_action: str, gold_action: str) -> bool:
        """Check if model's clinical decision is close to gold."""
        if not model_action or not gold_action:
            return False
        ma = model_action.lower().strip()
        ga = gold_action.lower().strip()
        if ma == ga:
            return True
        if ma in ga or ga in ma:
            return True
        # Shared keyword set
        m_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ma))
        g_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ga))
        overlap = len(m_words & g_words) / max(1, len(g_words))
        return overlap > 0.5


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CKPA-Bench Evaluator")
    parser.add_argument("--input", default=None, help="Path to passed items JSON")
    parser.add_argument("--model", default=None, help="Target model (default: deepseek-chat)")
    parser.add_argument("--max-items", type=int, default=0, help="Limit items for quick test")
    parser.add_argument("--mode", default="no_source",
                        choices=["no_source", "ns_hint", "source_provided", "sp_hint", "both", "all"],
                        help="Evaluation mode (default: no_source)")
    parser.add_argument("--output", default=None, help="Save results to JSON")
    args = parser.parse_args()

    evaluator = Evaluator()
    items = evaluator.load_items(args.input)
    if args.max_items:
        items = items[:args.max_items]

    if args.mode in ("both", "all"):
        if args.mode == "all":
            modes = ["no_source", "ns_hint", "source_provided", "sp_hint"]
        else:
            modes = ["no_source", "source_provided"]

        label_map = {"no_source": "NS", "ns_hint": "NS+Hint",
                     "source_provided": "SP", "sp_hint": "SP+Hint"}
        print(f"Running {len(modes)} modes on {len(items)} items ({args.model or evaluator.api_cfg.model})...\n")
        result = evaluator.evaluate_all(items, model=args.model, modes=modes, output_path=args.output)

        print(f"\n{'='*65}")
        print("CKPA-Bench: Multi-Mode Analysis")
        print(f"{'='*65}")
        print(f"  Mode pass rates:")
        for mode in modes:
            label = label_map.get(mode, mode)
            print(f"    {label:10s} {result['mode_rates'][mode]:.0%}")
        print(f"\n  Gaps:")
        for gap_name, gap_val in result['gaps'].items():
            print(f"    {gap_name:30s} {gap_val:+.1%}")
        all_pt = result.get('paired_transitions', {})
        if all_pt:
            print(f"\n  Paired transitions:")
            for pair_label, pt in all_pt.items():
                print(f"    {pair_label}:  R→R:{pt['right_right']}  W→R:{pt['wrong_right']}  R→W:{pt['right_wrong']}  W→W:{pt['wrong_wrong']}")
        print(f"\n  By layer:")
        for layer in sorted(result.get('by_layer', {}).keys()):
            rates = result['by_layer'][layer]
            parts = [f"{label_map.get(m,m)}={rates.get(m,0):.0%}" for m in modes]
            print(f"    {layer:12s}  {' '.join(parts)}")

        if args.output:
            result["per_item"] = evaluator.results  # all per-item details
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\nSaved to {args.output} ({len(evaluator.results)} per-item records)")
        return

    print(f"Evaluating {len(items)} items [{args.mode}] on {args.model or evaluator.api_cfg.model}...")
    results = evaluator.evaluate(items, model=args.model, mode=args.mode)
    summary = evaluator.summary()

    print(f"\n{'='*50}")
    print("CKPA-Bench Evaluation Results")
    print(f"{'='*50}")
    print(f"  Total: {summary['total']}")
    print(f"  Passed: {summary['passed']} ({summary['pass_rate']:.0%})")
    print(f"  By layer:")
    for layer, stats in sorted(summary.get('by_layer', {}).items()):
        print(f"    {layer}: {stats['pass_rate']:.0%}")
    print(f"  By harm level:")
    for harm, stats in sorted(summary.get('by_harm', {}).items()):
        print(f"    {harm}: correct on {stats['pass_rate']:.0%}")

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
