  # Save this as check_truncation.py and run it
import json
import os
from collections import defaultdict

models = ['doubao', 'deepseek', 'hunyuan', 'kimi', 'qianwen', 'zhipu']
base = os.environ.get('EVAL_ROOT', '.')  # folder holding model_eval outputs

truncated = []
for m in models:
     for line in open(f'{base}/model_response/results_doubal_only_new/{m}_results.jsonl'):
          d = json.loads(line)
          resp = d['response'].rstrip()
          if resp and not resp.endswith(('。','！','？','.','!','?','）',')','"','"','…')):
              truncated.append((d['question_id'], m, len(resp), resp[-100:]))

for qid, m, length, ending in sorted(truncated):
    print(f"{qid} {m:10s} {length:4d}ch ...{ending}")