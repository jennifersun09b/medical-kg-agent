# 骨转移知识库六平台配对 A/B 评测

这个包把严格生成提示、逐题详细 Rubric、审核后知识库和红线作为一个优化包注入六个平台，使用同一 Rubric 做独立裁判，并输出普通回答与优化回答的配对差值。

默认六平台来自本项目既有配置：通义千问、DeepSeek、豆包、Kimi、腾讯混元/元宝、智谱 GLM。它们通过各自官方或既有 OpenAI-compatible API 调用；消费者网页并不提供统一、可复现的 KB 管理接口，因此不把网页 UI 操作当作实验主路径。

## 生成与评分的分工

优化臂的回答模型会看到六维标准、红线、本题详细 Rubric 和检索 KB，以主动生成更严格、更高分的答案；但它不负责给自己打分。独立裁判再按相同 Rubric 评分，避免 self-grading bias。本包采用三个层：

1. `answer_system_zh.md`：优化臂共用回答行为、六维目标与医学安全边界；
2. 本题详细 Rubric + Reviewed KB retriever：仅优化臂获得逐题满分锚点和 top-k 审核知识片段；
3. `judge_system_zh.md`：独立裁判看到原问题、隐藏 Rubric、回答和用于核验的 KB 证据。

最终结果仍在同一条记录中包含回答、逐维评分、题目检查、红线和 before/after 差值。

## 输入

- KB：`../bone-modifying-kb-v3-reviewed`
- Benchmark：`../骨转移患者问答_Benchmark与六维评分标准_246题_20260817.xlsx`
- 六平台配置：`config/platforms.example.json`

程序直接解析 Excel，不依赖一份可能过期的 Rubric 转换文件。原始工作簿只读，不会修改。

## 运行

在项目根目录使用已有虚拟环境：

```bash
cp kb_ab_eval/config/platforms.example.json kb_ab_eval/config/platforms.local.json
set -a
source model_response/.env
set +a

# 离线校验 KB、Rubric、检索与提示组装；不调用模型
./.venv/bin/python -m kb_ab_eval.run validate

# 每个平台跑 3 题、两臂；只采集回答（baseline + optimized）
./.venv/bin/python -m kb_ab_eval.run answers \
  --platform-config kb_ab_eval/config/platforms.local.json \
  --limit 3 --workers 6 --run-id smoke-001

# 使用独立裁判逐条评分。不要把六个被测模型之一同时当裁判。
export JUDGE_API_KEY='...'
export JUDGE_BASE_URL='https://your-openai-compatible-gateway/v1'
export JUDGE_MODEL='gpt-5.6-sol'
./.venv/bin/python -m kb_ab_eval.run judge --run-id smoke-001 --workers 4

# 合并结果，生成 JSON、JSONL、CSV 和 HTML 报告
./.venv/bin/python -m kb_ab_eval.run report --run-id smoke-001
```

若使用当前 Codex 已配置的 OpenAI-compatible 裁判凭据，可改传 `--judge-api-key-env OPENAI_API_KEY`；程序只读取密钥，不写入结果或日志。

完整 246 题运行会产生 `6 × 246 × 2 = 2,952` 次回答调用，另有 2,952 次独立评分调用。先用 3–10 题 smoke test 核对平台身份、输出完整度与成本，再做全量。

## 公平实验约束

- 同平台两臂使用同一模型版本、temperature、max tokens 和问题文本。实验变量是完整优化包：严格 system prompt + 通用六维标准 + 红线 + 本题详细 Rubric + KB。
- 默认关闭额外联网搜索，避免“搜索差异”污染 KB 因果效应。
- 两臂顺序按 question/platform 稳定哈希交叉，减少时间漂移和 first-call bias。
- 裁判输入不含 `baseline`/`optimized` 臂标签，避免期望偏差。
- 原样保存模型版本、提示哈希、KB 哈希、检索 node_id、时延、错误、裁判理由和原始 JSON。
- 主指标使用逐题配对 delta；同时报告 improved/tied/regressed、通过率变化、红线变化和检索覆盖检查。该结果是“优化包 uplift”，不能单独归因为 KB uplift；若要拆解 KB 的独立贡献，应另加 prompt-only 第三臂。
- D6 是跨题一致性维度。第一阶段按专属 Rubric 的关联口径评分；全量报告还会输出需要做跨题一致性二次审计的关联题提示。

## 主要输出

每个 run 位于 `kb_ab_eval/runs/<run-id>/`：

- `manifest.json`：版本、哈希、平台及实验参数；
- `answers.jsonl`：两个臂的原始回答和检索证据；
- `scores.jsonl`：盲法六维评分与红线；
- `paired_results.jsonl`：逐平台逐题 before/after 记录；
- `summary.csv`：平台汇总；
- `report.html`：可浏览的总体和逐题报告；
- `result.json`：完整机器可读结果。

密钥只从环境变量读取，不写入日志或输出。任何患者真实数据在进入这个流程前必须完成授权与去标识化。本系统用于模型质量评测与患者教育内容验证，不用于自动诊断、处方或替代医学审核。
