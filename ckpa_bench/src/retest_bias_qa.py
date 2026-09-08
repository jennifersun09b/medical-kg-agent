#!/usr/bin/env python3
"""Retest the ORIGINAL bias QA with full golden knowledge (Experiment 2 on real data).

Data: 问答语料/AI问答语料qwen.xls — real patient-facing AI (qwen) answers to four-high
questions. These原始回答 are the biased baseline.

Pipeline per item (LLM = OpenAI-compatible target):
  A. Curate + gold: from (原始问题, 原始qwen回答) produce a self-contained四高事实性问题、
     金标准答案(中国指南)、完整金标准知识(full golden evidence)，并判定原始回答是否有偏差。
     非事实性/无法自足的问题被丢弃。
  B. Retest: 目标模型分别在两种条件下回答自足问题——
       no_knowledge : 只有问题（复现无检索先验）
       full_golden  : 问题 + 完整金标准知识（充分证据）
  C. Judge: 用金标准答案判定 原始qwen / no_knowledge / full_golden 三种回答是否正确。

输出对比：原始偏差率 vs 无知识重测 vs 充分金标准知识重测。
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI

CURATE_SYS = """你是一名严格的中国临床指南专家，负责审核医疗AI问答语料。
给你一条真实的患者问题和AI原始回答（可能来自多轮问诊、可能含偏差）。
请判断该问答是否是一个关于"四高"（高血压/糖尿病/高血脂/高尿酸血症）的、
有明确指南可核对的事实性临床问题（如诊断阈值、控制目标、用药选择、禁忌、分级等）。

只输出JSON：
{
  "is_factual_four_high": true/false,   // 是否四高且有可核对的指南事实
  "self_contained_question": "把它改写成一个自足的、单轮可回答的临床问题（补全必要背景），若非事实性则空字符串",
  "gold_answer": "依据中国最新指南的正确简答（含关键数值/结论）",
  "gold_knowledge": "回答该问题所需的完整金标准证据，逐条列出中国指南的关键标准（阈值/目标/分级/禁忌/适用人群），可含少量必要背景",
  "original_verdict": "correct | biased | unclear",   // 原始AI回答相对中国指南是否有偏差
  "bias_note": "若biased，简述偏差类型（指南混用/阈值误用/分类模板残留/概念混淆），否则空"
}"""

CURATE_USER = """【原始患者问题】
{q}

【AI原始回答】
{a}"""

ANSWER_SYS = """你是一名临床决策支持系统。请根据患者问题给出简明、专业的临床回答，
必要时给出关键数值、目标或结论。"""

JUDGE_SYS = """你是一名中国临床指南评审专家。给你一个四高临床问题、金标准答案，以及三个待评回答。
请分别判断每个回答是否与金标准一致（关键结论/数值正确即算correct，出现指南混用/阈值误用/
分类错误即算biased）。只输出JSON：
{"original":"correct|biased","no_knowledge":"correct|biased","full_golden":"correct|biased"}"""

JUDGE_USER = """【问题】{q}
【金标准答案】{gold}

【回答1 · original(qwen)】{orig}
【回答2 · no_knowledge】{nok}
【回答3 · full_golden】{gold_ans}"""


def parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


class BiasRetester:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=2)
        self.model = model

    def _chat(self, sys: str, user: str, json_mode: bool = False, max_tokens: int = 700) -> str:
        kw = dict(model=self.model, temperature=0, max_tokens=max_tokens,
                  messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}])
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        return self.client.chat.completions.create(**kw).choices[0].message.content or ""

    def curate(self, q: str, a: str) -> dict:
        return parse_json(self._chat(CURATE_SYS, CURATE_USER.format(q=q[:1500], a=a[:2000]),
                                     json_mode=True, max_tokens=900))

    def answer(self, question: str, knowledge: Optional[str]) -> str:
        user = question if not knowledge else f"## 参考金标准知识\n{knowledge}\n\n## 临床问题\n{question}"
        return self._chat(ANSWER_SYS, user, max_tokens=500)

    def judge(self, q: str, gold: str, orig: str, nok: str, goldans: str) -> dict:
        return parse_json(self._chat(
            JUDGE_SYS,
            JUDGE_USER.format(q=q, gold=gold, orig=orig[:1200], nok=nok[:1200], gold_ans=goldans[:1200]),
            json_mode=True, max_tokens=120))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/tmp/factbearing_qa.json")
    ap.add_argument("--api-key", default=os.environ.get("CKPA_EVAL_API_KEY", ""))
    ap.add_argument("--base-url", default=os.environ.get("CKPA_EVAL_BASE_URL", "https://api.openai.com/v1"))
    ap.add_argument("--model", default=os.environ.get("CKPA_EVAL_MODEL", "gpt-4o"))
    ap.add_argument("--max-candidates", type=int, default=40)
    ap.add_argument("--max-keep", type=int, default=22)
    ap.add_argument("--output", default="../output/bias_retest_result.json")
    args = ap.parse_args()

    if not args.api_key:
        raise SystemExit("Need --api-key or CKPA_EVAL_API_KEY")

    cand = json.load(open(args.input, encoding="utf-8"))[:args.max_candidates]
    bot = BiasRetester(args.api_key, args.base_url, args.model)
    print(f"Model {args.model} | candidates {len(cand)} | keep up to {args.max_keep}\n", flush=True)

    kept: List[dict] = []
    for i, p in enumerate(cand):
        if len(kept) >= args.max_keep:
            break
        try:
            c = bot.curate(p["q"], p["a"])
        except Exception as e:
            print(f"[{i}] curate error: {str(e)[:80]}", flush=True); continue
        if not c.get("is_factual_four_high") or not c.get("self_contained_question"):
            print(f"[{i}] skip (not factual four-high)", flush=True); continue
        q = c["self_contained_question"]; gold = c.get("gold_answer", ""); know = c.get("gold_knowledge", "")
        try:
            nok = bot.answer(q, None)
            goldans = bot.answer(q, know)
            v = bot.judge(q, gold, p["a"], nok, goldans)
        except Exception as e:
            print(f"[{i}] retest error: {str(e)[:80]}", flush=True); continue
        rec = {"q": q, "gold_answer": gold, "gold_knowledge": know,
               "original_qwen": p["a"], "original_verdict_curator": c.get("original_verdict"),
               "bias_note": c.get("bias_note", ""), "answer_no_knowledge": nok,
               "answer_full_golden": goldans, "judge": v}
        kept.append(rec)
        print(f"[{i}] KEEP {len(kept)}/{args.max_keep} | judge={v} | {q[:50]}", flush=True)
        Path(args.output).write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    # Aggregate
    def rate(field):
        vals = [k["judge"].get(field) for k in kept if k.get("judge")]
        n = sum(1 for v in vals if v == "correct")
        return n, len(vals)

    print("\n" + "=" * 56)
    print("原始 bias QA 重测 · 充分金标准知识 (Experiment 2, real data)")
    print("=" * 56)
    print(f"  评测题数: {len(kept)}")
    for label, field in [("原始 qwen 回答", "original"),
                          ("重测·无知识", "no_knowledge"),
                          ("重测·充分金标准知识", "full_golden")]:
        n, t = rate(field)
        print(f"  {label:20s}: {n}/{t} correct  ({(100*n/t if t else 0):.0f}%)")
    biased = sum(1 for k in kept if k.get("original_verdict_curator") == "biased")
    print(f"  策展判定原始回答有偏差: {biased}/{len(kept)}")
    print(f"\nSaved → {args.output}")


if __name__ == "__main__":
    main()
