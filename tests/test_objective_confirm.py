# -*- coding: utf-8 -*-
"""A close read of one MCQ row confirms the hold the release gate would place.

MEASURED 2026-10-12 (Parishram Humanities, 70 held copies): 21 held for an
unconfirmed MCQ option, 8 for a verdict on the wrong line; the tool's MCQ marks
were right on 106 of the 110 with faculty marks. The read confirms or it does not;
it never changes a mark.
"""
from __future__ import annotations

import os
import sys
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import preflight  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from objective_confirm import (RowReading, confirm_objective_rows, row_strip,  # noqa: E402
                               rows_to_confirm)


def _png(width=1000, height=1400):
    image = Image.new("RGB", (width, height), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _question(qid, chosen, correct, anchor, page=1, y=0.2, score=None):
    return QuestionMark(qid=qid, score=(1 if chosen == correct else 0) if score is None else score,
                        max_score=1, page=page, y_fraction=y, anchor_line_id=anchor,
                        objective=True, attempted=True, chosen_option=chosen, correct_option=correct,
                        is_correct=chosen == correct, mark_style="single", criteria=[])


def _report(questions, unconfirmed=()):
    return GradeReport(total_score=sum(q.score for q in questions), max_total=len(questions),
                       overall_remarks="", evidence_contract=True, questions=questions,
                       objective_unconfirmed_qids=list(unconfirmed))


PAGES = [PageOCR(1, 1000, 1400, [
    LineBox("P1L1", "Section A", (100, 100, 300, 130), 1),
    LineBox("P1L2", "1) (B)", (100, 200, 300, 230), 1),
    LineBox("P1L3", "2)", (100, 300, 300, 330), 1),           # Q2's row: the OCR kept only the label
    LineBox("P1L4", "3) (A)", (100, 400, 300, 430), 1),
    LineBox("P1L5", "4. D", (100, 500, 300, 530), 1),
]), PageOCR(2, 1000, 1400, [
    LineBox("P2L1", "(iv) The Muslim league (B)", (100, 200, 600, 230), 2),
])]


def reader_saying(letter, number_visible=True, clear=True, state="single_active"):
    calls = []

    def read(strip, qid, number, options):
        calls.append((qid, number, len(strip)))
        return RowReading(question_number_visible=number_visible, chosen_option=letter,
                          clear=clear, selection_state=state)
    read.calls = calls
    return read


class RowsToConfirm(unittest.TestCase):
    def test_unconfirmed_bare_and_wrong_line_rows_are_picked(self):
        report = _report([_question("Q1", "B", "B", "P1L2"),
                          _question("Q2", "C", "C", "P1L3"),
                          _question("Q4", "B", "D", "P2L1")], unconfirmed=["Q1"])
        rows = rows_to_confirm(report, PAGES)
        self.assertEqual(rows, {"q1": {"unconfirmed"}, "q2": {"bare"}, "q4": {"anchor"}})

    def test_a_confirmed_row_on_its_own_line_is_not_picked(self):
        report = _report([_question("Q1", "B", "B", "P1L2"), _question("Q3", "A", "A", "P1L4")])
        self.assertEqual(rows_to_confirm(report, PAGES), {})


class ConfirmRows(unittest.TestCase):
    def test_an_agreeing_read_confirms_and_the_hold_goes(self):
        report = _report([_question("Q2", "C", "C", "P1L3")], unconfirmed=["Q2"])
        before = [i.code for i in preflight.check_copy(report, ocr_pages=PAGES, total_marks=1)]
        self.assertIn("objective-unconfirmed", before)
        read = reader_saying("C")
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k", reader=read)
        self.assertEqual(result.confirmed, ["Q2"])
        self.assertEqual(result.reanchored, [])
        self.assertEqual(report.objective_unconfirmed_qids, [])
        self.assertEqual(report.objective_visual_verified_qids, ["Q2"])
        self.assertEqual(report.objective_crop_confirmed_qids, ["Q2"])
        self.assertEqual(report.questions[0].score, 1)
        after = [i.code for i in preflight.check_copy(report, ocr_pages=PAGES, total_marks=1)]
        self.assertNotIn("objective-unconfirmed", after)
        self.assertNotIn("objective-bare-label", after)
        self.assertEqual(read.calls[0][:2], ("Q2", 2))

    def test_a_disagreeing_read_keeps_the_hold_and_the_mark(self):
        report = _report([_question("Q1", "C", "B", "P1L2")], unconfirmed=["Q1"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("B"), second_reader=reader_saying("", clear=False))
        self.assertEqual((result.confirmed, result.corrected), ([], []))
        self.assertEqual(report.objective_unconfirmed_qids, ["Q1"])
        self.assertEqual(report.questions[0].score, 0, "one reader never flips a mark")
        self.assertIn("two close reads of its answer row disagree (B and nothing clear)", result.notes[0])

    def test_an_unclear_read_or_a_failed_read_keeps_the_hold(self):
        report = _report([_question("Q1", "B", "B", "P1L2")], unconfirmed=["Q1"])
        confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                               reader=reader_saying("B", clear=False))
        self.assertEqual(report.objective_unconfirmed_qids, ["Q1"])

        def broken(*args):
            raise RuntimeError("proxy down")
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k", reader=broken)
        self.assertEqual(report.objective_unconfirmed_qids, ["Q1"])
        self.assertIn("close read failed", result.notes[0])

    def test_a_confirmed_row_on_the_wrong_line_is_re_anchored_to_its_numbered_row(self):
        # Political Science 27954445 Q4: anchored to Q13's sub-part "(iv)" on page 2,
        # while "4. D" sits on page 1.
        report = _report([_question("Q4", "D", "D", "P2L1", page=2, y=0.15)])
        self.assertEqual(rows_to_confirm(report, PAGES), {"q4": {"anchor"}})
        read = reader_saying("D")
        result = confirm_objective_rows(report, PAGES, [_png(), _png()], google_token="t", task_id="k", reader=read)
        self.assertEqual(result.reanchored, ["Q4"])
        q = report.questions[0]
        self.assertEqual((q.anchor_line_id, q.page), ("P1L5", 1))
        self.assertAlmostEqual(q.y_fraction, 515 / 1400, places=2)
        self.assertEqual([i.code for i in preflight.check_copy(report, ocr_pages=PAGES, total_marks=1)
                          if i.code.startswith("objective")], [])

    def test_no_re_anchor_without_the_question_number_on_the_strip(self):
        report = _report([_question("Q4", "D", "D", "P2L1", page=2)])
        result = confirm_objective_rows(report, PAGES, [_png(), _png()], google_token="t", task_id="k",
                                        reader=reader_saying("D", number_visible=False))
        self.assertEqual(result.reanchored, [])
        self.assertEqual(report.questions[0].anchor_line_id, "P2L1")

    def test_two_agreeing_close_reads_score_a_row_with_no_option_on_record(self):
        # Auditing f5b09984: the key-blind reader saw nothing on rows 5 to 9, so each
        # was scored 0 with no option; a close read of the row can see the letter.
        report = _report([_question("Q1", "", "D", "P1L2", score=0)], unconfirmed=["Q1"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("D"), second_reader=reader_saying("D"))
        self.assertEqual(result.corrected, ["Q1"])
        q = report.questions[0]
        self.assertEqual((q.chosen_option, q.is_correct, q.score, q.remark), ("D", True, 1.0, ""))
        self.assertEqual(report.total_score, 1.0)
        self.assertEqual(report.objective_unconfirmed_qids, [])
        self.assertIn("D read by two close reads of its answer row (was no option); scored 1 of 1",
                      result.notes[0])

    def test_two_agreeing_close_reads_also_take_a_wrongly_credited_mark_away(self):
        # CMA 548a000c Q5: credited as A, the row reads "2) 145500", option 2 = B.
        report = _report([_question("Q1", "A", "A", "P1L2")], unconfirmed=["Q1"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("B"), second_reader=reader_saying("B"))
        self.assertEqual(result.corrected, ["Q1"])
        q = report.questions[0]
        self.assertEqual((q.chosen_option, q.score, q.remark), ("B", 0.0, "Correct answer is (A)."))

    def test_a_corrected_row_says_the_answer_in_the_papers_own_numbering(self):
        # CA FM 63c82ae8 (22 Oct 2026): "Ans: D" beside "Correct answer is (3)." on a
        # paper that numbers its options (1) to (4).
        report = _report([_question("Q1", "", "D", "P1L2", score=0)], unconfirmed=["Q1"])
        paper = ("1. Which one is right?\n(1) alpha\n(2) beta\n(3) gamma\n(4) delta\n"
                 "2. And this one?\n(1) one\n(2) two\n(3) three\n(4) four\n")
        confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k", paper_text=paper,
                               reader=reader_saying("B"), second_reader=reader_saying("B"))
        self.assertTrue(report.questions[0].remark.startswith("Correct answer is (4)"), report.questions[0].remark)
        self.assertEqual(report.total_score, 0.0)

    def test_close_reads_that_disagree_with_each_other_keep_the_hold(self):
        report = _report([_question("Q1", "A", "A", "P1L2")], unconfirmed=["Q1"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("B"), second_reader=reader_saying("C"))
        self.assertEqual((result.confirmed, result.corrected), ([], []))
        self.assertEqual((report.questions[0].chosen_option, report.questions[0].score), ("A", 1))
        self.assertIn("two close reads of its answer row disagree (B and C)", result.notes[0])

    def test_a_blank_row_or_no_official_option_is_never_scored(self):
        report = _report([_question("Q1", "", "D", "P1L2", score=0)], unconfirmed=["Q1"])
        confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                               reader=reader_saying("", clear=False), second_reader=reader_saying("D"))
        self.assertEqual(report.questions[0].score, 0)
        report = _report([_question("Q1", "", "", "P1L2", score=0)], unconfirmed=["Q1"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("D"), second_reader=reader_saying("D"))
        self.assertEqual((result.corrected, report.questions[0].score), ([], 0))
        # No anchor and no numbered row of its own anywhere on the page: nothing to read.
        report = _report([_question("Q9", "", "D", "", score=0)], unconfirmed=["Q9"])
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("D"), second_reader=reader_saying("D"))
        self.assertIn("no option on record and no row to read", result.notes[0])
        self.assertEqual(result.cost_rows, 0)

    def test_a_many_mark_row_is_not_a_single_option_mcq(self):
        # Political Science Q13: a 4-mark source-based question reached the gate as one
        # objective row; two close reads scored it 0 of 4 and would have released it.
        question = _question("Q13", "", "B", "P1L2", score=0)
        question.max_score = 4
        report = _report([question], unconfirmed=["Q13"])
        read = reader_saying("B")
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k",
                                        reader=read, second_reader=read)
        self.assertEqual(read.calls, [])
        self.assertEqual(report.objective_unconfirmed_qids, ["Q13"])
        self.assertIn("carries 4 marks, not a single-option MCQ", result.notes[0])

    def test_a_part_label_before_an_option_number_never_corrects_a_row(self):
        # CA FM MT01 8bc28fde (21 Oct 2026): case study part A answered "A=3" (option 3,
        # right); two close reads saw A and took 2 marks away. 63c82ae8's Q3 was read off
        # the Part B heading "3. (B) (1) cost of equity" as option B.
        from objective_confirm import label_read_as_option
        self.assertTrue(label_read_as_option("A", "A=3"))
        self.assertTrue(label_read_as_option("A", "(A)(3) 336000"))
        self.assertTrue(label_read_as_option("B", r"3. \( B \) ) (1) cor. of eay \( =\frac{D_{1}}{N P}+g \)"))
        self.assertTrue(label_read_as_option("A", "1) & A & (1)"))
        for letter, text in (("B", "1) (B)"), ("B", "B) 1.29"), ("D", "4. D"), ("C", "(A)(3) 336000"), ("", "A=3")):
            self.assertFalse(label_read_as_option(letter, text), (letter, text))
        pages = [PageOCR(1, 1000, 1400, [LineBox("P1L1", "A=3", (100, 200, 300, 230), 1)])]
        question = _question("Q1.A", "C", "C", "P1L1")
        question.max_score, question.score = 2, 2
        report = _report([question], unconfirmed=["Q1.A"])
        result = confirm_objective_rows(report, pages, [_png()], google_token="t", task_id="k",
                                        reader=reader_saying("A"), second_reader=reader_saying("A"))
        self.assertEqual((result.corrected, result.confirmed), ([], []))
        self.assertEqual((question.chosen_option, question.score), ("C", 2))
        self.assertEqual(report.objective_unconfirmed_qids, ["Q1.A"], "left for faculty")
        self.assertIn("A is a part label before an option number", result.notes[0])

    def test_a_corrected_row_is_drawn_again(self):
        import inspect
        import pipeline
        self.assertIn("if confirmed.reanchored or confirmed.corrected:", inspect.getsource(pipeline))

    def test_a_part_label_is_told_apart_from_the_option(self):
        from objective_confirm import row_label
        self.assertEqual(row_label("Q1.C"), (1, "c"))
        self.assertEqual(row_label("Q1.iii"), (1, "iii"))
        self.assertEqual(row_label("Q7"), (7, ""))
        self.assertEqual(row_label("SB-Q3"), (None, ""))

    def test_too_many_rows_means_a_reading_problem_not_a_few_rows(self):
        questions = [_question(f"Q{n}", "B", "B", "P1L1") for n in range(1, 11)]
        report = _report(questions, unconfirmed=[q.qid for q in questions])
        read = reader_saying("B")
        result = confirm_objective_rows(report, PAGES, [_png()], google_token="t", task_id="k", reader=read)
        self.assertEqual(read.calls, [])
        self.assertEqual(len(report.objective_unconfirmed_qids), 10)
        self.assertIn("more than 8", result.notes[0])

    def test_the_printed_question_is_cut_from_the_paper_for_the_reader(self):
        # History Infinity Test 10 Q1: a statement-combination MCQ the option parser
        # misses; the students wrote "1, 2 and 4 only", not a letter.
        from objective_confirm import question_block
        paper = ("===== PAGE 2 =====\nSECTION A\nQ.1.Consider the following statements:\n"
                 "Which are correct?\n(A) 1, 2 and 4 only\n(B) 1 and 3 only\n(C) 2 and 3 only\n"
                 "(D) 1, 2, 3 and 4\n\nQ.2.Which source ...\n(A) coins\n")
        block = question_block(paper, 1)
        self.assertTrue(block.startswith("Q.1.Consider"))
        self.assertIn("(B) 1 and 3 only", block)
        self.assertNotIn("Q.2.", block)
        self.assertEqual(question_block(paper, 9), "")
        self.assertEqual(question_block("", 1), "")

    def test_the_strip_is_a_band_around_the_row_across_the_full_width(self):
        strip, centre = row_strip(_png(), PAGES[0], (100, 300, 300, 330), 0.5)
        image = Image.open(BytesIO(strip))
        self.assertEqual(image.width, 1000)
        self.assertTrue(70 <= image.height <= 160, image.height)
        self.assertAlmostEqual(centre, 315 / 1400, places=2)
        strip, centre = row_strip(_png(), None, None, 0.25)
        self.assertAlmostEqual(centre, 0.25, places=2)


class CalibratedSubjectsRelease(unittest.TestCase):
    def test_the_release_rule_reads_the_measured_table(self):
        from categories.vidyapeeth import calibrated_subjects
        table = {"Economics": (12, 10, 12, 0.9), "History": (25, 9, 11, 3.5), "Thin": (4, 4, 4, 0.1)}
        rule = {"min_copies": 10, "tolerance": 1.0, "share": 0.8}
        self.assertEqual(calibrated_subjects(table, rule), frozenset({"Economics"}))
        self.assertEqual(calibrated_subjects(table, {**rule, "tolerance": 2.0}), frozenset({"Economics"}))
        self.assertEqual(calibrated_subjects(table, {**rule, "min_copies": 4}), frozenset({"Economics", "Thin"}))

    def test_a_calibrated_subject_is_released_the_others_held(self):
        import pipeline
        from categories.vidyapeeth import VIDYAPEETH_CALIBRATION, VIDYAPEETH_RELEASE_RULE, calibrated_subjects
        cfg = SimpleNamespace(exam_type="school", category="vidyapeeth", answer_language="")
        # Vidyapeeth releases fault-free copies (user's decision, 12 Oct 2026); the
        # calibrated-subject rule still works for a category that holds the rest.
        self.assertIsNone(pipeline._category_review_hold(cfg, "History"))
        from categories.base import ReviewPolicy
        policy = ReviewPolicy(auto_release=False, review_reason="held",
                              calibrated_subjects=frozenset({"Economics"}))
        with mock.patch.object(pipeline.categories, "get", return_value=SimpleNamespace(review=policy)):
            self.assertIsNone(pipeline._category_review_hold(cfg, "economics"))
            self.assertEqual(pipeline._category_review_hold(cfg, "History"), "held")
        # The shipped table: nothing passes the 80 percent-within-1 rule on 10 copies yet.
        self.assertEqual(calibrated_subjects(VIDYAPEETH_CALIBRATION, VIDYAPEETH_RELEASE_RULE), frozenset())


if __name__ == "__main__":
    unittest.main()
