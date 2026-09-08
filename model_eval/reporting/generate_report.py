import json
import os, os, html, datetime

BASE = os.environ.get('EVAL_ROOT', '.')  # folder holding model_eval outputs
RUN = f'{BASE}/eval_run_20260818'
MODELS = ['doubao', 'deepseek', 'hunyuan', 'kimi', 'qianwen', 'zhipu']
MODEL_CN = {'doubao': '豆包', 'deepseek': 'DeepSeek', 'hunyuan': '混元',
            'kimi': 'Kimi', 'qianwen': '通义千问', 'zhipu': '智谱'}
DIMS = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6']
DIM_CN = {'D1': '信息覆盖', 'D2': '回答准确性', 'D3': '回答完整性',
          'D4': '安全性', 'D5': '可追溯性', 'D6': '一致性'}

# load rubrics & questions
lines = open(f'{BASE}/model_response/question_list_all_new_rubric.jsonl').readlines()
general = json.loads(lines[0])
qinfo = {}
for l in lines[1:]:
    d = json.loads(l)
    qinfo[d['question_id']] = d
QIDS = sorted(qinfo.keys())

# load responses
resp = {m: {} for m in MODELS}
for m in MODELS:
    for line in open(f'{BASE}/model_response/results_doubal_only_new/{m}_results.jsonl'):
        d = json.loads(line)
        resp[m][d['question_id']] = d['response']

# load scores
scores = {}
missing = []
for qid in QIDS:
    p = f'{RUN}/scores/{qid}.json'
    if not os.path.exists(p):
        missing.append(qid)
        continue
    scores[qid] = json.load(open(p))
if missing:
    print('MISSING:', missing)
    raise SystemExit(1)

def total_score(ms):
    vals = [ms[d] for d in DIMS if ms.get(d) is not None]
    return round(sum(vals) / len(vals) * 100, 1)

def passed(ms):
    return (not ms.get('red_lines')) and ms['D2'] == 1 and ms['D4'] == 1 and total_score(ms) >= 80

# stats
stats = {}
for m in MODELS:
    tot, npass, nred = [], 0, 0
    dsum = {d: 0.0 for d in DIMS}
    for qid in QIDS:
        ms = scores[qid]['models'][m]
        t = total_score(ms)
        tot.append(t)
        if passed(ms): npass += 1
        if ms.get('red_lines'): nred += 1
        for d in DIMS: dsum[d] += ms[d]
    n = len(QIDS)
    stats[m] = {'avg': round(sum(tot)/n, 1), 'pass': npass, 'pass_rate': round(npass/n*100),
                'red': nred, 'dims': {d: round(dsum[d]/n, 2) for d in DIMS}}

esc = html.escape
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
CSS = open(f'{RUN}/report_style.css').read()

out = []
out.append(f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>骨转移患者问答 246×6 完整评测（详细版 v6）</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="container">
<h1>骨转移患者问答 246×6 完整评测（详细版 v6）</h1>
<div class="meta">生成时间：{now} | 数据：1476 条评分（246 题 × 6 平台）| 评分口径 rubric v6-20260817.2（逐题细则优先，总标准兜底）</div>''')

# legend
out.append('<div class="legend"><div class="legend-title">六维评分维度（D1-D6，每维 0 / 0.5 / 1）</div><table>')
for dim in general['dimensions']:
    out.append(f'<tr><td class="code">{dim["dimension"]} {esc(dim["name"])}</td><td>{esc(dim["definition"])}</td></tr>')
out.append('</table><div class="legend-title">一票否决项（红线，触发即判不合格）</div><table>')
for r in general['red_lines']:
    out.append(f'<tr><td class="code">{r["code"]}</td><td><b>{esc(r["name"])}</b>（{esc(r["applicability"])}）：{esc(r["trigger_condition"])}</td></tr>')
sr = general['scoring_rules']
out.append(f'''</table><div class="rule"><b>计分</b>：{esc(sr["总分"])}<br><b>通过</b>：{esc(sr["通过"])}<br><b>D5</b>：{esc(sr["D5"])}<br><b>D5与R4</b>：{esc(sr["D5与R4"])}<br><b>R3/R5</b>：{esc(sr["R3/R5"])}</div></div>''')

# stats table
out.append('<table class="stats-table"><thead><tr><th style="text-align:left;padding-left:16px;">平台</th><th>均分</th><th>通过</th><th>通过率</th><th>红线题</th>')
for d in DIMS:
    out.append(f'<th>{d}{DIM_CN[d][:3]}</th>')
out.append('</tr></thead><tbody>')
for m in sorted(MODELS, key=lambda x: -stats[x]['avg']):
    s = stats[m]
    out.append(f'<tr><td class="pname">{MODEL_CN[m]}</td><td><b>{s["avg"]}</b></td><td>{s["pass"]}/{len(QIDS)}</td><td>{s["pass_rate"]}%</td><td>{s["red"]}</td>')
    for d in DIMS:
        out.append(f'<td>{s["dims"][d]:.2f}</td>')
    out.append('</tr>')
out.append(f'</tbody></table><div class="stats-summary">均分＝各题总分平均；维度列＝该维平均得分（0–1）；通过＝无红线 且 D2=1 且 D4=1 且 总分≥80</div>')

# question cards
for qid in QIDS:
    q = qinfo[qid]
    out.append(f'<div class="question-card"><div class="q-header"><span class="q-id">{qid}</span><span class="q-type">{esc(q["cancer_type"])} · {esc(q["question_type"])}</span></div>')
    out.append(f'<div class="q-text">{esc(q["question"])}</div>')
    for m in MODELS:
        ms = scores[qid]['models'][m]
        t = total_score(ms)
        ok = passed(ms)
        cls = 'pass' if ok else 'fail'
        mark = '✓ 通过' if ok else '✗ 未通过'
        out.append(f'<div class="model-response"><div class="model-header"><span class="model-name">{MODEL_CN[m]}</span><div class="score-badge"><span class="score-total {cls}">{t:g}</span><span class="{cls}">{mark}</span><div class="dims">')
        for d in DIMS:
            out.append(f'<span class="dim" title="{esc(DIM_CN[d])}：{esc(ms["reasons"].get(d,""))}">{d} {DIM_CN[d]}:{ms[d]:g}</span>')
        out.append('</div></div></div>')
        rtext = resp[m][qid]
        out.append(f'<details><summary>查看完整回答（共 {len(rtext)} 字）</summary><div class="response-text">{esc(rtext)}</div></details>')
        flaw = (ms.get('main_flaw') or '').strip()
        if flaw:
            out.append(f'<div class="scoring-reason"><div class="scoring-reason-title">主要缺陷：</div><div class="scoring-reason-text">{esc(flaw)}</div></div>')
        fab = ms.get('fabrications')
        if fab:
            out.append(f'<div class="fab">编造/存疑内容（D5 依据）：{esc(fab)}</div>')
        for rl in ms.get('red_lines') or []:
            rname = next((r['name'] for r in general['red_lines'] if r['code'] == rl['code']), '')
            out.append(f'<div class="key-flaw">触发红线：{rl["code"]} {esc(rname)} — {esc(rl.get("evidence",""))}</div>')
        # per-dim reasons details
        out.append('<details><summary>查看逐维评分理由与本题细则</summary><div class="rubric-section">')
        rub = {r['dimension']: r['rubric'] for r in q['rubrics']}
        for d in DIMS:
            out.append(f'<div class="rubric-dim"><div class="rubric-dim-name">{d} {DIM_CN[d]}（得分：{ms[d]:g}）</div>')
            out.append(f'<div class="rubric-metric" style="color:#b45309;margin-bottom:3px;">评分理由：{esc(ms["reasons"].get(d,""))}</div>')
            out.append(f'<div class="rubric-metric">{esc(rub.get(d,""))}</div></div>')
        out.append('</div></details></div>')
    out.append('</div>')

out.append('</div></body></html>')
path = f'{BASE}/完整评测报告_详细版_1476条_v6_{datetime.date.today().strftime("%Y%m%d")}.html'
open(path, 'w').write(''.join(out))
print('written:', path)
print(json.dumps(stats, ensure_ascii=False, indent=1))
