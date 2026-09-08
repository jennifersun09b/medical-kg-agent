#!/usr/bin/env python3
"""Re-score faithful_ckpa_full_io.json with a SEMANTIC judge (replaces brittle _fuzzy_match).
Adds conditions[c]['judge'] in {correct, partial, wrong} + a one-line reason.
Writes output/faithful_ckpa_full_io_judged.json and prints corrected aggregates.
"""
import json, os
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"],
                base_url=os.environ.get("CKPA_EVAL_BASE_URL","https://api.openai.com/v1"),
                timeout=60, max_retries=4)
JUDGE = os.environ.get("CKPA_JUDGE_MODEL", "gpt-4o")

SYS = ("你是严格但公正的临床评分员。判断【模型答案】是否与【金标准答案】临床一致。"
       "忽略措辞、语言(中/英)、分隔符(如'ACEI/ARB'≡'ACEI或ARB'≡'ACEI和ARB')、单位写法、冗长表述、"
       "'患者'与'患病人群'等无关差异。只看关键临床结论/数值/人群是否正确。"
       "若所有关键项正确=correct；部分关键项正确、其余缺失或错误=partial；关键结论错误=wrong。"
       "注意：数值必须实质正确(如金标准<140/90而答案<130/80属wrong)。"
       "只输出JSON：{\"verdict\":\"correct|partial|wrong\",\"reason\":\"简短理由\"}")

def judge(question, gold, answer):
    user = (f"【问题】{question}\n【金标准答案】{json.dumps(gold, ensure_ascii=False)}\n"
            f"【模型答案】{json.dumps(answer, ensure_ascii=False)}")
    try:
        r = client.chat.completions.create(model=JUDGE, temperature=0, max_tokens=120,
            response_format={"type":"json_object"},
            messages=[{"role":"system","content":SYS},{"role":"user","content":user}])
        d = json.loads(r.choices[0].message.content)
        return d.get("verdict","wrong"), d.get("reason","")
    except Exception as e:
        return "error", str(e)[:60]

data = json.loads((ROOT/"output/faithful_ckpa_full_io.json").read_text(encoding="utf-8"))
CONDS = ["no_source","source_provided","oracle"]
for i, r in enumerate(data):
    gold = r["gold"]; q = r["shared_input"]["question"]
    for c in CONDS:
        ans = r["conditions"][c]["output_parsed"]
        ans = ans.get("key_values", ans) if isinstance(ans, dict) else ans
        v, why = judge(q, gold, ans)
        r["conditions"][c]["judge"] = v
        r["conditions"][c]["judge_reason"] = why
    jr = {c: r["conditions"][c]["judge"] for c in CONDS}
    print(f"[{i+1}/{len(data)}] {r['layer']:11s} {r['item_id']:16s} {jr}", flush=True)
    (ROOT/"output/faithful_ckpa_full_io_judged.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# aggregates: strict (correct only) and lenient (correct+partial)
def rate(c, kinds): return sum(1 for r in data if r["conditions"][c]["judge"] in kinds)/max(1,len(data))
print("\n=== corrected aggregate (judge) ===")
print(f"{'cond':16s} {'rule_pass':>9s} {'correct':>8s} {'+partial':>9s}")
rule = {c: sum(1 for r in data if r['conditions'][c]['pass'])/len(data) for c in CONDS}
for c in CONDS:
    print(f"{c:16s} {rule[c]:8.0%} {rate(c,{'correct'}):7.0%} {rate(c,{'correct','partial'}):8.0%}")
# by layer for source_provided
from collections import defaultdict
by = defaultdict(lambda: defaultdict(int))
for r in data:
    for c in CONDS:
        by[r["layer"]][c+"_"+r["conditions"][c]["judge"]] += 1
        by[r["layer"]]["n"] += 1 if c=="no_source" else 0
print("\n=== source_provided correct by layer (judge) ===")
for L in sorted(by):
    n = by[L]["n"]; ok = by[L]["source_provided_correct"]; pa = by[L]["source_provided_partial"]
    print(f"  {L:12s} n={n:2d}  correct={ok}  partial={pa}")
