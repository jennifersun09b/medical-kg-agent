import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_DIR / "scripts" / "validate_kb.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_kb", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_kb = _load_module()
KBValidator = validate_kb.KBValidator


def good_card(**overrides):
    card = {
        "id": "AP-01",
        "缺陷代码": "R1",
        "缺陷名称": "示例错误答法",
        "错误表述": "模型说错的原话。",
        "为什么错": "与权威来源的表述不符。",
        "正确说法": "有据可依的替代表述，足够长以便直接引用。",
        "来源": "[S] 示例规范",
    }
    card.update(overrides)
    return card


def entry(name, **fields):
    base = {"标准名": name, "实体类型": "过程", "一句话定义": "定义。", "来源": "[S] 示例规范"}
    base.update(fields)
    return base


def kb(**parts):
    data = {"title": "测试知识库"}
    data.update(parts)
    return data


def sections(*entries):
    return [{"title": "词条", "entries": list(entries)}]


class AntipatternCheckTests(unittest.TestCase):
    def check(self, data):
        validator = KBValidator(data)
        validator.check_antipatterns()
        return validator

    def test_accepts_a_complete_card(self):
        validator = self.check(kb(antipatterns=[good_card()]))
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.warnings, [])
        self.assertIn("反例卡: 1 张", validator.info)

    def test_absent_block_is_not_an_error(self):
        validator = self.check(kb())
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.info, [])

    def test_rejects_card_without_a_correct_version(self):
        card = good_card()
        del card["正确说法"]
        validator = self.check(kb(antipatterns=[card]))
        self.assertEqual(len(validator.errors), 1)
        self.assertIn("正确说法", validator.errors[0])
        self.assertIn("AP-01", validator.errors[0])

    def test_reports_every_missing_required_field_at_once(self):
        validator = self.check(kb(antipatterns=[{"id": "AP-BAD"}]))
        self.assertEqual(len(validator.errors), 1)
        for field in validate_kb.ANTIPATTERN_REQUIRED_FIELDS:
            self.assertIn(field, validator.errors[0])

    def test_treats_whitespace_as_missing(self):
        validator = self.check(kb(antipatterns=[good_card(来源="   ")]))
        self.assertEqual(len(validator.errors), 1)
        self.assertIn("来源", validator.errors[0])

    def test_warns_when_the_correct_version_is_only_a_negation(self):
        validator = self.check(kb(antipatterns=[good_card(正确说法="不是这样")]))
        self.assertEqual(validator.errors, [])
        self.assertEqual(len(validator.warnings), 1)
        self.assertIn("正确说法过短", validator.warnings[0])

    def test_rejects_non_object_card(self):
        validator = self.check(kb(antipatterns=["随手写的一行字"]))
        self.assertIn("不是对象", validator.errors[0])


class ConfusablePairCheckTests(unittest.TestCase):
    def check(self, data):
        validator = KBValidator(data)
        validator.check_confusable_pairs()
        return validator

    def clean_kb(self, **overrides):
        data = kb(
            sections=sections(
                entry("甲实体", 易混淆辨析="与乙实体的区别在于是否需要寄回。"),
                entry("乙实体", 易混淆辨析="与甲实体的区别在于是否需要寄回。"),
            ),
            appendix={
                "易混淆对": [{
                    "A": "甲实体", "B": "乙实体",
                    "区别维度": "是否需要寄回",
                    "混淆后果": "会把甲的规则套到乙上。",
                    "来源": "[S] 示例规范",
                }]
            },
        )
        data.update(overrides)
        return data

    def test_accepts_a_well_formed_pair(self):
        validator = self.check(self.clean_kb())
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.warnings, [])
        self.assertIn("易混淆对: 1 组", validator.info)

    def test_errors_when_a_pair_is_also_declared_synonymous(self):
        data = self.clean_kb()
        data["appendix"]["归一化规则"] = [
            {"出现的写法": "乙实体", "归一到": "甲实体", "关系类型": "同义(=)"}
        ]
        validator = self.check(data)
        self.assertEqual(len(validator.errors), 1)
        self.assertIn("禁止合并", validator.errors[0])
        self.assertIn("同义(=)", validator.errors[0])

    def test_allows_a_pair_normalized_under_a_non_synonym_relation(self):
        # is-a is not a merge claim, so it does not contradict do-not-merge.
        data = self.clean_kb()
        data["appendix"]["归一化规则"] = [
            {"出现的写法": "乙实体", "归一到": "甲实体", "关系类型": "上下位(is-a)"}
        ]
        validator = self.check(data)
        self.assertEqual(validator.errors, [])

    def test_errors_on_identical_or_incomplete_pairs(self):
        data = self.clean_kb()
        data["appendix"]["易混淆对"] = [
            {"A": "甲实体", "B": "甲实体", "区别维度": "x"},
            {"A": "甲实体", "区别维度": "x"},
        ]
        validator = self.check(data)
        self.assertEqual(len(validator.errors), 2)
        self.assertIn("A 与 B 相同", validator.errors[0])
        self.assertIn("缺少 A 或 B", validator.errors[1])

    def test_warns_when_the_distinguishing_axis_is_missing(self):
        data = self.clean_kb()
        del data["appendix"]["易混淆对"][0]["区别维度"]
        validator = self.check(data)
        self.assertEqual(validator.errors, [])
        self.assertTrue(any("区别维度" in w for w in validator.warnings))

    def test_warns_when_an_entry_lacks_the_inline_distinction(self):
        # The pair table lives in the appendix; a single retrieved card must
        # still carry the distinction or it is lost at retrieval time.
        data = self.clean_kb(sections=sections(entry("甲实体"), entry("乙实体")))
        validator = self.check(data)
        self.assertEqual(validator.errors, [])
        self.assertEqual(len(validator.warnings), 2)
        self.assertTrue(all("易混淆辨析" in w for w in validator.warnings))

    def test_warns_when_a_named_entity_has_no_entry(self):
        data = self.clean_kb(sections=sections(entry("甲实体", 易混淆辨析="与乙实体不同。")))
        validator = self.check(data)
        self.assertTrue(any("没有对应词条" in w and "乙实体" in w for w in validator.warnings))


class NumericClaimCheckTests(unittest.TestCase):
    def check(self, *entries):
        validator = KBValidator(kb(sections=sections(*entries)))
        validator.check_numeric_claims_have_evidence()
        return validator

    def test_flags_unsourced_numbers(self):
        validator = self.check(entry("甲实体", **{"关键数据/证据": "有效率约 43%。"}))
        self.assertEqual(len(validator.warnings), 1)
        self.assertIn("甲实体", validator.warnings[0])
        self.assertIn("43%", validator.warnings[0])

    def test_evidence_clears_the_flag(self):
        validator = self.check(entry(
            "甲实体",
            **{"关键数据/证据": "有效率约 43%。",
               "evidence": [{"claim": "有效率约 43%。", "source": "[T] 示例试验"}]},
        ))
        self.assertEqual(validator.warnings, [])

    def test_catches_the_shapes_models_actually_fabricate(self):
        cases = {
            "百分比": {"关键数据/证据": "缓解率 62%。"},
            "生存月数": {"关键数据/证据": "中位无进展生存 9.7 个月。"},
            "风险比": {"关键数据/证据": "HR=0.58。"},
            "P 值": {"关键数据/证据": "P<0.001。"},
            "试验号": {"关键数据/证据": "见 NCT01234567。"},
            "剂量": {"用法用量要点": "120 mg 皮下注射。"},
            "置信区间": {"关键数据/证据": "95% CI 0.4–0.8。"},
        }
        for label, fields in cases.items():
            with self.subTest(label):
                validator = self.check(entry("甲实体", **fields))
                self.assertEqual(len(validator.warnings), 1, f"{label} should be flagged")

    def test_ignores_numbers_in_citation_fields(self):
        # Citations legitimately carry years, volumes, and page numbers.
        validator = self.check(entry(
            "甲实体", **{"来源": "[G] 某指南 2024 年版 第 3 章", "合规备注": "2024 年生效。"}))
        self.assertEqual(validator.warnings, [])

    def test_ignores_prose_without_hard_numbers(self):
        validator = self.check(entry(
            "甲实体", **{"关键数据/证据": "多数患者可获益，具体比例见来源。"}))
        self.assertEqual(validator.warnings, [])


class DefectCoverageCheckTests(unittest.TestCase):
    def profile(self, clusters, topic_risk=None):
        return {
            "source": {"file": "eval.json", "scoredResponses": 100},
            "clusters": clusters,
            "topic_risk": topic_risk or [],
        }

    def check(self, data, defects=None):
        validator = KBValidator(data, defects)
        validator.check_defect_coverage()
        return validator

    def remediated_kb(self, **parts):
        data = kb(sections=sections(entry(
            "甲实体",
            **{"易混淆辨析": "与乙实体不同。",
               "边界与禁止表述": "不得超出来源表述。",
               "风险信号与行动": "出现信号 → 立即就医。",
               "可执行下一步": "先核对状态再提交。"},
        )))
        data.update(parts)
        return data

    def test_skips_and_says_so_without_a_profile(self):
        validator = self.check(kb())
        self.assertEqual(validator.errors, [])
        self.assertTrue(any("跳过缺陷覆盖检查" in n for n in validator.info))
        self.assertEqual(validator.coverage_rows, [])

    def test_errors_on_a_p0_red_line_with_no_card(self):
        defects = self.profile([
            {"code": "R1", "name": "剂量混淆", "type": "redline", "priority": "P0", "hits": 62}
        ])
        validator = self.check(self.remediated_kb(), defects)
        self.assertEqual(len(validator.errors), 1)
        self.assertIn("R1", validator.errors[0])
        self.assertIn("反例卡", validator.errors[0])
        self.assertEqual(validator.coverage_rows, [("R1", "剂量混淆", 62, "—", "❌")])

    def test_a_matching_card_closes_the_red_line(self):
        defects = self.profile([
            {"code": "R1", "name": "剂量混淆", "type": "redline", "priority": "P0", "hits": 62}
        ])
        validator = self.check(
            self.remediated_kb(antipatterns=[good_card(缺陷代码="R1")]), defects)
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.coverage_rows, [("R1", "剂量混淆", 62, "反例卡", "✅")])

    def test_p0_dimension_warns_rather_than_errors(self):
        # A dimension is a quality axis, not a quotable statement; demanding an
        # anti-pattern card for it would ask for the wrong artifact.
        defects = self.profile([
            {"code": "D2", "name": "回答准确性", "type": "dimension", "priority": "P0", "hits": 163}
        ])
        validator = self.check(self.remediated_kb(), defects)
        self.assertEqual(validator.errors, [])
        self.assertTrue(any("无法自动确认覆盖" in w for w in validator.warnings))
        self.assertEqual(validator.coverage_rows,
                         [("D2", "回答准确性", 163, "字段/证据（需人工确认）", "⚠️")])

    def test_non_p0_clusters_are_left_alone(self):
        defects = self.profile([
            {"code": "R3", "name": "次要红线", "type": "redline", "priority": "P1", "hits": 5}
        ])
        validator = self.check(self.remediated_kb(), defects)
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.coverage_rows, [])
        self.assertTrue(any("没有 P0 缺陷" in n for n in validator.info))

    def test_warns_when_no_entry_uses_any_remediation_field(self):
        defects = self.profile([
            {"code": "R1", "name": "剂量混淆", "type": "redline", "priority": "P0", "hits": 62}
        ])
        data = kb(sections=sections(entry("甲实体")),
                  antipatterns=[good_card(缺陷代码="R1")])
        validator = self.check(data, defects)
        unused = [w for w in validator.warnings if "缺陷弥补字段完全未使用" in w]
        self.assertEqual(len(unused), 1)
        for field in ("边界与禁止表述", "易混淆辨析", "风险信号与行动", "可执行下一步"):
            self.assertIn(field, unused[0])

    def test_warns_when_a_high_risk_topic_is_absent_from_the_kb(self):
        defects = self.profile(
            [{"code": "R1", "name": "剂量混淆", "type": "redline", "priority": "P0", "hits": 62}],
            topic_risk=[
                {"topic": "费用与可及性", "priority": "P0", "averageScore": 46.6},
                {"topic": "已覆盖题型", "priority": "P0", "averageScore": 50.0},
                {"topic": "低风险题型", "priority": "P2", "averageScore": 70.0},
            ],
        )
        data = self.remediated_kb(
            antipatterns=[good_card(缺陷代码="R1")],
            qa=[{"q": "已覆盖题型相关的问题？", "answers": []}],
        )
        validator = self.check(data, defects)
        topic_warnings = [w for w in validator.warnings if "找不到任何提及" in w]
        self.assertEqual(len(topic_warnings), 1)
        self.assertIn("费用与可及性", topic_warnings[0])
        self.assertNotIn("已覆盖题型", topic_warnings[0])
        self.assertNotIn("低风险题型", topic_warnings[0])

    def test_coverage_table_lands_in_the_report(self):
        defects = self.profile([
            {"code": "R1", "name": "剂量混淆", "type": "redline", "priority": "P0", "hits": 62},
            {"code": "D2", "name": "回答准确性", "type": "dimension", "priority": "P0", "hits": 163},
        ])
        validator = self.check(
            self.remediated_kb(antipatterns=[good_card(缺陷代码="R1")]), defects)
        report = validator.generate_report()
        self.assertIn("## 缺陷覆盖对照", report)
        self.assertIn("eval.json", report)
        self.assertIn("| R1 | 剂量混淆 | 62 | 反例卡 | ✅ |", report)
        self.assertIn("| D2 | 回答准确性 | 163 | 字段/证据（需人工确认） | ⚠️ |", report)


class ValidatorCliTests(unittest.TestCase):
    def run_validator(self, data, defects=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        source = root / "kb.json"
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        args = [sys.executable, str(VALIDATOR), str(source)]
        if defects is not None:
            profile = root / "defect-profile.json"
            profile.write_text(json.dumps(defects, ensure_ascii=False), encoding="utf-8")
            args += ["--defects", str(profile)]
        result = subprocess.run(args, capture_output=True, text=True)
        report = root / "质量报告.md"
        return result, report.read_text(encoding="utf-8") if report.exists() else ""

    def clean_kb(self):
        return kb(
            summary_table=[{"标准名": "甲实体"}, {"标准名": "乙实体"}],
            sections=sections(
                entry("甲实体", **{"关联实体与关系": "乙实体（适用于）"}),
                entry("乙实体", **{"实体类型": "对象", "关联实体与关系": "甲实体（可发起）"}),
            ),
            appendix={"关系动词": ["适用于", "可发起"]},
        )

    def test_exit_0_on_a_clean_kb(self):
        result, report = self.run_validator(self.clean_kb())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("通过所有检查", report)

    def test_exit_1_on_warnings_only(self):
        data = self.clean_kb()
        data["sections"][0]["entries"][0]["关键数据/证据"] = "有效率 43%。"
        result, report = self.run_validator(data)
        self.assertEqual(result.returncode, 1)
        self.assertIn("⚠️ 警告", report)
        self.assertNotIn("❌ 错误", report)

    def test_exit_2_on_errors(self):
        data = self.clean_kb()
        data["antipatterns"] = [{"id": "AP-BAD", "缺陷代码": "R1"}]
        result, report = self.run_validator(data)
        self.assertEqual(result.returncode, 2)
        self.assertIn("❌ 错误", report)
        self.assertIn("AP-BAD", report)

    def test_accepts_the_defects_flag_in_equals_form(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        source = root / "kb.json"
        source.write_text(json.dumps(self.clean_kb(), ensure_ascii=False), encoding="utf-8")
        profile = root / "defect-profile.json"
        profile.write_text(json.dumps({
            "source": {"file": "eval.json", "scoredResponses": 100},
            "clusters": [{"code": "R1", "name": "剂量混淆", "type": "redline",
                          "priority": "P0", "hits": 62}],
        }, ensure_ascii=False), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(source), f"--defects={profile}"],
            capture_output=True, text=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn("缺陷覆盖对照", result.stdout)
        self.assertIn("R1", result.stdout)

    def test_missing_defect_profile_is_a_usage_error(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source = Path(temp_dir.name) / "kb.json"
        source.write_text(json.dumps(self.clean_kb(), ensure_ascii=False), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(source), "--defects", "/nonexistent.json"],
            capture_output=True, text=True)

        self.assertEqual(result.returncode, 3)
        self.assertIn("defect profile not found", result.stderr)

    def test_unknown_flags_do_not_swallow_the_input_path(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source = Path(temp_dir.name) / "kb.json"
        source.write_text(json.dumps(self.clean_kb(), ensure_ascii=False), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--fix-auto", str(source)],
            capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ShippedExampleTests(unittest.TestCase):
    """The shipped example must survive its own validator — it is what users copy."""

    def test_example_kb_passes_validation(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        source = Path(temp_dir.name) / "example-kb.json"
        source.write_text(
            (SKILL_DIR / "examples" / "example-kb.json").read_text(encoding="utf-8"),
            encoding="utf-8")

        result = subprocess.run([sys.executable, str(VALIDATOR), str(source)],
                                capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_example_kb_covers_the_example_defect_profile(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        source = root / "example-kb.json"
        source.write_text(
            (SKILL_DIR / "examples" / "example-kb.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        profile = SKILL_DIR / "examples" / "example-defect-profile.json"

        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(source), "--defects", str(profile)],
            capture_output=True, text=True)

        # No P0 red line may be left uncovered; the P0 dimension is expected to
        # warn, since no automated check can confirm a quality axis was fixed.
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("❌ 错误", result.stdout)
        self.assertIn("| X1 |", result.stdout)
        self.assertIn("| X2 |", result.stdout)
        self.assertIn("✅", result.stdout)


if __name__ == "__main__":
    unittest.main()
