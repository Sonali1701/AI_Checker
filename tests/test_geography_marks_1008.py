"""Parishram Geography copies of 8 Oct 2026: what the student saw on the page did not
match the margin.

- Q11 printed "2.5/3" in the margin over one circled 0.5: its two 1-mark steps were in
  the student's table (one tall OCR block), and the legacy layout refused to mark a
  tall block it had no verified row for.
- A page holding only the Q13 table carried no mark at all, and the page with the end
  of another answer carried nothing either ("no checking on this page").
- Q14 printed "1/5" over a page with no mark: its only earned step quoted words the
  OCR had misread, so the step found no line and was drawn nowhere.
- Q7's answer ran to the edge of the page: it got a tick but no circled 1.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitz
from answer_map import build_answer_map
from grader import Criterion, GradeReport, QuestionMark
from grading_quality import check_render_manifest, initialize_evidence, prepare_evidence
from mathpix import LineBox, PageOCR
import pdf_renderer as _pr
from pdf_renderer import build_evaluated_pdf
from test_evidence_quality import package

_LAYOUT_BEFORE = _pr.TEACHER_LAYOUT


def setUpModule():
    _pr.TEACHER_LAYOUT = False


def tearDownModule():
    _pr.TEACHER_LAYOUT = _LAYOUT_BEFORE


def _copy(pages, criteria, score):
    """pages: [[(line_id, text, bbox, kind), ...], ...] on 595 x 842 pages."""
    doc = fitz.open()
    ocr = []
    for pno, rows in enumerate(pages, 1):
        pg = doc.new_page(width=595, height=842)
        lines = []
        for lid, text, bbox, kind in rows:
            if kind == "text":
                pg.insert_text((bbox[0], bbox[3] - 3), text, fontsize=11)
            else:
                pg.draw_rect(bbox, color=(0, 0, 0), width=0.8)
                for k in range(int(bbox[1]) + 30, int(bbox[3]) - 10, 40):
                    pg.insert_text((bbox[0] + 10, k), "seasonal workers less than 183 days",
                                   fontsize=10)
            lines.append(LineBox(lid, text, bbox, pno, kind=kind))
        ocr.append(PageOCR(pno, 595, 842, lines))
    pdf = doc.tobytes()
    doc.close()
    report = GradeReport(total_score=score, max_total=2, overall_remarks="", questions=[
        QuestionMark(qid="Q1", score=score, max_score=2, page=1, y_fraction=.1,
                     anchor_line_id="P1L1", mark_style="per_step", criteria=criteria,
                     remark="Explain why diversification helps growth." if score < 2 else "")])
    pkg = package()
    initialize_evidence(report, pkg, build_answer_map(pkg, ocr))
    prepare_evidence(report, ocr)
    return pdf, report, ocr


def _steps(report):
    return round(sum(float(r.get("score") or 0) for r in report.render_manifest
                     if r.get("qid") == "Q1" and r.get("role") == "step"), 2)


class TableSteps(unittest.TestCase):
    def test_steps_in_a_tall_table_are_marked_where_it_ends(self):
        table = (60, 120, 540, 420)                      # 36 percent of the page
        pages = [[("P1L1", "Q1 Main workers and marginal workers", (60, 85, 400, 103), "text"),
                  ("P1D2", "[table]", table, "table"),
                  ("P1L3", "Occupational diversification matters for growth", (60, 450, 420, 468), "text")]]
        criteria = [Criterion(description="Main worker", awarded=.5, max=.5, met=True,
                              step_line_id="P1D2", anchor_quote="workers who work more than 183 days"),
                    Criterion(description="Marginal worker", awarded=.5, max=.5, met=True,
                              step_line_id="P1D2", anchor_quote="seasonal workers less than 183 days"),
                    Criterion(description="Diversification", awarded=.5, max=1, met=False,
                              step_line_id="P1L3", anchor_quote="Occupational diversification matters")]
        pdf, report, ocr = _copy(pages, criteria, 1.5)
        pdf = build_evaluated_pdf(pdf, "copy.pdf", [], report, ocr)
        self.assertEqual(_steps(report), 1.5)        # the circles add up to the margin
        codes = {i.code for i in check_render_manifest(report, pdf)}
        self.assertNotIn("step-awards-do-not-reconcile", codes)
        # The rows inside the table were never verified: the release check still says
        # so (a note for the reviewer, it does not hold the copy).
        self.assertIn("required-mark-missing", codes)
        on_table = [r for r in report.render_manifest
                    if r.get("role") == "step" and r.get("line_id") == "P1D2"]
        self.assertEqual(len(on_table), 1)           # one mark for the table, awards added
        self.assertEqual(on_table[0]["score"], 1.0)
        self.assertEqual(on_table[0]["criteria"], [])
        self.assertEqual(sorted(on_table[0]["covers_criteria"]), [0, 1])
        # Page 1 sits under the cover strip: measure from the top of the sheet.
        top = report.render_page_layouts["1"]["content_bbox"][1]
        mark = fitz.Rect(on_table[0]["bbox"])
        self.assertTrue(top + table[3] - 40 <= mark.y1 <= top + table[3] + 20, mark)


class UnplacedStep(unittest.TestCase):
    def test_an_earned_step_whose_quote_was_misread_joins_a_drawn_mark(self):
        pages = [[("P1L1", "Q1 Thban setlements in Ind have euolued", (60, 85, 400, 103), "text"),
                  ("P1L2", "Ancient towns developed around temples", (60, 135, 400, 153), "text"),
                  ("P1L3", "Medieval towns were capitals and forts", (60, 185, 400, 203), "text")]]
        criteria = [Criterion(description="Introduction", awarded=1, max=1, met=True,
                              step_line_id="P1L1",
                              anchor_quote="Urban settlements in India have evolved continuously"),
                    Criterion(description="Ancient and medieval towns", awarded=.5, max=1, met=False,
                              step_line_id="P1L3", anchor_quote="Medieval towns were capitals and forts")]
        pdf, report, ocr = _copy(pages, criteria, 1.5)
        pdf = build_evaluated_pdf(pdf, "copy.pdf", [], report, ocr)
        self.assertEqual(_steps(report), 1.5)
        self.assertNotIn("step-awards-do-not-reconcile",
                         {i.code for i in check_render_manifest(report, pdf)})


class ContinuationPage(unittest.TestCase):
    def test_a_page_of_answer_with_no_step_still_shows_it_was_read(self):
        pages = [[("P1L1", "Q1 The three stages of the transition are", (60, 85, 400, 103), "text"),
                  ("P1L2", "Stage one high birth rate and high death rate", (60, 135, 420, 153), "text")],
                 [("P2L1", "Stage three low birth rate and low death rate", (60, 85, 420, 103), "text"),
                  ("P2L2", "Countries like Japan and the USA", (60, 135, 400, 153), "text"),
                  ("P2L3", "Hence the population becomes stable", (60, 185, 400, 203), "text")]]
        criteria = [Criterion(description="Introduction", awarded=1, max=1, met=True,
                              step_line_id="P1L1",
                              anchor_quote="The three stages of the transition are"),
                    Criterion(description="Stage one", awarded=1, max=1, met=True,
                              step_line_id="P1L2",
                              anchor_quote="Stage one high birth rate and high death rate")]
        pdf, report, ocr = _copy(pages, criteria, 2)
        region = report.answer_regions["q1"]
        region["line_ids"] = ["P1L1", "P1L2", "P2L1", "P2L2", "P2L3"]
        region["pages"] = [1, 2]
        pdf = build_evaluated_pdf(pdf, "copy.pdf", [], report, ocr)
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            sheet = fitz.Rect(report.render_page_layouts["2"]["content_bbox"])
            red = [d for d in doc[1].get_drawings()
                   if (d.get("color") or (0,))[0] > .6 and sheet.intersects(d["rect"])]
        self.assertTrue(red, "page 2 carries no mark")
        on_two = [r for r in report.render_manifest if r.get("page") == 2
                  and r.get("role") in {"answer", "step", "total", "verdict", "continuation"}]
        # One plain tick at the end of the answer's last line, carrying no score.
        self.assertEqual([(r["role"], r["line_id"], r.get("score")) for r in on_two],
                         [("continuation", "P2L3", None)])
        self.assertEqual(_steps(report), 2)

    def test_no_seen_tick_where_the_answers_total_already_marks_the_page(self):
        pages = [[("P1L1", "Q1 The three stages of the transition are", (60, 85, 400, 103), "text"),
                  ("P1L2", "Stage one high birth rate and high death rate", (60, 135, 420, 153), "text")],
                 [("P2L1", "Stage three low birth rate and low death rate", (60, 85, 420, 103), "text"),
                  ("P2L2", "Hence the population becomes stable", (60, 135, 400, 153), "text")]]
        criteria = [Criterion(description="Stages", awarded=2, max=2, met=True,
                              step_line_id="P1L2",
                              anchor_quote="Stage one high birth rate and high death rate")]
        pdf, report, ocr = _copy(pages, criteria, 2)
        report.answer_regions["q1"]["line_ids"] = ["P1L1", "P1L2", "P2L1", "P2L2"]
        report.answer_regions["q1"]["pages"] = [1, 2]
        build_evaluated_pdf(pdf, "copy.pdf", [], report, ocr)
        roles = [r["role"] for r in report.render_manifest if r.get("page") == 2]
        self.assertIn("total", roles)
        self.assertNotIn("continuation", roles)


class WrappedAnswer(unittest.TestCase):
    def test_the_answers_next_line_is_used_and_a_new_question_is_not(self):
        anchor = {"lid": "P1L7", "page": 1, "x0": 40, "y0": 400, "x1": 590, "y1": 420,
                  "text": "7. Both statement I and statement II are"}
        wrap = {"lid": "P1L8", "page": 1, "x0": 60, "y0": 424, "x1": 140, "y1": 442,
                "text": "correct."}
        nxt = {"lid": "P1L9", "page": 1, "x0": 40, "y0": 424, "x1": 300, "y1": 442,
               "text": "8. 1-B, 2-C, 3-A, 4-D"}
        self.assertEqual(_pr._answer_continuation_row(anchor, {"a": anchor, "b": wrap})["lid"], "P1L8")
        self.assertIsNone(_pr._answer_continuation_row(anchor, {"a": anchor, "c": nxt}))
        far = {**wrap, "y0": 500, "y1": 518}
        self.assertIsNone(_pr._answer_continuation_row(anchor, {"a": anchor, "d": far}))


if __name__ == "__main__":
    unittest.main()
