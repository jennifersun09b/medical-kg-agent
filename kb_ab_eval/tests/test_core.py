import unittest

from kb_ab_eval.core import DEFAULT_BENCHMARK, DEFAULT_KB, DIMENSIONS, KBRetriever, load_benchmark, validate_inputs


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.benchmark = load_benchmark(DEFAULT_BENCHMARK)
        cls.retriever = KBRetriever(DEFAULT_KB)

    def test_input_contract(self):
        result = validate_inputs(DEFAULT_KB, DEFAULT_BENCHMARK)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["question_count"], 246)
        self.assertEqual(result["entity_count"], 123)
        self.assertEqual(result["relation_count"], 161)

    def test_every_question_has_six_dimensions(self):
        for question in self.benchmark["questions"]:
            self.assertEqual([x["dimension"] for x in question["rubrics"]], DIMENSIONS)

    def test_dose_question_retrieves_both_regimens_and_boundary(self):
        q = self.benchmark["questions"][13]
        hits = self.retriever.search(" ".join([q["cancer_type"], q["question_type"], q["question"]]), top_k=8)
        ids = {x["node_id"] for x in hits}
        self.assertIn("gold-regimen-denosumab-bm", ids)
        self.assertIn("gold-regimen-denosumab-osteoporosis", ids)
        self.assertIn("gold-boundary-no-interchange", ids)

    def test_dental_question_retrieves_management(self):
        q = self.benchmark["questions"][19]
        hits = self.retriever.search(" ".join([q["cancer_type"], q["question_type"], q["question"]]), top_k=8)
        ids = {x["node_id"] for x in hits}
        self.assertIn("gold-claim-dental-management", ids)


if __name__ == "__main__":
    unittest.main()
