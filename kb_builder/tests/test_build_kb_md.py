import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
BUILDER = SKILL_DIR / "scripts" / "build_kb_md.py"


class BuildKnowledgeBaseMarkdownTests(unittest.TestCase):
    def run_builder(self, payload):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        source = root / "input.json"
        output = root / "output.md"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(BUILDER), str(source), str(output)],
            capture_output=True,
            text=True,
        )
        text = output.read_text(encoding="utf-8") if output.exists() else ""
        return result, text

    def test_defaults_to_minimal_document_without_front_matter_or_audit_tables(self):
        payload = {
            "title": "简洁知识库",
            "subtitle": "不应默认显示的副标题",
            "meta": "不应默认显示的版本信息",
            "note": "不应默认显示的编写说明",
            "coverage_map": [
                {"一级主题": "内部覆盖审计", "二级内容定义（模版）": "x", "对应知识库实体": "y"}
            ],
            "keyword_map": [
                {"关键词维度": "内部关键词审计", "代表关键词": "关键词", "对应知识库实体": "实体"}
            ],
            "summary_columns": ["标准名", "实体类型"],
            "summary_table": [{"标准名": "实体", "实体类型": "概念"}],
        }

        result, text = self.run_builder(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(text.startswith("# 简洁知识库"))
        self.assertNotIn("不应默认显示的副标题", text)
        self.assertNotIn("不应默认显示的版本信息", text)
        self.assertNotIn("不应默认显示的编写说明", text)
        self.assertNotIn("内部覆盖审计", text)
        self.assertNotIn("内部关键词审计", text)

    def test_renders_entries_topics_evidence_and_audience_answers(self):
        payload = {
            "title": "通用知识库示例",
            "summary_columns": ["标准名", "实体类型", "一句话定义"],
            "summary_table": [
                {"标准名": "示例实体", "实体类型": "概念", "一句话定义": "用于测试结构。"}
            ],
            "sections": [
                {
                    "title": "核心词条",
                    "entries": [
                        {
                            "标准名": "示例实体",
                            "实体类型": "概念",
                            "一句话定义": "用于测试结构。",
                            "关联实体与关系": "另一实体（适用于）",
                            "来源": "[S] 示例规范",
                            "evidence": [
                                {
                                    "claim": "证据条目应当渲染。",
                                    "source": "[S] 示例规范",
                                    "location": "第一章",
                                    "scope": "仅演示",
                                }
                            ],
                        }
                    ],
                }
            ],
            "topic_sections": [
                {
                    "title": "专题表",
                    "columns": ["阶段", "动作"],
                    "rows": [{"阶段": "开始", "动作": "核对"}],
                }
            ],
            "qa": [
                {
                    "q": "这个问题会被渲染吗？",
                    "answers": [
                        {"audience": "专业答案", "text": "会。"},
                        {"audience": "大众答案", "text": "会的。"},
                    ],
                    "boundary": "仅演示边界。",
                }
            ],
        }

        result, text = self.run_builder(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("### 示例实体", text)
        self.assertIn("证据条目应当渲染。", text)
        self.assertIn("## 专题表", text)
        self.assertIn("专业答案", text)
        self.assertIn("大众答案", text)
        self.assertIn("仅演示边界。", text)

    def test_defect_remediation_fields_render_in_schema_order(self):
        payload = {
            "title": "缺陷弥补字段",
            "sections": [
                {
                    "title": "词条",
                    "entries": [
                        {
                            "标准名": "示例实体",
                            "实体类型": "过程",
                            "别名/同义词": "别名甲",
                            "易混淆辨析": "不要与相似实体混为一谈。",
                            "一句话定义": "用于测试字段顺序。",
                            "关键数据/证据": "示例数据。",
                            "边界与禁止表述": "不得超出来源表述。",
                            "关键安全性": "示例安全性。",
                            "风险信号与行动": "出现信号 → 采取行动。",
                            "关联实体与关系": "另一实体（适用于）",
                            "可执行下一步": "先核对状态，再提交申请。",
                            "常见问题": "示例问题。",
                            "来源": "[S] 示例规范",
                        }
                    ],
                }
            ],
        }

        result, text = self.run_builder(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        for field in ("易混淆辨析", "边界与禁止表述", "风险信号与行动", "可执行下一步"):
            self.assertIn(field, text, f"{field} should render")

        # Position matters: each remediation field must sit beside the field it
        # qualifies, not get appended as an unknown field at the end.
        # (This generator hoists 实体类型/一句话定义 to the top as 类型/定义, so
        # those two are not part of the ordered run.)
        self.assertLess(text.index("别名/同义词"), text.index("易混淆辨析"))
        self.assertLess(text.index("易混淆辨析"), text.index("关键数据/证据"))
        self.assertLess(text.index("关键数据/证据"), text.index("边界与禁止表述"))
        self.assertLess(text.index("边界与禁止表述"), text.index("关键安全性"))
        self.assertLess(text.index("关键安全性"), text.index("风险信号与行动"))
        self.assertLess(text.index("风险信号与行动"), text.index("关联实体与关系"))
        self.assertLess(text.index("关联实体与关系"), text.index("可执行下一步"))
        self.assertLess(text.index("可执行下一步"), text.index("常见问题"))

    def test_renders_antipattern_cards_and_confusable_pairs(self):
        payload = {
            "title": "反例卡与易混淆对",
            "antipatterns": [
                {
                    "id": "AP-01",
                    "缺陷代码": "X1",
                    "缺陷名称": "示例错误答法",
                    "命中模型": ["模型甲", "模型乙"],
                    "命中次数": 7,
                    "universal": True,
                    "错误表述": "这是模型说错的原话。",
                    "为什么错": "与来源不符。",
                    "正确说法": "这是有据可依的正确表述。",
                    "关联实体": ["示例实体"],
                    "来源": "[S] 示例规范",
                }
            ],
            "appendix": {
                "易混淆对": [
                    {
                        "A": "实体甲",
                        "B": "实体乙",
                        "区别维度": "适用范围",
                        "混淆后果": "会把甲的规则套到乙上。",
                        "来源": "[S] 示例规范",
                    }
                ]
            },
        }

        result, text = self.run_builder(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("常见错误答法与正确说法", text)
        self.assertIn("这是模型说错的原话。", text)
        self.assertIn("与来源不符。", text)
        self.assertIn("这是有据可依的正确表述。", text)
        self.assertIn("命中 7 次", text)
        self.assertIn("全员命中", text)
        self.assertIn("易混淆对（禁止合并）", text)
        self.assertIn("会把甲的规则套到乙上。", text)

    def test_rejects_antipattern_missing_required_field(self):
        # The Markdown path is the default generator; it must not silently
        # accept data the Word path rejects.
        payload = {
            "title": "缺字段的反例卡",
            "antipatterns": [
                {
                    "id": "AP-BAD",
                    "缺陷代码": "X1",
                    "错误表述": "说错的话。",
                    "为什么错": "与来源不符。",
                    "来源": "[S] 示例规范",
                }
            ],
        }

        result, _ = self.run_builder(payload)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正确说法", result.stderr)

    def test_rejects_missing_title(self):
        result, _ = self.run_builder({"sections": []})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("title is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
