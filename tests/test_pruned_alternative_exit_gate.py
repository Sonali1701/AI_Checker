# -*- coding: utf-8 -*-
"""The exit reconcile must not refuse a copy because the choice rule already pruned
the rows of the alternative it did not count (Piyush's SST copy, 2026-09-18)."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grader import GradeReport, QuestionMark  # noqa: E402
from strict_grading import StrictGradeError, reconcile_report_to_package  # noqa: E402
from test_nested_choices import make_package  # noqa: E402


def _rows(qids, scores):
    return [QuestionMark(qid=q, score=scores.get(q, 1), max_score=1, page=1, y_fraction=0.1 * i,
                         attempted=True) for i, q in enumerate(qids, 1)]


class ExitGateTests(unittest.TestCase):
    def _map(self, located):
        return NS(locations=[NS(qid=q, status="located", first_position=(1, i), pages=(1,), label_line_ids=("P1L1",))
                             for i, q in enumerate(located, 1)], unresolved_qids=[])

    def test_a_pruned_alternative_is_not_an_omission_at_the_exit(self):
        package = make_package()
        qids = [q.qid for q in package.questions]
        report = GradeReport(total_score=0, max_total=0, overall_remarks="",
                             questions=[QuestionMark(qid=q.qid, score=0.0, max_score=float(q.max_marks),
                                                     page=1, y_fraction=0.1, attempted=True) for q in package.questions],
                             evidence_contract=False)
        amap = self._map(qids)
        first = reconcile_report_to_package(report, package, answer_map=amap, finalize_choices=True)
        self.assertLess(len(first.questions), len(qids))          # the surplus alternative was pruned
        # the old exit pass refused this copy; now it must accept the pruned report
        again = reconcile_report_to_package(first, package, answer_map=amap, finalize_choices=True,
                                            choices_already_finalized=True)
        self.assertEqual(len(again.questions), len(first.questions))
        with self.assertRaises(StrictGradeError):
            reconcile_report_to_package(first, package, answer_map=amap, finalize_choices=True)


if __name__ == "__main__":
    unittest.main()
