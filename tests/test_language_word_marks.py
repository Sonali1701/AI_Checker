# -*- coding: utf-8 -*-
"""Word marks on language papers (2026-09-21).

Operator's rule: only language papers, and only when we are confident, because an
underline under a CORRECT word is worse than a slip left unmarked. A slip is drawn
only when a vision reading of the page (never shown the OCR) and Mathpix's OCR of
the same line read the SAME wrong word, and the word's own box is measured.
"""
from __future__ import annotations

import os
import sys
import unittest

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import preflight  # noqa: E402
import teacher_layout  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402
from language_marks import (MAX_PER_ANSWER, WordSlip, WordSlipSheet, is_language_paper,  # noqa: E402
                            take_word_marks, verify_word_slips)
from mathpix import LineBox, PageOCR, WordBox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _line(lid, text, row, page=1, **kw):
    y = 100 + 60 * row
    return LineBox(lid, text, (100, y, 1100, y + 40), page, **kw)


def _words(line: LineBox, confidence=0.95):
    x, out = line.bbox[0], []
    for token in line.text.split():
        width = 22 * len(token)
        out.append(WordBox(token, (x, line.bbox[1] + 4, x + width, line.bbox[3] - 4), confidence))
        x += width + 20
    return out


def _page(lines, page=1, word_confidence=0.95):
    words = [w for line in lines for w in _words(line, word_confidence)]
    return PageOCR(page, 1200, 1700, list(lines), words)


def _report(regions, questions=None):
    questions = questions or [QuestionMark(qid="Q1", score=3, max_score=5, page=1, y_fraction=0.1,
                                           anchor_line_id="P1L1", criteria=[])]
    return GradeReport(total_score=3, max_total=5, overall_remarks="", questions=questions,
                       evidence_contract=True, answer_regions=regions)


def _slip(lid, written, correct, confidence=0.97, kind="spelling"):
    return WordSlip(line_id=lid, written=written, correct=correct, confidence=confidence, kind=kind)


class Scope(unittest.TestCase):
    def test_only_language_and_literature_papers(self):
        for subject in ("English", "English Language and Literature", "Hindi", "Sanskrit",
                        "ENGLISH GRAMMAR", "French"):
            self.assertTrue(is_language_paper(subject), subject)
        for subject in ("Social Science", "Accountancy", "Business Laws", "Physics", "", None,
                        "Taxation", "Mathematics", "Design and Architecture"):
            self.assertFalse(is_language_paper(subject), subject)

    def test_another_subject_never_pays_for_the_reader(self):
        called = []
        marks = take_word_marks(report=_report({}), ocr_pages=[_page([_line("P1L1", "a b c", 0)])],
                                student_pngs=[b"png"], subject="Social Science",
                                google_token="t", task_id="k",
                                reader=lambda *a, **k: called.append(1))
        self.assertEqual((marks, called), ([], []))

    def test_the_pass_fails_open(self):
        def broken(*a, **k):
            raise RuntimeError("proxy 502")
        lines = [_line("P1L1", "I recieve the letter", 0)]
        report = _report({"q1": {"qid": "Q1", "line_ids": ["P1L1"]}})
        self.assertEqual(take_word_marks(report=report, ocr_pages=[_page(lines)], student_pngs=[b"x"],
                                         subject="English", google_token="t", task_id="k",
                                         reader=broken), [])

    def test_production_turns_it_on_and_the_pipeline_gates_it_by_subject(self):
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as handle:
            self.assertIn("language_marks=True", handle.read())
        with open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("if is_language_paper(subject, student_class):", source)
        with open(os.path.join(ROOT, "language_marks.py"), encoding="utf-8") as handle:
            reader = handle.read().split("def read_word_slips", 1)[1].split("def take_word_marks", 1)[0]
        # The vision reader must never be shown the OCR text: agreement with the OCR
        # only means something when the two readings are independent.
        self.assertNotIn("build_transcript", reader)
        self.assertNotIn(".text", reader.replace('"text"', "").replace("_claude_text", ""))


class ConfidenceGate(unittest.TestCase):
    def setUp(self):
        self.lines = [
            _line("P1L1", "Q1 My Favourite Festival", 0),
            _line("P1L2", "I recieve many gifts from my freinds", 1),
            _line("P1L3", "We definately enjoy the the sweets", 2),
            _line("P1L4", "Diwali is celebrated in Ayodhya by Bharat", 3),
            _line("P1L5", "He go to the temple", 4),
            _line("P1L6", "this line was struck out recieve", 5, cancelled=True),
            _line("P1L7", "a faint line with recieve in it", 6, confidence=0.4),
        ]
        self.page = _page(self.lines)
        self.report = _report({"q1": {"qid": "Q1", "line_ids": [l.line_id for l in self.lines]}})

    def marks(self, slips, **kw):
        return verify_word_slips(WordSlipSheet(items=slips), [self.page], self.report, **kw)

    def test_a_slip_both_readings_agree_on_is_marked_on_its_own_word_box(self):
        marks, dropped = self.marks([_slip("P1L2", "recieve", "receive")])
        self.assertEqual(dropped, {})
        self.assertEqual([(m["written"], m["correct"], m["line_id"]) for m in marks],
                         [("recieve", "receive", "P1L2")])
        box = next(w.bbox for w in self.page.words if w.text == "recieve")
        self.assertEqual(tuple(marks[0]["bbox"]), tuple(float(v) for v in box))

    def test_when_the_ocr_read_the_word_correctly_nothing_is_drawn(self):
        # The model "saw" a misspelling that the OCR of that line does not contain.
        marks, dropped = self.marks([_slip("P1L4", "celebrted", "celebrated"),
                                     _slip("P1L3", "recieve", "receive")])
        self.assertEqual(marks, [])
        self.assertEqual(dropped, {"OCR does not read the same word once on that line": 2})

    def test_every_other_doubt_drops_the_mark(self):
        cases = {
            "model not confident": _slip("P1L2", "recieve", "receive", confidence=0.85),
            "model not confident ": _slip("P1L5", "go", "goes", confidence=0.92, kind="grammar"),
            "not a single plain word": _slip("P1L2", "many gifts", "many gift"),
            "line not eligible": _slip("P1L6", "recieve", "receive"),          # struck out
            "line not eligible ": _slip("P1L7", "recieve", "receive"),         # OCR unsure of the line
            "British and American spellings are both right": _slip("P1L1", "Favourite", "Favorite"),
            "OCR does not read the same word once on that line": _slip("P1L3", "the", "then"),
            "correction is a different word": _slip("P1L2", "gifts", "presentations"),
            "correction equals the word": _slip("P1L2", "Recieve", "recieve"),
        }
        for reason, slip in cases.items():
            marks, dropped = self.marks([slip])
            self.assertEqual(marks, [], reason)
            self.assertEqual(list(dropped), [reason.strip()], reason)

    def test_a_word_the_paper_itself_prints_is_never_a_slip(self):
        marks, dropped = self.marks([_slip("P1L4", "Ayodhya", "Ayodhaya")],
                                    reference_text="Write about Diwali in Ayodhya.")
        self.assertEqual(marks, [])
        self.assertEqual(list(dropped), ["the paper or key spells it that way"])

    def test_a_word_that_cannot_be_placed_exactly_is_not_marked(self):
        unsure = _page(self.lines, word_confidence=0.5)          # word contours not trusted
        marks, dropped = verify_word_slips(WordSlipSheet(items=[_slip("P1L2", "recieve", "receive")]),
                                           [unsure], self.report)
        self.assertEqual(marks, [])
        self.assertEqual(list(dropped), ["word box not located uniquely"])

    def test_a_clear_wrong_form_needs_the_higher_bar_and_passes_it(self):
        marks, _ = self.marks([_slip("P1L5", "go", "goes", confidence=0.97, kind="grammar")])
        self.assertEqual(marks, [])                               # two letters: below the word floor
        self.lines[4] = _line("P1L5", "The childs play outside", 4)
        self.page = _page(self.lines)
        marks, _ = self.marks([_slip("P1L5", "childs", "children", confidence=0.97, kind="grammar")])
        self.assertEqual([m["written"] for m in marks], ["childs"])

    def test_lines_of_an_mcq_or_of_two_answers_are_left_alone(self):
        objective = QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=0.1,
                                 anchor_line_id="P1L2", criteria=[], objective=True)
        report = _report({"q1": {"qid": "Q1", "line_ids": ["P1L2"]}}, [objective])
        self.assertEqual(verify_word_slips(WordSlipSheet(items=[_slip("P1L2", "recieve", "receive")]),
                                           [self.page], report)[0], [])
        shared = _report({"q1": {"qid": "Q1", "line_ids": ["P1L2"]},
                          "q2": {"qid": "Q2", "line_ids": ["P1L2"]}})
        self.assertEqual(verify_word_slips(WordSlipSheet(items=[_slip("P1L2", "recieve", "receive")]),
                                           [self.page], shared)[0], [])

    def test_only_a_handful_per_answer_the_clearest_first(self):
        words = ["recieve", "freind", "beleive", "seperate", "definately", "occured", "untill"]
        lines = [_line(f"P1L{i}", f"the word {w} here", i) for i, w in enumerate(words, start=1)]
        page = _page(lines)
        report = _report({"q1": {"qid": "Q1", "line_ids": [l.line_id for l in lines]}})
        fixes = ["receive", "friend", "believe", "separate", "definitely", "occurred", "until"]
        slips = [_slip(f"P1L{i}", w, f, confidence=0.90 + i / 100)
                 for i, (w, f) in enumerate(zip(words, fixes), start=1)]
        marks, dropped = verify_word_slips(WordSlipSheet(items=slips), [page], report)
        self.assertEqual(len(marks), MAX_PER_ANSWER)
        self.assertEqual(sorted(m["written"] for m in marks), sorted(words[-MAX_PER_ANSWER:]))
        self.assertEqual(dropped, {"over the per-answer or per-page cap": len(words) - MAX_PER_ANSWER})

    def test_the_readers_reply_is_parsed_whatever_it_calls_its_fields(self):
        sheet = WordSlipSheet.model_validate({"items": [
            {"line": "[P1L2]", "wrong": "recieve", "correction": "receive", "confidence": "96%",
             "kind": "Spelling mistake"}]})
        self.assertEqual((sheet.items[0].line_id, sheet.items[0].written, sheet.items[0].correct,
                          sheet.items[0].confidence, sheet.items[0].kind),
                         ("P1L2", "recieve", "receive", 0.96, "spelling"))


W, H, PX, PITCH = 595.0, 842.0, 2.0, 26.0


def _scan(rows):
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    lines = []
    for i, (lid, text, x0, x1) in enumerate(rows):
        base = 160 + i * PITCH * 2           # double spacing: clear paper above each row
        page.insert_text((x0, base), text, fontsize=14, color=(0.1, 0.1, 0.35))
        lines.append(LineBox(lid, text, (x0 * PX, (base - 13) * PX, x1 * PX, (base + 4) * PX), 1))
    return doc, [PageOCR(1, int(W * PX), int(H * PX), lines)]


class Drawing(unittest.TestCase):
    def build(self, word_marks):
        doc, ocr = _scan([("P1L1", "Q1 I recieve many gifts", 60, 300),
                          ("P1L2", "from my friends every year", 60, 320)])
        question = QuestionMark(qid="Q1", score=3, max_score=5, page=1, anchor_line_id="P1L2",
                                y_fraction=0.0, remark="Add two more points about the festival.",
                                criteria=[Criterion(description="point", awarded=3, max=5, met=False,
                                                    step_line_id="P1L2",
                                                    evidence="Two points are missing.")])
        report = GradeReport(total_score=3, max_total=5, overall_remarks="Good effort. Add more points.",
                             section_totals=[], questions=[question], evidence_contract=True,
                             word_marks=word_marks)
        pdf = teacher_layout.build_teacher_pdf(doc, report, ocr)
        return fitz.open(stream=pdf, filetype="pdf"), report, pdf

    def test_the_word_is_underlined_and_corrected_and_the_gate_still_passes(self):
        mark = {"page": 1, "line_id": "P1L1", "qid": "Q1", "written": "recieve", "correct": "receive",
                "kind": "spelling", "confidence": 0.97,
                "bbox": [95 * PX, 147 * PX, 145 * PX, 164 * PX],
                "image_size": [int(W * PX), int(H * PX)]}
        plain, plain_report, plain_pdf = self.build([])
        marked, report, pdf = self.build([mark])
        header = float(report.render_page_layouts["1"]["content_bbox"][1])
        under = fitz.Rect(95, 147 + header, 145, 164 + header + 6)

        def red_span(document):
            """Horizontal extent of the red strokes lying just under the word."""
            xs = [x for d in document[0].get_drawings()
                  if d.get("color") and d["color"][0] > 0.6 and d["color"][1] < 0.3
                  and fitz.Rect(d["rect"]).intersects(under)
                  for x in (d["rect"].x0, d["rect"].x1)]
            return (max(xs) - min(xs)) if xs else 0.0

        self.assertEqual(red_span(plain), 0.0)
        self.assertGreater(red_span(marked), 45.0)          # the whole 50pt word, not a dot
        self.assertIn("receive", marked[0].get_text())
        self.assertNotIn("receive", plain[0].get_text())
        before = {i.code for i in preflight.release_blocking(
            preflight.check_copy(plain_report, pdf_bytes=plain_pdf, total_marks=5))}
        after = {i.code for i in preflight.release_blocking(
            preflight.check_copy(report, pdf_bytes=pdf, total_marks=5))}
        self.assertEqual(after, before)

    def test_a_mark_with_a_broken_record_never_costs_the_copy(self):
        marked, _, _ = self.build([{"page": 1, "bbox": [1, 2], "image_size": [0, 0]}])
        self.assertEqual(marked.page_count, 1)


if __name__ == "__main__":
    unittest.main()
