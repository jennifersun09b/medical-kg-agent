# RPA 平台采集 LLM 聊天界面 - 完整指南

本文档说明如何用 RPA 平台对 6 个中文 LLM 网页版聊天界面批量采集问答数据。

---

## 一、RPA 平台架构速览

```
                触发 API
                   ↓
配置平台 (config-platform)  ← 管理任务/账号/脚本，对外暴露 HTTPS API
    ↓  入队 Redis Stream
调度器 (scheduler)          ← 从 Redis 派单到 worker
    ↓  消费队列
Worker (worker)             ← 执行 Playwright 脚本，回写结果
```

**核心概念**：

- **Platform**（平台）：一个目标站点（如 doubao.com）+ 账号池 + 登录脚本
- **Task**（任务）：指向一个脚本 + 一个账号 + 可选 cron
- **Script**（脚本）：Playwright Python 代码，存在 DB `script` 表
- **task_run**（执行实例）：一次运行，`pending → running → success/failed`
- **custom_input**：每次触发时传入的 JSON 参数，脚本通过 `os.environ["API_CUSTOM_INPUT"]` 或 stdin 读取

**脚本契约**（worker 子进程执行模型）：

```python
# 入参: stdin JSON + env API_CUSTOM_INPUT
# 登录态: env RPA_STORAGE_STATE（平台维护的 storage_state.json 路径）
# 浏览器: env CDP_ENDPOINT（WORKER_HEADED=1 时，attach worker 预启动的浏览器）
# 出参: stdout 最后一行必须是 {"summary": {...}, "rows": [...]}
# 日志: 全部走 stderr（stdout 会被解析成结果）
# 退出码: 异常 exit 1；登录态失效 summary.logged_in=false + exit 0
```

---

## 二、本地开发验证（可选，推荐先做）

在把脚本上传到线上 worker 前，可以在本地验证它能否正确抓取回答。

### 2.1 本地单独验证脚本

```bash
cd <repo>/rpa_collection
python3 -m venv .venv
.venv/bin/pip install playwright python-dotenv
.venv/bin/playwright install chromium

# 模拟 worker 环境变量，测试豆包
export API_CUSTOM_INPUT='{"platform":"doubao","questions":[{"question_id":"Q001","question":"肺癌骨转移有可能治好吗？"}]}'
.venv/bin/python chat_ui_task.py
```

**预期输出**：
- stderr 打印进度日志
- stdout 最后一行是 `{"summary": {...}, "rows": [...]}`
- `rows[0].response` 包含 LLM 回答文本

**如果报错 `RPA_STORAGE_STATE` 缺失**：这是正常的——本地没有登录态文件。要么：
1. 手动登录一次网站，用 Playwright 导出 `storage_state.json`，再 `export RPA_STORAGE_STATE=/path/to/it`
2. 跳过本地验证，直接在线上 worker 上测（线上账号已登录，平台自动拉 OSS）

### 2.2 选择器核对（重要）

各家前端改版频繁，`SITES` 里的选择器是**起点**，不保证长期有效。
在真实页面上用 Chrome DevTools 核对：

```python
"input": "textarea, div[contenteditable='true']",  # 输入框
"answer": "[class*='message-content']",            # 回答区域
"logged_out_hint": ["登录", "手机号登录"],          # 未登录关键词
```

F12 → Elements → Ctrl+F 搜选择器，确认能唯一定位到输入框和最后一条回答。

---

## 三、部署到 RPA 平台

### 3.1 前置条件

- RPA 平台已部署并可访问：`https://<your-rpa-platform>`
- 你有 `OPEN_API_TOKEN`（对外 API 鉴权）
- 6 个平台的账号已在「平台与账号管理」配置好，且**登录态有效**（status=valid）

### 3.2 上传脚本到平台

脚本存在 DB `script` 表，关联 `task_def.script_biz_key`。有两种方式：

**方式 A：通过配置平台 UI**
1. 登录配置平台 → 脚本管理 → 新增脚本
2. `biz_key` = `chat_ui_batch`（自定义，但要记住）
3. `lang` = `python`
4. `content` = 复制整个 `chat_ui_task.py` 内容
5. 平台会自动计算 sha256 并保存

**方式 B：seed 脚本（推荐，适合迭代）**
参考 `code/config-platform/scripts/seed_shangou.ts`，写一个 `seed_chat_ui.ts`：

```typescript
import fs from "fs";
import crypto from "crypto";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

async function main() {
  const content = fs.readFileSync("../../rpa_chat/chat_ui_task.py", "utf-8");
  const sha256 = crypto.createHash("sha256").update(content, "utf8").digest("hex");

  await prisma.script.upsert({
    where: { biz_key_version: { biz_key: "chat_ui_batch", version: 1 } },
    create: {
      biz_key: "chat_ui_batch",
      version: 1,
      lang: "python",
      storage_type: "db",
      content,
      sha256,
      enabled: true,
    },
    update: { content, sha256, enabled: true },
  });

  console.log("✓ chat_ui_batch v1 已更新，sha256 =", sha256.slice(0, 12));
}

main().finally(() => prisma.$disconnect());
```

```bash
cd <rpa-platform>/code/config-platform
npm run ts-node scripts/seed_chat_ui.ts
```

### 3.3 创建 6 个任务

在配置平台「任务配置」页，每个平台创建一个任务：

| 平台     | task_name          | script_biz_key | account_id | timeout_s | 备注 |
|----------|-------------------|----------------|-----------|----------|------|
| qianwen  | qianwen_chat_ui   | chat_ui_batch  | <千问账号>  | 600      |      |
| doubao   | doubao_chat_ui    | chat_ui_batch  | <豆包账号>  | 600      |      |
| zhipu    | zhipu_chat_ui     | chat_ui_batch  | <智谱账号>  | 600      |      |
| kimi     | kimi_chat_ui      | chat_ui_batch  | <Kimi账号> | 600      |      |
| deepseek | deepseek_chat_ui  | chat_ui_batch  | <DS账号>   | 600      |      |
| hunyuan  | hunyuan_chat_ui   | chat_ui_batch  | <混元账号>  | 600      |      |

**关键字段**：
- `script_biz_key` = `chat_ui_batch`（刚才上传的脚本）
- `script_version` = 留空（自动取最新 enabled 版本）
- `result_writer` = `{"driver": "script_self"}`（脚本返回 rows，worker 不落库）
- `timeout_s` = 600（50 题 + 流式等待，给足时间）
- `account_id` = 选对应平台的账号

保存后记下每个任务的 `task_id`（例如 101, 102, ..., 106）。

---

## 四、准备触发器环境

```bash
cd <repo>/rpa_collection
python3 -m venv .venv
.venv/bin/pip install requests python-dotenv

# 创建 .env 文件
cat > .env << 'EOF'
RPA_PLATFORM_URL=https://<your-rpa-platform>
OPEN_API_TOKEN=<你的Token>
TASK_IDS={"qianwen":"101","doubao":"102","zhipu":"103","kimi":"104","deepseek":"105","hunyuan":"106"}
EOF
```

把 `TASK_IDS` 里的数字换成刚才创建的真实 task_id。

---

## 五、执行采集

### 5.1 全部 6 个平台

```bash
cd <repo>/rpa_collection
.venv/bin/python trigger_all_platforms.py
```

**流程**：
1. 从 `question_list_all_new.jsonl` 随机抽 50 题
2. 逐个平台 POST `/api/v1/tasks/trigger/sync`
3. custom_input 带 `{platform, questions, interval_s}`
4. 等待 worker 执行完（最多 630s，600s 脚本 + 30s 余量）
5. 提取 `data.result.rows` 写入 `results/<platform>_results.jsonl`

### 5.2 指定平台 + 固定抽样

```bash
# 只跑千问和豆包，固定随机种子 42
.venv/bin/python trigger_all_platforms.py --platforms qianwen doubao --seed 42

# 断点续传（跳过 results/ 中已有的）
.venv/bin/python trigger_all_platforms.py --resume
```

### 5.3 观察进度

- **终端输出**：每个平台触发后实时打印 `ok/total 题，耗时 Xs`
- **配置平台 UI**：执行总览 → 找到对应 task_run → 状态 / result_summary
- **Worker 日志**（如果 worker 是本地跑）：stderr 看到脚本打印的 `[ask] 已发送` / `[1/50] Q001 ok 12.3s`

---

## 六、结果格式

`results/<platform>_results.jsonl` 每行一题：

```json
{
  "question_id": "Q001",
  "cancer_type": "肺癌骨转移",
  "question_type": "治疗目标",
  "question": "肺癌骨转移有可能治好吗？",
  "model": "doubao",
  "response": "肺癌骨转移通常属于晚期...",
  "error": null,
  "elapsed_s": 12.3,
  "channel": "web_rpa"
}
```

`error` 不为空时，`response` 为 null；`error: "empty_or_timeout"` 表示流式超时。

---

## 七、常见问题

### 7.1 任务失败 `status=not_logged_in`

**原因**：账号 storage_state 失效，脚本判定未登录。

**解决**：
1. 配置平台 → 平台与账号管理 → 找到对应账号
2. 点「登录验证」（自动重登）或「人工登录」（有头浏览器手动登）
3. 等 `会话状态` 变 `valid` 后重新触发任务

### 7.2 所有回答都是空 / 截断

**原因**：选择器不准确，`wait_for_answer` 没拿到文本。

**排查**：
1. 用 worker debug 接口触发一次：
   ```bash
   curl -X POST https://<your-rpa-worker>/debug/run/script \
     -H "Authorization: Bearer rpa-debug" \
     -H "Content-Type: application/json" \
     -d '{
       "kind": "login",
       "account_id": 342,
       "script_content": "<chat_ui_task.py 全文>",
       "params": {"platform":"doubao","questions":[{"question_id":"Q001","question":"测试"}]},
       "timeout_s": 180
     }'
   ```
2. 轮询 `/debug/jobs/<job_id>` 看 stderr 日志，确认卡在哪一步
3. 修改 `SITES[platform]["answer"]` 选择器后重新 seed 脚本

### 7.3 触发超时 `504 Gateway Timeout`

**原因**：50 题流式渲染耗时超过 `SYNC_TIMEOUT_S=600s`。

**解决**：
- 方案 A：拆成多批，每批 10 题
- 方案 B：用异步触发 `/api/v1/tasks/trigger`（不等结果），轮询 `/api/v1/task-runs/<task_run_id>` 拿结果

### 7.4 脚本改了但线上没生效

**原因**：DB `script` 表的 sha256 没同步更新，worker 校验失败。

**解决**：用 seed 脚本重新灌库（自动计算新 sha256）；或手动 `UPDATE script SET content=..., sha256=... WHERE biz_key='chat_ui_batch'`

---

## 八、优化建议

### 8.1 并行采集（多 worker）

目前是串行 6 个平台 × 50 题 = ~2 小时。部署多个 worker 后，6 个平台可以同时跑：

```bash
# 机器 A
WORKER_NODE_NAME=worker-1 .venv/bin/python -m app

# 机器 B
WORKER_NODE_NAME=worker-2 .venv/bin/python -m app
```

触发器改用异步触发，一口气入队 6 个任务，worker 抢单并行执行。

### 8.2 防风控策略

- `custom_input.interval_s` 设大一点（5~10s），避免过快触发反爬
- 每个平台用**真实用户账号**（不要用临时号），登录态更稳定
- 错峰采集（避开业务高峰）

### 8.3 选择器维护

前端改版后选择器会失效。建议：
1. 每周定期跑一次 10 题测试
2. 失败时立即核对选择器，更新脚本
3. 考虑用更稳定的属性选择器（`data-testid`）而非 class

---

## 九、与 API 采集对比

| 维度       | API 采集 (run.py)      | RPA 网页采集 (本方案)       |
|------------|----------------------|--------------------------|
| 速度       | 快（~2s/题）           | 慢（~12s/题，流式等待）    |
| 稳定性     | 高（官方接口）          | 低（选择器、登录态）       |
| 成本       | API quota              | 账号 + worker 机器        |
| 适用场景   | 有 API key             | 仅网页版 / 测试 UI 体验    |

**建议**：优先用 API；仅当某平台封 API / 想测 UI 实际渲染效果时才用 RPA。

---

## 附录：文件清单

```
<project-root>/
├── rpa_chat/
│   ├── chat_ui_task.py            # RPA 脚本（上传到平台）
│   ├── trigger_all_platforms.py   # 触发器
│   ├── .env                       # 环境变量
│   └── results/                   # 输出目录
│       ├── qianwen_results.jsonl
│       ├── doubao_results.jsonl
│       └── ...
└── model_response/
    ├── question_list_all_new.jsonl  # 题库
    └── models.py                    # API 配置（参考）
```

---

**下一步**：按「三、部署到 RPA 平台」上传脚本并创建任务，然后执行「五、执行采集」开始跑数据。
