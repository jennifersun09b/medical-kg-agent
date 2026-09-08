#!/usr/bin/env python3
"""CKPA evidence-sufficiency diagnostic: run the target model under 3 conditions and
score with CKPA's own evaluator, to localize where source-provided failure happens.

Conditions:
  no_source               : scenario + question only
  source_provided_current : + source_a_quote[:500]/source_b_quote[:500] (replicates evaluator)
  expanded_source         : + fuller evidence rebuilt from original files (expand_evidence)

Scoring reuses Evaluator._score / _fuzzy_match so "correct" matches CKPA's fuzzy matcher.
No judging model — Claude (agent) does the qualitative failure audit separately.
"""
import argparse, json, os, glob
from pathlib import Path
from openai import OpenAI
from evaluator import Evaluator
from prompts import EVAL_SYSTEM_PROMPT, EVAL_USER_PROMPT
import expand_evidence as EX

def current_quotes(item):
    prov = item.get("provenance", {})
    if isinstance(prov, str):
        try: prov = json.loads(prov)
        except: prov = {}
    a = prov.get("source_a_quote", "") or item.get("_verification", {}).get("source_a_quote", "")
    b = prov.get("source_b_quote", "") or item.get("_verification", {}).get("source_b_quote", "")
    lines = []
    if a: lines.append("来源A:\n" + a[:500])
    if b: lines.append("来源B:\n" + b[:500])
    return "\n\n".join(lines)

def load_items(cap=0):
    items = []
    for layer,f in [("core","core"),("temporal","temporal"),("drug-label","drug_label"),("combination","combination")]:
        p = glob.glob(f"../output/{f}/*_latest.json")
        if p:
            lst = json.load(open(p[0],encoding="utf-8"))
            if cap: lst = lst[:cap]
            for it in lst:
                it["_layer"] = layer
                items.append(it)
    return items

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--base-url", default=os.environ.get("CKPA_EVAL_BASE_URL","https://www.micuapi.ai/v1"))
    ap.add_argument("--api-key", default=os.environ.get("CKPA_EVAL_API_KEY",""))
    ap.add_argument("--layers", default="core,temporal,drug-label,combination")
    ap.add_argument("--out-answers", default="../output/ckpa_expanded_source_answers.json")
    ap.add_argument("--out-eval", default="../output/ckpa_expanded_source_eval.json")
    ap.add_argument("--max-per-layer", type=int, default=0)
    args = ap.parse_args()
    want = set(args.layers.split(","))
    client = OpenAI(api_key=args.api_key, base_url=args.base_url, timeout=90, max_retries=2)
    ev = Evaluator.__new__(Evaluator)  # for _score/_fuzzy_match/_parse_json (static-ish)

    items = [it for it in load_items(cap=args.max_per_layer) if it["_layer"] in want]
    print(f"items: {len(items)} | model {args.model}", flush=True)
    conds = ["no_source","source_provided_current","expanded_source"]
    answers = []
    for i,it in enumerate(items):
        vp = it["visible_prompt"]
        base = EVAL_USER_PROMPT.format(scenario=vp["patient_scenario"], question=vp["question"],
                                       output_format_spec=json.dumps(vp["output_format"],ensure_ascii=False))
        ctx_cur = current_quotes(it)
        ctx_exp = EX.build_expanded_context(it)
        rec = {"item_id":it.get("item_id"),"layer":it["_layer"],
               "current_ctx_len":len(ctx_cur),"expanded_ctx_len":len(ctx_exp),"cond":{}}
        for c in conds:
            ctx = "" if c=="no_source" else (ctx_cur if c=="source_provided_current" else ctx_exp)
            prompt = base if not ctx else base.replace("## 请按以下格式回复", f"## 参考来源\n{ctx}\n\n## 请按以下格式回复")
            try:
                r = client.chat.completions.create(model=args.model, temperature=0, max_tokens=600,
                    messages=[{"role":"system","content":EVAL_SYSTEM_PROMPT},{"role":"user","content":prompt}])
                raw = r.choices[0].message.content or ""
                rawj = raw[raw.find("{"):] if "{" in raw else raw
                ans = ev._parse_json(rawj)
            except Exception as e:
                raw, ans = str(e), {"_error":str(e)}
            scored = ev._score(it, ans, raw)
            rec["cond"][c] = {"passed":scored["passed"],"answer":ans,"kv_match":scored.get("kv_match",{})}
        answers.append(rec)
        pc = {c:rec["cond"][c]["passed"] for c in conds}
        print(f"[{i+1}/{len(items)}] {rec['layer']:11s} {rec['item_id']:16s} cur={rec['current_ctx_len']:4d} exp={rec['expanded_ctx_len']:5d} | {pc}", flush=True)
        Path(args.out_answers).write_text(json.dumps(answers,ensure_ascii=False,indent=2),encoding="utf-8")

    # aggregate
    def rate(layer,c):
        rs=[a for a in answers if a["layer"]==layer]
        return (sum(a["cond"][c]["passed"] for a in rs), len(rs))
    layers=sorted({a["layer"] for a in answers})
    summary={"model":args.model,"n":len(answers),"by_layer":{}}
    for L in layers:
        summary["by_layer"][L]={c:{"passed":rate(L,c)[0],"total":rate(L,c)[1],
            "acc":rate(L,c)[0]/max(1,rate(L,c)[1])} for c in conds}
    summary["overall"]={c:{"acc":sum(a["cond"][c]["passed"] for a in answers)/max(1,len(answers))} for c in conds}
    Path(args.out_eval).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print("\n=== by layer (no_source / current / expanded) ===")
    for L in layers:
        b=summary["by_layer"][L]
        print(f"  {L:12s}: {b['no_source']['acc']:.0%} / {b['source_provided_current']['acc']:.0%} / {b['expanded_source']['acc']:.0%}  (n={b['no_source']['total']})")
    print("Saved", args.out_eval)

if __name__=="__main__":
    main()
