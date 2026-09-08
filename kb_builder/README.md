# build-agent-kb — 通用结构化知识库构建引擎

## 概述

将任意来源（PDF、Word、Excel、扫描件、知网导出、API 数据）转换为结构化知识库（Markdown 或 Word 格式），保持实体关系可追溯、来源可验证。

**核心特性**：
- ✅ 多来源适配（文本/扫描件/Excel/知网/ClinicalTrials API）
- ✅ 优雅降级（验证失败时诚实标记，不拒绝收录）
- ✅ 自动质量检查（孤立实体、缺失来源、归一化冲突）
- ✅ 7 种 citation 验证通道（PubMed/DOI/中文期刊/专利/法规/内部材料）
- ✅ 双格式输出（Markdown 推荐 / Word 正式交付）
- ✅ **缺陷驱动优化**（读评测结果 → 按实测缺陷排产 → 覆盖率校验）

---

## 快速开始

### 1. 安装依赖

**核心依赖（必需）**：
```bash
pip3 install python-docx pdfplumber
```

**扩展功能（可选）**：
```bash
# 结构化数据支持（Excel/CSV）
pip3 install pandas openpyxl

# OCR 扫描件支持（二选一或都装）
pip3 install pytesseract pdf2image
brew install tesseract
# 或 macOS 原生：
pip3 install pyobjc-framework-Vision
```

### 2. 使用方式

#### 作为 Claude Code Skill

将此目录放到 `~/.claude/skills/build-agent-kb/`，然后在对话中：
```
/build-agent-kb
```
Claude 会加载完整工作流程。

#### 独立脚本使用

```bash
# 1. 生成 Markdown（推荐）
python3 scripts/build_kb_md.py your-kb.json output.md

# 2. 生成 Word
python3 scripts/build_kb_docx.py your-kb.json output.docx

# 3. 质量检查
python3 scripts/validate_kb.py your-kb.json

# 3b. 质量检查 + 缺陷覆盖对照
python3 scripts/validate_kb.py your-kb.json --defects defect-profile.json

# 4. 从评测导出生成缺陷画像（建 KB 之前跑）
python3 scripts/analyze_eval.py eval-export.json -o defect-profile.json

# 5. 提取结构化数据
python3 scripts/extract_structured.py data.xlsx
python3 scripts/extract_structured.py cnki-export.txt --type cnki

# 6. OCR 扫描件
python3 scripts/extract_image_pdf.py scanned.pdf output.txt
```

---

## 文件结构

```
build-agent-kb/
├── SKILL.md                        # 完整工作流文档（10 步方法）
├── README.md                       # 本文件
├── OPTIMIZATION_SUMMARY.md         # 优化历史记录
├── scripts/
│   ├── build_kb_md.py             # JSON → Markdown（推荐）
│   ├── build_kb_docx.py           # JSON → Word
│   ├── validate_kb.py             # 质量检查（--defects 可查缺陷覆盖）
│   ├── analyze_eval.py            # 评测导出 → 缺陷画像
│   ├── extract_pdf.py             # 提取文本 PDF
│   ├── extract_docx.py            # 提取 Word
│   ├── extract_image_pdf.py       # OCR 扫描件
│   └── extract_structured.py      # 解析 Excel/知网/API
├── references/
│   ├── entry-schema.md            # KB JSON 规范
│   ├── source-and-compliance.md   # 来源分级与验证
│   └── defect-driven-optimization.md  # 缺陷 → KB 原语的映射与判据
├── examples/
│   ├── example-kb.json            # 示例知识库
│   └── example-defect-profile.json  # 示例缺陷画像
└── tests/
    ├── test_build_kb_md.py        # Markdown 生成器测试
    ├── test_build_kb_docx.py      # Word 生成器测试
    ├── test_analyze_eval.py       # 缺陷聚合测试
    └── test_validate_kb.py        # 质量检查测试
```

---

## 工作流概览

```
1 确定范围与模板
  ↓
2 阅读现有资料 + 覆盖分析
  ↓
3 缺陷画像（有评测数据时；无则跳过）
  ↓
4 起草《所需资料清单》（按风险排序，不按主题顺序）
  ↓
[检查点①] 用户审核清单
  ↓
5 收集来源（自动下载 + 人工收集）
  ↓
[门控] 确认收集完成
  ↓
6 提取·分类·归一化·建立关系
  ↓
7 编写 KB JSON
  ↓
8 生成 Markdown/Word
  ↓
[检查点②] 用户审核交付物
  ↓
9 验证数字 + 质量检查（含缺陷覆盖）
  ↓
10 整理归档
```

详细说明见 `SKILL.md`

---

## 缺陷驱动优化

普通的 KB 建设是**单向**的：有什么资料 → 建什么内容。覆盖判断只能回答「有没有资料」，回答不了「模型实际错在哪」。

如果你手上有一份该领域的**模型评测结果**（题目 × 模型 × 评分），skill 就能把它变成排产依据：

```bash
# 1) 评测导出 → 缺陷画像（同时产出人类可读的《缺陷图谱.md》）
python3 scripts/analyze_eval.py eval-export.json -o defect-profile.json

# 2) 建 KB 时按画像排产（agent 在 SKILL.md step 3–4 完成）

# 3) 校验 KB 是否真的覆盖了实测缺陷
python3 scripts/validate_kb.py your-kb.json --defects defect-profile.json
```

**它做什么**：按维度/红线聚类缺陷并分 P0/P1/P2 档、按题型和分组排风险、抽取被编造对象的词频、挑出「全员失败」的题目热点，并为每类缺陷保留逐字样例。

**它不做什么**：不对自由文本的缺陷描述做语义归类。脚本只给计数、词频和样例——**读样例、判断这类缺陷到底是什么、决定怎么补，是 agent 的工作**。这与 skill 一贯的「脚本管机械、agent 管判断」分工一致。

**缺陷如何落到 KB 结构上**：

| 缺陷类型 | KB 对应原语 |
|---|---|
| 夸大/越界表述 | 词条字段 `边界与禁止表述` + 反例卡 |
| 实体混淆（把两个东西当成一个） | 词条字段 `易混淆辨析` + `appendix.易混淆对` |
| 编造数字/文献 | claim 级 `evidence`；数字无 evidence 时校验告警 |
| 安全信息漏报 | 词条字段 `风险信号与行动` |
| 答案不可执行 | 词条字段 `可执行下一步` |
| 跨题结论矛盾 | KB 单一真相源 + 归一化表 |

字段与 `antipatterns`（反例卡）、`appendix.易混淆对` 的完整规范见 `references/entry-schema.md`；判据与写法见 `references/defect-driven-optimization.md`。

**没有评测数据怎么办**：跳过即可。step 3 是可选的，其余 9 步与原来完全一致。

---

## KB JSON 格式

最小示例：
```json
{
  "title": "知识库标题",
  "summary_table": [
    {"标准名": "实体A", "实体类型": "概念", "一句话定义": "..."}
  ],
  "sections": [
    {
      "title": "核心词条",
      "entries": [
        {
          "标准名": "实体A",
          "实体类型": "概念",
          "一句话定义": "简洁定义",
          "关联实体与关系": "实体B（作用于）",
          "来源": "[S] 某某标准文档"
        }
      ]
    }
  ]
}
```

完整规范见 `references/entry-schema.md`

---

## 来源分级

| 标签 | 层级 | 示例 |
|-----|------|------|
| `[S]` | 一手标准（最高权威） | NMPA 说明书、FDA 标签 |
| `[G]` | 指南/共识 | CSCO 指南、专家共识 |
| `[T]` | 关键研究/文献 | 注册 III 期、PubMed 文献 |
| `[I]` | 内部材料 | 医保申报、企业公告 |

**优先级**：`[S] > [G] > [T] > [I]`，标签冲突时采用高等级

---

## 质量检查

`validate_kb.py` 自动检测：
- ❌ **错误**（必须修复）：缺失来源、归一化循环、模糊关系动词、反例卡缺必填字段、易混淆对与「同义(=)」自相矛盾、P0 红线缺陷无反例卡
- ⚠️ **警告**（建议修复）：孤立实体、未声明动词、过度归一化、含具体数字却无 claim 级 evidence、P0 维度缺陷无法自动确认覆盖
- ℹ️ **信息**：统计摘要

退出码：`0` = 通过，`1` = 警告，`2` = 错误

传入 `--defects defect-profile.json` 时，报告会多出一张「缺陷覆盖对照」表，逐条列出实测缺陷在 KB 中的落点。

---

## 适用场景

✅ **适合**：
- 医药知识库（药品、疾病、治疗方案）
- 企业内部知识库（产品、流程、规范）
- 法律法规库（条款、案例、解释）
- 技术文档库（API、架构、最佳实践）

❌ **不适合**：
- 图数据库构建（用专业图工具）
- 一次性总结文档
- 自由格式散文

---

## 依赖说明

| 功能 | 依赖 | 必需？ |
|------|------|--------|
| Markdown 生成 | 纯 Python stdlib | ✅ 必需 |
| Word 生成 | `python-docx` | ✅ 必需 |
| PDF 提取 | `pdfplumber` | ✅ 必需 |
| Excel 解析 | `pandas`, `openpyxl` | ⚪ 可选 |
| OCR 扫描件 | `pytesseract`/`pdf2image` 或 `pyobjc-framework-Vision` | ⚪ 可选 |

---

## 常见问题

### Q: 我的 PDF 是扫描件，能提取吗？
A: 可以。`extract_image_pdf.py` 会自动检测并调用 OCR（需安装 tesseract 或在 macOS 上用 Vision）。失败时会标记 `[需人工录入]`。

### Q: 我的来源在付费数据库，无法自动验证怎么办？
A: 使用多通道验证的 fallback 机制，诚实标记为 `[未核验·仅记录]`，记录所有可得的书目信息。不要伪造 PMID 或 DOI。

### Q: 我的领域不是医药，能用吗？
A: 可以。修改 `references/source-and-compliance.md` 中的合规表，将医药特定规则替换为你的领域边界（如金融：无前瞻性保证；法律：无跨辖区建议）。实体类型、关系动词、归一化规则都是项目自定义的。

### Q: 生成的 Markdown 能直接用于 RAG 吗？
A: Markdown 是人类可读的审阅格式。RAG 建议直接用 KB JSON（结构化、带元数据）。如果 RAG 系统要求文本输入，Markdown 比 Word 更好解析。

### Q: 我想改输出样式怎么办？
A: 不要手改生成的 .md 或 .docx（会与 JSON 脱节）。改 JSON 然后重新生成。如需深度定制样式，修改 `scripts/build_kb_md.py` 或 `build_kb_docx.py`。

### Q: 我的评测结果不是 GEO 格式，`analyze_eval.py` 还能用吗？
A: 能。脚本内置的字段路径只是**默认 profile**，用 `--field-map custom.json` 覆盖即可：

```json
{
  "questions": "payload.items[]",
  "model_results": "runs[]",
  "model_id": "engine",
  "scoring_ok_value": "done"
}
```

只需写出与默认不同的键，其余自动沿用。写错的键会直接报错而不是静默忽略。字段契约见 `references/defect-driven-optimization.md`。

### Q: 「全员失败」（universal）到底怎么判定？
A: **只在「同一道题 × 同一个维度 × 所有已评模型都拿 0 分」这个粒度上才成立。** 两个更粗的定义都会塌掉：维度层面「所有模型都犯过」在回答量一大时几乎必然为真；「没有模型通过这道题」在严格 rubric 下对绝大多数题都为真。只有交集才既锐利、又同时点名了**哪道题**和**缺哪类知识**——这正是资料清单需要的。推理过程见 `references/defect-driven-optimization.md`。

### Q: 缺陷覆盖检查通过了，是不是就说明模型不会再犯这些错？
A: **不是。** 覆盖检查只能确认这个缺陷在 KB 中**被处理过**（有反例卡 / 有对应字段），不能确认**处理得对**。补完之后仍应重跑评测来验证效果。

### Q: 为什么维度缺陷只告警、不报错？
A: 红线是一句**具体的、可以引用的错话**，反例卡正好是它的形状。而维度（如「回答准确性」）是一根质量轴，写不出「D2 的正确说法」——它靠散落全库的字段和 evidence 来弥补，没有任何自动检查能确认这件事做到位了。所以对红线报错，对维度告警并列进「需人工确认」。

---

## 版本历史

- **v2.1** (2026-08-27): 增加缺陷驱动优化——`analyze_eval.py` 缺陷画像、反例卡（`antipatterns`）、易混淆对、4 个缺陷弥补字段、缺陷覆盖校验；工作流 9 → 10 步；补齐 Markdown 生成器、缺陷聚合与质量检查的测试
- **v2.0** (2026-08-25): 增加 Markdown 生成器、结构化数据提取、OCR 支持、多通道验证、质量检查
- **v1.0** (2024-07): 初始版本，仅支持 Word 输出 + 文本 PDF

---

## 许可与贡献

本 skill 为开源工具，可自由修改和分发。

改进建议和 bug 报告欢迎通过项目仓库提交。

---

## 技术支持

详细文档：
- 工作流完整说明：`SKILL.md`
- JSON 规范：`references/entry-schema.md`
- 来源验证与合规：`references/source-and-compliance.md`
- 缺陷驱动优化：`references/defect-driven-optimization.md`
- 优化历史：`OPTIMIZATION_SUMMARY.md`

示例：`examples/example-kb.json`、`examples/example-defect-profile.json`

测试：
```bash
python3 tests/test_build_kb_md.py
python3 tests/test_build_kb_docx.py
python3 tests/test_analyze_eval.py
python3 tests/test_validate_kb.py
```
