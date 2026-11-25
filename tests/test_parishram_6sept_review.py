# -*- coding: utf-8 -*-
"""Parishram ISC Class 12 Test-09 (6 Sept) review, 2026-10-03.

A scanned Question & Solution key that froze the whole Physics run, ten English MCQs
read as one 10-mark question, a Physics booklet graded as Chemistry, and a solution's
lowercase "or" read as an internal choice.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mathpix  # noqa: E402
import pipeline  # noqa: E402
from grader import MarksItem, MarksScheme, paper_has_choice, split_product_tagged_rows  # noqa: E402
from paper_match import PaperMatchVerdict, wrong_paper_reason  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENGLISH = """Q4
(iv) Use any five of the following phrasal verbs in meaningful sentences of your own:
[5]
Q5
SECTION B - LITERATURE  [60 Marks]
PART I (20 Marks)
Choose the correct option for each of the
following:    [10 × 1 = 10]
In Act 3 Scene 5, Hecate is angry with the witches mainly because:
(A) they helped Banquo
""" + "".join(f"Q{n}\nQuestion {n}?\n(A) one\n(B) two\n" for n in range(6, 15)) + """Q15
Answer the following. [2]
"""


def _scheme(rows):
    return MarksScheme(total=sum(m for _, m in rows),
                       items=[MarksItem(qid=q, max=float(m)) for q, m in rows])


class McqBlockReadAsOneQuestion(unittest.TestCase):
    def test_the_block_row_is_split_into_its_labelled_questions(self):
        scheme = split_product_tagged_rows(_scheme([("Q4", 5), ("Q5", 10), ("Q15", 2)]), ENGLISH)
        self.assertEqual([(i.qid, i.max) for i in scheme.items],
                         [("Q4", 5.0)] + [(f"Q{n}", 1.0) for n in range(5, 15)] + [("Q15", 2.0)])

    def test_a_correct_reading_is_left_alone(self):
        rows = [("Q4", 5)] + [(f"Q{n}", 1) for n in range(5, 15)] + [("Q15", 2)]
        scheme = _scheme(rows)
        self.assertIs(split_product_tagged_rows(scheme, ENGLISH), scheme)

    def test_no_split_without_the_printed_labels(self):
        text = ENGLISH.split("Q6\n")[0] + "Q15\nAnswer. [2]\n"
        scheme = _scheme([("Q4", 5), ("Q5", 10), ("Q15", 2)])
        self.assertIs(split_product_tagged_rows(scheme, text), scheme)


class AnotherSubjectsBooklet(unittest.TestCase):
    def _pages(self):
        return [mathpix.PageOCR(1, 1000, 1400, [
            mathpix.LineBox("P1L1", "Board Subjective test 05", (0, 0, 9, 9), 1),
            mathpix.LineBox("P1L2", "(Physics)", (0, 10, 9, 19), 1),
            mathpix.LineBox("P1L3", "Section A MCQ", (0, 20, 9, 29), 1)])]

    def _verdict(self, heading="Board Subjective test 05 (Physics)", confidence=0.98):
        return PaperMatchVerdict(verdict="different_paper", confidence=confidence,
                                 mismatches=[], sheet_heading=heading)

    def test_a_physics_booklet_is_refused_under_chemistry(self):
        reason = wrong_paper_reason(self._verdict(), self._pages(), "", subject="Chemistry")
        self.assertIn("another subject", reason)

    def test_the_same_booklet_under_its_own_subject_is_not(self):
        self.assertEqual(wrong_paper_reason(self._verdict(), self._pages(), "", subject="Physics"), "")

    def test_a_heading_not_on_the_booklet_decides_nothing(self):
        verdict = self._verdict(heading="Biology Unit Test")
        self.assertEqual(wrong_paper_reason(verdict, self._pages(), "", subject="Chemistry"), "")

    def test_an_unsure_check_decides_nothing(self):
        verdict = self._verdict(confidence=0.5)
        self.assertEqual(wrong_paper_reason(verdict, self._pages(), "", subject="Chemistry"), "")


class SolutionOrIsNotAChoice(unittest.TestCase):
    def test_lowercase_or_between_equations(self):
        self.assertFalse(paper_has_choice("W = -U = 100 k q^2\nor\nW = 9 x 10^11 q^2 J\n"))

    def test_printed_or_between_questions_still_counts(self):
        self.assertTrue(paper_has_choice("Q19 Explain the mechanism.\nOR\nDerive the rate law.\n"))
        self.assertTrue(paper_has_choice("(a) one\nOr\n(b) two\n"))


class ScannedKey(unittest.TestCase):
    def test_a_scanned_key_is_read_by_the_ocr(self):
        def fake(png, page_num, **_kw):
            return mathpix.PageOCR(page_num, 9, 9, [
                mathpix.LineBox(f"P{page_num}L1", f"Q{page_num} Correct option (C)", (0, 0, 9, 9), page_num)])
        cfg = type("Cfg", (), {"google_token": "t", "task_id": "k"})()
        with patch.object(mathpix, "ocr_page", fake):
            text = pipeline.scanned_document_text([b"a", b"b"], cfg=cfg, label="answer key")
        self.assertIn("Q1 Correct option (C)", text)
        self.assertIn("Q2 Correct option (C)", text)

    def test_no_token_reads_nothing(self):
        cfg = type("Cfg", (), {"google_token": "", "task_id": "k"})()
        self.assertEqual(pipeline.scanned_document_text([b"a"], cfg=cfg), "")

    def test_the_job_reads_a_scanned_key_before_refusing_it(self):
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        ocr_at = src.index('ak_text_layer = scanned_document_text(ak_pngs')
        refuse_at = src.index('"ANSWER_KEY_TEXT_UNAVAILABLE"')
        self.assertLess(ocr_at, refuse_at)


class PackageFailureEndsTheJob(unittest.TestCase):
    def test_faculty_review_is_illegal_here_so_the_handler_falls_back(self):
        from automatic_workflow import AutomaticWorkflow, WorkflowState
        flow = AutomaticWorkflow()
        flow.transition(WorkflowState.PACKAGE_VALIDATING, "x")
        with self.assertRaises(RuntimeError):
            flow.faculty_review("ANSWER_KEY_TEXT_UNAVAILABLE", "m")
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        handler = src[src.index('_log_error("exam package could not be validated", exc)'):]
        handler = handler[:handler.index("_persist_job(job_id)")]
        self.assertIn("except RuntimeError:", handler)
        self.assertIn("setup_flow.ungradable(exc.code, str(exc))", handler)


if __name__ == "__main__":
    unittest.main()
