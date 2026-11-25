# -*- coding: utf-8 -*-
"""Every copy decided, none held for faculty (user, 29 Oct 2026).

aichecker.log of 29 Oct 2026, 819 school copies: the text check called an IRCTC ticket,
a residence certificate form, school notices, timetables, a syllabus and typed study
guides "other / notes / typed, different paper" at 0.95 to 0.99, and 196 such copies
were graded anyway because their evidence could not be verified. 269 copies Mathpix
read as printed were graded because their printed numbering counted as question
labels. Now a suspected copy is looked at on its pages by a second model and rejected
only when both readings agree; a genuine copy the look recognises is graded.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow  # noqa: E402
import pipeline  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from paper_match import (PaperMatchVerdict, SheetLook, _plain,  # noqa: E402
                         build_paper_check_prompt, first_read_flags, rejection_after_look)
from strict_grading import StrictGradeError  # noqa: E402

PAPER = "\n".join([
    "Q1 Which mineral is used in the making of electrical wires? (A) Mica (B) Copper (C) Coal (D) Iron",
    "Q2 Name the party system followed in India and give one feature of it.",
    "Q3 Explain any two features of the Non Cooperation Movement.",
    "Q4 What is meant by trade barriers? Give one example.",
    "Q5 Distinguish between conventional and non conventional sources of energy.",
    "Q6 Describe the role of opposition parties in a democracy.",
    "Q7 Why is manganese important for the iron and steel industry?",
    "Q8 State two reasons for the decline of the Khilafat agitation.",
])
ANSWERS = ["Q1 (B) Copper", "Q2 India follows a multi party system, many parties compete",
           "Q3 Boycott of foreign goods and surrender of titles were features",
           "Q4 Trade barrier is a restriction on trade, for example a tax on imports",
           "Q5 Coal is conventional while solar energy is non conventional",
           "Q6 Opposition parties criticise the government and keep it in check",
           "Q7 Manganese is used to make steel hard and strong",
           "Q8 Gandhiji withdrew the movement after Chauri Chaura"]
TICKET = ["IRCTC Electronic Reservation Slip", "1 PNR No 4521897763", "2 Train No 12301 Rajdhani",
          "3 Date of Journey 12 Oct 2026", "4 Passenger Name RAHUL KUMAR", "5 Booking Status CNF",
          "6 Coach B2 Berth 33", "7 Total Fare Rs 2,145", "8 Terms and conditions apply",
          "Please carry a valid photo identity card", "Customer care 139"]
PNG = [b"page1", b"page2"]


def _page(texts, printed=False, page=1):
    return PageOCR(page, 1000, 1400, [
        LineBox(f"P{page}L{i}", t, (40, 40 * i, 900, 40 * i + 30), page, printed=printed)
        for i, t in enumerate(texts, 1)])


def _cfg(typed=False):
    pkg = NS(questions=[NS(qid=f"Q{i}") for i in range(1, 9)], subject="SST")
    cfg = NS(paper_check=True, is_claude=True, provider="claude", google_token="t", task_id="k",
             exam_package=pkg, category="school")
    if typed:
        cfg.category = "innovation"
    return cfg


def _gate(pages, *, verdict, look=None, look_error=None, qp_text=PAPER, paper_pages=0, typed=False):
    transcript = "\n".join(l.text for p in pages for l in p.lines)
    looker = (patch.object(pipeline, "claude_look_at_sheet", side_effect=look_error)
              if look_error else patch.object(pipeline, "claude_look_at_sheet", return_value=look))
    with patch.object(pipeline, "claude_check_paper", return_value=verdict) as check, looker as seen:
        pipeline._refuse_other_paper(cfg=_cfg(typed), qp_text=qp_text, ocr_pages=pages,
                                     ocr_transcript=transcript, filename="copy.pdf",
                                     usage_out={}, fits_budget=lambda plan: True,
                                     paper_pages=paper_pages, sa_pngs=PNG, qp_pngs=[b"qp1", b"qp2"])
    return check, seen


OTHER_DOC = PaperMatchVerdict(verdict="different_paper", confidence=0.99, booklet_kind="other",
                              reason="The booklet is an IRCTC railway ticket booking confirmation.")
SAME = PaperMatchVerdict(verdict="same_paper", confidence=0.95, booklet_kind="answer_sheet")


class JunkIsRejected(unittest.TestCase):
    def test_a_printed_ticket_with_numbered_lines_is_rejected_when_the_look_agrees(self):
        look = SheetLook(upload="not_an_answer_sheet", handwritten=False, confidence=0.98,
                         document="a railway ticket; booking slip")
        with self.assertRaises(StrictGradeError) as ctx:
            _gate([_page(TICKET, printed=True)], verdict=OTHER_DOC, look=look)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")
        self.assertIn("railway ticket", str(ctx.exception))
        self.assertNotIn(";", str(ctx.exception))

    def test_typed_answers_to_this_paper_are_rejected(self):
        look = SheetLook(upload="answers_to_this_paper", handwritten=False, confidence=0.95,
                         document="typed answers in a Word document")
        with self.assertRaises(StrictGradeError) as ctx:
            _gate([_page(ANSWERS * 2, printed=True)], verdict=SAME, look=look)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")
        self.assertIn("typed or printed document", str(ctx.exception))

    def test_a_hindi_booklet_for_another_subject_is_rejected_not_held(self):
        hindi = ["Q1 उत्तर = सांख्यिकी आंकड़ों का समूह है", "Q2 उत्तर = गोटफ्रीड अचिनवाल",
                 "Q3 उत्तर = प्राथमिक आंकड़े अनुसंधानकर्ता स्वयं एकत्र करता है"] * 6
        look = SheetLook(upload="answers_to_another_paper", confidence=0.95,
                         other_paper="Economics - Statistics", document="handwritten Hindi answers")
        with self.assertRaises(StrictGradeError) as ctx:
            _gate([_page(hindi)], verdict=SAME, look=look)
        self.assertEqual(ctx.exception.code, "WRONG_QUESTION_PAPER")
        self.assertIn("Economics, Statistics", str(ctx.exception))


class GenuineCopiesAreGraded(unittest.TestCase):
    def test_one_reading_alone_never_rejects(self):
        look = SheetLook(upload="answers_to_this_paper", handwritten=True, confidence=0.97)
        _gate([_page(TICKET, printed=True)], verdict=OTHER_DOC, look=look)   # graded

    def test_an_unsure_or_low_confidence_look_grades(self):
        for look in (SheetLook(upload="unsure", confidence=0.99),
                     SheetLook(upload="not_an_answer_sheet", confidence=0.7)):
            _gate([_page(TICKET, printed=True)], verdict=OTHER_DOC, look=look)

    def test_a_scanner_app_copy_read_as_printed_is_graded(self):
        look = SheetLook(upload="answers_to_this_paper", handwritten=True, confidence=0.96)
        _gate([_page(ANSWERS * 2, printed=True)], verdict=SAME, look=look)

    def test_a_hindi_answered_copy_of_this_paper_is_graded_not_held(self):
        hindi = ["Q1 (B) तांबा", "Q2 भारत में बहुदलीय प्रणाली है जिसमें कई दल होते हैं"] * 8
        look = SheetLook(upload="answers_to_this_paper", handwritten=True, confidence=0.93)
        _gate([_page(hindi)], verdict=SAME, look=look)

    def test_an_unsuspected_copy_is_not_looked_at(self):
        _, seen = _gate([_page(ANSWERS)], verdict=SAME)
        seen.assert_not_called()

    def test_the_look_is_never_told_the_first_verdict(self):
        look = SheetLook(upload="unsure", confidence=0.5)
        _, seen = _gate([_page(TICKET, printed=True)], verdict=OTHER_DOC, look=look)
        args, kwargs = seen.call_args
        self.assertEqual(args, (PNG, PAPER, [b"qp1", b"qp2"]))
        self.assertNotIn("verdict", kwargs)


class PaperPrintedAsPictures(unittest.TestCase):
    def test_the_booklet_kind_is_still_read_and_the_look_sees_the_paper_pages(self):
        note = "Note: all questions are compulsory."
        look = SheetLook(upload="not_an_answer_sheet", confidence=0.97, document="a school notice")
        with self.assertRaises(StrictGradeError):
            check, _ = _gate([_page(TICKET, printed=True)], verdict=OTHER_DOC, look=look,
                             qp_text=note, paper_pages=2)
        with patch.object(pipeline, "claude_check_paper", return_value=OTHER_DOC) as check, \
                patch.object(pipeline, "claude_look_at_sheet", return_value=SheetLook()) as seen:
            pipeline._refuse_other_paper(cfg=_cfg(), qp_text=note, ocr_pages=[_page(TICKET)],
                                         ocr_transcript="\n".join(TICKET), filename="c.pdf",
                                         usage_out={}, fits_budget=lambda plan: True,
                                         paper_pages=2, sa_pngs=PNG, qp_pngs=[b"qp1"])
        self.assertEqual(check.call_args.args[0], "")         # booklet-only prompt
        self.assertEqual(seen.call_args.args[1], "")          # the look gets the paper's pages
        self.assertIn("text is not available", build_paper_check_prompt("", "some answers here"))


class WithoutALook(unittest.TestCase):
    def test_the_verified_evidence_rules_decide_and_nothing_is_held(self):
        boom = RuntimeError("proxy 500")
        with self.assertRaises(StrictGradeError) as ctx:   # printed, no labels: refused as before
            _gate([_page(["Goodwill is an intangible asset of the firm"] * 12, printed=True)],
                  verdict=OTHER_DOC, look_error=boom)
        self.assertEqual(ctx.exception.code, "NOT_AN_ANSWER_SHEET")
        unverified = PaperMatchVerdict(verdict="different_paper", confidence=0.99, booklet_kind="notes")
        _gate([_page(ANSWERS)], verdict=unverified, look_error=boom)   # graded, not held

    def test_a_typed_category_keeps_its_rules(self):
        _, seen = _gate([_page(ANSWERS * 2, printed=True)], verdict=OTHER_DOC, typed=True,
                        look=SheetLook(upload="not_an_answer_sheet", confidence=0.99))
        seen.assert_not_called()


class Parts(unittest.TestCase):
    def test_plain_words_for_the_student(self):
        self.assertEqual(_plain("a typed study guide; Test-12 — Physics"),
                         "a typed study guide, Test 12, Physics")

    def test_the_look_reply_is_parsed_tolerantly(self):
        look = SheetLook.model_validate({"upload": "Not an answer sheet", "confidence": "97%",
                                         "handwritten": "no", "document": None})
        self.assertEqual((look.upload, look.confidence, look.handwritten, look.document),
                         ("not_an_answer_sheet", 0.97, False, ""))
        self.assertEqual(SheetLook.model_validate({"upload": "answers to another paper"}).upload,
                         "answers_to_another_paper")

    def test_first_read_flags(self):
        self.assertTrue(first_read_flags(OTHER_DOC))
        self.assertFalse(first_read_flags(SAME))
        self.assertFalse(first_read_flags(None))
        self.assertFalse(first_read_flags(PaperMatchVerdict(verdict="different_paper", confidence=0.6)))
        self.assertIsNone(rejection_after_look(None, verdict=OTHER_DOC))


class WholeTestGuard(unittest.TestCase):
    def store(self):
        return integrated_workflow.WorkflowStore.__new__(integrated_workflow.WorkflowStore)

    def run_with(self, other, checked):
        rows = ([{"stage": "rejected", "rejection_code": "WRONG_QUESTION_PAPER"} for _ in range(other)]
                + [{"stage": "uploaded"} for _ in range(checked)])
        return {"rows": rows}

    def test_it_trips_on_the_third_of_few_copies_and_not_among_many(self):
        store, row = self.store(), {"stage": "downloaded"}
        self.assertFalse(store.paper_guard_trips(self.run_with(1, 0), row))
        self.assertTrue(store.paper_guard_trips(self.run_with(2, 3), row))
        self.assertFalse(store.paper_guard_trips(self.run_with(2, 20), row))

    def test_resume_checks_the_unposted_ones_again_and_turns_the_guard_off(self):
        store, run = self.store(), self.run_with(2, 1)
        run["rows"].append({"stage": "rejection_submitted", "rejection_code": "WRONG_QUESTION_PAPER"})
        run["paper_guard_tripped"] = True
        store.resume_paper_guard(run)
        self.assertEqual([r["stage"] for r in run["rows"]],
                         ["downloaded", "downloaded", "uploaded", "rejection_submitted"])
        self.assertTrue(run["paper_guard_cleared"])
        self.assertFalse(store.paper_guard_trips(self.run_with(5, 0) | {"paper_guard_cleared": True},
                                                 {"stage": "downloaded"}))

    def test_its_stop_is_a_run_level_cause(self):
        self.assertIn("question paper", integrated_workflow.run_level_cause(
            "Not posted: 3 copies of this test look like answers to a different test. Check ..."))


if __name__ == "__main__":
    unittest.main()
