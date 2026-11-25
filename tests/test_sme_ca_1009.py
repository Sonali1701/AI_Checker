# -*- coding: utf-8 -*-
"""SME review of the checked CA copies (sheet "Fresh Sample copy", 9 Oct 2026), in code.

* Auditing, both copies: "In Q 4a marking should also be mentioned". Q4(B) and Q4(C)
  showed their totals, Q4(A) only showed (i) and (ii). A part the paper splits further
  now shows its own total above its first sub-part.
* Auditing a735dd51: "Q6 c2 marks are improperly given. Each point carries 0.5 marks
  but in one point double marking is done." CA rule 4: one point earns at most its
  share, its name and its explanation together.
* Taxation 9976fe04 / f2f7d2be: a wrong standard deduction, a cess on a wrong tax and a
  wrong gross salary were ticked and paid. CA rule 5: a computed amount earns its step
  only when the amount is right; no error carried forward in taxation.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import grader  # noqa: E402
import pdf_renderer as pr  # noqa: E402
from test_teacher_layout import _build, _c, _q, _report, _sheet  # noqa: E402
from wording import part_totals, report_part_totals  # noqa: E402


class PartTotals(unittest.TestCase):
    def test_a_split_part_gets_its_total_on_its_first_sub_part(self):
        out = part_totals([("QB4.A.i", "Q4(A)(i)", 1.25, 1.5, True),
                           ("QB4.A.ii", "Q4(A)(ii)", 1, 1, True),
                           ("QB4.B", "Q4(B)", 2.5, 2.5, True)])
        self.assertEqual(out, {"QB4.A.i": "Q4(A)  2.25/2.5"})

    def test_an_unanswered_sub_part_still_counts_in_the_part_total(self):
        out = part_totals([("QB4.A.i", "Q4(A)(i)", 1.25, 1.5, True),
                           ("QB4.A.ii", "Q4(A)(ii)", 0, 1, True)])
        self.assertEqual(out, {"QB4.A.i": "Q4(A)  1.25/2.5"})

    def test_whole_questions_mcqs_and_lone_sub_parts_get_nothing(self):
        self.assertEqual(part_totals([("Q1.a", "Q1(a)", 1, 2, True),
                                      ("Q1.b", "Q1(b)", 2, 2, True)]), {},
                         "a question number with parts is not a part")
        self.assertEqual(part_totals([("MCQ.1", "MCQ 1", 1, 1, True),
                                      ("MCQ.2", "MCQ 2", 0, 1, True)]), {})
        self.assertEqual(part_totals([("QB4.A.i", "Q4(A)(i)", 1, 1.5, True),
                                      ("QB4.B", "Q4(B)", 2, 2.5, True)]), {},
                         "one sub-part alone needs no part total")
        self.assertEqual(part_totals([("QB4.A", "Q4(A)", 2, 2.5, True),
                                      ("QB4.A.i", "Q4(A)(i)", 1, 1.5, True),
                                      ("QB4.A.ii", "Q4(A)(ii)", 1, 1, True)]), {},
                         "a part the report already marks on its own row")

    def test_an_uncounted_alternative_stays_out_of_the_total(self):
        out = part_totals([("Q7.b.i", "Q7(b)(i)", 2, 3, True),
                           ("Q7.b.ii", "Q7(b)(ii)", 3, 3, False),
                           ("Q7.b.iii", "Q7(b)(iii)", 1, 2, True)])
        self.assertEqual(out, {"Q7.b.i": "Q7(b)  3/5"})

    def test_the_part_label_keeps_the_copy_s_own_wording(self):
        out = part_totals([("QB4.A.i", "Part B Q4(A)(i)", 1, 1.5, True),
                           ("QB4.A.ii", "Part B Q4(A)(ii)", 1, 1, True)])
        self.assertEqual(out, {"QB4.A.i": "Part B Q4(A)  2/2.5"})


class MarginShowsThePartTotal(unittest.TestCase):
    def test_teacher_layout_writes_q4a_above_q4a_i(self):
        rows = [("P1L1", "(A) (i) Factors considered by CA Taman", 60, 420),
                ("P1L2", "Assessed risk of material misstatement", 60, 420),
                ("P1L3", "(ii) She should revise the audit plan", 60, 420),
                ("P1L4", "(B) Self interest threat financial benefit", 60, 420)]
        doc, ocr = _sheet(rows)
        report = _report([
            _q("QB4.A.i", 1.25, 1.5, 1, "P1L1", [_c(0.75, 0.75, "P1L1"), _c(0.5, 0.75, "P1L2")],
               remark="Say that direction and review vary with these factors."),
            _q("QB4.A.ii", 1, 1, 1, "P1L3", [_c(1, 1, "P1L3")]),
            _q("QB4.B", 2.5, 2.5, 1, "P1L4", [_c(2.5, 2.5, "P1L4")])])
        out, _ = _build(doc, ocr, report)
        text = " ".join(out[0].get_text().split())
        self.assertIn("Q4(A) 2.25/2.5", text)
        self.assertIn("Q4(A)(i) 1.25/1.5", text)
        self.assertEqual(text.count("Q4(A) 2.25/2.5"), 1, "the part total is written once")
        self.assertLess(text.index("Q4(A) 2.25/2.5"), text.index("Q4(A)(i) 1.25/1.5"))
        self.assertEqual(report_part_totals(report), {"QB4.A.i": "Q4(A)  2.25/2.5"})

    def test_legacy_strip_helper_reads_the_same_rule(self):
        report = _report([_q("QB4.A.i", 1.25, 1.5, 1, "P1L1"), _q("QB4.A.ii", 0, 1, 1, "P1L2")])
        self.assertEqual(pr._part_total_head(report, "QB4.A.i"), "Q4(A)  1.25/2.5")
        self.assertEqual(pr._part_total_head(report, "QB4.A.ii"), "")
        self.assertEqual(pr._part_total_head(None, "QB4.A.i"), "")


class CorrectedCopyToolShowsThePartTotal(unittest.TestCase):
    def test_teacher_mark_writes_the_part_total(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "tools", "teacher_mark"))
        from teacher_mark import Sheet
        tmp = tempfile.mkdtemp()
        raw = os.path.join(tmp, "raw.pdf")
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        lines = []
        for i, text in enumerate(["(A) (i) Factors considered", "Assessed risk",
                                  "(ii) Revise the audit plan", "(B) Self interest threat"]):
            y = 120 + 40 * i
            page.insert_text((60, y), text, fontsize=14)
            lines.append({"line_id": f"P1L{i + 1}", "bbox": [60, y - 14, 400, y + 4], "text": text})
        doc.save(raw)
        ocr = [{"page": 1, "image_width": 595, "image_height": 842, "lines": lines}]
        plan = {"subject": "Auditing", "copy": "raw.pdf", "whole_total": True,
                "remarks": "Good lists of points. Tie each point to the case.",
                "questions": [
                    {"qid": "QB4.A.i", "label": "Q4(A)(i)", "max": 1.5, "score": 1.25, "at": "P1L1",
                     "marks": [["tick", "P1L1", 0.75], ["tick", "P1L2", 0.5]],
                     "reason": "Say that direction and review vary with these factors."},
                    {"qid": "QB4.A.ii", "label": "Q4(A)(ii)", "max": 1, "score": 1, "at": "P1L3",
                     "marks": [["tick", "P1L3", 1]]},
                    {"qid": "QB4.B", "label": "Q4(B)", "max": 2.5, "score": 2.5, "at": "P1L4",
                     "marks": [["tick", "P1L4", 2.5]]}]}
        out = os.path.join(tmp, "out.pdf")
        Sheet(raw, ocr, plan).build(out)
        text = " ".join(fitz.open(out)[0].get_text().split())
        self.assertIn("Q4(A) 2.25/2.5", text)
        self.assertIn("Q4(A)(i) 1.25/1.5", text)


class CaRules(unittest.TestCase):
    def test_point_share_and_taxation_amount_rules_reach_the_ca_prompt(self):
        rules = categories.get("ca").prompt.extra_rules
        self.assertIn("one point the student writes earns at most that share", rules)
        self.assertIn("name and its explanation or purpose line are ONE point", rules)
        self.assertIn("A right rate or method applied to\n   a wrong figure earns nothing", rules)
        self.assertIn("overrides error carried forward for taxation", rules)
        prompt = grader.build_system_prompt("Taxation", "CA Intermediate", "professional",
                                            category="ca")
        self.assertIn("RULES FOR THIS EXAM CATEGORY", prompt)
        self.assertIn("cess at 4 percent on a wrong tax", prompt)

    def test_other_categories_do_not_get_the_ca_rules(self):
        for other in ("school", "commerce", "vidyapeeth"):
            self.assertNotIn("overrides error carried forward for taxation",
                             categories.get(other).prompt.extra_rules)


class NestedChoiceReading(unittest.TestCase):
    """CA Inter Taxation MTS 01 (live, 9 Oct 2026): "answer any two of Q2 to Q4", and
    Q4(b) has its own OR. The reader's recheck listed only one Q4(b) alternative in
    the outer Q4 option, the choice graph refused the overlap, and the paper could
    not be graded at all. This is that reply, verbatim in structure."""
    REPLY = {
        "items": [{"qid": q, "max": m} for q, m in (
            ("Q1", 2), ("Q2", 2), ("Q3", 2), ("Q4", 2), ("Q5", 2), ("Q6", 2), ("Q7", 2), ("Q8", 1),
            ("Q1a", 5), ("Q1b", 5), ("Q1c", 5), ("Q2a", 6), ("Q2b", 4), ("Q3a", 5), ("Q3b", 5),
            ("Q4a", 6), ("Q4b.alt1", 4), ("Q4b.alt2", 4))],
        "choice_groups": [
            {"group_id": "choice-subjective-q2-q3-q4", "select_count": 2, "options": [
                {"option_id": "q2", "question_ids": ["Q2a", "Q2b"]},
                {"option_id": "q3", "question_ids": ["Q3a", "Q3b"]},
                {"option_id": "q4", "question_ids": ["Q4a", "Q4b.alt1"]}]},
            {"group_id": "choice-q4b", "select_count": 1, "options": [
                {"option_id": "alt1", "question_ids": ["Q4b.alt1"]},
                {"option_id": "alt2", "question_ids": ["Q4b.alt2"]}]}],
        "allocation_issues": []}

    def test_the_outer_option_gets_both_sides_of_the_inner_or(self):
        from marks_attestation import choice_total_gap
        scheme = grader.parse_strict_marks_scheme(json.dumps(self.REPLY))
        outer = next(g for g in scheme.choice_groups if g.group_id == "choice-subjective-q2-q3-q4")
        self.assertEqual(outer.options[2].question_ids, ["Q4a", "Q4b.alt1", "Q4b.alt2"])
        self.assertEqual(outer.contribution_marks, 20)
        self.assertFalse(choice_total_gap(scheme, 50), "15 MCQ + 15 Q1 + 20 = 50")

    def test_a_small_or_is_never_grown_from_the_big_rule(self):
        scheme = grader.parse_strict_marks_scheme(json.dumps(self.REPLY))
        inner = next(g for g in scheme.choice_groups if g.group_id == "choice-q4b")
        self.assertEqual([o.question_ids for o in inner.options], [["Q4b.alt1"], ["Q4b.alt2"]])

    def test_an_inner_or_split_across_two_outer_options_still_fails(self):
        reply = json.loads(json.dumps(self.REPLY))
        reply["choice_groups"][0]["options"][1]["question_ids"] = ["Q3a", "Q3b", "Q4b.alt2"]
        with self.assertRaises(grader.MarksExtractionError):
            grader.parse_strict_marks_scheme(json.dumps(reply))


class UnreadableMcqOnCaPaper(unittest.TestCase):
    """CA live check (9 Oct 2026): on unreadable MCQ rows the key-aware grading pass
    reported the key's own letter, so blank rows earned marks and ticks."""
    PAPER = ("Q1\nAgricultural income is\n(A) Rs 1,00,000\n(B) Rs 1,75,000\n(C) Rs 2,00,000\n(D) Nil\n"
             "Q2\nDeduction allowed is\n(A) Rs 10,000\n(B) Rs 12,000\n(C) Rs 24,000\n(D) Rs 30,000\n")

    def _run(self, exam_type, lines, observations=None):
        from exam_package import build_exam_package
        from grader import GradeReport, MarksItem, MarksScheme, QuestionMark
        from mathpix import LineBox, PageOCR
        from strict_grading import apply_official_objective_evidence, reconcile_report_to_package
        scheme = MarksScheme(total=4, items=[MarksItem(qid="Q1", max=2), MarksItem(qid="Q2", max=2)])
        package = build_exam_package(
            exam_type=exam_type, student_class="CA Intermediate" if exam_type == "professional" else "9",
            subject="Taxation", level="", paper="", attempt="", applicable_law_date=None, total_marks=4,
            question_paper=b"p", answer_key=b"k", key_source="upload", marks_scheme=scheme,
            scheme_source="text", question_paper_text=self.PAPER,
            answer_key_text="Answer Key\nQ1\n(B)\nQ2\n(C)\n")
        report = GradeReport(total_score=0, max_total=4, overall_remarks="", questions=[
            QuestionMark(qid=f"Q{n}", score=0, max_score=2, page=1, y_fraction=0.2 * n,
                         anchor_line_id=f"P1L{n}", chosen_option=choice)
            for n, choice in zip((1, 2), ("B", "C"))])
        report.evidence_contract = True
        pages = [PageOCR(page=1, image_width=800, image_height=1000, lines=[
            LineBox(f"P1L{i + 1}", text, (60, 40 + 40 * i, 700, 70 + 40 * i), 1)
            for i, text in enumerate(lines)])]
        report = reconcile_report_to_package(report, package)
        notes = apply_official_objective_evidence(
            report, package, pages, visual_choices=observations or {},
            question_paper_text=self.PAPER, allow_unconfirmed=True)
        return report, notes, package

    def test_a_blank_row_does_not_take_the_key_letter_on_a_ca_paper(self):
        report, notes, package = self._run("professional", ["1.", "2."])
        self.assertEqual(package.department, "ca")
        self.assertEqual([q.score for q in report.questions], [0.0, 0.0])
        self.assertEqual(report.objective_unconfirmed_qids, ["Q1", "Q2"])
        self.assertTrue(any("scored 0" in n for n in notes), notes)

    def test_a_key_blind_reading_still_counts(self):
        from grader import ObjectiveChoiceObservation
        report, _, _ = self._run("professional", ["1.", "2."], {
            "q1": ObjectiveChoiceObservation(qid="Q1", chosen_option="B", clear=True)})
        self.assertEqual([q.score for q in report.questions], [2.0, 0.0])

    def test_school_papers_keep_the_examiners_reading(self):
        report, _, package = self._run("school", ["1.", "2."])
        self.assertNotEqual(package.department, "ca")
        self.assertEqual([q.score for q in report.questions], [2.0, 2.0])


class NumberedOptionKey(unittest.TestCase):
    """CA Inter CMA keys print MCQ answers as option numbers ("1. (A) (2)"); all 15
    claims were refused on 9 Oct 2026, so the paper's MCQs were marked as prose."""
    KEY = "Part - A\nAnswer Key\n1.\n(A)\n(2)\n\n(B)\n(3)\n2.\n(1)\nHints and Solution\n"

    def _package(self):
        from exam_package import build_exam_package
        from grader import MarksItem, MarksScheme
        scheme = MarksScheme(total=6, items=[MarksItem(qid="Q1.A", max=2), MarksItem(qid="Q1.B", max=2),
                                             MarksItem(qid="Q2", max=2)])
        return build_exam_package(
            exam_type="professional", student_class="CA Intermediate", subject="Cost and Management Accounting",
            level="", paper="", attempt="", applicable_law_date=None, total_marks=6,
            question_paper=b"p", answer_key=b"k", key_source="upload", marks_scheme=scheme,
            scheme_source="ai_verified")

    def test_numbered_options_are_read_as_letters(self):
        from key_answers import KeyAnswer, KeyAnswerSheet, verify_key_answers
        sheet = KeyAnswerSheet(items=[
            KeyAnswer(qid="Q1.A", objective=True, option="B", quote="1. (A) (2)"),
            KeyAnswer(qid="Q1.B", objective=True, option="C", quote="(B) (3)"),
            KeyAnswer(qid="Q2", objective=True, option="A", quote="2. (1)")])
        self.assertEqual(verify_key_answers(sheet, self.KEY, self._package()),
                         {"Q1.A": "B", "Q1.B": "C", "Q2": "A"})

    def test_the_sub_question_label_is_not_taken_as_the_answer(self):
        from key_answers import KeyAnswer, KeyAnswerSheet, verify_key_answers
        sheet = KeyAnswerSheet(items=[KeyAnswer(qid="Q1.A", objective=True, option="A", quote="1. (A) (2)")])
        self.assertEqual(verify_key_answers(sheet, self.KEY, self._package()), {})


class InventorySeesThePaper(unittest.TestCase):
    """A CA package has no question descriptions, so the inventory could not file an
    answer by what it answers; it now gets the paper's text (Luna, cost unchanged)."""

    def test_the_prompt_carries_the_question_paper(self):
        from answer_inventory import build_inventory_prompt, inventory_model_for
        package = NumberedOptionKey()._package()
        prompt = build_inventory_prompt(package, [], "Q1(b) Explain perpetual succession. (7 Marks)")
        self.assertIn("=== QUESTION PAPER", prompt)
        self.assertIn("perpetual succession", prompt)
        self.assertNotIn("=== QUESTION PAPER", build_inventory_prompt(package, []))
        self.assertEqual(inventory_model_for("professional"), "gpt-5.6-luna-ard-content")
        self.assertEqual(inventory_model_for("school"), "gpt-5.6-luna-ard-content")


class PartLabelThenOption(unittest.TestCase):
    """CA Inter CMA Master Test 04 (9 Oct 2026): "d) 3) 1185.17..." is part (d), option 3.
    Every row of a readable MCQ page came back "could not be read"."""

    def test_the_option_after_the_part_label_is_read(self):
        from strict_grading import _objective_choice_from_line as read
        cases = {r"a). 2) \( 18.5,13.875,9.25 \)": "B", "b) 2) 520 units": "B",
                 r"d) 3) \( 1185.1777,2562 \)": "C", r"E) 4) \( 118.5,93.875 .49 .25 \)": "D",
                 "a) i) 21575500": "A", "b) 4) 234347 wo": "D"}
        for line, letter in cases.items():
            self.assertEqual(read(line), letter, line)

    def test_ambiguous_and_old_forms_are_unchanged(self):
        from strict_grading import _objective_choice_from_line as read
        self.assertEqual(read("2) 217.99"), "", "question 2 or option 2: left to the value match")
        self.assertEqual(read("Answer. 1"), "")
        self.assertEqual(read("6) (3) is negative"), "C")
        self.assertEqual(read("2) (A) A and R"), "A")


if __name__ == "__main__":
    unittest.main()
