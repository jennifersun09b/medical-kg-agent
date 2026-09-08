import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
ANALYZER = SKILL_DIR / "scripts" / "analyze_eval.py"


def _load_module():
    """Import analyze_eval.py directly so the pure helpers can be unit-tested."""
    spec = importlib.util.spec_from_file_location("analyze_eval", ANALYZER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze_eval = _load_module()


def scoring(dimensions, total, passed=False, red_lines=None,
            status="COMPLETED", main_flaw=None, fabrications=None):
    return {
        "status": status,
        "dimensions": {
            code: {"score": score, "reason": "维度 %s 的评语" % code}
            for code, score in dimensions.items()
        },
        "redLines": [
            {"code": code, "evidence": "红线 %s 的原话证据" % code}
            for code in (red_lines or [])
        ],
        "totalScore": total,
        "passed": passed,
        "mainFlaw": main_flaw,
        "fabrications": fabrications,
    }


def build_export():
    """A synthetic export whose every aggregate is hand-checkable.

    Shape: 3 models x 4 questions, with two responses deliberately left
    unscored so the hotspot quorum has something to chew on.

      Q1  all 3 models scored, every one fails D2   -> universal dimension
      Q2  only 2 of 3 scored, both fail D1 and R2   -> universal via quorum
      Q3  all 3 scored, only M1 fails D1            -> not universal
      Q4  only 1 of 3 scored, it fails D2           -> below quorum, not universal
    """
    return {
        "task": {"taskName": "合成评测任务"},
        "scoring": {
            "judgeModel": "合成裁判模型",
            "rubricTitle": "合成评分标准",
            "rubricVersion": "v0",
            "dimensions": [
                {"dimension": "D1", "name": "信息覆盖", "definition": "是否覆盖要点。"},
                {"dimension": "D2", "name": "回答准确性", "definition": "是否与来源一致。"},
            ],
            "redLines": [
                {"code": "R1", "name": "示例红线一", "trigger_condition": "触发条件一。"},
                {"code": "R2", "name": "示例红线二", "trigger_condition": "触发条件二。"},
            ],
        },
        "questions": [
            {
                "questionId": "Q1",
                "question": "全员在同一维度归零的题",
                "questionType": "题型甲",
                "cancerType": "组一",
                "modelResults": [
                    {"model": "M1", "scoring": scoring(
                        {"D1": 1, "D2": 0}, 30, red_lines=["R1"],
                        main_flaw="把时限说成了固定值。",
                        fabrications="「24 小时」在来源中不存在。")},
                    {"model": "M2", "scoring": scoring(
                        {"D1": 0.5, "D2": 0}, 32, red_lines=["R1"],
                        main_flaw="把时限说成了固定值。")},
                    {"model": "M3", "scoring": scoring(
                        {"D1": 1, "D2": 0}, 34,
                        main_flaw="遗漏了关键限定。")},
                ],
            },
            {
                "questionId": "Q2",
                "question": "只有两个模型被评分的题",
                "questionType": "题型甲",
                "cancerType": "组一",
                "modelResults": [
                    {"model": "M1", "scoring": scoring(
                        {"D1": 0, "D2": 1}, 40, red_lines=["R2"])},
                    {"model": "M2", "scoring": scoring(
                        {"D1": 0, "D2": 0.5}, 44, red_lines=["R2"])},
                    {"model": "M3", "scoring": scoring(
                        {"D1": 0, "D2": 0}, 10, status="FAILED")},
                ],
            },
            {
                "questionId": "Q3",
                "question": "只有单个模型出错的题",
                "questionType": "题型乙",
                "cancerType": "组二",
                "modelResults": [
                    {"model": "M1", "scoring": scoring({"D1": 0, "D2": 1}, 70)},
                    {"model": "M2", "scoring": scoring({"D1": 1, "D2": 1}, 80, passed=True)},
                    {"model": "M3", "scoring": scoring({"D1": 1, "D2": 1}, 90, passed=True)},
                ],
            },
            {
                "questionId": "Q4",
                "question": "只有一个模型作答的题",
                "questionType": "题型乙",
                "cancerType": "组二",
                "modelResults": [
                    {"model": "M1", "scoring": scoring({"D1": 1, "D2": 0}, 20)},
                ],
            },
        ],
    }


class AnalyzeEvalCliTests(unittest.TestCase):
    def run_analyzer(self, export, extra_args=(), field_map=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        source = root / "eval.json"
        output = root / "defect-profile.json"
        source.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

        args = [sys.executable, str(ANALYZER), str(source), "-o", str(output)]
        if field_map is not None:
            map_path = root / "field-map.json"
            map_path.write_text(json.dumps(field_map, ensure_ascii=False), encoding="utf-8")
            args += ["--field-map", str(map_path)]
        args += list(extra_args)

        result = subprocess.run(args, capture_output=True, text=True)
        profile = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
        return result, profile, root

    def test_aggregates_counts_scores_and_metadata(self):
        result, profile, _ = self.run_analyzer(build_export())

        self.assertEqual(result.returncode, 0, result.stderr)
        source = profile["source"]
        # 3 + 2 + 3 + 1 scored responses; the FAILED one is excluded.
        self.assertEqual(source["scoredResponses"], 9)
        self.assertEqual(source["questions"], 4)
        self.assertEqual(source["models"], ["M1", "M2", "M3"])
        self.assertEqual(source["taskName"], "合成评测任务")
        self.assertEqual(source["judgeModel"], "合成裁判模型")
        self.assertEqual(source["rubricVersion"], "v0")

        self.assertEqual(profile["dimension_stats"]["D1"], {"1": 5, "0.5": 1, "0": 3})
        self.assertEqual(profile["dimension_stats"]["D2"], {"1": 4, "0.5": 1, "0": 4})
        self.assertEqual(profile["redline_stats"], {"R1": 2, "R2": 2})

        by_model = {row["model"]: row for row in profile["model_stats"]}
        self.assertEqual(by_model["M1"]["scored"], 4)
        self.assertEqual(by_model["M1"]["averageScore"], 40.0)
        self.assertEqual(by_model["M2"]["averageScore"], 52.0)
        self.assertEqual(by_model["M3"]["scored"], 2)
        self.assertEqual(by_model["M3"]["passed"], 1)
        self.assertEqual(by_model["M3"]["passRate"], 50.0)

    def test_topic_and_group_risk_are_ranked_worst_first(self):
        _, profile, _ = self.run_analyzer(build_export())

        topics = profile["topic_risk"]
        self.assertEqual(topics[0]["topic"], "题型甲")
        self.assertEqual(topics[0]["averageScore"], 36.0)
        self.assertEqual(topics[0]["priority"], "P0")
        self.assertEqual(topics[0]["responses"], 5)
        self.assertEqual(topics[1]["topic"], "题型乙")
        self.assertEqual(topics[1]["averageScore"], 65.0)
        self.assertEqual(topics[1]["priority"], "P2")

        groups = {row["topic"]: row for row in profile["group_risk"]}
        self.assertEqual(groups["组一"]["averageScore"], 36.0)
        self.assertEqual(groups["组二"]["averageScore"], 65.0)

    def test_clusters_carry_rubric_definitions_and_fleet_universal_flag(self):
        _, profile, _ = self.run_analyzer(build_export())

        clusters = {c["id"]: c for c in profile["clusters"]}
        self.assertEqual(clusters["DEF-R1"]["name"], "示例红线一")
        self.assertEqual(clusters["DEF-R1"]["definition"], "触发条件一。")
        self.assertEqual(clusters["DEF-D1"]["name"], "信息覆盖")
        self.assertEqual(clusters["DEF-D2"]["hits"], 4)
        self.assertEqual(clusters["DEF-D2"]["partialHits"], 1)

        # Fleet-level `universal` is only about whether every model is implicated
        # at all — D2 fails somewhere for all three, D1 only for two.
        self.assertTrue(clusters["DEF-D2"]["universal"])
        self.assertFalse(clusters["DEF-D1"]["universal"])
        self.assertFalse(clusters["DEF-R1"]["universal"])

        self.assertTrue(clusters["DEF-R1"]["evidence_samples"])
        self.assertIn("R1", clusters["DEF-R1"]["evidence_samples"][0]["text"])

    def test_question_hotspots_fire_only_on_whole_fleet_failures(self):
        _, profile, _ = self.run_analyzer(build_export())

        hotspots = {h["questionId"]: h for h in profile["question_hotspots"]}

        # Q1: every scored model got D2 wrong -> universal. R1 hit only 2 of 3,
        # so it must NOT be reported as a universal red line.
        self.assertTrue(hotspots["Q1"]["universal"])
        self.assertEqual(hotspots["Q1"]["universalDimensions"], ["D2"])
        self.assertEqual(hotspots["Q1"]["universalRedlines"], [])

        # Q2: only 2 of 3 models scored, but that clears the coverage quorum,
        # so a genuine knowledge hole is not masked by the unscored response.
        self.assertEqual(hotspots["Q2"]["modelsAnswered"], 2)
        self.assertTrue(hotspots["Q2"]["universal"])
        self.assertEqual(hotspots["Q2"]["universalDimensions"], ["D1"])
        self.assertEqual(hotspots["Q2"]["universalRedlines"], ["R2"])

        # Q3: one model failing D1 is a model-capability problem, not a corpus hole.
        self.assertFalse(hotspots["Q3"]["universal"])
        self.assertEqual(hotspots["Q3"]["universalDimensions"], [])

        # Q4: a single scored response is below quorum — "all models failed" is
        # not a claim one model can support, however bad the score.
        self.assertEqual(hotspots["Q4"]["modelsAnswered"], 1)
        self.assertFalse(hotspots["Q4"]["universal"])
        self.assertEqual(hotspots["Q4"]["universalDimensions"], [])
        self.assertEqual(hotspots["Q4"]["dimensionFails"], {"D2": 1})

        self.assertEqual(profile["hotspot_summary"],
                         {"totalQuestions": 4, "universalFailures": 2, "carriedInProfile": 4})

        # Universal hotspots sort ahead of everything else, worst score first,
        # even though Q4 scored lower than both of them.
        self.assertEqual([h["questionId"] for h in profile["question_hotspots"]],
                         ["Q1", "Q2", "Q4", "Q3"])

    def test_extracts_flaw_and_fabrication_keywords(self):
        _, profile, _ = self.run_analyzer(build_export())

        flaw_keywords = {item["keyword"] for item in profile["flaw_keywords"]}
        self.assertIn("时限", flaw_keywords)
        fabrication_keywords = {item["keyword"] for item in profile["fabrication_targets"]}
        self.assertIn("来源", fabrication_keywords)
        self.assertTrue(profile["samples"]["mainFlaw"])
        self.assertTrue(profile["samples"]["fabrications"])

    def test_writes_human_readable_report_unless_suppressed(self):
        _, _, root = self.run_analyzer(build_export())
        report = root / "缺陷图谱.md"
        self.assertTrue(report.exists())
        text = report.read_text(encoding="utf-8")
        self.assertIn("# 缺陷图谱", text)
        self.assertIn("题目热点", text)
        self.assertIn("全员在同一维度归零的题", text)

        _, _, root2 = self.run_analyzer(build_export(), extra_args=["--no-report"])
        self.assertFalse((root2 / "缺陷图谱.md").exists())

    def test_field_map_retargets_a_foreign_export_shape(self):
        export = {
            "payload": {
                "items": [
                    {
                        "qid": "A1",
                        "prompt": "外部评测体系的题",
                        "category": "外部题型",
                        "domain": "外部分组",
                        "runs": [
                            {
                                "engine": "E1",
                                "judgement": {
                                    "state": "done",
                                    "axes": {"AX": {"value": 0, "note": "错在这里"}},
                                    "total": 20,
                                    "ok": False,
                                    "violations": [{"id": "V1", "quote": "越界的原话"}],
                                    "flaw": "外部体系的主要缺陷",
                                },
                            },
                            {
                                "engine": "E2",
                                "judgement": {
                                    "state": "done",
                                    "axes": {"AX": {"value": 0, "note": "也错在这里"}},
                                    "total": 24,
                                    "ok": False,
                                    "violations": [{"id": "V1", "quote": "另一句越界的原话"}],
                                },
                            },
                        ],
                    }
                ]
            }
        }
        field_map = {
            "questions": "payload.items[]",
            "question_id": "qid",
            "question_text": "prompt",
            "question_type": "category",
            "question_group": "domain",
            "model_results": "runs[]",
            "model_id": "engine",
            "scoring": "judgement",
            "scoring_status": "state",
            "scoring_ok_value": "done",
            "dimensions": "axes",
            "dimension_score": "value",
            "dimension_reason": "note",
            "total_score": "total",
            "passed": "ok",
            "red_lines": "violations",
            "red_line_code": "id",
            "red_line_evidence": "quote",
            "main_flaw": "flaw",
        }

        result, profile, _ = self.run_analyzer(export, field_map=field_map)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(profile["source"]["scoredResponses"], 2)
        self.assertEqual(profile["source"]["models"], ["E1", "E2"])
        self.assertEqual(profile["dimension_stats"]["AX"], {"0": 2})
        self.assertEqual(profile["redline_stats"], {"V1": 2})
        # Metadata paths that do not exist in this shape degrade to null rather
        # than crashing the run.
        self.assertIsNone(profile["source"]["taskName"])

        hotspot = profile["question_hotspots"][0]
        self.assertEqual(hotspot["questionId"], "A1")
        self.assertEqual(hotspot["universalDimensions"], ["AX"])
        self.assertEqual(hotspot["universalRedlines"], ["V1"])

    def test_rejects_field_map_with_unknown_keys(self):
        result, profile, _ = self.run_analyzer(
            build_export(), field_map={"questions": "questions[]", "bogus_key": "x"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(profile)
        self.assertIn("unknown keys", result.stderr)
        self.assertIn("bogus_key", result.stderr)

    def test_exits_2_when_nothing_was_scored(self):
        export = build_export()
        for question in export["questions"]:
            for run in question["modelResults"]:
                run["scoring"]["status"] = "PENDING"

        result, profile, _ = self.run_analyzer(export)

        self.assertEqual(result.returncode, 2)
        self.assertIsNone(profile)
        self.assertIn("no scored responses", result.stderr)

    def test_reports_a_usable_error_when_questions_path_is_wrong(self):
        result, _, _ = self.run_analyzer({"rows": []})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--field-map", result.stderr)


class PriorityBandingTests(unittest.TestCase):
    """The share denominator is the same for every cluster type; only the
    thresholds differ. Before this was fixed, red-line share was measured
    against total red lines and dimension share against total responses, which
    ranked a 一票否决 red line below a mere coverage dimension."""

    def test_universal_forces_p0_regardless_of_volume(self):
        self.assertEqual(analyze_eval._priority(True, 0.0, "dimension"), "P0")
        self.assertEqual(analyze_eval._priority(True, 0.0, "redline"), "P0")

    def test_red_lines_band_more_aggressively_than_dimensions(self):
        # Same share, different severity: a red line firing on 6% of responses
        # is P0, a dimension scoring 0 on 6% of them is only P1.
        self.assertEqual(analyze_eval._priority(False, 0.06, "redline"), "P0")
        self.assertEqual(analyze_eval._priority(False, 0.06, "dimension"), "P1")

    def test_bands_cover_the_full_range(self):
        self.assertEqual(analyze_eval._priority(False, 0.25, "dimension"), "P0")
        self.assertEqual(analyze_eval._priority(False, 0.02, "dimension"), "P2")
        self.assertEqual(analyze_eval._priority(False, 0.02, "redline"), "P1")
        self.assertEqual(analyze_eval._priority(False, 0.005, "redline"), "P2")


class PathResolutionTests(unittest.TestCase):
    def test_resolves_nested_paths_and_tolerates_missing_segments(self):
        data = {"a": {"b": {"c": [1, 2]}}}
        self.assertEqual(analyze_eval.resolve_path(data, "a.b.c[]"), [1, 2])
        self.assertEqual(analyze_eval.resolve_path(data, "a.b"), {"c": [1, 2]})
        self.assertIsNone(analyze_eval.resolve_path(data, "a.missing.c"))
        self.assertIsNone(analyze_eval.resolve_path(data, ""))


if __name__ == "__main__":
    unittest.main()
