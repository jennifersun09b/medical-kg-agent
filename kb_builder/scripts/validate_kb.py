#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_kb.py — Data quality checks for KB JSON files.

Runs automated checks to catch common quality issues before Word generation:
- Orphan entities (no relationships)
- Missing sources
- Normalization conflicts (circular references, contradictions)
- Relationship verb consistency
- Empty required fields
- Broken cross-references
- Anti-pattern card completeness
- Do-not-merge pairs contradicting normalization rules
- Numeric claims with no traceable evidence
- Coverage of measured model defects (with --defects)

Usage:
    python3 validate_kb.py input.json [--defects defect-profile.json]

Exit codes:
    0 = all checks passed
    1 = warnings found (non-fatal)
    2 = errors found (should be fixed before delivery)

Output: 质量报告.md with findings grouped by severity.
"""
import sys
import json
import os
import re
from collections import defaultdict
from pathlib import Path

# Anti-pattern cards must carry all of these; see references/entry-schema.md.
ANTIPATTERN_REQUIRED_FIELDS = ["缺陷代码", "错误表述", "为什么错", "正确说法", "来源"]

# Fields scanned for bare numbers that ought to be traceable. Deliberately
# excludes 来源/合规备注 (citations legitimately contain years and numbers) and
# 用法用量要点 is included precisely because dose confusion is a known failure.
NUMERIC_SCAN_FIELDS = [
    "关键数据/证据", "用法用量要点", "关键安全性", "适应症/适用人群",
    "作用机制/关键内容", "病理机制", "发生机制", "一句话定义",
    "边界与禁止表述", "风险信号与行动", "可执行下一步",
]

# Patterns that mark a claim as the kind models fabricate: percentages, survival
# months, hazard ratios, p-values, trial registrations, dose strengths.
NUMERIC_CLAIM_PATTERNS = [
    r"\d+(?:\.\d+)?\s*%",
    r"\d+(?:\.\d+)?\s*(?:个月|月|年|周|天)",
    r"(?:HR|OR|RR)\s*[=＝:：]?\s*\d",
    r"[Pp]\s*[<>=＜＞]\s*0?\.\d+",
    r"(?:NCT|ChiCTR)\d+",
    r"\d+(?:\.\d+)?\s*(?:mg|g|ml|mL|mmol|IU|μg|ug)\b",
    r"95%\s*CI",
]

def die(msg):
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(3)

class KBValidator:
    def __init__(self, data, defects=None):
        self.data = data
        self.defects = defects or {}
        self.errors = []
        self.warnings = []
        self.info = []
        self.coverage_rows = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.info.append(msg)

    def get_all_entities(self):
        """Extract all entity names from the KB."""
        entities = set()

        # From summary table
        for row in self.data.get("summary_table", []):
            name = row.get("标准名", row.get("name", ""))
            if name:
                entities.add(name)

        # From sections
        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                name = entry.get("标准名", entry.get("name", ""))
                if name:
                    entities.add(name)

        return entities

    def check_orphan_entities(self):
        """Find entities with no relationships."""
        entities = self.get_all_entities()
        entities_with_relations = set()

        # Collect entities mentioned in relationships
        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                relations_field = entry.get("关联实体与关系", "")
                if relations_field:
                    # Parse entity names from relationship field
                    # Format expected: "实体1（关系）、实体2（关系）"
                    import re
                    mentioned = re.findall(r'([^（）、]+)(?:（[^）]+）)?', relations_field)
                    for entity_name in mentioned:
                        entity_name = entity_name.strip()
                        if entity_name:
                            entities_with_relations.add(entity_name)

        orphans = entities - entities_with_relations

        # Filter out entities that might be intentionally standalone
        # (e.g., top-level concepts, appendix-only terms)
        significant_orphans = []
        for orphan in orphans:
            # Check if it has substantial content (not just a definition)
            for section in self.data.get("sections", []):
                for entry in section.get("entries", []):
                    if entry.get("标准名") == orphan or entry.get("name") == orphan:
                        # Has mechanism or key data = should have relations
                        if entry.get("作用机制/关键内容") or entry.get("病理机制") or entry.get("关键数据/证据"):
                            significant_orphans.append(orphan)
                        break

        if significant_orphans:
            self.warn(f"孤立实体（有实质内容但无关系边）: {', '.join(sorted(significant_orphans)[:10])}")
            if len(significant_orphans) > 10:
                self.warn(f"  ...还有 {len(significant_orphans) - 10} 个孤立实体")

    def check_missing_sources(self):
        """Find entries with missing source citations."""
        missing_sources = []

        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                name = entry.get("标准名", entry.get("name", "未命名"))
                source = entry.get("来源", "")

                if not source or source.strip() == "":
                    missing_sources.append(name)

        if missing_sources:
            self.error(f"缺失来源字段的条目（{len(missing_sources)}个）: {', '.join(missing_sources[:10])}")
            if len(missing_sources) > 10:
                self.error(f"  ...还有 {len(missing_sources) - 10} 个条目缺失来源")

    def check_normalization_conflicts(self):
        """Detect circular references and contradictory normalization rules."""
        norm_rules = self.data.get("appendix", {}).get("归一化规则", [])

        if not norm_rules:
            return

        # Build mapping
        norm_map = {}  # variant -> (canonical, relation_type)
        for rule in norm_rules:
            variant = rule.get("出现的写法", "")
            canonical = rule.get("归一到", "")
            rel_type = rule.get("关系类型", "")

            if variant and canonical:
                if variant in norm_map:
                    prev_canonical, prev_type = norm_map[variant]
                    if prev_canonical != canonical:
                        self.error(f"归一化冲突: \"{variant}\" 同时归一到 \"{prev_canonical}\" 和 \"{canonical}\"")
                norm_map[variant] = (canonical, rel_type)

        # Check for circular references
        for variant, (canonical, rel_type) in norm_map.items():
            if canonical in norm_map:
                next_canonical, next_type = norm_map[canonical]
                if next_canonical == variant:
                    self.error(f"归一化循环引用: \"{variant}\" ↔ \"{canonical}\"")
                elif next_canonical in norm_map:
                    # Check for longer chains (A->B->C->A)
                    visited = {variant, canonical}
                    current = next_canonical
                    while current in norm_map:
                        if current in visited:
                            self.error(f"归一化循环链: {' -> '.join(visited)} -> {current}")
                            break
                        visited.add(current)
                        current, _ = norm_map[current]

        # Check for over-merging (synonym vs is-a confusion)
        synonym_pairs = [(v, c) for v, (c, t) in norm_map.items() if t == "同义(=)"]
        for variant, canonical in synonym_pairs:
            # Warning if one is clearly a subtype (contains the other + modifier)
            if canonical in variant and variant != canonical:
                if len(variant) > len(canonical) + 3:  # Has substantial addition
                    self.warn(f"可能的过度归一: \"{variant}\" 标记为同义(=)，但看起来是 \"{canonical}\" 的细化（建议改用 is-a）")

    def check_relationship_verb_consistency(self):
        """Find relationships using non-standard verbs."""
        declared_verbs = set(self.data.get("appendix", {}).get("关系动词", []))

        if not declared_verbs:
            self.note("附录中未声明关系动词表，跳过一致性检查")
            return

        used_verbs = set()
        vague_relations = []

        import re
        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                name = entry.get("标准名", entry.get("name", "未命名"))
                relations_field = entry.get("关联实体与关系", "")

                if relations_field:
                    # Extract verbs from (verb) patterns
                    verbs_in_text = re.findall(r'（([^）]+)）', relations_field)
                    for verb in verbs_in_text:
                        verb = verb.strip()
                        used_verbs.add(verb)

                        if verb in ["相关", "有关", "关联", ""]:
                            vague_relations.append(f"{name}: \"{verb}\"")

        undeclared_verbs = used_verbs - declared_verbs
        if undeclared_verbs:
            self.warn(f"使用了未在附录中声明的关系动词: {', '.join(sorted(undeclared_verbs))}")

        if vague_relations:
            self.error(f"发现模糊关系动词（{len(vague_relations)}处）: {'; '.join(vague_relations[:5])}")
            if len(vague_relations) > 5:
                self.error(f"  ...还有 {len(vague_relations) - 5} 处模糊关系")

    def check_empty_required_fields(self):
        """Check for entries with missing critical fields."""
        for section_idx, section in enumerate(self.data.get("sections", []), 1):
            for entry_idx, entry in enumerate(section.get("entries", []), 1):
                name = entry.get("标准名", entry.get("name", ""))

                if not name:
                    self.error(f"Section {section_idx}, Entry {entry_idx}: 缺失标准名")

                entity_type = entry.get("实体类型", "")
                if not entity_type:
                    self.warn(f"{name}: 缺失实体类型")

                definition = entry.get("一句话定义", "")
                if not definition:
                    self.warn(f"{name}: 缺失一句话定义")

    def check_structured_evidence(self):
        """Validate structured evidence items."""
        for section_idx, section in enumerate(self.data.get("sections", []), 1):
            for entry_idx, entry in enumerate(section.get("entries", []), 1):
                name = entry.get("标准名", entry.get("name", "未命名"))
                evidence_list = entry.get("evidence", [])

                if evidence_list:
                    for ev_idx, ev in enumerate(evidence_list, 1):
                        if not isinstance(ev, dict):
                            self.error(f"{name}: evidence[{ev_idx}] 不是对象")
                            continue

                        if not ev.get("claim"):
                            self.error(f"{name}: evidence[{ev_idx}] 缺失 claim")
                        if not ev.get("source"):
                            self.error(f"{name}: evidence[{ev_idx}] 缺失 source")

        # Check QA evidence
        for qa_idx, qa_item in enumerate(self.data.get("qa", []), 1):
            evidence_list = qa_item.get("evidence", [])
            if evidence_list:
                for ev_idx, ev in enumerate(evidence_list, 1):
                    if not isinstance(ev, dict):
                        self.error(f"QA[{qa_idx}]: evidence[{ev_idx}] 不是对象")
                        continue
                    if not ev.get("claim"):
                        self.error(f"QA[{qa_idx}]: evidence[{ev_idx}] 缺失 claim")
                    if not ev.get("source"):
                        self.error(f"QA[{qa_idx}]: evidence[{ev_idx}] 缺失 source")

    def check_antipatterns(self):
        """Anti-pattern cards must name the wrong answer AND the right one."""
        cards = self.data.get("antipatterns", []) or []
        if not cards:
            return

        for idx, card in enumerate(cards, 1):
            if not isinstance(card, dict):
                self.error(f"antipatterns[{idx}] 不是对象")
                continue
            label = card.get("id") or card.get("缺陷名称") or f"antipatterns[{idx}]"
            missing = [f for f in ANTIPATTERN_REQUIRED_FIELDS
                       if not str(card.get(f, "")).strip()]
            if missing:
                self.error(f"反例卡 {label} 缺少必填字段: {', '.join(missing)}")

            # A "correct version" that just negates the wrong one tells the
            # reader what not to say without telling them what to say.
            correct = str(card.get("正确说法", "")).strip()
            if correct and len(correct) < 10:
                self.warn(f"反例卡 {label}: 正确说法过短（{len(correct)} 字），"
                          f"可能只是否定而没有给出可用的替代表述")

        self.note(f"反例卡: {len(cards)} 张")

    def check_confusable_pairs(self):
        """Do-not-merge pairs must not contradict the normalization table."""
        appendix = self.data.get("appendix", {}) or {}
        pairs = appendix.get("易混淆对", []) or []
        if not pairs:
            return

        # Build the set of synonym merges declared elsewhere.
        synonym_merges = set()
        for rule in appendix.get("归一化规则", []) or []:
            if rule.get("关系类型") == "同义(=)":
                variant = str(rule.get("出现的写法", "")).strip()
                canonical = str(rule.get("归一到", "")).strip()
                if variant and canonical:
                    synonym_merges.add(frozenset((variant, canonical)))

        entries_by_name = {}
        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                name = entry.get("标准名") or entry.get("name")
                if name:
                    entries_by_name[name] = entry

        for idx, pair in enumerate(pairs, 1):
            if not isinstance(pair, dict):
                self.error(f"易混淆对[{idx}] 不是对象")
                continue
            a = str(pair.get("A", "")).strip()
            b = str(pair.get("B", "")).strip()
            if not a or not b:
                self.error(f"易混淆对[{idx}] 缺少 A 或 B")
                continue
            if a == b:
                self.error(f"易混淆对[{idx}]: A 与 B 相同（\"{a}\"）")
                continue

            # The contradiction that matters: asserting "never merge these"
            # while the normalization table says "these are the same thing".
            if frozenset((a, b)) in synonym_merges:
                self.error(f"易混淆对与归一化规则冲突: \"{a}\" 与 \"{b}\" "
                           f"既被标为「禁止合并」又被归一化规则标为「同义(=)」")

            if not str(pair.get("区别维度", "")).strip():
                self.warn(f"易混淆对 \"{a}\" / \"{b}\": 缺少「区别维度」，"
                          f"读者无从判断二者差在哪里")

            # The distinction has to survive retrieval of a single card.
            for name in (a, b):
                entry = entries_by_name.get(name)
                if entry is None:
                    self.warn(f"易混淆对中的 \"{name}\" 在 KB 中没有对应词条")
                elif not str(entry.get("易混淆辨析", "")).strip():
                    self.warn(f"\"{name}\" 属于易混淆对但词条缺少「易混淆辨析」字段"
                              f"（单卡被检索时区分信息会丢失）")

        self.note(f"易混淆对: {len(pairs)} 组")

    def check_numeric_claims_have_evidence(self):
        """Flag entries carrying hard numbers but no claim-level evidence.

        Fabricated figures are the single largest measured failure mode; an
        entry full of percentages and survival months with nothing in `evidence`
        is precisely the shape that produces them.
        """
        patterns = [re.compile(p) for p in NUMERIC_CLAIM_PATTERNS]
        unsourced = []

        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                if entry.get("evidence"):
                    continue
                name = entry.get("标准名") or entry.get("name") or "未命名"
                hits = []
                for field in NUMERIC_SCAN_FIELDS:
                    value = entry.get(field)
                    if not value:
                        continue
                    text = str(value)
                    for pattern in patterns:
                        found = pattern.search(text)
                        if found:
                            hits.append(f"{field}「{found.group(0)}」")
                            break
                if hits:
                    unsourced.append(f"{name}（{'、'.join(hits[:3])}）")

        if unsourced:
            self.warn(f"含具体数字但无 claim 级 evidence 的词条（{len(unsourced)} 个）: "
                      f"{'; '.join(unsourced[:8])}")
            if len(unsourced) > 8:
                self.warn(f"  ...还有 {len(unsourced) - 8} 个词条")

    def check_defect_coverage(self):
        """Check the KB against a measured defect profile from analyze_eval.py.

        Every P0 defect must be addressed somewhere the reader will actually
        encounter it: an entity field, an anti-pattern card, or a Q&A. A KB that
        reproduces the sources faithfully but leaves the measured failure modes
        untouched has not done the job it was built for.
        """
        if not self.defects:
            self.note("未提供 --defects 缺陷画像，跳过缺陷覆盖检查")
            return

        clusters = self.defects.get("clusters", []) or []
        p0_clusters = [c for c in clusters if c.get("priority") == "P0"]
        if not p0_clusters:
            self.note("缺陷画像中没有 P0 缺陷")
            return

        covered_codes = set()
        for card in self.data.get("antipatterns", []) or []:
            if isinstance(card, dict) and card.get("缺陷代码"):
                covered_codes.add(str(card["缺陷代码"]).strip())

        # A field-level remedy counts too: these fields exist to answer specific
        # defect classes, so their presence is evidence the class was considered.
        field_remedies = {
            "边界与禁止表述": 0, "易混淆辨析": 0,
            "风险信号与行动": 0, "可执行下一步": 0,
        }
        entry_count = 0
        for section in self.data.get("sections", []):
            for entry in section.get("entries", []):
                entry_count += 1
                for field in field_remedies:
                    if str(entry.get(field, "")).strip():
                        field_remedies[field] += 1

        uncovered_redlines = []
        unconfirmed_dimensions = []
        for cluster in p0_clusters:
            code = str(cluster.get("code", "")).strip()
            name = cluster.get("name") or code
            hits = cluster.get("hits")
            is_redline = cluster.get("type") == "redline"

            if code in covered_codes:
                self.coverage_rows.append((code, name, hits, "反例卡", "✅"))
            elif is_redline:
                # A red line is a specific, quotable wrong statement. An
                # anti-pattern card is exactly the right shape for it, and its
                # absence is a real gap.
                self.coverage_rows.append((code, name, hits, "—", "❌"))
                uncovered_redlines.append(f"{code}（{name}，{hits} 次）")
            else:
                # A dimension is a quality axis, not a statement — there is no
                # "correct version of D2" to write on a card. It is remedied by
                # fields and evidence spread across the KB, which no automated
                # check can confirm. Flag it for human confirmation rather than
                # demanding a card that would be the wrong artifact.
                self.coverage_rows.append((code, name, hits, "字段/证据（需人工确认）", "⚠️"))
                unconfirmed_dimensions.append(f"{code}（{name}）")

        if uncovered_redlines:
            self.error(f"P0 红线缺陷在 KB 中无对应反例卡（{len(uncovered_redlines)} 项）: "
                       f"{'; '.join(uncovered_redlines[:6])}")
            if len(uncovered_redlines) > 6:
                self.error(f"  ...还有 {len(uncovered_redlines) - 6} 项未覆盖")

        if unconfirmed_dimensions:
            self.warn(f"P0 维度缺陷无法自动确认覆盖（{len(unconfirmed_dimensions)} 项）: "
                      f"{'; '.join(unconfirmed_dimensions[:6])}"
                      f"——请人工确认对应的弥补字段与 evidence 已到位")

        if entry_count:
            unused = [f for f, n in field_remedies.items() if n == 0]
            if unused:
                self.warn(f"缺陷弥补字段完全未使用: {', '.join(unused)}"
                          f"（共 {entry_count} 个词条）")

        # Topic-level risk: the worst-scoring topics should be visibly present.
        p0_topics = [t.get("topic") for t in self.defects.get("topic_risk", [])
                     if t.get("priority") == "P0" and t.get("topic")]
        if p0_topics:
            blob = json.dumps(self.data, ensure_ascii=False)
            missing_topics = [t for t in p0_topics if t not in blob]
            if missing_topics:
                self.warn(f"高风险题型在 KB 中找不到任何提及: "
                          f"{', '.join(missing_topics[:8])}")

        redline_p0 = sum(1 for c in p0_clusters if c.get("type") == "redline")
        self.note(f"缺陷覆盖: P0 缺陷 {len(p0_clusters)} 项"
                  f"（红线 {redline_p0} 项，已由反例卡覆盖 "
                  f"{redline_p0 - len(uncovered_redlines)} 项）")

    def run_all_checks(self):
        """Run all validation checks."""
        self.note(f"验证知识库: {self.data.get('title', '(无标题)')}")

        entity_count = len(self.get_all_entities())
        self.note(f"实体总数: {entity_count}")

        self.check_empty_required_fields()
        self.check_missing_sources()
        self.check_orphan_entities()
        self.check_normalization_conflicts()
        self.check_relationship_verb_consistency()
        self.check_structured_evidence()
        self.check_antipatterns()
        self.check_confusable_pairs()
        self.check_numeric_claims_have_evidence()
        self.check_defect_coverage()

    def generate_report(self):
        """Generate markdown quality report."""
        lines = ["# 知识库质量检查报告\n"]

        lines.append(f"**检查对象**: {self.data.get('title', '(无标题)')}\n")
        lines.append(f"**实体数**: {len(self.get_all_entities())}\n")
        lines.append(f"**检查结果**: {len(self.errors)} 错误, {len(self.warnings)} 警告, {len(self.info)} 信息\n")

        if self.coverage_rows:
            source = self.defects.get("source", {})
            lines.append("\n## 缺陷覆盖对照\n")
            lines.append(f"缺陷画像来源: `{source.get('file', '(未知)')}`"
                         f"（{source.get('scoredResponses', '?')} 条已评分回答）\n\n")
            lines.append("| 缺陷代码 | 名称 | 实测次数 | KB 中的对应 | 状态 |\n")
            lines.append("|---|---|---|---|---|\n")
            for code, name, hits, remedy, status in self.coverage_rows:
                lines.append(f"| {code} | {name} | {hits} | {remedy} | {status} |\n")

        if self.errors:
            lines.append("\n## ❌ 错误（必须修复）\n")
            for err in self.errors:
                lines.append(f"- {err}\n")

        if self.warnings:
            lines.append("\n## ⚠️ 警告（建议修复）\n")
            for warn in self.warnings:
                lines.append(f"- {warn}\n")

        if self.info:
            lines.append("\n## ℹ️ 信息\n")
            for note in self.info:
                lines.append(f"- {note}\n")

        if not self.errors and not self.warnings:
            lines.append("\n## ✅ 通过所有检查\n")
            lines.append("知识库质量良好，可以生成 Word 文档。\n")

        return "".join(lines)

def main():
    args = sys.argv[1:]
    defects_path = None
    positional = []
    idx = 0
    while idx < len(args):
        if args[idx] == "--defects":
            if idx + 1 >= len(args):
                die("--defects requires a path to a defect-profile.json")
            defects_path = args[idx + 1]
            idx += 2
        elif args[idx].startswith("--defects="):
            defects_path = args[idx].split("=", 1)[1]
            idx += 1
        elif args[idx].startswith("--"):
            idx += 1  # tolerate other flags (e.g. --fix-auto)
        else:
            positional.append(args[idx])
            idx += 1

    if not positional:
        die("usage: python3 validate_kb.py input.json [--defects defect-profile.json]")

    input_path = positional[0]
    if not os.path.exists(input_path):
        die(f"input not found: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    defects = None
    if defects_path:
        if not os.path.exists(defects_path):
            die(f"defect profile not found: {defects_path}")
        with open(defects_path, 'r', encoding='utf-8') as f:
            defects = json.load(f)

    validator = KBValidator(data, defects)
    validator.run_all_checks()

    # Generate report
    report = validator.generate_report()

    # Save to file
    report_path = Path(input_path).parent / "质量报告.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # Also print to console
    print(report)
    print(f"\n报告已保存到: {report_path}")

    # Exit code based on severity
    if validator.errors:
        sys.exit(2)
    elif validator.warnings:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
