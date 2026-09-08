#!/usr/bin/env python3
"""Evaluate the 200-item CKPA pipeline set under two conditions:
  no_source        -> closed book (measures the model's built-in bias)
  source_provided  -> both source quotes injected (measures arbitration when evidence present)

Subject model = CKPA_EVAL_MODEL (default gpt-4o; the model prior work showed reproduces the biases).
Judge model   = CKPA_JUDGE_MODEL (default gpt-5.4; NOT 4o) — semantic scorer, since the rule
                scorer (_fuzzy_match) under-counts clinical correctness ~2x.

Writes output/eval_200_full_io.json (inputs+outputs+rule_pass+judge) and prints aggregates
including Arbitration Gain (source_provided - no_source), overall and per layer.
"""
import json, os, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT

ROOT = Path(__file__).resolve().parent.parent
ev = Evaluator.__new__(Evaluator)

SUBJECT_MODEL = os.environ.get("CKPA_EVAL_MODEL", "gpt-4o")
JUDGE_MODEL = os.environ.get("CKPA_JUDGE_MODEL", "gpt-5.4")
BASE = os.environ.get("CKPA_EVAL_BASE_URL", "https://api.openai.com/v1")
subj_client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"], base_url=BASE, timeout=90, max_retries=4)
judge_client = OpenAI(api_key=os.environ.get("CKPA_JUDGE_API_KEY", os.environ["CKPA_EVAL_API_KEY"]),
                      base_url=os.environ.get("CKPA_JUDGE_BASE_URL", BASE), timeout=90, max_retries=4)

CONDS = ["no_source", "source_provided"]
items = json.load(open(ROOT / "data/ckpa_pipeline_200.json", encoding="utf-8"))
lock = threading.Lock()
TAG = os.environ.get("CKPA_RUN_TAG", "")
OUT = ROOT / (f"output/eval_200_full_io{'_'+TAG if TAG else ''}.json")


def create(client, model, base_url, messages, max_tokens, json_mode=False):
    """Adapt params: gpt-5 on native OpenAI needs max_completion_tokens + default temp."""
    kw = {"model": model, "messages": messages}
    if ("gpt-5" in model) and ("api.openai.com" in base_url):
        kw["max_completion_tokens"] = max_tokens
    else:
        kw["temperature"] = 0
        kw["max_tokens"] = max_tokens
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**kw).choices[0].message.content


def run_subject(item, condition):
    vp = item["visible_prompt"]
    base = EVAL_USER_PROMPT.format(scenario=vp["patient_scenario"], question=vp["question"],
                                   output_format_spec=json.dumps(vp.get("output_format", {}), ensure_ascii=False))
    ctx = "" if condition == "no_source" else ev._build_source_context(item)
    prompt = base if not ctx else base.replace("## 请按以下格式回复", f"## 参考来源\n{ctx}\n\n## 请按以下格式回复")
    try:
        raw = create(subj_client, SUBJECT_MODEL, BASE,
                     [{"role": "system", "content": EVAL_SYSTEM_PROMPT}, {"role": "user", "content": prompt}], 600,
                     json_mode=bool(os.environ.get("CKPA_SUBJECT_JSON"))) or ""
        parsed = ev._parse_json(raw[raw.find("{"):] if "{" in raw else raw)
    except Exception as e:
        raw, parsed = str(e), {"_error": str(e)}
    score_in = parsed if isinstance(parsed, dict) else {}  # qwen sometimes returns a JSON array
    return {"input_evidence": ctx or "(none — closed book)", "output_text": raw,
            "output_parsed": parsed, "rule_pass": ev._score(item, score_in, raw)["passed"]}


JUDGE_SYS = ("你是严格但公正的临床评分员。判断【模型答案】是否与【金标准答案】临床一致。"
             "忽略措辞、语言(中/英)、分隔符(如'ACEI/ARB'≡'ACEI或ARB')、单位写法、冗长表述等无关差异，"
             "只看关键临床结论/数值/人群/用药决策是否正确。数值须实质正确(如金标准<140/90而答案<130/80属wrong)。"
             "所有关键项正确=correct；部分正确其余缺失/错误=partial；关键结论错误=wrong。"
             "只输出JSON：{\"verdict\":\"correct|partial|wrong\",\"reason\":\"简短理由\"}")


def judge(question, gold, answer):
    ans = answer.get("key_values", answer) if isinstance(answer, dict) else answer
    user = f"【问题】{question}\n【金标准答案】{json.dumps(gold, ensure_ascii=False)}\n【模型答案】{json.dumps(ans, ensure_ascii=False)}"
    try:
        raw = create(judge_client, JUDGE_MODEL, os.environ.get("CKPA_JUDGE_BASE_URL", BASE),
                     [{"role": "system", "content": JUDGE_SYS}, {"role": "user", "content": user}], 150, json_mode=True)
        d = json.loads(raw)
        return d.get("verdict", "wrong"), d.get("reason", "")
    except Exception as e:
        return "error", str(e)[:80]


results = {}


def process(idx_item):
    idx, item = idx_item
    vp = item["visible_prompt"]
    gold = item.get("hidden_labels", {}).get("key_values_gold")
    rec = {"item_id": item["item_id"], "layer": item["_layer"],
           "shared_input": {"scenario": vp["patient_scenario"], "question": vp["question"]},
           "gold": gold, "conditions": {}}
    for c in CONDS:
        try:
            r = run_subject(item, c)
            op = r["output_parsed"]
            # if the model didn't emit usable JSON (e.g. qwen prose/degenerate), judge the raw text
            usable = isinstance(op, dict) and "_error" not in op and (op.get("key_values") or op)
            judge_ans = op if usable else (r["output_text"] or op)
            v, why = judge(vp["question"], gold, judge_ans)
            r["judge"], r["judge_reason"] = v, why
        except Exception as e:  # never let one item abort the whole run
            r = {"input_evidence": "", "output_text": str(e), "output_parsed": {"_error": str(e)},
                 "rule_pass": False, "judge": "error", "judge_reason": str(e)[:80]}
        rec["conditions"][c] = r
    with lock:
        results[item["item_id"]] = rec
        n = len(results)
        if n % 10 == 0 or n == len(items):
            ordered = [results[i["item_id"]] for i in items if i["item_id"] in results]
            OUT.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
        j = {c: rec["conditions"][c]["judge"] for c in CONDS}
        print(f"[{n:3d}/200] {rec['layer']:11s} {rec['item_id']:18s} ns={j['no_source']:7s} sp={j['source_provided']:7s}", flush=True)


print(f"subject={SUBJECT_MODEL}  judge={JUDGE_MODEL}  base={BASE}\n")
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(process, enumerate(items)))

data = [results[i["item_id"]] for i in items if i["item_id"] in results]
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pct(sub, c, kinds, key="judge"):
    rel = [r for r in data if r["layer"] == sub] if sub else data
    return sum(1 for r in rel if r["conditions"][c][key] in kinds) / max(1, len(rel))


def rulepct(sub, c):
    rel = [r for r in data if r["layer"] == sub] if sub else data
    return sum(1 for r in rel if r["conditions"][c]["rule_pass"]) / max(1, len(rel))


print("\n" + "=" * 70)
print(f"CKPA 200-item eval   subject={SUBJECT_MODEL}   judge={JUDGE_MODEL}")
print("=" * 70)
hdr = f"{'layer':12s} {'n':>3s} | {'NS rule':>7s} {'SP rule':>7s} | {'NS corr':>7s} {'SP corr':>7s} {'gain':>6s} | {'NS +par':>7s} {'SP +par':>7s}"
print(hdr)
for L in ["core", "temporal", "drug_label", "combination", None]:
    name = L or "ALL"
    n = len([r for r in data if (L is None or r["layer"] == L)])
    nsr, spr = rulepct(L, "no_source"), rulepct(L, "source_provided")
    nsc, spc = pct(L, "no_source", {"correct"}), pct(L, "source_provided", {"correct"})
    nsp, spp = pct(L, "no_source", {"correct", "partial"}), pct(L, "source_provided", {"correct", "partial"})
    print(f"{name:12s} {n:3d} | {nsr:6.0%} {spr:6.0%} | {nsc:6.0%} {spc:6.0%} {spc-nsc:+5.0%} | {nsp:6.0%} {spp:6.0%}")
print("\nNS=no_source  SP=source_provided  corr=judge:correct  +par=correct+partial  gain=SP_corr-NS_corr")
