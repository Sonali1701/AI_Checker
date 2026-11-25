"""A correct total cannot conceal incorrect question marks or misplaced ticks."""
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.benchmark_quality import compare_copy, summarize


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.truth = {"id": "maths", "subject": "Maths", "total_marks": 4,
                      "questions": [{"qid": "Q1", "score": 1, "max_score": 2, "attempted": True,
                                     "mark_regions": [{"page": 1, "bbox": [.6, .1, 1.0, .2]}]},
                                    {"qid": "Q2", "score": 1, "max_score": 2, "attempted": True}]}
        self.record = {"release": True, "report": {"evidence_contract": True, "total_score": 2, "max_total": 4,
                       "questions": [{"qid": "Q1", "score": 2, "max_score": 2},
                                     {"qid": "Q2", "score": 0, "max_score": 2}],
                       "render_manifest": [{"qid": "Q1", "role": "answer", "page": 1,
                                            "anchor_normalized": [.6, .1, 1.0, .2],
                                            "mark_normalized": [.6, .5, 1.0, .6]}]}}

    def test_opposite_errors_do_not_cancel(self):
        row = compare_copy(self.truth, self.record)
        self.assertEqual(row["total_delta"], 0)
        self.assertEqual(summarize([row])["question_mae"], 1)

    def test_checks_actual_marks_not_only_the_intended_anchor(self):
        row = compare_copy(self.truth, self.record)
        self.assertEqual(row["placement_errors"], 1)
        self.assertEqual(row["unmarked_answers"], 1)

    def test_holds_and_repeated_run_variance_are_counted(self):
        first = compare_copy(self.truth, self.record)
        changed = copy.deepcopy(self.record)
        changed["report"]["questions"][0]["score"] = 1
        second = compare_copy(self.truth, changed)
        held = compare_copy(self.truth, {"release": False})
        summary = summarize([first, second, held])
        self.assertAlmostEqual(summary["withheld_rate"], 1/3)
        self.assertEqual(summary["max_repeat_question_range"], 1)

    def test_legacy_results_cannot_masquerade_as_production(self):
        self.record["report"]["evidence_contract"] = False
        self.assertTrue(compare_copy(self.truth, self.record)["errors"])


if __name__ == "__main__":
    unittest.main()
