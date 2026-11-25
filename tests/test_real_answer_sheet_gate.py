# -*- coding: utf-8 -*-
"""Only a real handwritten answer sheet for this paper is graded (2026-09-17).

* a printed or typed document is refused from Mathpix's own printed flags, free;
* study notes or an unrelated document are refused when the model says so with a
  quote that is really on the booklet and the student wrote none of this paper's
  question labels; two labels make it an answer sheet whatever the model says;
* a booklet for another paper is refused as before.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
from mathpix import (LineBox, PageOCR, booklet_is_typed, pageocr_from_dict,  # noqa: E402
                     pageocr_to_dict, question_labels_found)
from paper_match import PaperMatchVerdict, not_answer_sheet_reason, typed_document_reason  # noqa: E402
from strict_grading import StrictGradeError  # noqa: E402


def _page(texts, printed=False, page=1):
    return PageOCR(page, 1000, 1400, [
        LineBox(f"P{page}L{i}", t, (40, 40 * i, 900, 40 * i + 30), page, printed=printed)
        for i, t in enumerate(texts, 1)])


NOTES = ["Chapter 3 Partnership Accounts notes", "Goodwill is an intangible asset of the firm",
         "Average profit method: average of past profits", "Super profit = average profit minus normal profit",
         "Sacrificing ratio = old ratio minus new ratio", "Revaluation account records changes in assets",
         "Workmen compensation reserve is a liability", "Fixed capital method keeps capital constant",
         "Current account records drawings and interest", "Interest on drawings is charged to partners",
         "Partnership deed governs the partners", "Profit and loss appropriation account"]
ANSWERS = ["Q1 A) Partners", "Q2 Capital = 400000", "SP = AP - NP", "Q3 Interest on drawings 4800",
           "Q4 A) Both A and R are true", "Q5 B) Profit and loss appropriation account",
           "Q6 A) Gain 80000", "Q7 D) 71000", "Q8 A) 30000 and 20000", "Q9 B) Sacrifice 1/30",
           "Q10 Profit and loss appropriation account", "To interest on capital Ravi 60000"]


class TypedTests(unittest.TestCase):
    def test_a_printed_document_is_refused_and_a_worksheet_is_not(self):
        typed, printed, hand = booklet_is_typed([_page(NOTES, printed=True)])
        self.assertTrue(typed)
        self.assertIn("printed or typed document", typed_document_reason([_page(NOTES, printed=True)]))
        self.assertFalse(booklet_is_typed([_page(ANSWERS)])[0])
        worksheet = [_page(NOTES, printed=True), _page(ANSWERS, page=2)]      # printed questions, written answers
        self.assertFalse(booklet_is_typed(worksheet)[0])
        self.assertEqual(typed_document_reason(worksheet), "")
        self.assertFalse(booklet_is_typed([_page(NOTES[:4], printed=True)])[0])   # too little to judge

    def test_the_printed_flag_survives_the_job_store(self):
        page = _page(NOTES[:3], printed=True)
        back = pageocr_from_dict(pageocr_to_dict(page))
        self.assertTrue(all(l.printed for l in back.lines))
        self.assertFalse(pageocr_from_dict({"page": 1, "image_width": 10, "image_height": 10,
                                            "lines": [{"line_id": "P1L1", "text": "x", "bbox": [0, 0, 1, 1], "page": 1}]}).lines[0].printed)


class LabelTests(unittest.TestCase):
    def test_question_labels_are_counted_against_the_paper(self):
        self.assertEqual(question_labels_found([_page(ANSWERS)], range(1, 14)), 10)
        self.assertEqual(question_labels_found([_page(NOTES)], range(1, 14)), 0)
        self.assertEqual(question_labels_found([_page(["Ans 3) 2400", "(4) B", "Question 5: x"])], {3, 4, 5, 6}), 3)


class NotesTests(unittest.TestCase):
    def _verdict(self, kind, quotes, confidence=0.95):
        return PaperMatchVerdict(verdict="unsure", confidence=confidence, booklet_kind=kind,
                                 kind_evidence=quotes)

    def test_notes_with_a_verified_quote_and_no_labels_are_refused(self):
        pages = [_page(NOTES)]
        reason = not_answer_sheet_reason(self._verdict("notes", ["Chapter 3 Partnership Accounts notes"]), pages, 0)
        self.assertIn("study notes", reason)
        self.assertIn("Chapter 3 Partnership", reason)

    def test_a_quote_that_is_not_on_the_booklet_refuses_nothing(self):
        self.assertEqual(not_answer_sheet_reason(self._verdict("notes", ["Physics chapter 7 notes"]), [_page(NOTES)], 0), "")

    def test_an_answer_sheet_is_never_refused_on_the_models_word(self):
        pages = [_page(ANSWERS)]
        self.assertEqual(not_answer_sheet_reason(self._verdict("notes", ["Q1 A) Partners"]), pages, 10), "")
        self.assertEqual(not_answer_sheet_reason(self._verdict("answer_sheet", []), [_page(NOTES)], 0), "")
        self.assertEqual(not_answer_sheet_reason(self._verdict("notes", ["Chapter 3 Partnership Accounts notes"], 0.6), [_page(NOTES)], 0), "")

    def test_a_model_that_also_says_same_paper_refuses_nothing(self):
        verdict = PaperMatchVerdict(verdict="same_paper", confidence=0.95, booklet_kind="notes",
                                    kind_evidence=["Chapter 3 Partnership Accounts notes"])
        self.assertEqual(not_answer_sheet_reason(verdict, [_page(NOTES)], 0), "")

    def test_the_verdict_tolerates_the_models_spelling(self):
        v = PaperMatchVerdict.model_validate({"verdict": "unsure", "confidence": "90%", "booklet_kind": "Study notes",
                                              "kind_evidence": "Chapter 3 notes"})
        self.assertEqual((v.booklet_kind, v.confidence, v.kind_evidence), ("notes", 0.9, ["Chapter 3 notes"]))
        self.assertEqual(PaperMatchVerdict.model_validate({"booklet_kind": "Answer Sheet"}).booklet_kind, "answer_sheet")


class GateTests(unittest.TestCase):
    def _cfg(self):
        pkg = NS(questions=[NS(qid=f"Q{i}") for i in range(1, 14)])
        return NS(paper_check=True, is_claude=True, provider="claude", google_token="t", task_id="k",
                  exam_package=pkg)

    def test_a_typed_upload_is_refused_on_mathpix_flags_when_no_look_can_be_had(self):
        # From 29 Oct 2026 the flags alone suspect a copy and a look at its pages decides
        # (test_no_faculty_gates_1029); a copy no look can be had for is refused as before.
        pages = [_page(NOTES, printed=True)]
        unsure = PaperMatchVerdict(verdict="unsure", confidence=0.5)
        with patch.object(pipeline, "claude_check_paper", return_value=unsure), \
                self.assertRaises(StrictGradeError) as ctx:
            pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=pages,
                                         ocr_transcript="\n".join(NOTES), filename="notes.pdf",
                                         usage_out={}, fits_budget=lambda plan: True)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")

    def test_handwritten_notes_are_refused_on_verified_evidence(self):
        pages = [_page(NOTES)]
        verdict = PaperMatchVerdict(verdict="unsure", confidence=0.9, booklet_kind="notes",
                                    kind_evidence=["Chapter 3 Partnership Accounts notes"])
        with patch.object(pipeline, "claude_check_paper", return_value=verdict), \
                self.assertRaises(StrictGradeError) as ctx:
            pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=pages,
                                         ocr_transcript="\n".join(NOTES), filename="notes.pdf",
                                         usage_out={}, fits_budget=lambda plan: True)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")

    def test_a_blank_or_unreadable_upload_is_refused_before_any_model_call(self):
        # 2026-09-20: a blank page, a photo of something else or a scan too faint to
        # read used to be graded, come out "not attempted" throughout and go to the
        # student as 0 marks. Nothing written and no answer labelled = not a copy.
        for pages in ([_page([])], [_page(["~", "| |"]), _page([], page=2)]):
            transcript = "\n".join(l.text for p in pages for l in p.lines)
            with patch.object(pipeline, "claude_check_paper") as check, \
                    self.assertRaises(StrictGradeError) as ctx:
                pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=pages,
                                             ocr_transcript=transcript, filename="blank.pdf",
                                             usage_out={}, fits_budget=lambda plan: True)
            check.assert_not_called()
            self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")
            self.assertIn("No readable handwritten answers", str(ctx.exception))

    def test_a_tiny_sheet_that_labels_its_answers_is_still_a_copy(self):
        # Ten MCQ answers are fewer characters than the blank threshold; the labels decide.
        pages = [_page(["1) b", "2) c", "3) a", "4) d"])]
        with patch.object(pipeline, "claude_check_paper") as check:
            pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=pages,
                                         ocr_transcript="1) b\n2) c\n3) a\n4) d", filename="mcq.pdf",
                                         usage_out={}, fits_budget=lambda plan: True)
        check.assert_not_called()      # too short to compare, so it goes straight to grading

    def test_without_ocr_nothing_is_refused_for_being_blank(self):
        pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=None,
                                     ocr_transcript=None, filename="x.pdf",
                                     usage_out={}, fits_budget=lambda plan: True)

    def test_a_real_answer_sheet_is_graded_even_if_the_model_doubts_it(self):
        pages = [_page(ANSWERS)]
        verdict = PaperMatchVerdict(verdict="same_paper", confidence=0.7, booklet_kind="notes",
                                    kind_evidence=["Q1 A) Partners"])
        with patch.object(pipeline, "claude_check_paper", return_value=verdict):
            pipeline._refuse_other_paper(cfg=self._cfg(), qp_text="Q1 " * 200, ocr_pages=pages,
                                         ocr_transcript="\n".join(ANSWERS), filename="sheet.pdf",
                                         usage_out={}, fits_budget=lambda plan: True)


if __name__ == "__main__":
    unittest.main()
