# -*- coding: utf-8 -*-
"""Which layout draws a copy, and what the teacher layout does with non-prose rows.

* a step written as an equation (OCR kind "math") is a row like any other and is
  ticked (stored Physics copy 9ccee8a8 lost every formula step);
* an answer the grader cited on a ledger table or a diagram block goes to the legacy
  renderer, whose row boxes and checking plans place the marks (stored Accounting
  copy 4b6e3d85 lost its table steps on the teacher layout);
* a diagram that only sits inside an answer's region does not change the layout;
* a question whose only evidence is a block still gets its margin score.
"""
from __future__ import annotations

import os
import sys
import unittest

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdf_renderer as pr  # noqa: E402
import teacher_layout  # noqa: E402
from mathpix import LineBox  # noqa: E402
from test_teacher_layout import PX, _build, _c, _q, _report, _sheet  # noqa: E402


def _with_kind(ocr, lid, kind, bbox=None):
    for page in ocr:
        for i, line in enumerate(page.lines):
            if line.line_id == lid:
                page.lines[i] = LineBox(lid, line.text, bbox or line.bbox, line.page, kind=kind)


def _add_block(doc, ocr, lid, kind, rect):
    """A block (table or diagram) drawn as dense ink with one OCR region over it."""
    page = doc[0]
    page.draw_rect(fitz.Rect(*rect), color=(0.1, 0.1, 0.35), width=1.2)
    for y in range(int(rect[1]) + 8, int(rect[3]) - 4, 18):
        page.insert_text((rect[0] + 10, y), "Machinery Alc 1316480", fontsize=11, color=(0.1, 0.1, 0.35))
    ocr[0].lines.append(LineBox(lid, "\\begin{tabular}{|l|l|} To Machinery Alc & 1316480 \\end{tabular}",
                                tuple(v * PX for v in rect), 1, kind=kind))


class RoutingTests(unittest.TestCase):
    def test_a_cited_table_sends_the_copy_to_the_legacy_layout(self):
        doc, ocr = _sheet([("P1L1", "Answer 2 Machinery Account", 60, 330)])
        _add_block(doc, ocr, "P1D2", "table", (40, 160, 560, 420))
        rep = _report([_q("Q2", 2, 3, 1, "P1L1",
                          [_c(1, 1, "P1L1", "Machinery Account"), _c(1, 2, "P1D2", "1316480")])])
        self.assertTrue(pr._cites_block_evidence(rep, ocr))

    def test_a_diagram_only_inside_the_region_keeps_the_teacher_layout(self):
        doc, ocr = _sheet([("P1L1", "Q6 i) The ray bends towards the normal", 60, 420)])
        _add_block(doc, ocr, "P1D2", "diagram", (60, 160, 300, 300))
        rep = _report([_q("Q6.i", 1, 1, 1, "P1L1", [_c(1, 1, "P1L1", "bends towards the normal")])])
        rep.answer_regions = {"q6.i": {"qid": "Q6.i", "status": "located", "pages": [1],
                                       "line_ids": ["P1L1", "P1D2"], "label_line_ids": ["P1L1"]}}
        self.assertFalse(pr._cites_block_evidence(rep, ocr))

    def test_uncited_prose_keeps_the_teacher_layout(self):
        doc, ocr = _sheet([("P1L1", "Q1 (B) Indika", 60, 200)])
        rep = _report([_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True)])
        self.assertFalse(pr._cites_block_evidence(rep, ocr))

    def test_a_short_student_table_stays_on_the_teacher_layout_and_is_ticked(self):
        # 49a65640: a two-column Endogenic/Exogenic table, 12% of the page tall.
        doc, ocr = _sheet([("P1L1", "Q9 b) Difference between the two forces", 60, 420)])
        _add_block(doc, ocr, "P1D2", "table", (60, 150, 420, 250))
        rep = _report([_q("Q9.b", 1.5, 2, 1, "P1D2",
                          [_c(0.75, 1, "P1D2", "Endogenic"), _c(0.75, 1, "P1D2", "Exogenic")],
                          remark="Definitions lack precision.")])
        self.assertFalse(pr._cites_block_evidence(rep, ocr))
        _, manifest = _build(doc, ocr, rep)
        marks = [r for r in manifest if r["qid"] == "Q9.b" and r["role"] in ("step", "answer")]
        self.assertEqual(len(marks), 1, manifest)
        self.assertEqual(marks[0]["score"], 1.5)
        self.assertGreaterEqual(marks[0]["verdict_bbox"][0], 400)    # after the table, on the right


class TeacherRowsTests(unittest.TestCase):
    def test_a_step_written_as_an_equation_is_ticked(self):
        rows = [("P1L1", "Q5 Given u = 0, a = 2 m/s2, t = 5 s", 60, 380),
                ("P1L2", "v = u + a t", 90, 260),
                ("P1L3", "v = 0 + 2 x 5 = 10 m/s", 90, 320)]
        doc, ocr = _sheet(rows)
        _with_kind(ocr, "P1L2", "math")
        _with_kind(ocr, "P1L3", "math")
        rep = _report([_q("Q5", 2, 3, 1, "P1L3",
                          [_c(1, 1, "P1L2", "v = u + a t"), _c(1, 2, "P1L3", "10 m/s")],
                          remark="Units of acceleration missing.")])
        _, manifest = _build(doc, ocr, rep)
        drawn = {r["line_id"] for r in manifest if r["qid"] == "Q5" and r["role"] in ("step", "answer")}
        self.assertTrue(drawn & {"P1L2", "P1L3"}, manifest)
        self.assertFalse(teacher_layout.TeacherLayout(doc, rep, ocr).unanchored.get("Q5"))
        self.assertTrue(any(r["qid"] == "Q5" and r["role"] == "feedback" for r in manifest), manifest)

    def test_a_zero_point_with_nothing_cited_needs_no_mark(self):
        # d365ca25 Q7.c: the grader awarded 0 to a point and cited no line or quote.
        doc, ocr = _sheet([("P1L1", "Q7 c) There was a council of ministers", 60, 420),
                           ("P1L2", "There were four major provinces", 60, 400)])
        rep = _report([_q("Q7.c", 1.5, 3, 1, "P1L1",
                          [_c(0.5, 1, "P1L1", "council of ministers"), _c(0, 1, ""),
                           _c(1, 1, "P1L2", "four major provinces")],
                          remark="Second feature missing.")])
        _, manifest = _build(doc, ocr, rep)
        required = [r.get("criterion") for r in rep.render_requirements if r["qid"] == "Q7.c" and r["role"] == "step"]
        self.assertEqual(required, [0, 2])
        self.assertNotIn("Q7.c", rep.unanchored_step_qids)

    def test_a_question_cited_only_on_a_table_still_gets_its_margin_score(self):
        doc, ocr = _sheet([("P1L1", "Answer 3", 260, 360)])
        _add_block(doc, ocr, "P1D2", "table", (40, 160, 560, 420))
        rep = _report([_q("Q3", 5.5, 7, 1, "P1D2",
                          [_c(1, 1, "P1D2", "Discount"), _c(4.5, 6, "P1D2", "14500")],
                          remark="Closing balance wrong.")])
        _, manifest = _build(doc, ocr, rep)
        entries = [r for r in manifest if r["qid"] == "Q3" and r["role"] == "feedback"]
        self.assertEqual(len(entries), 1, manifest)
        self.assertGreaterEqual(entries[0]["bbox"][1], 150)   # beside the table, not at the top


class MarginOrderTests(unittest.TestCase):
    def _order(self, rows, questions):
        doc, ocr = _sheet(rows)
        _, manifest = _build(doc, ocr, _report(questions))
        entries = sorted((r for r in manifest if r["role"] == "feedback"), key=lambda r: r["bbox"][1])
        return [r["qid"] for r in entries], {r["qid"]: r for r in entries}

    def test_answers_written_out_of_order_are_listed_as_written(self):
        rows = [("P1L1", "Q3 (B) Fold mountain", 60, 230),
                ("P1L2", "Q1 (B) Indika", 60, 200),
                ("P1L3", "Q2 (C) Tamil", 60, 200)]
        qs = [_q("Q1", 1, 1, 1, "P1L2", objective=True, is_correct=True),
              _q("Q2", 1, 1, 1, "P1L3", objective=True, is_correct=True),
              _q("Q3", 1, 1, 1, "P1L1", objective=True, is_correct=True)]
        order, _ = self._order(rows, qs)
        self.assertEqual(order, ["Q3", "Q1", "Q2"])

    def test_a_second_block_lower_down_starts_beside_its_own_rows(self):
        rows = [("P1L1", "Q1 (B) Indika", 60, 200),
                ("P1L2", "Q2 (C) Tamil", 60, 200)]
        rows += [(f"P1L{i}", "the answer to question six runs on here", 60, 420) for i in range(3, 13)]
        rows += [("P1L13", "Q16 (A) Delhi", 60, 200),
                 ("P1L14", "Q17 (D) Agra", 60, 200)]
        qs = [_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True),
              _q("Q2", 1, 1, 1, "P1L2", objective=True, is_correct=True),
              _q("Q16", 1, 1, 1, "P1L13", objective=True, is_correct=True),
              _q("Q17", 1, 1, 1, "P1L14", objective=True, is_correct=True)]
        order, by_qid = self._order(rows, qs)
        self.assertEqual(order, ["Q1", "Q2", "Q16", "Q17"])
        # Q16's entry sits near its row (row 13 of the sheet), not under Q2's.
        self.assertGreater(by_qid["Q16"]["bbox"][1], by_qid["Q2"]["bbox"][3] + 100)

    def test_two_columns_do_not_interleave(self):
        doc, ocr = _sheet([("P1L1", "Q1 (B) Indika", 60, 200),
                           ("P1L2", "Q2 (C) Tamil", 60, 200),
                           ("P1L3", "Q3 (A) Delta", 60, 200)])
        # a right-hand column on the same rows
        for i, (lid, text) in enumerate((("P1L4", "Q4 (D) Agra"), ("P1L5", "Q5 (A) Delhi"))):
            y = 120 + i * 26.0
            doc[0].insert_text((330, y), text, fontsize=14, color=(0.1, 0.1, 0.35))
            ocr[0].lines.append(LineBox(lid, text, (330 * PX, (y - 13) * PX, 470 * PX, (y + 4) * PX), 1))
        qs = [_q(f"Q{i}", 1, 1, 1, f"P1L{i}", objective=True, is_correct=True) for i in range(1, 6)]
        _, manifest = _build(doc, ocr, _report(qs))
        entries = sorted((r for r in manifest if r["role"] == "feedback"), key=lambda r: r["bbox"][1])
        self.assertEqual([r["qid"] for r in entries], ["Q1", "Q2", "Q3", "Q4", "Q5"])


class UnderRowTests(unittest.TestCase):
    def test_a_full_row_mark_never_reads_as_the_next_points_mark(self):
        # 139e179a page 3: "Structural plain" ran to the edge; its pair went under
        # it, after "ii) Erosional plain ... wind", and read as that point's.
        doc, ocr = _sheet([("P1L1", "c) i) Structural plain Great plains of USA and more", 60, 585),
                           ("P1L2", "ii) Erosional plain Rivers and wind", 60, 330)])
        rep = _report([_q("Q10.c", 2, 2, 1, "P1L2", [_c(1, 1, "P1L1"), _c(1, 1, "P1L2")])])
        _, manifest = _build(doc, ocr, rep)
        steps = {tuple(r["criteria"]): r for r in manifest if r["qid"] == "Q10.c" and r["role"] == "step"}
        first, second = steps[(0,)], steps[(1,)]
        # manifest boxes are in finished-page coordinates (cover strip included)
        y1 = (first["anchor_bbox"][1] + first["anchor_bbox"][3]) / 2
        y2 = (second["anchor_bbox"][1] + second["anchor_bbox"][3]) / 2
        yc = (first["verdict_bbox"][1] + first["verdict_bbox"][3]) / 2
        self.assertLess(abs(yc - y1), abs(yc - y2), first)
        self.assertGreaterEqual(second["verdict_bbox"][0], second["anchor_bbox"][2])   # after "wind"


class SameRowTests(unittest.TestCase):
    def test_two_points_whose_boxes_end_on_one_row_get_one_mark(self):
        # 5d94afe5 page 2: two tilted OCR boxes over rows 2 to 3 and 3 both end on
        # row 3; two pairs there collided past the divider.
        doc, ocr = _sheet([("P1L1", "Q7 d) 1) Written in Prakrit using Brahmi", 60, 420),
                           ("P1L2", "2) Ashoka dhamma policy", 60, 420),
                           ("P1L3", "3) They give evidence of the empire", 60, 420)])
        l2 = ocr[0].lines[1]
        l3 = ocr[0].lines[2]
        ocr[0].lines[1] = LineBox("P1L2", l2.text, (l2.bbox[0], l2.bbox[1], l2.bbox[2], l3.bbox[3]), 1)
        rep = _report([_q("Q7.d", 2, 3, 1, "P1L3",
                          [_c(1, 1, "P1L1"), _c(0.5, 1, "P1L2"), _c(0.5, 1, "P1L3")],
                          remark="Other points unclear.")])
        _, manifest = _build(doc, ocr, rep)
        steps = [r for r in manifest if r["qid"] == "Q7.d" and r["role"] == "step"]
        self.assertEqual(len(steps), 2, steps)
        self.assertEqual(sorted(r["score"] for r in steps), [1.0, 1.0])
        self.assertEqual(sorted(len(r["criteria"]) for r in steps), [1, 2])


if __name__ == "__main__":
    unittest.main()
