"""Read actual MCQ numerals back from released PDFs, beside their own answers."""
import hashlib
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import GradeReport, QuestionMark
from grading_quality import check_render_manifest
from mathpix import LineBox, PageOCR
from pdf_renderer import build_evaluated_pdf, student_ink_mask, rect_ink_fraction

# A legacy-contract suite: marks stay inside the sheet and within reach of their
# evidence row, and checking plans are drawn. The teacher layout (the product
# default) has its own contract in test_teacher_layout.py.
import pdf_renderer as _pr
_LAYOUT_BEFORE = _pr.TEACHER_LAYOUT


def setUpModule():
    _pr.TEACHER_LAYOUT = False


def tearDownModule():
    _pr.TEACHER_LAYOUT = _LAYOUT_BEFORE



def render_mcqs(entries=None, shaded=False, positions=None):
    # Include a non-one-mark MCQ, a partial award and legacy option-only flags.
    entries = entries or [(1, 1, "C"), (0, 1, "B"), (2, 2, "D"), (.5, 1, "A")]
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    if shaded:
        pg.draw_rect(pg.rect, color=None, fill=(.52, .52, .52))
    lines, questions, regions = [], [], {}
    for i, (score, maximum, option) in enumerate(entries):
        qid, lid = f"Q{i+1}", f"P1L{i+1}"
        x, y, end = positions[i] if positions else (65, 90 + 35 * i, 150)
        text = f"{qid}) ({option})" if option else ""
        if text:
            pg.insert_text((x, y), text, fontsize=12)
            lines.append(LineBox(lid, text, (x, y - 14, end, y + 4), 1))
            regions[qid.lower()] = {"line_ids": [lid], "pages": [1]}
        questions.append(QuestionMark(qid=qid, score=score, max_score=maximum,
            page=1, y_fraction=y / 842, attempted=bool(option),
            objective=i != 0, chosen_option=option, anchor_line_id=lid if option else ""))
    report = GradeReport(total_score=sum(q.score for q in questions),
        max_total=sum(q.max_score for q in questions), overall_remarks="", questions=questions,
        evidence_contract=True, answer_regions=regions)
    source = doc.tobytes()
    doc.close()
    ocr = [PageOCR(1, 595, 842, lines)]
    pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
    return report, pdf


class MCQScoreTests(unittest.TestCase):
    def test_cover_keeps_restarted_objective_and_descriptive_numbers_separate(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((80, 100), "1) B")
        page.insert_text((80, 200), "Q1(a) Audit explanation")
        source = doc.tobytes()
        doc.close()
        report = GradeReport(total_score=5, max_total=7, overall_remarks="", questions=[
            QuestionMark(qid="Q1", score=2, max_score=2, objective=True, chosen_option="B",
                         page=1, y_fraction=.12, attempted=True),
            QuestionMark(qid="Q1.a", score=3, max_score=5, page=1, y_fraction=.24, attempted=True)])
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report)
        self.assertEqual([(r["objective"], r["score"], r["max_score"]) for r in report.section_totals],
                         [(True, 2, 2), (False, 3, 5)])
        with fitz.open(stream=pdf, filetype="pdf") as rendered:
            text = rendered[0].get_text()
            self.assertIn("Objective", text)
            self.assertIn("Descriptive", text)

    def test_score_placement_rejects_ink_that_would_hold_strict_release(self):
        # The live MCQ circles passed a 6% placement threshold but produced 5%
        # release warnings. Placement must also avoid the warning-level collision.
        probes = []
        def ink(mask, scale, box):
            probes.append(tuple(box))
            return .05 if len(probes) == 1 else 0
        with patch("preflight._ink_under_mark", side_effect=ink):
            report, pdf = render_mcqs([(2, 2, "B")])
        self.assertGreater(len(probes), 1)
        self.assertEqual(check_render_manifest(report, pdf), [])

    def test_correct_wrong_and_partial_marks_are_beside_their_answers(self):
        report, pdf = render_mcqs()
        self.assertEqual(check_render_manifest(report, pdf), [])
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            rows = [r for r in report.render_manifest if r["role"] == "answer"]
            self.assertEqual(len(rows), 4)
            for row, expected in zip(rows, ("1", "0", "2", "0.5")):
                box, anchor = fitz.Rect(row["bbox"]), fitz.Rect(row["anchor_bbox"])
                self.assertLess(abs((box.y0 + box.y1 - anchor.y0 - anchor.y1) / 2), 5)
                self.assertLess(box.x0 - anchor.x1, 35)
                if expected == "0":
                    # The cross and its circled 0 are one mark on the answer's row
                    # (14 Oct 2026: drawn apart, the 0 landed on the next answer).
                    self.assertEqual(row["score_style"], "cross_and_zero")
                    self.assertEqual(doc[0].get_textbox(fitz.Rect(row["numeral_bbox"]) + (-1, -1, 1, 1)).strip(), "0")
                    self.assertFalse(anchor.intersects(fitz.Rect(row["numeral_bbox"])))
                else:
                    self.assertEqual(doc[0].get_textbox(fitz.Rect(row["numeral_bbox"]) + (-1, -1, 1, 1)).strip(), expected)
                    self.assertFalse(anchor.intersects(fitz.Rect(row["numeral_bbox"])))
                self.assertTrue(any(d["rect"].intersects(box) for d in doc[0].get_drawings()))

    def test_awards_are_circled_on_sheet_and_also_shown_in_margin(self):
        report, pdf = render_mcqs()
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            margin = fitz.Rect(report.render_page_layouts["1"]["margin_bbox"])
            printed = "".join(doc[0].get_textbox(margin).split())
            for expected in ("1/1", "0/1", "2/2", "0.5/1"):
                self.assertIn(expected, printed)
            totals = [r for r in report.render_manifest if r.get("question_total")]
            self.assertEqual(len(totals), 4)
            self.assertTrue(all(r.get("numeral_bbox") for r in totals))
            for total in totals:
                numeral = doc[0].get_textbox(fitz.Rect(total['numeral_bbox']) + (-1, -1, 1, 1))
                self.assertEqual(numeral.strip(), f"{total['score']:g}")

    def test_a_bare_awarded_number_does_not_substitute_for_a_circle(self):
        report, pdf = render_mcqs()
        for row in report.render_manifest:
            if row["role"] == "answer":
                row.pop("score_style", None)
        self.assertEqual(sum(i.code == "mcq-score-not-marked" for i in check_render_manifest(report, pdf)), 3)

    def test_circle_must_really_exist_in_the_pdf(self):
        with patch.object(fitz.Page, "draw_circle", return_value=None):
            report, pdf = render_mcqs()
        self.assertEqual(sum(i.code == "mcq-score-not-marked" for i in check_render_manifest(report, pdf)), 3)

    def test_wrong_mcq_has_a_cross_and_labelled_circled_zero_on_the_sheet(self):
        report, pdf = render_mcqs([(0, 1, "B")])
        self.assertEqual(check_render_manifest(report, pdf), [])
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            row = next(r for r in report.render_manifest if r["role"] == "answer")
            box = fitz.Rect(row["bbox"])
            # One mark: the cross, then the circled 0, on the answer's own row.
            self.assertEqual(doc[0].get_textbox(fitz.Rect(row["numeral_bbox"]) + (-1, -1, 1, 1)).strip(), "0")
            self.assertTrue(fitz.Rect(row["verdict_bbox"]).x1 <= fitz.Rect(row["numeral_bbox"]).x0)
            anchor = fitz.Rect(row["anchor_bbox"])
            self.assertLess(abs((box.y0 + box.y1 - anchor.y0 - anchor.y1) / 2), 5)
            paths = [d for d in doc[0].get_drawings() if d["rect"].intersects(box)]
            self.assertTrue(paths)
            margin = fitz.Rect(report.render_page_layouts["1"]["margin_bbox"])
            self.assertIn("0/1", "".join(doc[0].get_textbox(margin).split()))
            total = next(r for r in report.render_manifest if r.get("question_total"))
            self.assertEqual(total['score'], 0)
            self.assertFalse(fitz.Rect(total['bbox']).intersects(margin))

    def test_wrong_mcq_still_requires_a_visible_cross(self):
        with patch("pdf_renderer._draw_cross", return_value=None):
            report, pdf = render_mcqs([(0, 1, "B")])
        # The circled 0 is drawn, so the mark is not invisible; its cross is missing.
        self.assertTrue({"invisible-mark", "verdict-not-visible"}
                        & {i.code for i in check_render_manifest(report, pdf)})

    def test_right_strip_attaches_to_each_original_page_without_extra_padding(self):
        doc = fitz.open()
        ocr, questions, regions = [], [], {}
        for pno, (w, h) in enumerate(((500, 700), (700, 500)), 1):
            page = doc.new_page(width=w, height=h)
            text = f"Q{pno}) (C) 3/4"
            page.insert_text((65, 90), text, fontsize=12)
            lid = f"P{pno}L1"
            ocr.append(PageOCR(pno, w, h, [LineBox(lid, text, (65, 76, 175, 94), pno)]))
            questions.append(QuestionMark(qid=f"Q{pno}", score=3-pno, max_score=2,
                page=pno, y_fraction=90/h, objective=True, chosen_option="C", anchor_line_id=lid))
            regions[f"q{pno}"] = {"line_ids": [lid], "pages": [pno]}
        report = GradeReport(total_score=3, max_total=4, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions=regions)
        pdf = build_evaluated_pdf(doc.tobytes(), "copy.pdf", [], report, ocr)
        doc.close()
        self.assertEqual(check_render_manifest(report, pdf), [])
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            for pno, original in enumerate(ocr, 1):
                layout = report.render_page_layouts[str(pno)]
                content = fitz.Rect(layout["content_bbox"])
                margin = fitz.Rect(layout["margin_bbox"])
                self.assertEqual(layout["margin_side"], "right")
                self.assertEqual(content.x0, 0)
                self.assertAlmostEqual(margin.x0, content.x1, places=3)
                self.assertAlmostEqual(margin.x1, doc[pno-1].rect.width, places=3)
                self.assertAlmostEqual(doc[pno-1].rect.width,
                    original.image_width + max(175, original.image_width * .24), places=3)
                self.assertEqual(content.width, original.image_width)
                self.assertEqual(content.height, original.image_height)
                self.assertIn("3/4", doc[pno-1].get_textbox(content))
                self.assertIn(f"{3-pno}/2", "".join(doc[pno-1].get_textbox(margin).split()))
                total = next(r for r in report.render_manifest if r.get("question_total") and r['page'] == pno)
                self.assertTrue(content.contains(fitz.Rect(total['bbox'])))
                row = next(r for r in report.render_manifest if r["page"] == pno and r["role"] == "answer")
                self.assertAlmostEqual((row["bbox"][0] - content.x0) / original.image_width,
                                       row["mark_normalized"][0], places=5)
        report.render_page_layouts["2"]["margin_bbox"][0] -= 10
        self.assertIn("inconsistent-margin-layout", {i.code for i in check_render_manifest(report, pdf)})

    def test_a_tick_and_distant_total_cannot_substitute_for_inline_score(self):
        report, pdf = render_mcqs()
        for row in report.render_manifest:
            if row["role"] == "answer":
                row.pop("numeral_bbox", None)
                row.pop("score_text", None)
        problems = [i for i in check_render_manifest(report, pdf) if i.code == "mcq-score-not-marked"]
        self.assertEqual(len(problems), 3)

    def test_missing_pdf_text_is_detected_even_when_manifest_claims_it_exists(self):
        report, pdf = render_mcqs()
        row = next(r for r in report.render_manifest if r["role"] == "answer")
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            doc[0].add_redact_annot(fitz.Rect(row["numeral_bbox"]) + (-.1, -.1, .1, .1))
            doc[0].apply_redactions(images=0, graphics=0)
            pdf = doc.tobytes()
        report.render_manifest_pdf_sha256 = hashlib.sha256(pdf).hexdigest()
        self.assertIn("mcq-score-not-marked", [i.code for i in check_render_manifest(report, pdf)])

    def test_unattempted_mcq_does_not_get_a_mark_on_someone_elses_answer(self):
        report, pdf = render_mcqs([(1, 1, "C"), (0, 1, "")])
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertFalse(any(r["qid"] == "Q2" and r["role"] == "answer" for r in report.render_manifest))

    def test_shaded_paper_has_room_for_scores_and_still_detects_writing(self):
        report, pdf = render_mcqs(shaded=True)
        self.assertEqual(check_render_manifest(report, pdf), [])
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            mask, scale = student_ink_mask(doc[0])
            row = next(r for r in report.render_manifest if r["role"] == "answer")
            self.assertGreater(rect_ink_fraction(mask, scale, *row["anchor_bbox"]), .04)
            box = fitz.Rect(row["numeral_bbox"])
            self.assertLess(rect_ink_fraction(mask, scale, *box), .01)

    def test_two_mcq_columns_keep_their_own_scores(self):
        for second_x in (135, 350):
            with self.subTest(second_x=second_x):
                report, pdf = render_mcqs([(1, 1, "C"), (0, 1, "B")],
                    positions=[(65, 90, 120), (second_x, 90, second_x + 60)])
                self.assertEqual(check_render_manifest(report, pdf), [])
                rows = [r for r in report.render_manifest if r["role"] == "answer"]
                self.assertEqual(len(rows), 2)
                for i, row in enumerate(rows):
                    box, anchor = fitz.Rect(row["bbox"]), fitz.Rect(row["anchor_bbox"])
                    self.assertGreaterEqual(box.x0, anchor.x1)
                    self.assertLess(box.x0 - anchor.x1, 35)
                    self.assertFalse(box.intersects(fitz.Rect(rows[1-i]["anchor_bbox"])))

    def test_closely_spaced_answers_do_not_lose_scores_or_cover_neighbours(self):
        entries = [(i % 2, 1, "B") for i in range(8)]
        report, pdf = render_mcqs(entries, positions=[(65, 90 + 22*i, 150) for i in range(8)])
        self.assertEqual(check_render_manifest(report, pdf), [])
        rows = [r for r in report.render_manifest if r["role"] == "answer"]
        self.assertEqual(len(rows), 8)
        for i, row in enumerate(rows):
            for other in rows[i + 1:]:
                self.assertFalse(fitz.Rect(row["bbox"]).intersects(fitz.Rect(other["bbox"])))


if __name__ == "__main__":
    unittest.main()
