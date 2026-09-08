#!/usr/bin/env python3
"""Dumb top-k BM25 RAG baseline for the Knowledge Coverage Benchmark.
No agent loop, no verifier. Retrieve top-k knowledge units, pass to model, log coverage.
"""
import argparse, json, os, re
from pathlib import Path
from openai import OpenAI
from experiment2 import BM25  # CJK char-bigram BM25

ROOT = Path(__file__).resolve().parent.parent
KS = [3, 5, 10, 20]
SYS = ("你是一名临床决策支持系统。请根据提供的参考知识回答临床问题。"
       "当患者背景为中国或未指明时，应依据适用的中国指南标准作答。先简要推理，再给出结论。")

def _is_numericish(tok):
    """A bare threshold/value token (e.g. '<2.6', '420', '7%', '135/85') — appears in
    right AND wrong answers as a reference value, so it is not a decisive conclusion token."""
    return re.fullmatch(r"[<>≥≤=~\-\d./%]+", tok.replace(" ", "")) is not None

def score_answer(ans, q):
    """final_answer_correct = the model's CONCLUSION matches gold.
    Uses conclusion (non-numeric) answer_key tokens with a negation guard; threshold numbers
    and 'wrong_marker' teaching-asides are ignored to avoid false negatives on verbose answers."""
    t = (ans or "").replace(" ", "")
    if not t or t.startswith("__ERROR"):
        return None  # uncertain
    def asserted(m):
        m2 = m.replace(" ", "")
        for i in range(len(t)):
            if t[i:i+len(m2)] == m2:
                pre = t[max(0, i-2):i]
                if not any(n in pre for n in "不非未无勿否"):
                    return True
        return False
    concl = [k for k in q["answer_key"] if not _is_numericish(k)]
    keys = concl or q["answer_key"]           # fall back to numeric keys if that is all we have
    return bool(any(asserted(k) for k in keys))

def failure_type(strict, correct, dist_hit, ans, q):
    if correct is None:
        return "evaluator_uncertain"
    if strict and correct:
        return "full_coverage_correct"
    if strict and not correct:
        return "full_coverage_wrong_reasoning"
    # not strict coverage
    if not correct and dist_hit > 0 and any(w.replace(" ","") in (ans or "").replace(" ","") for w in q["wrong_markers"]):
        return "distractor_induced_error"
    if not correct:
        return "missing_knowledge_wrong"
    return "missing_knowledge_but_correct_by_prior"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    units = json.loads((ROOT/"data/pilot_knowledge_units.json").read_text(encoding="utf-8"))
    qs = json.loads((ROOT/"data/pilot_50_questions.json").read_text(encoding="utf-8"))
    if args.limit: qs = qs[:args.limit]
    uidx = {u["kid"]: u for u in units}
    bm = BM25([u["text"] for u in units])
    kid_by_row = [u["kid"] for u in units]
    client = OpenAI(api_key=os.environ["CKPA_EVAL_API_KEY"], base_url=os.environ.get("CKPA_EVAL_BASE_URL","https://www.micuapi.ai/v1"), timeout=90, max_retries=4)

    # RESUME: reuse already-saved model answers (skip re-calling), keep only good ones
    prev = {}
    apath = ROOT/"output/topk_rag_answers.json"
    if apath.exists():
        for a in json.loads(apath.read_text(encoding="utf-8")):
            if not str(a.get("answer","")).startswith("__ERROR"):
                prev[(a["qid"], a["k"])] = a
    print(f"resume: {len(prev)} answers already saved", flush=True)

    answers, coverage = [], []
    for qi, q in enumerate(qs):
        req_kids = {r["kid"] for r in q["required_knowledge"]}
        dis_kids = {d["kid"] for d in q["distractor_knowledge"]}
        req_domains = {uidx[k]["domain"] for k in req_kids if k in uidx}
        for k in KS:
            hits = bm.search(q["question"], top_k=k)
            ret_kids = [kid_by_row[i] for i, _ in hits]
            ret_set = set(ret_kids)
            # retrieval-only metrics
            req_hit = req_kids & ret_set
            strict = req_kids.issubset(ret_set)
            over = ret_set - req_kids
            dist_hit = ret_set & dis_kids
            contra = {kk for kk in ret_set if uidx[kk]["jurisdiction"] in ("US","International")
                      or "old" in str(uidx[kk]["version"])} & {kk for kk in ret_set if uidx[kk]["domain"] in req_domains}
            # model answer
            if (q["qid"], k) in prev:
                ans = prev[(q["qid"], k)]["answer"]      # resume: reuse saved answer
            else:
                ev = "\n".join(f"[知识{j+1}] {uidx[kk]['text']}" for j, kk in enumerate(ret_kids))
                user = f"## 参考知识（top-{k} 检索）\n{ev}\n\n## 临床问题\n{q['question']}\n\n请依据适用的中国指南给出结论。"
                try:
                    r = client.chat.completions.create(model=args.model, temperature=0, max_tokens=500,
                        messages=[{"role":"system","content":SYS},{"role":"user","content":user}])
                    ans = r.choices[0].message.content or ""
                except Exception as e:
                    ans = "__ERROR__ " + str(e)[:80]
            correct = score_answer(ans, q)
            ft = failure_type(strict, correct, len(dist_hit), ans, q)
            answers.append({"qid": q["qid"], "k": k, "retrieved_kids": ret_kids, "answer": ans})
            coverage.append({"qid": q["qid"], "category": q["category"], "k": k,
                "required_total": len(req_kids), "required_hit_count": len(req_hit),
                "required_recall": round(len(req_hit)/max(1,len(req_kids)),3),
                "strict_coverage_pass": strict, "over_hit_count": len(over),
                "distractor_hit_count": len(dist_hit), "contradictory_hit_count": len(contra),
                "missing_required": sorted(req_kids - ret_set),
                "final_answer_correct": correct, "failure_type": ft})
        (ROOT/"output/topk_rag_answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT/"output/topk_rag_coverage_eval.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{qi+1}/{len(qs)}] {q['qid']} done ({q['category']})", flush=True)
    print("SAVED answers + coverage")

if __name__ == "__main__":
    main()
