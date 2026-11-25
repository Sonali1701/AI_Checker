# -*- coding: utf-8 -*-
"""Commerce and CA SME review of 29 Oct 2026, in code.

The commerce sheet's state board rows (42 to 101) were reviewed on the copies corrected
by hand on 24 Oct, so what the subject SMEs still found wrong is a standard the tool
and the corrector both missed:
  rows 52, 53  UP Business Studies 5-mark answers, one-line points given 5/5
  row 47       UP Economics, misspelt subject words with full marks
  rows 54, 56  UP English, remarks that say "quote the words" and no reason
  rows 90, 92  Maharashtra HSC English activities (completion, web, suffixes, matching)
  row 101      Maharashtra HSC Maths activity, box values alone
  row 71       MP Board Maths, a cofactor list with two wrong entries ticked
  row 78       Maharashtra HSC Book Keeping, a 15-mark question in three lumps
  row 18       Online Applied Maths, "awarded 1 but written 2"
  row 38       Bihar English, wrong MCQs crossed in the margin, off the page
CA sheet rows F40 and F41 (Corporate Law): "MCQs tick marking is misplaced".
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
from categories import commerce  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402

_UP = "Uday UP Board 11th Commerce 2027 (English + Hindi Medium)"
_MH = "Parishram Maharashtra HSC Commerce 2027 (English Medium)"
# Checking plans send a copy to the legacy renderer (teacher_layout.needs_legacy_layout).
_LEGACY = {"qb.9:0": {"award_scope": "section"}}


def _flat(text: str) -> str:
    return " ".join(text.split())


class TheStateBoardSmesStandard(unittest.TestCase):
    def setUp(self):
        self.rules = _flat(commerce.board_rules(_UP))

    def test_a_five_mark_answer_of_one_line_points_is_not_full_marks(self):
        self.assertIn("In an answer of 5 marks or more a point earns its full share only when "
                      "it is explained in two lines or more", self.rules)
        self.assertIn("(four or five such points: 3.5/5)", self.rules)

    def test_misspelt_subject_words_cost_marks_in_theory_answers_only(self):
        self.assertIn("cost half a mark in an answer with two or three such words and one mark "
                      "with four or more", self.rules)
        self.assertIn("Never in a one-word answer, an MCQ or an Accountancy figure", self.rules)
        self.assertIn('this replaces "never deduct for spelling"', self.rules)

    def test_english_remarks_give_the_reason(self):
        self.assertIn("Name the fault, then say what to write instead", self.rules)
        self.assertIn('An instruction with no reason ("Quote the words.") is not a remark.', self.rules)
        self.assertIn('This replaces "word limits are guidance"', self.rules)
        self.assertIn('"Below the word limit of 80 words.', self.rules)
        self.assertIn('"The conclusion is missing."', self.rules)

    def test_english_activities_are_marked_box_by_box(self):
        rules = _flat(commerce.board_rules(_MH))
        self.assertIn("names Maharashtra HSC", rules)
        self.assertIn('"lift" is not the lift-man', rules)
        self.assertIn("One right box of three on a 2-mark web is 0.5", rules)
        self.assertIn("the matching and the new words are two halves, not full or nothing", rules)
        self.assertIn('("d) O. Henry" where the key gives a) is a wrong pair', rules)
        self.assertIn("Each activity that lost marks gets its own remark", rules)

    def test_a_maths_activity_needs_the_activity_written_out(self):
        self.assertIn("box values written alone, without the activity's statements, earn "
                      "nothing", _flat(commerce.board_rules(_MH)))

    def test_bihar_keeps_its_own_block(self):
        self.assertEqual(commerce.board_rules("Uday Bihar Board 11th Commerce"),
                         commerce.BIHAR_BOARD_RULES)
        self.assertNotIn("subject words written wrongly", commerce.BIHAR_BOARD_RULES)


class TheCommerceRules(unittest.TestCase):
    def setUp(self):
        self.rules = _flat(commerce.COMMERCE_RULES)

    def test_a_list_of_values_is_checked_entry_by_entry(self):
        self.assertIn("checked entry by entry against the key: it earns its mark only when "
                      "every entry is right", self.rules)
        self.assertIn('("C13 is -5 and C21 is -1.")', self.rules)

    def test_a_proof_in_parts_and_its_counter_example(self):
        self.assertIn("A proof in parts (one-one and onto; reflexive, symmetric and transitive) "
                      "gives each part its own share", self.rules)
        self.assertIn("those pairs count wherever the student wrote them", self.rules)

    def test_a_block_crossed_out_diagonally_is_cancelled(self):
        self.assertIn("crossed out as a block with one or two diagonal strokes; such a block "
                      "is never ticked", self.rules)

    def test_a_long_statement_is_marked_item_by_item(self):
        self.assertIn("worth 6 marks or more is never marked as one criterion per account", self.rules)
        self.assertIn("Gross Profit, Net Profit and the Balance Sheet total are criteria of their "
                      "own, earned only when they equal the key's", self.rules)
        self.assertIn('("Opening stock is 89,500.")', self.rules)

    def test_they_reach_the_commerce_prompt(self):
        self.assertIn(commerce.COMMERCE_RULES, categories.get("commerce").prompt.extra_rules)


class CaHoldsAnMcqMarkedOnAnotherRow(unittest.TestCase):
    def test_the_codes_hold_a_ca_copy(self):
        import preflight
        ca = categories.get("ca")
        for code in ("mark-on-another-row", "objective-answers-share-a-row"):
            self.assertIn(code, ca.review.extra_hold_codes)
        self.assertFalse(ca.review.auto_release, "CA still goes to faculty review")
        self.assertTrue(hasattr(preflight, "SCORE_INTEGRITY_CODES"))


class TwoMcqsOnOneRow(unittest.TestCase):
    """F40's MCQ page: OCR never read "4)" and "7)", so Q4 and Q7 sat on Q5's row,
    rows 4 and 7 carried no mark and row 5 two ticks."""

    def repair(self, texts, anchors):
        import fitz
        from evidence_repair import repair_report_evidence
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        lines = [LineBox(f"P1L{k + 1}", text, (100, 100 + 40 * k, 330, 120 + 40 * k), 1)
                 for k, text in enumerate(texts)]
        questions = [QuestionMark(qid=f"Q{n}", score=1, max_score=1, page=1, y_fraction=.2,
                                  objective=True, chosen_option="B", anchor_line_id=lid)
                     for n, lid in anchors.items()]
        regions = {f"q{n}": {"qid": f"Q{n}", "status": "inferred", "pages": [1],
                             "line_ids": [lid], "label_line_ids": []} for n, lid in anchors.items()}
        report = GradeReport(total_score=len(questions), max_total=len(questions),
                             overall_remarks="", questions=questions, evidence_contract=True,
                             answer_regions=regions)
        changed = repair_report_evidence(report, [PageOCR(1, 595, 842, lines)], doc)
        return {q.qid: q.anchor_line_id for q in questions}, changed

    ROWS = ["1) (B) Board meeting", "(C) Ten days", "3) (A) Special resolution",
            "(D) All of these", "5) (B) Registrar", "6) (C) 21 days", "(A) Section 8"]

    def test_each_moves_to_the_free_row_between_its_neighbours(self):
        anchors, changed = self.repair(self.ROWS, {1: "P1L1", 2: "P1L2", 3: "P1L3", 4: "P1L5",
                                                   5: "P1L5", 6: "P1L6", 7: "P1L5"})
        self.assertEqual(anchors["Q5"], "P1L5", "the row's own number keeps it")
        self.assertEqual(anchors["Q4"], "P1L4")
        self.assertEqual(anchors["Q7"], "P1L7")
        self.assertTrue(any("shared row" in note for note in changed.get("Q4", [])), changed)

    def test_nothing_moves_when_the_free_row_is_not_one(self):
        rows = ["1) (B) Board meeting", "(C) Ten days", "(D) All of these",
                "(A) Section 8", "5) (B) Registrar"]
        anchors, _ = self.repair(rows, {1: "P1L1", 2: "P1L5", 5: "P1L5"})
        self.assertEqual(anchors["Q2"], "P1L5", "three free rows below Q1: no guess")
        self.assertEqual(anchors["Q5"], "P1L5")


class AVisualBoxAcrossTheRow(unittest.TestCase):
    """F40: a tick past the photo's right edge, level with row 4, whose "4)" OCR missed."""

    def repair(self, box_text_row):
        import fitz
        from evidence_repair import repair_report_evidence
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        lines = [LineBox("P1L1", "3) (A) Special resolution", (100, 100, 330, 120), 1),
                 LineBox("P1L2", box_text_row, (100, 140, 330, 160), 1),
                 LineBox("P1L3", "5) (B) Registrar", (100, 180, 330, 200), 1),
                 # The visual reader's box for Q4: the whole row, to the page's edge.
                 LineBox("P1V4", "(D)", (100, 141, 590, 159), 1)]
        questions = [QuestionMark(qid="Q3", score=1, max_score=1, page=1, y_fraction=.12,
                                  objective=True, chosen_option="A", anchor_line_id="P1L1"),
                     QuestionMark(qid="Q4", score=1, max_score=1, page=1, y_fraction=.17,
                                  objective=True, chosen_option="D", anchor_line_id="P1V4"),
                     QuestionMark(qid="Q5", score=1, max_score=1, page=1, y_fraction=.22,
                                  objective=True, chosen_option="B", anchor_line_id="P1L3")]
        regions = {"q3": {"qid": "Q3", "status": "located", "pages": [1], "line_ids": ["P1L1"]},
                   "q4": {"qid": "Q4", "status": "located", "pages": [1], "line_ids": ["P1V4"],
                          "source": "independent_visual_location"},
                   "q5": {"qid": "Q5", "status": "located", "pages": [1], "line_ids": ["P1L3"]}}
        report = GradeReport(total_score=3, max_total=3, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions=regions)
        repair_report_evidence(report, [PageOCR(1, 595, 842, lines)], doc)
        return questions[1].anchor_line_id

    def test_it_moves_onto_the_one_option_line_of_its_row(self):
        self.assertEqual(self.repair("(D) All of these"), "P1L2")

    def test_another_letter_on_that_row_is_not_its_answer(self):
        self.assertEqual(self.repair("(B) Ten days"), "P1V4")


class OrphanStepsInEveryCategory(unittest.TestCase):
    """Online Applied Maths Q9: two 1-mark steps, one circled 2 on one line."""

    def render(self, kd_line):
        import fitz
        import pdf_renderer as pr
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        texts = ["Solution 9", "Mean = 120/8 = 15", "Variance = 40/8 = 5", "SD = 2.24"]
        for k, text in enumerate(texts):
            page.insert_text((60, 116 + 60 * k), text, fontsize=16)
        s = 2.0
        lines = [LineBox(f"P1L{k + 1}", text, (60 * s, (100 + 60 * k) * s, 360 * s, (120 + 60 * k) * s), 1)
                 for k, text in enumerate(texts)]
        q = QuestionMark(qid="Q9", score=2.0, max_score=3, page=1, y_fraction=.15, attempted=True,
                         anchor_line_id="P1L2", mark_style="per_step", remark="SD is wrong.",
                         criteria=[Criterion(description="Mean", awarded=1, max=1, met=True,
                                             step_line_id="P1L2", anchor_quote="120/8 = 15"),
                                   Criterion(description="Variance", awarded=1, max=1, met=True,
                                             step_line_id=kd_line, anchor_quote="garbled 4o/8"),
                                   Criterion(description="SD", awarded=0, max=1, met=False,
                                             step_line_id="P1L4", anchor_quote="SD = 2.24")])
        report = GradeReport(total_score=2, max_total=3, overall_remarks="", evidence_contract=True,
                             whole_total=False, questions=[q], verified_step_boxes=dict(_LEGACY),
                             answer_regions={"q9": {"qid": "Q9", "status": "located", "pages": [1],
                                                    "line_ids": ["P1L1", "P1L2", "P1L3", "P1L4"]}})
        pr.build_evaluated_pdf(doc.tobytes(), "copy.pdf", [], report,
                               ocr_pages=[PageOCR(1, 1200, 1600, lines)], mark_scale=0.8)
        return [r for r in report.render_manifest if r.get("qid") == "Q9" and r.get("role") == "step"]

    # A step ticked on its own cited line outside CA: test_ca_fm_faculty_1023.
    def test_a_step_with_no_line_joins_a_mark_that_lists_it(self):
        for row in self.render(""):
            if row.get("score"):
                expected = sum(1.0 for i in row.get("criteria") or [] if i in (0, 1))
                self.assertEqual(row["score"], expected, row)


class AWrongMcqIsCrossedOnItsOwnRow(unittest.TestCase):
    """Bihar English Q2: no clear paper on the row, so its cross went to the margin lane."""

    def test_the_cross_stays_on_the_page(self):
        import fitz
        import pdf_renderer as pr
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        texts = ["1) (b) Animals in prison", "2) (c) Nehru wrote it in jail"]
        for k, text in enumerate(texts):
            page.insert_text((60, 116 + 40 * k), text, fontsize=16)
        s = 2.0
        lines = [LineBox(f"P1L{k + 1}", text, (60 * s, (100 + 40 * k) * s, 400 * s, (120 + 40 * k) * s), 1)
                 for k, text in enumerate(texts)]
        questions = [QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=.13,
                                  objective=True, chosen_option="B", anchor_line_id="P1L1"),
                     QuestionMark(qid="Q2", score=0, max_score=1, page=1, y_fraction=.18,
                                  objective=True, chosen_option="C", anchor_line_id="P1L2",
                                  remark="Correct answer is (A).")]
        report = GradeReport(total_score=1, max_total=2, overall_remarks="", evidence_contract=True,
                             questions=questions, verified_step_boxes=dict(_LEGACY))
        real_verdict, real_strip = pr._draw_mcq_verdict, pr._draw_strip_entry
        lane = []

        def no_room(doc_, page_, qid, *args, **kwargs):
            if qid == "Q2":
                return False
            return real_verdict(doc_, page_, qid, *args, **kwargs)

        def strip(page_, qid, *args, **kwargs):
            lane.append((qid, kwargs.get("verdict", "")))
            return real_strip(page_, qid, *args, **kwargs)
        with patch.object(pr, "_draw_mcq_verdict", no_room), patch.object(pr, "_draw_strip_entry", strip):
            pr.build_evaluated_pdf(doc.tobytes(), "copy.pdf", [], report,
                                   ocr_pages=[PageOCR(1, 1200, 1600, lines)], mark_scale=0.8)
        crosses = [r for r in report.render_manifest if r.get("qid") == "Q2" and r.get("role") == "answer"]
        self.assertEqual(len(crosses), 1, report.render_manifest)
        self.assertEqual(crosses[0]["line_id"], "P1L2")
        self.assertLessEqual(crosses[0]["bbox"][2], 600, "on the student's page, not in the lane")
        self.assertNotIn(("Q2", "wrong"), lane, "the margin lane draws no second cross")


if __name__ == "__main__":
    unittest.main()
