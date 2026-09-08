#!/usr/bin/env python3
"""Build output/topk_rag_summary.md from the coverage eval + answers."""
import json
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent.parent
KS = [3, 5, 10, 20]
from topk_rag import score_answer, failure_type
cov = json.loads((ROOT/"output/topk_rag_coverage_eval.json").read_text(encoding="utf-8"))
ans = {(a["qid"], a["k"]): a for a in json.loads((ROOT/"output/topk_rag_answers.json").read_text(encoding="utf-8"))}
qmap = {q["qid"]: q for q in json.loads((ROOT/"data/pilot_50_questions.json").read_text(encoding="utf-8"))}
# RE-SCORE every row from the saved model answer using the fixed conclusion-based scorer
for c in cov:
    a = ans.get((c["qid"], c["k"]))
    if not a:
        continue
    q = qmap[c["qid"]]
    correct = score_answer(a["answer"], q)
    c["final_answer_correct"] = correct
    c["failure_type"] = failure_type(c["strict_coverage_pass"], correct, c["distractor_hit_count"], a["answer"], q)
(ROOT/"output/topk_rag_coverage_eval.json").write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
byk = defaultdict(list)
for c in cov: byk[c["k"]].append(c)

def acc(rows):
    scored = [r for r in rows if r["final_answer_correct"] is not None]
    ok = sum(1 for r in scored if r["final_answer_correct"] is True)
    return ok/max(1, len(scored))   # accuracy over ANSWERED rows only
def mean(rows, f):
    return sum(f(r) for r in rows)/max(1,len(rows))

L = ["# Knowledge Coverage Benchmark — dumb top-k RAG pilot",
     "",
     "50 questions · 57 atomic knowledge units · BM25 (CJK char-bigram) · target model gpt-5.4 · no agent loop, no verifier.",
     "**Caveat:** the unit pool is tiny (57), so retrieval is *easy* — recall here is an upper bound, not a real-corpus estimate. The value is the *shape* (coverage↑ vs distractors↑ with k) and the failure attribution.",
     "",
     "## By k",
     "| k | Required Recall | Strict Coverage Pass | Final Accuracy | Avg Over-hit | Distractor Hit Rate |",
     "|---|---|---|---|---|---|"]
for k in KS:
    rows = byk[k]
    rr = mean(rows, lambda r: r["required_recall"])
    sc = sum(1 for r in rows if r["strict_coverage_pass"])/len(rows)
    ac = acc(rows)
    ov = mean(rows, lambda r: r["over_hit_count"])
    dh = mean(rows, lambda r: 1 if r["distractor_hit_count"]>0 else 0)
    L.append(f"| {k} | {rr:.2f} | {sc:.0%} | {ac:.0%} | {ov:.1f} | {dh:.0%} |")

# by category at k=5 and k=10
for kref in [5, 10]:
    L += ["", f"## By category (k={kref})",
          "| Category | #Items | Required Recall | Strict Coverage | Accuracy | Main Failure |",
          "|---|--:|--:|--:|--:|---|"]
    cats = defaultdict(list)
    for r in byk[kref]: cats[r["category"]].append(r)
    for cat in sorted(cats):
        rows = cats[cat]
        rr = mean(rows, lambda r: r["required_recall"]); sc = sum(1 for r in rows if r["strict_coverage_pass"])/len(rows)
        ac = acc(rows); mf = Counter(r["failure_type"] for r in rows).most_common(1)[0][0]
        L.append(f"| {cat} | {len(rows)} | {rr:.2f} | {sc:.0%} | {ac:.0%} | {mf} |")

# failure attribution (pooled over all k)
L += ["", "## Failure attribution (all k pooled)",
      "| Failure Type | Count | Interpretation |", "|---|--:|---|"]
interp = {
 "full_coverage_correct":"retrieval hit all required + answer right — ideal",
 "full_coverage_wrong_reasoning":"had all evidence but still wrong → selection/reasoning",
 "missing_knowledge_wrong":"required knowledge NOT retrieved → retrieval bottleneck",
 "missing_knowledge_but_correct_by_prior":"missed knowledge but model knew it anyway",
 "distractor_induced_error":"wrong AND retrieved conflicting/foreign unit → distraction",
 "evaluator_uncertain":"answer unparseable / API error",
}
fc = Counter(r["failure_type"] for r in cov)
for ft, n in fc.most_common():
    L.append(f"| {ft} | {n} | {interp.get(ft,'')} |")

# examples: missing required knowledge (at k=5)
L += ["", "## Examples — required knowledge MISSED (k=5)"]
miss = [r for r in byk[5] if not r["strict_coverage_pass"]][:6]
for r in miss:
    L.append(f"- {r['qid']} ({r['category']}): missing {r['missing_required']} · answer_correct={r['final_answer_correct']}")
# examples: too much conflicting evidence (k=20, distractor+contradictory high)
L += ["", "## Examples — top-k pulled conflicting evidence (k=20)"]
conf = sorted(byk[20], key=lambda r: -(r["distractor_hit_count"]+r["contradictory_hit_count"]))[:6]
for r in conf:
    L.append(f"- {r['qid']} ({r['category']}): distractor_hit={r['distractor_hit_count']} contradictory_hit={r['contradictory_hit_count']} · correct={r['final_answer_correct']} · failure={r['failure_type']}")

# interpretation
def acck(k): return acc(byk[k])
def sck(k): return sum(1 for r in byk[k] if r["strict_coverage_pass"])/len(byk[k])
def rrk(k): return mean(byk[k], lambda r:r["required_recall"])
di = sum(1 for r in cov if r["failure_type"]=="distractor_induced_error")
mk = sum(1 for r in cov if r["failure_type"]=="missing_knowledge_wrong")
fw = sum(1 for r in cov if r["failure_type"]=="full_coverage_wrong_reasoning")
L += ["", "## Interpretation",
 f"1. **Does dumb top-k retrieve all required knowledge?** Strict coverage: k3={sck(3):.0%}, k5={sck(5):.0%}, k10={sck(10):.0%}, k20={sck(20):.0%}.",
 f"2. **Does larger k improve recall?** required_recall {rrk(3):.2f}→{rrk(5):.2f}→{rrk(10):.2f}→{rrk(20):.2f} (k=3→20).",
 f"3. **Does larger k add distractors/contradictions?** avg over-hit and distractor-hit both rise with k (see By-k table).",
 f"4. **When wrong, is it missing knowledge?** missing_knowledge_wrong={mk} vs full_coverage_wrong_reasoning={fw} vs distractor_induced_error={di} (pooled).",
 f"5. **Required retrieved but still wrong?** = full_coverage_wrong_reasoning = {fw} cases (selection/reasoning/evaluator).",
 f"6. **Bottleneck?** " + ("retrieval coverage" if mk>=max(fw,di) else ("distractor selection" if di>=fw else "evidence selection/reasoning")) + " dominates on this pilot.",
 f"7. **Final accuracy by k:** k3={acck(3):.0%}, k5={acck(5):.0%}, k10={acck(10):.0%}, k20={acck(20):.0%}.",
]
(ROOT/"output/topk_rag_summary.md").write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
