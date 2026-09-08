# 安装与使用指南

## 快速安装（3 步）

### 1. 解压到 Claude Code skills 目录

```bash
# macOS/Linux
unzip build-agent-kb.zip -d ~/.claude/skills/

# Windows
# 解压到 %USERPROFILE%\.claude\skills\
```

### 2. 安装依赖

```bash
# 核心依赖（必需）
pip3 install python-docx pdfplumber

# 可选扩展（按需安装）
pip3 install pandas openpyxl                    # Excel 支持
pip3 install pytesseract pdf2image              # OCR 支持
brew install tesseract                          # macOS OCR 引擎
```

### 3. 验证安装

```bash
cd ~/.claude/skills/build-agent-kb

# 全部测试（全部应显示 OK）
python3 tests/test_build_kb_md.py
python3 tests/test_build_kb_docx.py
python3 tests/test_analyze_eval.py
python3 tests/test_validate_kb.py

# 生成冒烟测试
python3 scripts/build_kb_md.py examples/example-kb.json /tmp/test.md
# 应生成 /tmp/test.md

# 缺陷分析冒烟测试（用自带的示例画像反查覆盖情况）
python3 scripts/validate_kb.py examples/example-kb.json \
  --defects examples/example-defect-profile.json
# 预期退出码 1：无错误，但 P0 维度缺陷需人工确认
# 注意：validate_kb.py 会在输入文件旁写出「质量报告.md」
```

`analyze_eval.py` 需要一份评测导出才能跑，示例目录中只提供了它的**输出**样例（`example-defect-profile.json`）；用法见 `references/defect-driven-optimization.md`。

---

## 作为 Claude Code Skill 使用

安装完成后，在 Claude Code 对话中：

```
/build-agent-kb
```

Claude 会加载完整的 10 步工作流。

---

## 独立脚本使用（无需 Claude Code）

所有脚本都可以独立运行：

```bash
# 生成 Markdown
python3 ~/.claude/skills/build-agent-kb/scripts/build_kb_md.py your-kb.json output.md

# 生成 Word
python3 ~/.claude/skills/build-agent-kb/scripts/build_kb_docx.py your-kb.json output.docx

# 质量检查
python3 ~/.claude/skills/build-agent-kb/scripts/validate_kb.py your-kb.json

# 评测导出 → 缺陷画像（可选，有评测数据时）
python3 ~/.claude/skills/build-agent-kb/scripts/analyze_eval.py eval.json -o defect-profile.json

# 质量检查 + 缺陷覆盖对照
python3 ~/.claude/skills/build-agent-kb/scripts/validate_kb.py your-kb.json --defects defect-profile.json

# 提取结构化数据
python3 ~/.claude/skills/build-agent-kb/scripts/extract_structured.py data.xlsx

# OCR 扫描件
python3 ~/.claude/skills/build-agent-kb/scripts/extract_image_pdf.py scanned.pdf output.txt
```

---

## 依赖问题排查

### Python 版本
需要 Python 3.8+
```bash
python3 --version
# 应显示 Python 3.8.0 或更高
```

### 依赖安装失败

**macOS externally-managed-environment 错误**：
```bash
pip3 install --break-system-packages python-docx pdfplumber
```

**Windows 权限错误**：
```bash
pip3 install --user python-docx pdfplumber
```

### OCR 不工作

**Tesseract 未安装**：
```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# Windows
# 从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装
```

**中文 OCR 支持**：
```bash
# macOS
brew install tesseract-lang

# Linux
sudo apt-get install tesseract-ocr-chi-sim
```

---

## 目录结构

安装后应该看到：

```
~/.claude/skills/build-agent-kb/
├── README.md                   # 概述文档
├── INSTALL.md                  # 本安装指南
├── SKILL.md                    # 完整工作流文档
├── scripts/                    # 所有可执行脚本
├── references/                 # 规范文档
├── examples/                   # 示例文件
└── tests/                      # 测试文件
```

---

## 第一次使用

### 方式 A：通过 Claude Code（推荐）

1. 在 Claude Code 中输入 `/build-agent-kb`
2. 描述你的需求（如"构建地舒单抗药品知识库"）
3. 如果手上有该领域的模型评测结果，一并给 Claude——它会先做缺陷画像再排资料清单
4. Claude 会引导你完成 10 步流程

### 方式 B：直接使用脚本

1. 准备你的源文件（PDF/Word/Excel）
2. 按照 `SKILL.md` 的 10 步流程手工执行
3. 使用脚本辅助提取、验证、生成

---

## 更新

要更新到新版本：

1. 备份你的项目数据（源文件 + JSON）
2. 删除旧的 `~/.claude/skills/build-agent-kb/`
3. 解压新版本 zip 到相同位置
4. 重新运行测试验证

**注意**：你的项目数据（放在项目目录的 JSON 和源文件）不会受影响。

---

## 获取帮助

- **完整工作流说明**：查看 `SKILL.md`
- **JSON 格式规范**：查看 `references/entry-schema.md`
- **来源验证方法**：查看 `references/source-and-compliance.md`
- **缺陷驱动优化**：查看 `references/defect-driven-optimization.md`
- **示例文件**：查看 `examples/example-kb.json`、`examples/example-defect-profile.json`

---

## 常见使用场景

### 场景 1：医药知识库
```bash
# 1. 收集说明书 PDF、指南、文献
# 2. 使用 /build-agent-kb，让 Claude 引导流程
# 3. 生成 Markdown 供内部审阅
# 4. 质量检查通过后生成 Word 正式交付
```

### 场景 1b：已有评测结果，按模型缺陷补 KB
```bash
# 1. 从评测导出生成缺陷画像
python3 scripts/analyze_eval.py eval-export.json -o defect-profile.json
# 同时产出《缺陷图谱.md》，可直接给人看

# 2. 用 /build-agent-kb 建 KB，把画像交给 Claude 作排产依据

# 3. 校验实测缺陷是否真的被覆盖
python3 scripts/validate_kb.py kb.json --defects defect-profile.json
```

### 场景 2：Excel 数据整理
```bash
# 1. 提取 Excel 为结构化 JSON
python3 scripts/extract_structured.py data.xlsx > extracted.json

# 2. 手工编辑 JSON 补充关系和来源
# 3. 质量检查
python3 scripts/validate_kb.py kb.json

# 4. 生成 Markdown
python3 scripts/build_kb_md.py kb.json output.md
```

### 场景 3：扫描件 PDF 处理
```bash
# 1. OCR 提取
python3 scripts/extract_image_pdf.py scanned.pdf extracted.txt

# 2. 检查提取质量，必要时人工补充
# 3. 将内容整合到 KB JSON
# 4. 按正常流程生成和验证
```

---

## 移除

如不再需要，删除整个目录即可：

```bash
rm -rf ~/.claude/skills/build-agent-kb
```

你的项目数据（JSON 和源文件）不在此目录，不会被删除。
