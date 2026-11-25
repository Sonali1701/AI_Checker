# -*- coding: utf-8 -*-
"""Two genuine Maharashtra HSC Book-keeping copies refused before grading (22 Oct 2026).

* Row 81 (faculty 21/25): a phone scanner app's high-contrast scan of a handwritten
  copy came back from Mathpix as 17 printed lines and 2 handwritten, and was refused
  as "a printed or typed document" although it answers Q1 to Q11 under their labels.
* Row 80 (faculty 22/25): the paper prints its questions as a picture; its text layer
  is the 207-character instructions note. The check, shown no options, refused the
  copy as "a different test" while its answers were this paper's own options.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from paper_match import (PaperMatchVerdict, booklet_copies_paper,  # noqa: E402
                         paper_text_is_partial)
from strict_grading import StrictGradeError  # noqa: E402

INSTRUCTIONS = ("Note-\nAll questions are compulsory\nRead each question carefully before "
                "answering.\nQuestion 1 to 10 carry 1mark each\nQuestion 11 is Long question 15 "
                "marks\nAttempt the answers in a clear and systematic manner.")
ROW81 = ["Accounts Unit Test - 1", "Q1 -> B] Capital introduced in Kind",
         "Q2 -> D] Goods withdrawn by partners reduce purchases",
         "Q3 -> B] 4000", "Q4 -> B] Capital expenditure", "Q5 -> C] Income & Expenditure A/c",
         "Q6 -> False, becoz every adjustment in final A/cs has 2 effects",
         "Q7 -> False, if not mention then it is not compulsory", "Q8 -> True.",
         "Q9 -> false, becoz cash transactions are recorded in Receipt and Payments A/c",
         "Q10 -> False, profit & loss A/c is Nominal A/c",
         "Q11 -> In the books of Rucha and Juili", "Trading A/c for the year ended 31 March 2019",
         "To Opening stock 89,500 By Sales 1,48,000", "To Wages & Salaries 2,800 By Closing stock 88,000",
         "To Carriage inward 800 To Coal gas fuel 1,000", "To Gross profit c/d 93,100 total 2,39,000"]
PAPER = "\n".join([
    "Q1 Under Fixed Capital Method, which of the following is NOT recorded in Capital Account?",
    "(A) Additional Capital (B) Capital Introduced in Kind (C) Withdrawal of Capital",
    "Q2 Which of the given statements is correct?",
    "Q3 Goods costing 12,000 were destroyed by fire. Insurance company admitted claim 8,000.",
    "Q4 The purchase of Machinery is a revenue or capital expenditure",
    "Q5 Which account records only current year's revenue items?",
    "Q6 State whether the statement is True or False: every adjustment affects one account.",
    "Q7 State whether the statement is True or False: interest on capital is compulsory.",
    "Q8 State whether statement is True or False: charitable institutions prepare P&L.",
    "Q9 State whether the statement is True or False: credit transactions in Receipts.",
    "Q10 State whether the statement is True or False: Profit and Loss Account is Real.",
    "Q11 Rucha and Juili are partners sharing profits and losses in their capital ratio.",
])


def _page(texts, printed=False, page=1):
    return PageOCR(page, 1000, 1400, [
        LineBox(f"P{page}L{i}", t, (40, 40 * i, 900, 40 * i + 30), page, printed=printed)
        for i, t in enumerate(texts, 1)])


def _cfg():
    pkg = NS(questions=[NS(qid=f"Q{i}") for i in range(1, 12)])
    return NS(paper_check=True, is_claude=True, provider="claude", google_token="t", task_id="k",
              exam_package=pkg, category="commerce")


def _gate(pages, qp_text, paper_pages=0, verdict=None):
    transcript = "\n".join(l.text for p in pages for l in p.lines)
    verdict = verdict or PaperMatchVerdict(verdict="same_paper", confidence=0.9,
                                           booklet_kind="answer_sheet")
    with patch.object(pipeline, "claude_check_paper", return_value=verdict) as check:
        pipeline._refuse_other_paper(cfg=_cfg(), qp_text=qp_text, ocr_pages=pages,
                                     ocr_transcript=transcript, filename="copy.pdf",
                                     usage_out={}, fits_budget=lambda plan: True,
                                     paper_pages=paper_pages)
    return check


class HighContrastScan(unittest.TestCase):
    def test_a_labelled_copy_read_as_printed_is_graded(self):
        _gate([_page(ROW81, printed=True)], PAPER)

    def test_the_question_paper_uploaded_as_the_copy_is_still_refused(self):
        pages = [_page(PAPER.splitlines(), printed=True)]
        self.assertTrue(booklet_copies_paper(pages, PAPER))
        with self.assertRaises(StrictGradeError) as ctx:
            _gate(pages, PAPER)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")

    def test_printed_notes_without_labels_are_still_refused(self):
        notes = ["Goodwill is an intangible asset of the firm"] * 12
        with self.assertRaises(StrictGradeError):
            _gate([_page(notes, printed=True)], PAPER)


class PaperPrintedAsPictures(unittest.TestCase):
    def test_only_what_the_booklet_is_is_read_and_another_paper_is_never_said(self):
        # From 29 Oct 2026 the text check still reads what the booklet IS, without the
        # paper; shown no paper, its "different paper" is dropped, so row 80 is graded.
        self.assertTrue(paper_text_is_partial(INSTRUCTIONS, 2))
        wrong = PaperMatchVerdict(verdict="different_paper", confidence=0.95)
        check = _gate([_page(ROW81)], INSTRUCTIONS, paper_pages=2, verdict=wrong)
        self.assertEqual(check.call_args.args[0], "")

    def test_a_paper_with_its_text_is_still_checked(self):
        self.assertFalse(paper_text_is_partial(PAPER, 2))
        self.assertFalse(paper_text_is_partial(INSTRUCTIONS, 0))   # page count unknown
        check = _gate([_page(ROW81)], PAPER, paper_pages=2)
        check.assert_called_once()


class AnotherSubjectInHindi(unittest.TestCase):
    """MP Board rows 74 to 77: Hindi Economics booklets filed under the English test;
    row 76 labels Q1 to Q11 and was graded against the English paper at 12/20."""
    HINDI = ["Q1 उत्तर = सांख्यिकी आंकड़ों का समूह है", "Q2 उत्तर = गोटफ्रीड अचिनवाल",
             "Q3 उत्तर = प्राथमिक आंकड़े अनुसंधानकर्ता स्वयं एकत्र करता है",
             "Q4 उत्तर = वर्गीकरण आंकड़ों को समान गुणों के आधार पर बांटना है"] * 4

    def test_a_hindi_booklet_for_an_english_only_paper_is_rejected_when_the_look_agrees(self):
        # Suspected, not held (no faculty, 29 Oct 2026): the look at its pages decides.
        from paper_match import SheetLook, booklet_script_mismatch
        self.assertIn("written in Hindi", booklet_script_mismatch(PAPER, "\n".join(self.HINDI)))
        pages = [_page(self.HINDI)]
        same = PaperMatchVerdict(verdict="same_paper", confidence=0.9, booklet_kind="answer_sheet")
        other = SheetLook(upload="answers_to_another_paper", confidence=0.95, other_paper="Economics")
        for look, refused in ((other, True), (SheetLook(upload="answers_to_this_paper", confidence=0.95), False)):
            with patch.object(pipeline, "claude_check_paper", return_value=same), \
                    patch.object(pipeline, "claude_look_at_sheet", return_value=look):
                call = lambda: pipeline._refuse_other_paper(
                    cfg=_cfg(), qp_text=PAPER, ocr_pages=pages, ocr_transcript="\n".join(self.HINDI),
                    filename="copy.pdf", usage_out={}, fits_budget=lambda plan: True, paper_pages=2,
                    sa_pngs=[b"page"], qp_pngs=[b"qp"])
                if refused:
                    with self.assertRaises(StrictGradeError) as ctx:
                        call()
                    self.assertEqual(ctx.exception.code, "WRONG_QUESTION_PAPER")
                else:
                    call()
        _gate(pages, PAPER, paper_pages=2)   # no look to be had: graded, nothing held

    def test_hindi_answers_to_a_bilingual_paper_are_graded(self):
        from paper_match import booklet_script_mismatch
        bilingual = PAPER + "\nप्रश्न 1 स्थायी पूंजी पद्धति में निम्न में से कौन सा पूंजी खाते में दर्ज नहीं होता?"
        self.assertEqual(booklet_script_mismatch(bilingual, "\n".join(self.HINDI)), "")
        self.assertEqual(booklet_script_mismatch(PAPER, "\n".join(ROW81)), "")
        self.assertEqual(booklet_script_mismatch("", "\n".join(self.HINDI)), "")   # no paper text


if __name__ == "__main__":
    unittest.main()
