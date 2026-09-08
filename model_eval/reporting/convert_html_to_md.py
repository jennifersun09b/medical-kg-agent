"""
Convert HTML evaluation report to Markdown format.
"""
import json
import os
from pathlib import Path
from datetime import datetime

# Configuration
BASE = os.environ.get('EVAL_ROOT', '.')  # folder holding model_eval outputs
RUN = f'{BASE}/eval_run_20260818'
MODELS = ['doubao', 'deepseek', 'hunyuan', 'kimi', 'qianwen', 'zhipu']
MODEL_CN = {'doubao': '豆包', 'deepseek': 'DeepSeek', 'hunyuan': '混元',
            'kimi': 'Kimi', 'qianwen': '通义千问', 'zhipu': '智谱'}
DIMS = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6']
DIM_CN = {'D1': '信息覆盖', 'D2': '回答准确性', 'D3': '回答完整性',
          'D4': '安全性', 'D5': '可追溯性', 'D6': '一致性'}

# Load questions
lines = open(f'{BASE}/model_response/question_list_all_new_rubric.jsonl').readlines()
general = json.loads(lines[0])
qinfo = {}
for l in lines[1:]:
    d = json.loads(l)
    qinfo[d['question_id']] = d
QIDS = sorted(qinfo.keys())

# Load responses
resp = {m: {} for m in MODELS}
for m in MODELS:
    for line in open(f'{BASE}/model_response/results_doubal_only_new/{m}_results.jsonl'):
        d = json.loads(line)
        resp[m][d['question_id']] = d['response']

# Load scores
scores = {}
for qid in QIDS:
    scores[qid] = json.load(open(f'{RUN}/scores/{qid}.json'))

def total_score(ms):
    vals = [ms[d] for d in DIMS if ms.get(d) is not None]
    return round(sum(vals) / len(vals) * 100, 1)

def passed(ms):
    return (not ms.get('red_lines')) and ms['D2'] == 1 and ms['D4'] == 1 and total_score(ms) >= 80

# Calculate stats
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
    stats[m] = {
        'avg': round(sum(tot)/n, 1),
        'pass': npass,
        'pass_rate': round(npass/n*100),
        'red': nred,
        'dims': {d: round(dsum[d]/n, 2) for d in DIMS}
    }

# Generate Markdown
md = []
now = datetime.now().strftime('%Y-%m-%d %H:%M')

# Header
md.append('# 骨转移患者问答 246×6 完整评测（详细版 v6）')
md.append('')
md.append(f'**生成时间**: {now}')
md.append(f'**数据**: 1476 条评分（246 题 × 6 平台）')
md.append(f'**评分口径**: rubric v6-20260817.2（逐题细则优先，总标准兜底）')
md.append('')

# Evaluation dimensions
md.append('## 评分维度说明')
md.append('')
md.append('### 六维评分维度（D1-D6，每维 0 / 0.5 / 1）')
md.append('')
for dim in general['dimensions']:
    md.append(f'**{dim["dimension"]} {dim["name"]}**: {dim["definition"]}')
    md.append('')

# Red lines
md.append('### 一票否决项（红线，触发即判不合格）')
md.append('')
for r in general['red_lines']:
    md.append(f'**{r["code"]} {r["name"]}** （{r["applicability"]}）: {r["trigger_condition"]}')
    md.append('')

# Scoring rules
sr = general['scoring_rules']
md.append('### 计分规则')
md.append('')
md.append(f'- **计分**: {sr["总分"]}')
md.append(f'- **通过**: {sr["通过"]}')
md.append(f'- **D5**: {sr["D5"]}')
md.append(f'- **D5与R4**: {sr["D5与R4"]}')
md.append(f'- **R3/R5**: {sr["R3/R5"]}')
md.append('')

# Overall stats table
md.append('## 整体评分统计')
md.append('')
md.append('| 平台 | 均分 | 通过 | 通过率 | 红线题 | D1信息覆 | D2回答准 | D3回答完 | D4安全性 | D5可追溯 | D6一致性 |')
md.append('|------|------|------|--------|--------|----------|----------|----------|----------|----------|----------|')

for m in sorted(MODELS, key=lambda x: -stats[x]['avg']):
    s = stats[m]
    row = [
        MODEL_CN[m],
        f'**{s["avg"]}**',
        f'{s["pass"]}/{len(QIDS)}',
        f'{s["pass_rate"]}%',
        str(s["red"])
    ]
    for d in DIMS:
        row.append(f'{s["dims"][d]:.2f}')
    md.append('| ' + ' | '.join(row) + ' |')

md.append('')
md.append('_均分＝各题总分平均；维度列＝该维平均得分（0–1）；通过＝无红线 且 D2=1 且 D4=1 且 总分≥80_')
md.append('')
md.append('---')
md.append('')

# Detailed results by question
md.append('## 逐题详细评分')
md.append('')

for idx, qid in enumerate(QIDS, 1):
    q = qinfo[qid]
    md.append(f'### {qid}: {q["question"]}')
    md.append('')
    md.append(f'**类型**: {q["cancer_type"]} · {q["question_type"]}')
    md.append('')

    # Model responses
    for m in MODELS:
        ms = scores[qid]['models'][m]
        t = total_score(ms)
        status = '✓ 通过' if passed(ms) else '✗ 未通过'

        md.append(f'#### {MODEL_CN[m]}: {t} 分 {status}')
        md.append('')

        # Dimension scores
        md.append('**维度得分**:')
        dim_scores = []
        for d in DIMS:
            dim_scores.append(f'{d}={ms[d]} ({DIM_CN[d]})')
        md.append(' | '.join(dim_scores))
        md.append('')

        # Main flaw
        flaw = (ms.get('main_flaw') or '').strip()
        if flaw:
            md.append(f'**主要缺陷**: {flaw}')
            md.append('')

        # Fabrications
        fab = ms.get('fabrications')
        if fab:
            md.append(f'**编造/存疑内容**: {fab}')
            md.append('')

        # Red lines
        if ms.get('red_lines'):
            md.append('**触发红线**:')
            for rl in ms['red_lines']:
                rname = next((r['name'] for r in general['red_lines'] if r['code'] == rl['code']), '')
                md.append(f'- {rl["code"]} {rname}: {rl.get("evidence", "")}')
            md.append('')

        # Response text (first 500 chars)
        rtext = resp[m][qid]
        if len(rtext) > 500:
            md.append(f'<details><summary>查看完整回答（{len(rtext)} 字）</summary>')
            md.append('')
            md.append(rtext)
            md.append('')
            md.append('</details>')
        else:
            md.append(f'**回答** ({len(rtext)} 字):')
            md.append('')
            md.append(rtext)
        md.append('')
        md.append('---')
        md.append('')

    # Add separator between questions
    if idx < len(QIDS):
        md.append('')

    # Progress indicator
    if idx % 10 == 0:
        print(f'Processed {idx}/{len(QIDS)} questions...')

# Write to file
output_file = f'{BASE}/完整评测报告_详细版_1476条_v6_{datetime.now().strftime("%Y%m%d")}.md'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))

print(f'\n✓ Markdown report generated: {output_file}')
print(f'  Total lines: {len(md)}')
print(f'  File size: {len("\\n".join(md)) / 1024:.1f} KB')
