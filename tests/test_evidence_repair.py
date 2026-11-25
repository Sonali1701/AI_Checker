# -*- coding: utf-8 -*-
"""The evidence repair pass: what the grader cited beats what the map guessed.

Each case reproduces a defect measured on the SST review batch of 2026-09-16 (see
evidence_repair.py). Run with the other suites via tools/selftest.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from evidence_repair import repair_report_evidence
from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR


def _doc(pages=1, width=595, height=842):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    return doc


class EvidenceRepairTests(unittest.TestCase):
    def test_guessed_region_on_another_page_is_trimmed_to_the_cited_line(self):
        # 2546c5cf: "c. Intermountain" (P1L14) was mapped to the last line of page 1
        # plus the first lines of page 2, another question's answer.
        ocr = [PageOCR(1, 595, 842, [LineBox("P1L14", "c. Intermountain", (60, 300, 200, 318), 1),
                                     LineBox("P1L30", "The Arthashastra tells us", (60, 700, 400, 718), 1)]),
               PageOCR(2, 595, 842, [LineBox("P2L1", "level as well as provincial", (60, 60, 400, 78), 2),
                                     LineBox("P2L2", "Structural plains are formed", (60, 100, 400, 118), 2)])]
        q = QuestionMark(qid="Q6.c", score=.5, max_score=1, page=1, y_fraction=.8, anchor_line_id="P1L30",
                         criteria=[Criterion(description="Intermontane", awarded=.5, max=1, met=False,
                                             step_line_id="P1L14", anchor_quote="Intermountain")])
        report = GradeReport(total_score=.5, max_total=1, overall_remarks="", questions=[q],
                             evidence_contract=True, answer_regions={
                                 "q6.c": {"qid": "Q6.c", "status": "located", "confidence": .28,
                                          "pages": [1, 2], "line_ids": ["P1L30", "P2L1", "P2L2"],
                                          "label_line_ids": ["P1L30"]}})
        doc = _doc(2)
        changed = repair_report_evidence(report, ocr, doc)
        self.assertEqual(report.answer_regions["q6.c"]["line_ids"], ["P1L14"])
        self.assertEqual(report.answer_regions["q6.c"]["pages"], [1])
        self.assertEqual(q.anchor_line_id, "P1L14")
        self.assertEqual(q.page, 1)
        self.assertIn("Q6.c", changed)
        # Idempotent: a second pass changes nothing.
        self.assertEqual(repair_report_evidence(report, ocr, doc), {})

    def test_merged_mcq_block_gets_one_synthetic_row_per_question(self):
        # d365ca25: Mathpix returned rows 1-5 as one LaTeX array; four questions
        # anchored to the block and drew beside its middle row or nowhere.
        text = ("\\[\n\\begin{array}{l}\n\\text { 1) }(B) \\\\\n\\text { 2) }(C) \\\\\n"
                "\\text { 3) }(B) \\\\\n\\end{array}\n\\]")
        ocr = [PageOCR(1, 595, 842, [LineBox("P1L5", text, (60, 100, 250, 220), 1)])]
        questions = [QuestionMark(qid=f"Q{i}", score=1, max_score=1, page=1, y_fraction=.15,
                                  objective=True, chosen_option="B", anchor_line_id="P1L5" if i != 2 else "")
                     for i in (1, 2, 3)]
        report = GradeReport(total_score=3, max_total=3, overall_remarks="", questions=questions,
                             evidence_contract=True,
                             answer_regions={"q1": {"qid": "Q1", "status": "located", "pages": [1],
                                                    "line_ids": ["P1L5"], "label_line_ids": ["P1L5"]}})
        doc = _doc()
        repair_report_evidence(report, ocr, doc)
        rows = {q.qid: q.anchor_line_id for q in questions}
        self.assertEqual(rows, {"Q1": "P1L5#r0", "Q2": "P1L5#r1", "Q3": "P1L5#r2"})
        added = {line.line_id: line for line in ocr[0].lines}
        self.assertIn("P1L5#r1", added)
        self.assertEqual(added["P1L5#r1"].text, "2) (C)")
        # Rows are stacked top to bottom inside the block.
        y = [added[f"P1L5#r{i}"].bbox[1] for i in range(3)]
        self.assertEqual(y, sorted(y))
        self.assertTrue(100 <= y[0] < y[2] < 220)
        self.assertEqual(report.answer_regions["q2"]["line_ids"], ["P1L5#r1"])

    def test_mcq_anchor_is_kept_even_when_the_map_gathered_other_rows(self):
        # 139e179a Q1: the map's region for an MCQ held three unrelated rows on
        # page 3; the grader's anchor line on page 1 is the answer.
        ocr = [PageOCR(1, 595, 842, [LineBox("P1L15", "Q1 (B) Indika", (60, 300, 200, 318), 1)]),
               PageOCR(3, 595, 842, [LineBox("P3L5", "(1) first written records", (60, 60, 300, 78), 3)])]
        q = QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=.3, objective=True,
                         chosen_option="B", anchor_line_id="P1L15")
        report = GradeReport(total_score=1, max_total=1, overall_remarks="", questions=[q],
                             evidence_contract=True,
                             answer_regions={"q1": {"qid": "Q1", "status": "located", "pages": [1, 3],
                                                    "line_ids": ["P1L15", "P3L5"], "label_line_ids": ["P1L15"]}})
        repair_report_evidence(report, ocr, _doc(3))
        self.assertEqual(q.anchor_line_id, "P1L15")
        self.assertEqual(report.answer_regions["q1"]["line_ids"], ["P1L15"])

    def test_step_whose_quote_crosses_a_line_break_keeps_the_cited_line(self):
        # 3fb6d0f9 Q7.c: the grader quoted a sentence that runs on to the next OCR
        # line; the exact-substring check rejected the cited line and the step was
        # drawn nowhere.
        ocr = [PageOCR(2, 595, 842, [
            LineBox("P2L22", "- The king's day was divided into 8 parts,", (60, 100, 400, 118), 2),
            LineBox("P2L24", "- There was a network of spies, who to were", (60, 140, 400, 158), 2),
            LineBox("P2L25", "sent to every corner of the empire", (60, 160, 400, 178), 2)])]
        q = QuestionMark(qid="Q7.c", score=2, max_score=3, page=2, y_fraction=.2, anchor_line_id="P2L22",
                         mark_style="per_step", criteria=[
                             Criterion(description="king's day", awarded=1, max=1, met=True,
                                       step_line_id="P2L22", anchor_quote="The king's day was divided into 8 parts"),
                             Criterion(description="spies", awarded=1, max=1, met=True, step_line_id="P2L24",
                                       anchor_quote="There was a network of spies, who told the king everything")])
        report = GradeReport(total_score=2, max_total=3, overall_remarks="", questions=[q],
                             evidence_contract=True,
                             answer_regions={"q7.c": {"qid": "Q7.c", "status": "inferred", "pages": [2],
                                                      "line_ids": ["P2L22", "P2L24", "P2L25"], "label_line_ids": []}})
        repair_report_evidence(report, ocr, _doc(2))
        self.assertEqual([c.step_line_id for c in q.criteria], ["P2L22", "P2L24"])
        self.assertEqual(report.answer_regions["q7.c"]["line_ids"], ["P2L22", "P2L24", "P2L25"])

    def test_objective_question_anchored_to_a_numbered_point_moves_onto_the_mcq_block(self):
        # d4d4853f: the map gave Q1 "(1) Silappadikaran" (a point of Q7.b) and Q2, Q3
        # lines on page 3; 3fb6d0f9: Q4 got "(4) Obedience to parents" on page 2.
        page1 = PageOCR(1, 595, 842, [
            LineBox("P1L1", "Section - A", (300, 60, 420, 80), 1),
            LineBox("P1L2", "IB Indika", (100, 100, 210, 120), 1),
            LineBox("P1L3", "2 c Jamil", (100, 140, 210, 160), 1),
            LineBox("P1L4", "3 B Fold Mountain", (100, 180, 280, 200), 1),
            LineBox("P1L5", "\\( A B \\) Rift Vally", (100, 220, 260, 240), 1),
            LineBox("P1L6", "5.c 25 years", (100, 260, 240, 280), 1),
            LineBox("P1L15", "(1) Silappadikaran", (100, 600, 300, 620), 1),
            LineBox("P1L16", "Marimegalai", (120, 640, 300, 660), 1)])
        page3 = PageOCR(3, 595, 842, [
            LineBox("P3L1", "(3) It was formed by two opposite forces", (100, 100, 500, 120), 3),
            LineBox("P3L2", "(2) It was formed by tectonic uplift", (100, 140, 500, 160), 3)])
        questions = [
            QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=.7, objective=True, chosen_option="B", anchor_line_id="P1L15"),
            QuestionMark(qid="Q2", score=1, max_score=1, page=3, y_fraction=.2, objective=True, chosen_option="C", anchor_line_id="P3L2"),
            QuestionMark(qid="Q3", score=1, max_score=1, page=3, y_fraction=.1, objective=True, chosen_option="B", anchor_line_id="P3L1"),
            QuestionMark(qid="Q4", score=0, max_score=1, page=1, y_fraction=.26, objective=True, chosen_option="B", anchor_line_id="P1L5"),
            QuestionMark(qid="Q5", score=1, max_score=1, page=1, y_fraction=.3, objective=True, chosen_option="C", anchor_line_id="P1L6"),
            QuestionMark(qid="Q7.b", score=2, max_score=2, page=1, y_fraction=.75, anchor_line_id="P1L16",
                         criteria=[Criterion(description="Silappadikaram", awarded=1, max=1, met=True,
                                             step_line_id="P1L15", anchor_quote="Silappadikaran"),
                                   Criterion(description="Manimekalai", awarded=1, max=1, met=True,
                                             step_line_id="P1L16", anchor_quote="Marimegalai")])]
        regions = {"q1": {"qid": "Q1", "status": "located", "pages": [1, 3], "line_ids": ["P1L15", "P3L3"], "label_line_ids": ["P1L15"]},
                   "q2": {"qid": "Q2", "status": "located", "pages": [3], "line_ids": ["P3L2"], "label_line_ids": ["P3L2"]},
                   "q3": {"qid": "Q3", "status": "located", "pages": [3], "line_ids": ["P3L1"], "label_line_ids": ["P3L1"]},
                   "q4": {"qid": "Q4", "status": "inferred", "pages": [1], "line_ids": ["P1L5"], "label_line_ids": []},
                   "q5": {"qid": "Q5", "status": "inferred", "pages": [1], "line_ids": ["P1L6"], "label_line_ids": []},
                   "q7.b": {"qid": "Q7.b", "status": "inferred", "pages": [1], "line_ids": ["P1L16"], "label_line_ids": []}}
        report = GradeReport(total_score=5, max_total=6, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions=regions)
        changed = repair_report_evidence(report, [page1, page3], _doc(3))
        anchors = {q.qid: q.anchor_line_id for q in questions}
        self.assertEqual(anchors["Q1"], "P1L2")
        self.assertEqual(anchors["Q2"], "P1L3")
        self.assertEqual(anchors["Q3"], "P1L4")
        self.assertEqual(anchors["Q4"], "P1L5")     # unreadable number, taken in order
        self.assertEqual(anchors["Q5"], "P1L6")
        self.assertEqual(report.answer_regions["q1"]["line_ids"], ["P1L2"])
        self.assertEqual(anchors["Q7.b"], "P1L16")  # a descriptive answer keeps its own lines
        self.assertIn("Q1", changed)

    def test_nothing_happens_outside_the_evidence_contract(self):
        ocr = [PageOCR(1, 595, 842, [LineBox("P1L1", "x", (0, 0, 10, 10), 1)])]
        q = QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=.1, anchor_line_id="P1L9")
        report = GradeReport(total_score=1, max_total=1, overall_remarks="", questions=[q])
        self.assertEqual(repair_report_evidence(report, ocr, _doc()), {})
        self.assertEqual(q.anchor_line_id, "P1L9")


if __name__ == "__main__":
    unittest.main()
