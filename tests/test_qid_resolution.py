# -*- coding: utf-8 -*-
"""A model's spelling of a question id never fails a copy on its own.

Operator's rule (2026-09-17): two models do not spell every id the same way, and a
name is not a mark. A spelling of a package question resolves to the package's
spelling; only a question the package does not have stays unknown.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
from exam_package import qid_stem, resolve_qid  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from strict_grading import StrictGradeError  # noqa: E402

PACKAGE = ["Q1", "Q2", "Q10", "Q10.OR", "Q11", "Q11.OR", "Q12.a", "Q12.b", "Q12.OR"]


class StemTests(unittest.TestCase):
    def test_markers_are_stripped_and_the_alternative_index_kept(self):
        self.assertEqual(qid_stem("q10.alt.2"), ("q10", 2))
        self.assertEqual(qid_stem("q10.or"), ("q10", 2))
        self.assertEqual(qid_stem("q10.alt.1.a"), ("q10.a", 1))
        self.assertEqual(qid_stem("q10.option.3"), ("q10", 3))
        self.assertEqual(qid_stem("q10"), ("q10", None))
        self.assertEqual(qid_stem("q12.b"), ("q12.b", None))


class ResolveTests(unittest.TestCase):
    def test_exact_spellings_and_punctuation_variants(self):
        self.assertEqual(resolve_qid("Q10", PACKAGE), "Q10")
        self.assertEqual(resolve_qid("q10_or", PACKAGE), "Q10.OR")
        self.assertEqual(resolve_qid("Q10 OR", PACKAGE), "Q10.OR")
        self.assertEqual(resolve_qid("Q12(a)", PACKAGE), "Q12.a")

    def test_invented_alternative_markers_resolve_by_position(self):
        self.assertEqual(resolve_qid("Q10.alt1", PACKAGE), "Q10")
        self.assertEqual(resolve_qid("Q10.alt2", PACKAGE), "Q10.OR")
        self.assertEqual(resolve_qid("Q11-alt-2", PACKAGE), "Q11.OR")
        self.assertEqual(resolve_qid("Q12.alt1.b", PACKAGE), "Q12.b")
        self.assertEqual(resolve_qid("Q12.alt2", PACKAGE), "Q12.OR")
        # the package itself spelt with markers, the model without
        alt_package = ["Q1", "Q10.alt1", "Q10.alt2"]
        self.assertEqual(resolve_qid("Q10.OR", alt_package), "Q10.alt2")
        self.assertEqual(resolve_qid("Q10.option2", alt_package), "Q10.alt2")

    def test_a_question_the_package_lacks_stays_unknown(self):
        self.assertIsNone(resolve_qid("Q99", PACKAGE))
        self.assertIsNone(resolve_qid("Q10.alt3", PACKAGE))
        # a bare id where the package has only marked alternatives is never guessed
        self.assertIsNone(resolve_qid("Q10", ["Q1", "Q10.alt1", "Q10.alt2"]))


class IndexTests(unittest.TestCase):
    def _package(self, qids):
        return SimpleNamespace(questions=[SimpleNamespace(qid=q) for q in qids])

    def _report(self, qids):
        return GradeReport(total_score=0, max_total=0, overall_remarks="", questions=[
            QuestionMark(qid=q, score=1, max_score=1, page=1, y_fraction=0.1) for q in qids])

    def test_drifted_spellings_are_read_as_the_package_questions(self):
        report = self._report(["Q1", "Q2", "Q10.alt1", "Q10-alt-2", "Q11 OR"])
        official, present = pipeline._index_question_decisions(report, self._package(PACKAGE))
        self.assertEqual(sorted(present), ["q1", "q10", "q10.or", "q11.or", "q2"])
        # a spelling that already normalizes to a package id is kept as written
        # a spelling that already normalizes to a package id is kept as written
        self.assertEqual([q.qid for q in report.questions], ["Q1", "Q2", "Q10", "Q10.OR", "Q11 OR"])

    def test_a_truly_unknown_question_still_fails_closed(self):
        with self.assertRaises(StrictGradeError) as ctx:
            pipeline._index_question_decisions(self._report(["Q1", "Q99"]), self._package(PACKAGE))
        self.assertEqual(ctx.exception.code, "UNKNOWN_QUESTION")

    def test_two_readings_of_one_question_keep_one_row(self):
        # Build 75: an answer over two pages came back twice and the whole copy was
        # refused (Akshat, 2026-09-30). One row is kept; the copy is graded.
        report = self._report(["Q10", "Q10.alt1"])
        official, present = pipeline._index_question_decisions(report, self._package(PACKAGE))
        self.assertEqual(len(report.questions), 1)
        self.assertEqual(len([k for k in present if k == "q10"]), 1)


if __name__ == "__main__":
    unittest.main()
