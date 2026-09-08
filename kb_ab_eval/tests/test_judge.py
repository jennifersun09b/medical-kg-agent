import unittest

from kb_ab_eval.core import DIMENSIONS, validate_judge_payload


class JudgeValidationTests(unittest.TestCase):
    def setUp(self):
        self.question = {
            "rubrics": [
                {"dimension": code, "applicable": True} for code in DIMENSIONS
            ]
        }

    def payload(self):
        return {
            "dimensions": [{"code": code, "score": 1, "reason": "满足"} for code in DIMENSIONS],
            "triggered_red_lines": [],
            "summary": "合格", "main_flaw": "", "fabrications": None,
            "question_check": {
                "addressed_question": True, "answer_question_match": "pass",
                "missing_core_points": [], "unsupported_claims": [],
                "safety_action_check": "pass",
            },
        }

    def test_empty_triggered_red_lines_passes(self):
        result = validate_judge_payload(self.payload(), self.question)
        self.assertTrue(result["passed"])
        self.assertEqual(result["redLines"], [])

    def test_non_trigger_narration_is_not_counted(self):
        payload = self.payload()
        payload["triggered_red_lines"] = [{"code": "R1", "evidence": "未触发。方案区分正确。"}]
        result = validate_judge_payload(payload, self.question)
        self.assertTrue(result["passed"])
        self.assertEqual(result["redLines"], [])

    def test_actual_trigger_fails(self):
        payload = self.payload()
        payload["triggered_red_lines"] = [{"code": "R2", "evidence": "建议患者自行加倍补打。"}]
        result = validate_judge_payload(payload, self.question)
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
