# -*- coding: utf-8 -*-
"""Review of the CA FM MT01 copies (sheet rows 22-26, graded 21 Oct 2026), in code.

Case study parts (A)-(E) answered with numbered options had every tick a row off;
right figures misread by OCR were docked; struck-out work and partial computation
steps were mis-marked.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import evidence_repair as er  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402


class TheRowReaders(unittest.TestCase):
    def test_lettered_part_rows_as_the_students_wrote_them(self):
        rows = {"A=3": "a", "(A) (3) 336000": "a", "B - 1": "b", "(c) 3 (4)": "c", "1) & A & (1)": "a",
                "& B & (1)": "b", "C": "c", "E (3)": "e", r"\( (E)(2) \) Jurease the financial": "e",
                "(d) (3)": "d"}
        for text, letter in rows.items():
            self.assertEqual((er._letter_row(text) or (None, None))[1], letter, text)
        for text in ("a (1) Patters for raring additions", "A company is a person", "(AA) (3) 336000",
                     "(2) Increase the financial leverage"):
            self.assertIsNone(er._letter_row(text), text)
        self.assertEqual(er._letter_part("Q1.A"), (1, "a"))
        self.assertEqual(er._letter_part("Q1(c)"), (1, "c"))
        self.assertIsNone(er._letter_part("Q18.ii"))
        self.assertIsNone(er._letter_part("Q3"))

    def test_numbered_mcq_rows(self):
        rows = {"3 & in & 26.25 & (11) 142": 3, "4 & 2 & 10.53\\% &": 4, r"\( 3(2) \)": 3, "4 (2)": 4,
                r"\text { (2) }-1": 2, "(3)-1": 3, "2 (1)": 2, "Q3 (1)": 3}
        for text, number in rows.items():
            self.assertEqual(er._leading_number(text), number, text)
        self.assertIsNone(er._leading_number("A=3"))


class CaseStudyRows(unittest.TestCase):
    """CA FM 8bc28fde: "A=3 / B=1 / C=4 / D=3 / E=2" written, OCR kept A to D in one block
    whose box leaves out the letters, and the visual reader's boxes sat a row below."""

    def build(self, whole_total=True):
        import fitz
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        for k, text in enumerate(["A = 3", "B = 1", "C = 4", "D = 3", "E = 2"]):
            page.insert_text((60, 120 + 40 * k), text, fontsize=22)
        for k, text in enumerate(["(2) - 1", "(3) - 1", "(4) - 2"]):
            page.insert_text((60, 360 + 40 * k), text, fontsize=22)
        s = 2.0       # OCR pixels per point
        block = LineBox("P1L1", "\\[\n\\begin{array}{l}\nA=3 \\\\\nB=1 \\\\\nC=4 \\\\\nD=3\n\\end{array}\n\\]",
                        (110 * s, 96 * s, 200 * s, 290 * s), 1, kind="math")
        tail = LineBox("P1L2", "\\[\n\\begin{array}{l}\n\\text { (2) }-1 \\\\\n\\text { (3) }-1 \\\\\n"
                       "\\text { (4) }-2\n\\end{array}\n\\]", (110 * s, 336 * s, 200 * s, 452 * s), 1, kind="math")
        # The visual reader's boxes, one row lower than the answers they name.
        boxes = [LineBox(f"P1L{3 + k}", str(v), (120 * s, (140 + 40 * k) * s, 200 * s, (165 + 40 * k) * s), 1)
                 for k, v in enumerate([1, 4, 3, 2, 1, 1, 2])]
        ocr = [PageOCR(1, 1200, 1600, [block, tail] + boxes)]
        qids = ["Q1.B", "Q1.C", "Q1.D", "Q1.E", "Q2", "Q3", "Q4"]
        questions = [QuestionMark(qid="Q1.A", score=2, max_score=2, page=1, y_fraction=.1, objective=True,
                                  attempted=True, chosen_option="C", correct_option="C", is_correct=True,
                                  anchor_line_id="P1L1#r0", mark_style="single", criteria=[])]
        regions = {}
        for qid, box in zip(qids, boxes):
            questions.append(QuestionMark(qid=qid, score=2, max_score=2, page=1, y_fraction=.3, objective=True,
                                          attempted=True, chosen_option="A", correct_option="A", is_correct=True,
                                          anchor_line_id=box.line_id, mark_style="single", criteria=[]))
            regions[qid.lower()] = {"qid": qid, "status": "located", "line_ids": [box.line_id], "pages": [1],
                                    "source": "independent_visual_location"}
        report = GradeReport(total_score=16, max_total=16, overall_remarks="", evidence_contract=True,
                             whole_total=whole_total, questions=questions, answer_regions=regions,
                             objective_visual_verified_qids=qids)
        return doc, ocr, report

    def centre_of(self, ocr, lid):
        line = next(l for l in ocr[0].lines if l.line_id == lid)
        return (line.bbox[1] + line.bbox[3]) / 2 / 2.0      # back to points

    def test_each_part_and_each_numbered_mcq_takes_its_own_row(self):
        doc, ocr, report = self.build()
        changed = er.repair_report_evidence(report, ocr, doc)
        written = {"Q1.B": 152, "Q1.C": 192, "Q1.D": 232, "Q1.E": 272, "Q2": 352, "Q3": 392, "Q4": 432}
        for q in report.questions[1:]:
            centre = self.centre_of(ocr, q.anchor_line_id)
            self.assertLess(abs(centre - written[q.qid]), 14, (q.qid, q.anchor_line_id, centre, changed.get(q.qid)))

    def test_other_categories_are_unchanged(self):
        doc, ocr, report = self.build(whole_total=False)
        er.repair_report_evidence(report, ocr, doc)
        self.assertEqual(report.questions[1].anchor_line_id, "P1L3")


class NoMarkOnTheNextAnswersRow(unittest.TestCase):
    """CA FM b9cfb6d3: an MCQ mark with no room on its own row went onto the next
    question's row ("(C) (4) 1.46", "Q3 (1)")."""

    def line(self, lid, text, y):
        return {"lid": lid, "page": 1, "x0": 40, "x1": 200, "y0": y, "y1": y + 18, "text": text}

    def test_a_list_row_or_another_answers_row_is_not_a_continuation(self):
        import pdf_renderer as pr
        report = GradeReport(total_score=0, max_total=4, overall_remarks="", questions=[
            QuestionMark(qid="Q1.B", score=0, max_score=2, page=1, y_fraction=.1, attempted=True, objective=True,
                         anchor_line_id="P1L1"),
            QuestionMark(qid="Q9", score=0, max_score=2, page=1, y_fraction=.2, attempted=True, objective=True,
                         anchor_line_id="P1L4")])
        token = pr._RENDER_REPORT.set(report)
        try:
            anchor = self.line("P1L1", "(B) (2) 1.29", 100)
            for text in ("(C) (4) 1.46", "Q3 (1)", "2 (1)", "B - 1"):
                self.assertIsNone(pr._answer_continuation_row(anchor, {"P1L1": anchor, "P1L2": self.line("P1L2", text, 121)}), text)
            other = self.line("P1L4", "statement II are", 121)
            self.assertIsNone(pr._answer_continuation_row(anchor, {"P1L1": anchor, "P1L4": other}))
            words = self.line("P1L3", "correct.", 121)
            self.assertEqual(pr._answer_continuation_row(anchor, {"P1L1": anchor, "P1L3": words})["lid"], "P1L3",
                             "an answer's own next line still carries its mark")
        finally:
            pr._RENDER_REPORT.reset(token)


class MisreadFigures(unittest.TestCase):
    """CA FM 79fb40f0: "Kp = 8/45 = 17.78%" read "8/45 + 12.781"; b9cfb6d3: "8 x 43.75 = 350"
    read "8 x 43.35 = 350"."""

    def report(self, description, evidence, text):
        from mathpix import LineBox, PageOCR
        q = QuestionMark(qid="QB3.B", score=1, max_score=3, page=1, y_fraction=.3, attempted=True,
                         anchor_line_id="P1L1", remark=evidence, mark_style="per_step",
                         criteria=[Criterion(description="Ke = 8.75%", awarded=1, max=1, met=True,
                                             step_line_id="P1L1"),
                                   Criterion(description=description, awarded=0, max=2, met=False,
                                             evidence=evidence, step_line_id="P1L1")])
        report = GradeReport(total_score=1, max_total=3, overall_remarks="", questions=[q])
        ocr = [PageOCR(1, 400, 300, [LineBox("P1L1", text, (10, 100, 390, 140), 1)])]
        return report, ocr

    def png(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
        return buf.getvalue()

    def run_with(self, report, ocr, reading):
        from value_recheck import restore_misread_steps
        return restore_misread_steps(report, [self.png()], ocr, google_token="t", task_id="k",
                                     call=lambda *a, **k: reading)

    def test_a_step_docked_for_a_misread_figure_gets_its_marks_back(self):
        report, ocr = self.report("Kp = PD/P0 = 8/45 = 17.78%",
                                  "Incorrectly adds 12.781. Correct preference-share cost is 8/45 = 17.78%.",
                                  r"ii) \( k_{p}=\frac{8}{45}+12.781 \)")
        report.verified_step_boxes = {"qb.3.b:1": {
            "line_id": "P1L1", "quote": "Kp", "verdict": "wrong", "deduction_reason": "Incorrectly adds 12.781.",
            "checking_points": [{"line_id": "P1L1", "verdict": "wrong", "reason": "adds 12.781"}]}}
        notes = self.run_with(report, ocr, "Kp = PD(1-t)/Np = 8/45 = 17.78%")
        q = report.questions[0]
        self.assertEqual((q.score, q.criteria[1].awarded, q.criteria[1].met), (3, 2.0, True))
        box = report.verified_step_boxes["qb.3.b:1"]
        self.assertEqual((box["verdict"], box["deduction_reason"]), ("correct", ""), "drawn as a tick")
        self.assertEqual(box["checking_points"][0]["verdict"], "correct")
        self.assertEqual(report.total_score, 3)
        self.assertNotIn("12.781", q.remark)
        self.assertIn('"12.781" was read again from the page as 17.78', notes[0])

    def test_rounding_counts_and_the_quoted_figure_on_the_page_keeps_the_deduction(self):
        report, ocr = self.report("Kp = PD/P0 = 8/45 = 17.78%", "Preference share cost should be 17.78%, not 17.27%.",
                                  "Kp = 8/45 = 17.27")
        self.run_with(report, ocr, "Kp = D/NP = 8/45 = 17.77")
        self.assertEqual(report.questions[0].score, 3, "17.77 is 17.78 to rounding")
        report, ocr = self.report("Kp = PD/P0 = 8/45 = 17.78%", "Preference share cost should be 17.78%, not 17.27%.",
                                  "Kp = 8/45 = 17.27")
        self.assertEqual(self.run_with(report, ocr, "Kp = 8/45 = 17.27"), [])
        self.assertEqual(report.questions[0].score, 1)
        report, ocr = self.report("Kp = PD/P0 = 8/45 = 17.78%", "Preference share cost should be 17.78%, not 17.27%.",
                                  "Kp = 8/45 = 17.27")
        self.run_with(report, ocr, "Kp = 8/45 = 17.71")
        self.assertEqual(report.questions[0].score, 1, "a third reading that agrees with nobody changes nothing")

    def test_steps_that_name_two_results_or_no_wrong_figure_are_left_alone(self):
        from value_recheck import step_result
        self.assertEqual(step_result("Kp = PD/P0 = 8/45 = 17.78%"), "17.78")
        self.assertEqual(step_result("Market price per share = EPS × PE = 43.75 × 8 = 350"), "350")
        self.assertEqual(step_result("EPS computation: PAT 5,46,875 / 12,500 shares = EPS 43.75"), "43.75")
        self.assertEqual(step_result("Combined Leverage: EBT = 7,81,250, Total Interest 4,68,750, "
                                     "Amount of bank loan = 21,09,375"), "")
        report, ocr = self.report("Kp = PD/P0 = 8/45 = 17.78%", "Required calculation is not shown.", "Kp = 8/45")
        self.assertEqual(self.run_with(report, ocr, "17.78"), [])

    def test_it_runs_on_ca_papers_before_the_quoted_value_check(self):
        import inspect
        import pipeline
        source = inspect.getsource(pipeline)
        restore = source.index("restore_misread_steps(\n")
        self.assertIn('and _category_of(cfg) == "ca":', source[restore - 400: restore])
        self.assertLess(restore, source.index("disputed = recheck_quoted_values("))


class TheCaRules(unittest.TestCase):
    def setUp(self):
        self.rules = " ".join(categories.get("ca").prompt.extra_rules.split())

    def test_struck_out_work_partial_steps_and_lettered_case_studies(self):
        self.assertIn("Working the student struck out is cancelled and earns nothing", self.rules)
        self.assertIn("two diagonal strokes drawn across a block", self.rules)
        self.assertIn("A computation step is marked by what is right in it", self.rules)
        self.assertIn("A valid alternative method that reaches the key's figures earns the whole step", self.rules)
        self.assertIn("the letter on a row is the part and the number is the option", self.rules)


if __name__ == "__main__":
    unittest.main()
