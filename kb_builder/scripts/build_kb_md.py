#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kb_md.py — Data-driven structured knowledge-base Markdown generator.

Reads ONE knowledge-base JSON file and renders a structured .md file. By default the
document starts with a title and goes straight into the knowledge content: summary
table, entries, topic sections, Q&A, appendix, and sources.

Domain-agnostic: works for any project. All content comes from the JSON;
this script only formats it as Markdown.

Usage:
    python3 build_kb_md.py input.json [output.md]

If output.md is omitted, uses the JSON's "output" field (with .md extension),
else input basename + .md.

No external dependencies required (pure Python stdlib).

JSON schema: identical to build_kb_docx.py (see that file for full documentation).
"""
import sys
import json
import os
from pathlib import Path

PREFERRED_FIELD_ORDER = [
    "实体类型", "别名/同义词", "易混淆辨析", "一句话定义",
    "作用机制/关键内容", "病理机制", "发生机制",
    "关键数据/证据", "边界与禁止表述",
    "适应症/适用人群", "用法用量要点", "关键安全性", "风险信号与行动",
    "关联实体与关系",
    "可执行下一步", "常见问题", "来源", "合规备注",
]

# Anti-pattern cards are worthless without the correct replacement and its
# source — a card that only says "don't say X" leaves the same gap it found.
ANTIPATTERN_REQUIRED_FIELDS = ["缺陷代码", "错误表述", "为什么错", "正确说法", "来源"]

def die(msg):
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(1)

def validate(data):
    """Basic validation - same as docx version."""
    if not str(data.get("title", "")).strip():
        die("title is required")
    validate_antipatterns(data)

def validate_antipatterns(data):
    """Reject anti-pattern cards missing a required field.

    Shared by both generators so the Markdown path cannot silently accept data
    the Word path rejects.
    """
    for idx, card in enumerate(data.get("antipatterns", []) or [], 1):
        if not isinstance(card, dict):
            die(f"antipatterns[{idx}] is not an object")
        label = card.get("id") or f"antipatterns[{idx}]"
        missing = [f for f in ANTIPATTERN_REQUIRED_FIELDS if not str(card.get(f, "")).strip()]
        if missing:
            die(f"{label} 缺少必填字段: {', '.join(missing)}")

def escape_md(text):
    """Escape special Markdown characters in text."""
    if not text:
        return ""
    text = str(text)
    # Escape backslashes first, then other special chars
    text = text.replace("\\", "\\\\")
    # Don't escape everything - keep some formatting
    # Only escape pipe in tables context
    return text

def render_table(headers, rows, output):
    """Render a markdown table."""
    if not headers or not rows:
        return

    # Header row
    output.append("| " + " | ".join(str(h) for h in headers) + " |")
    # Separator
    output.append("| " + " | ".join("---" for _ in headers) + " |")
    # Data rows
    for row in rows:
        cells = []
        for val in row:
            cell_text = str(val) if val is not None else ""
            # Replace newlines with <br> for table cells
            cell_text = cell_text.replace("\n", "<br>")
            # Escape pipes in cell content
            cell_text = cell_text.replace("|", "\\|")
            cells.append(cell_text)
        output.append("| " + " | ".join(cells) + " |")
    output.append("")  # Blank line after table

def render_evidence_table(items, output):
    """Render structured evidence as a table."""
    if not items:
        return

    headers = ["结论", "来源", "原文位置", "适用边界"]
    rows = [
        [
            item.get("claim", ""),
            item.get("source", ""),
            item.get("location", ""),
            item.get("scope", ""),
        ]
        for item in items
    ]
    render_table(headers, rows, output)

def render_entry(entry, output):
    """Render a single KB entry (supports both Chinese and English field names)."""
    # Try multiple possible title fields
    title = (entry.get("标准名") or entry.get("name") or
             entry.get("实体名") or "(未命名实体)")
    output.append(f"### {title}\n")

    seen = set(["标准名", "name", "实体名", "id", "evidence", "sources"])

    # Special handling for type field
    entity_type = entry.get("实体类型") or entry.get("type")
    if entity_type:
        output.append(f"**类型**：{entity_type}\n")
        seen.update(["实体类型", "type"])

    # Definition (handle multiple field names)
    definition = (entry.get("一句话定义") or entry.get("定义") or
                  entry.get("definition") or entry.get("释义"))
    if definition:
        output.append(f"**定义**：{definition}\n")
        seen.update(["一句话定义", "定义", "definition", "释义"])

    # Render other fields in preferred order
    for field_name in PREFERRED_FIELD_ORDER:
        if field_name in entry and entry[field_name] and field_name not in seen:
            value = str(entry[field_name])
            output.append(f"**{field_name}**：{value}\n")
            seen.add(field_name)

    # Render any extra fields not in preferred order or seen set
    for key, value in entry.items():
        if key not in seen and value:
            # Format field name nicely
            if key in ["mechanism", "epidemiology", "clinical_significance",
                       "characteristics", "clinical_impact", "indications",
                       "dosage", "administration", "key_advantages", "key_limitations",
                       "advantages", "limitations", "clinical_value",
                       "clinical_manifestations", "emergency_management",
                       "treatment", "incidence", "risk_factors", "prevention",
                       "management", "contraindication", "reference_range",
                       "subtypes", "modalities", "procedures", "drug_class",
                       "brand_names", "indications_china", "high_risk_sites",
                       "laboratory_monitoring", "imaging_follow_up",
                       "key_evidence", "recommendation", "denosumab_preferred",
                       "bisphosphonate_preferred"]:
                # Convert snake_case to readable Chinese
                field_map = {
                    "mechanism": "作用机制/病理机制",
                    "epidemiology": "流行病学",
                    "clinical_significance": "临床意义",
                    "characteristics": "特征",
                    "clinical_impact": "临床影响",
                    "indications": "适应症",
                    "dosage": "用法用量",
                    "administration": "给药方式",
                    "key_advantages": "主要优势",
                    "key_limitations": "主要局限",
                    "advantages": "优点",
                    "limitations": "局限性",
                    "clinical_value": "临床价值",
                    "clinical_manifestations": "临床表现",
                    "emergency_management": "紧急处理",
                    "treatment": "治疗",
                    "incidence": "发生率",
                    "risk_factors": "危险因素",
                    "prevention": "预防",
                    "management": "管理",
                    "contraindication": "禁忌症",
                    "reference_range": "参考范围",
                    "subtypes": "亚型",
                    "modalities": "方式",
                    "procedures": "操作",
                    "drug_class": "药物分类",
                    "brand_names": "商品名",
                    "indications_china": "中国适应症",
                    "high_risk_sites": "高危部位",
                    "laboratory_monitoring": "实验室监测",
                    "imaging_follow_up": "影像学随访",
                    "key_evidence": "关键证据",
                    "recommendation": "推荐",
                    "denosumab_preferred": "优选地舒单抗",
                    "bisphosphonate_preferred": "优选双膦酸盐"
                }
                display_name = field_map.get(key, key)
            else:
                display_name = key

            output.append(f"**{display_name}**：{value}\n")
            seen.add(key)

    # Render sources if present
    if entry.get("sources"):
        output.append("\n**来源**：\n")
        for source in entry["sources"]:
            cite = source.get("cite", "")
            grade = source.get("grade", "")
            location = source.get("location", "")
            output.append(f"- {grade} {cite}")
            if location:
                output.append(f" （{location}）")
            output.append("\n")

    # Render structured evidence if present
    if entry.get("evidence"):
        output.append("\n#### 逐条证据\n")
        render_evidence_table(entry["evidence"], output)

    output.append("")  # Blank line after entry

def render_antipatterns(cards, output, title="常见错误答法与正确说法"):
    """Render anti-pattern cards: what was said wrong, why, and what to say."""
    if not cards:
        return

    output.append(f"## {title}\n")
    for card in cards:
        heading = card.get("缺陷名称") or card.get("缺陷代码") or card.get("id") or "错误答法"
        code = card.get("缺陷代码", "")
        output.append(f"### {heading}" + (f"（{code}）" if code and code != heading else "") + "\n")

        hits = card.get("命中次数")
        models = card.get("命中模型")
        provenance = []
        if hits:
            provenance.append(f"命中 {hits} 次")
        if models:
            provenance.append("模型：" + "、".join(str(m) for m in models))
        if card.get("universal"):
            provenance.append("**全员命中**")
        if provenance:
            output.append(f"*{' · '.join(provenance)}*\n")

        output.append(f"**❌ 错误表述**：{card.get('错误表述', '')}\n")
        output.append(f"**为什么错**：{card.get('为什么错', '')}\n")
        output.append(f"**✅ 正确说法**：{card.get('正确说法', '')}\n")

        if card.get("关联实体"):
            entities = card["关联实体"]
            if isinstance(entities, list):
                entities = "、".join(str(e) for e in entities)
            output.append(f"**关联实体**：{entities}\n")

        output.append(f"**来源**：{card.get('来源', '')}\n")

        if card.get("evidence"):
            render_evidence_table(card["evidence"], output)

        output.append("")

def main():
    if len(sys.argv) < 2:
        die("usage: python3 build_kb_md.py input.json [output.md]")

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        die(f"input not found: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    validate(data)

    # Determine output path
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    if not output_path:
        output_field = data.get("output", "")
        if output_field:
            # Change extension to .md
            output_path = str(Path(output_field).with_suffix('.md'))
        else:
            output_path = str(Path(input_path).with_suffix('.md'))

    output = []  # List of lines

    # Title
    title = data.get("title", "知识库")
    output.append(f"# {title}\n")

    # Front matter (optional)
    if data.get("show_front_matter"):
        if data.get("subtitle"):
            output.append(f"## {data['subtitle']}\n")
        if data.get("meta"):
            output.append(f"*{data['meta']}*\n")
        if data.get("note"):
            for line in str(data["note"]).split("\n"):
                output.append(f"{line}\n")
            output.append("")

    # Internal audit sections (optional)
    if data.get("show_audit_sections"):
        if data.get("coverage_map"):
            output.append(f"## {data.get('coverage_title', '本知识库对建设模版的覆盖对照')}\n")
            coverage_cols = data.get("coverage_columns",
                                    ["一级主题", "二级内容定义（模版）", "对应知识库实体"])
            coverage_rows = [[row.get(c, "") for c in coverage_cols]
                           for row in data["coverage_map"]]
            render_table(coverage_cols, coverage_rows, output)

    # Scenario map (optional)
    if data.get("scenario_map"):
        output.append(f"## {data.get('scenario_title', '使用场景 → 核心实体/关系')}\n")
        scenario_cols = data.get("scenario_columns",
                                ["使用场景", "触发/问题", "核心实体链", "关键关系"])
        scenario_rows = [[row.get(c, "") for c in scenario_cols]
                        for row in data["scenario_map"]]
        render_table(scenario_cols, scenario_rows, output)

    if data.get("show_audit_sections") and data.get("keyword_map"):
        output.append("## 客户关键词维度覆盖\n")
        keyword_cols = ["关键词维度", "代表关键词", "对应知识库实体"]
        keyword_rows = [[row.get(c, "") for c in keyword_cols]
                       for row in data["keyword_map"]]
        render_table(keyword_cols, keyword_rows, output)

    # Summary table
    if data.get("summary_table"):
        cols = data.get("summary_columns",
                       ["标准名", "实体类型", "别名/同义词", "一句话定义", "关联实体（关系）", "来源"])
        output.append("## 一、术语与实体总表\n")
        rows = [[row.get(c, "") for c in cols] for row in data["summary_table"]]
        render_table(cols, rows, output)

    # Sections of entries (support both "sections" and "topics" keys)
    sections = data.get("sections", []) or data.get("topics", [])
    for section in sections:
        section_title = section.get("title") or section.get("name")
        if section_title:
            output.append(f"## {section_title}\n")

        # Add description if present
        if section.get("description"):
            output.append(f"*{section['description']}*\n\n")

        # Process entries (support both "entries" and "entities" keys)
        entries = section.get("entries", []) or section.get("entities", [])
        for entry in entries:
            render_entry(entry, output)

        # Add relationships if present
        if section.get("relationships"):
            output.append("### 关系网络\n\n")
            for rel in section["relationships"]:
                source = rel.get("source", "")
                verb = rel.get("verb", "")
                target = rel.get("target", "")
                context = rel.get("context", "")
                output.append(f"- {source} **{verb}** {target}")
                if context:
                    output.append(f" （{context}）")
                output.append("\n")
            output.append("\n")

        # Add Q&A at section level if present
        if section.get("qa"):
            output.append("### 常见问答\n\n")
            for qa_item in section["qa"]:
                output.append(f"**Q**：{qa_item.get('question', '')}\n\n")
                output.append(f"**A**：{qa_item.get('answer', '')}\n\n")

                # Add evidence if present
                if qa_item.get("evidence"):
                    output.append("**证据**：\n\n")
                    for ev in qa_item["evidence"]:
                        claim = ev.get("claim", "")
                        source = ev.get("source", "")
                        grade = ev.get("grade", "")
                        location = ev.get("location", "")
                        output.append(f"- {claim} （{grade} {source}")
                        if location:
                            output.append(f"，{location}")
                        output.append("）\n")
                    output.append("\n")
            output.append("")

    # Generic topic tables
    for topic in data.get("topic_sections", []):
        output.append(f"## {topic['title']}\n")
        if topic.get("intro"):
            output.append(f"*{topic['intro']}*\n")
        cols = topic["columns"]
        rows = [[row.get(c, "") for c in cols] for row in topic["rows"]]
        render_table(cols, rows, output)

    # Anti-pattern cards (wrong answer → why → correct phrasing)
    render_antipatterns(data.get("antipatterns", []), output,
                        data.get("antipatterns_title", "常见错误答法与正确说法"))

    # Q&A
    if data.get("qa"):
        output.append("## 常见问答\n")
        if data.get("qa_intro"):
            output.append(f"*{data['qa_intro']}*\n")

        current_group = None
        for item in data["qa"]:
            group = item.get("group")
            if group and group != current_group:
                output.append(f"### {group}\n")
                current_group = group

            # Question
            output.append(f"**Q**：{item.get('q', '')}\n")

            # Answer(s)
            if item.get("answers"):
                for answer in item["answers"]:
                    audience = str(answer.get("audience", "答案")).strip() or "答案"
                    output.append(f"**{audience}**：{answer['text']}\n")
            else:
                output.append(f"**A**：{item.get('a', '')}\n")

            # Boundary
            if item.get("boundary"):
                output.append(f"*适用边界*：{item['boundary']}\n")

            # Entities
            if item.get("entities"):
                output.append(f"*涉及实体*：{item['entities']}\n")

            # Evidence
            if item.get("evidence"):
                output.append("**答案依据**：\n")
                render_evidence_table(item["evidence"], output)

            output.append("")  # Blank line between Q&A items

    # Appendix
    appendix = data.get("appendix", {})
    if appendix:
        output.append("## 附录\n")

        if appendix.get("实体类型"):
            output.append("### 实体类型定义\n")
            for item in appendix["实体类型"]:
                output.append(f"- {item}\n")
            output.append("")

        if appendix.get("关系动词"):
            output.append("### 关系动词表\n")
            output.append("、".join(str(v) for v in appendix["关系动词"]) + "\n\n")

        if appendix.get("关系模板"):
            output.append("### 关系模板表\n")
            template_headers = ["主体类型", "关系", "客体类型", "示例"]
            template_rows = [[r.get(h, "") for h in template_headers]
                           for r in appendix["关系模板"]]
            render_table(template_headers, template_rows, output)

        if appendix.get("归一化规则"):
            output.append("### 归一化规则表\n")
            norm_headers = ["出现的写法", "归一到", "关系类型"]
            norm_rows = [[r.get(h, "") for h in norm_headers]
                        for r in appendix["归一化规则"]]
            render_table(norm_headers, norm_rows, output)

        if appendix.get("易混淆对"):
            output.append("### 易混淆对（禁止合并）\n")
            output.append("*以下各对实体外观相近但不等价，任何情况下不得归一为同义。*\n\n")
            confusable_headers = ["A", "B", "区别维度", "混淆后果", "来源"]
            confusable_rows = [[r.get(h, "") for h in confusable_headers]
                               for r in appendix["易混淆对"]]
            render_table(confusable_headers, confusable_rows, output)

    # Sources
    if data.get("sources"):
        output.append("## 来源与合规说明\n")
        for source in data["sources"]:
            source_text = str(source)
            # Highlight compliance-related items
            if "合规" in source_text or "禁区" in source_text:
                output.append(f"⚠️ {source_text}\n")
            else:
                output.append(f"- {source_text}\n")
        output.append("")

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(output))

    # Statistics (support both old and new structure)
    sections = data.get("sections", []) or data.get("topics", [])
    n_entries = sum(len(s.get("entries", []) or s.get("entities", [])) for s in sections)
    n_topics = len(data.get("topic_sections", [])) or len(sections)
    n_qa = len(data.get("qa", []))
    # Also count Q&A at section/topic level
    for section in sections:
        n_qa += len(section.get("qa", []))

    print(f"SAVED: {output_path}")
    stats = (f"  实体总表 {len(data.get('summary_table', []))} 行 | "
             f"词条 {n_entries} 条 | "
             f"专题 {n_topics} 个 | "
             f"问答 {n_qa} 条")
    n_antipatterns = len(data.get("antipatterns", []) or [])
    if n_antipatterns:
        stats += f" | 反例卡 {n_antipatterns} 张"
    print(stats)

if __name__ == "__main__":
    main()
