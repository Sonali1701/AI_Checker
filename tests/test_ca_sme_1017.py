# -*- coding: utf-8 -*-
"""CA faculty feedback of 17 Oct 2026 on the 21 tool-checked copies of 10 Oct, in code.

Double credit (one answer paid under two questions), marks for unnumbered or
unwritten answers, one-line restatements paid, wrong and derived figures ticked,
margin remarks written over each other, and pages left with no mark.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import preflight  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402


def _q(qid, score, max_score, lines, objective=False):
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=2, y_fraction=0.2,
                        anchor_line_id=lines[0] if lines else "", objective=objective, attempted=True,
                        mark_style="per_step",
                        criteria=[Criterion(description=f"step {i}", awarded=1.0, max=1.0, met=True,
                                            step_line_id=line) for i, line in enumerate(lines)])


def _report(questions, regions):
    return GradeReport(total_score=sum(q.score for q in questions), max_total=sum(q.max_score for q in questions),
                       overall_remarks="", questions=questions, answer_regions=regions)


class DoubleCredit(unittest.TestCase):
    """Accounting 8a3f83b2: Q2's machinery account continues on page 3 and Q6 was
    paid 7.5 on those same page 3 lines."""

    def regions(self):
        return {"q2": {"qid": "Q2", "line_ids": ["P2L1", "P2L2", "P2L3", "P3L1", "P3L2", "P3L3", "P3L4"]},
                "q6": {"qid": "Q6", "line_ids": ["P3L2", "P3L3", "P3L4"]}}

    def test_a_question_paid_on_another_answers_lines_is_found(self):
        report = _report([_q("Q2", 3.5, 5, ["P2L1", "P2L3", "P3L1"]), _q("Q6", 7.5, 10, ["P3L2", "P3L3", "P3L4"])],
                         self.regions())
        found = preflight.double_credited(report)
        self.assertEqual(list(found), ["q6"])
        self.assertIn("Q6 is marked on writing that belongs to Q2", found["q6"])
        codes = [i.code for i in preflight.check_copy(report)]
        self.assertIn("double-credit", codes)
        self.assertIn("double-credit", preflight.SCORE_INTEGRITY_CODES)

    def test_separate_answers_are_not_double_credit(self):
        regions = {"q2": {"qid": "Q2", "line_ids": ["P2L1", "P2L2", "P2L3"]},
                   "q6": {"qid": "Q6", "line_ids": ["P3L2", "P3L3", "P3L4"]}}
        report = _report([_q("Q2", 3.5, 5, ["P2L1", "P2L3"]), _q("Q6", 7.5, 10, ["P3L2", "P3L3", "P3L4"])], regions)
        self.assertEqual(preflight.double_credited(report), {})

    def test_a_part_and_its_sub_parts_and_or_alternatives_are_not_compared(self):
        regions = {"q4": {"qid": "Q4", "line_ids": ["P2L1", "P2L2", "P2L3"]},
                   "q4a": {"qid": "Q4(a)", "line_ids": ["P2L1", "P2L2", "P2L3"]},
                   "q5alt1": {"qid": "Q5.alt1", "line_ids": ["P3L1", "P3L2"]},
                   "q5alt2": {"qid": "Q5.alt2", "line_ids": ["P3L1", "P3L2"]}}
        report = _report([_q("Q4", 4, 6, ["P2L1", "P2L2"]), _q("Q4(a)", 3, 4, ["P2L1", "P2L2", "P2L3"]),
                          _q("Q5.alt1", 3, 5, ["P3L1", "P3L2"]), _q("Q5.alt2", 3, 5, ["P3L1", "P3L2"])], regions)
        self.assertEqual(preflight.double_credited(report), {})

    def test_mcqs_and_small_parts_are_left_to_their_own_checks(self):
        regions = {"q1": {"qid": "Q1", "line_ids": ["P1L1", "P1L2", "P1L3"]},
                   "q2": {"qid": "Q2", "line_ids": ["P1L1", "P1L2", "P1L3"]}}
        report = _report([_q("Q1", 2, 2, ["P1L1", "P1L2"]), _q("Q2", 2, 2, ["P1L1", "P1L2"])], regions)
        self.assertEqual(preflight.double_credited(report), {})

    def test_the_regrade_is_asked_to_mark_it_again(self):
        import inspect
        import pipeline
        source = inspect.getsource(pipeline._regrade_invalid_questions)
        self.assertIn("double_credited(report)", source)
        self.assertIn('StrictGradeError("DOUBLE_CREDIT", why)', source)


class TheCaRules(unittest.TestCase):
    def setUp(self):
        self.rules = " ".join(categories.get("ca").prompt.extra_rules.split())

    def test_one_piece_of_writing_answers_one_question(self):
        self.assertIn("One piece of writing answers ONE question", self.rules)
        self.assertIn("never guess an answer for a question", self.rules)

    def test_restating_the_question_and_wrong_points_earn_nothing(self):
        self.assertIn("a line that only names the topic or restates the question", self.rules)
        self.assertIn("Do not give half marks for effort", self.rules)
        self.assertIn("one point the student wrote is paid once, never twice", self.rules)

    def test_a_wrong_or_derived_figure_is_crossed(self):
        self.assertIn("A figure that is wrong is crossed, never ticked", self.rules)
        self.assertIn("a figure DERIVED from a wrong one", self.rules)
        self.assertIn('its evidence must say "carried forward"', self.rules)

    def test_every_written_page_is_accounted_for(self):
        self.assertIn("Every page the student wrote on is accounted for", self.rules)
        self.assertIn('"not evaluated"', self.rules)


class WrongFiguresAreCrossed(unittest.TestCase):
    """Taxation 4536f8b8 Q2(a): "Incorrect salary amount is marked correct, despite
    remarks stating it is incorrect"; Adv Accounting 4e6ca813: "PPE working note is
    incorrect but it is marked correct"."""

    def setUp(self):
        import pdf_renderer as pr
        self.pr = pr
        self.report_token = pr._RENDER_REPORT.set(None)
        self.safe_token = pr._SAFE_LAYOUT.set(False)
        for store in (pr._MARK_BOXES, pr._MARK_INK, pr._CONTENT_W, pr._SHEET_RIGHT):
            store.clear()

    def tearDown(self):
        self.pr._RENDER_REPORT.reset(self.report_token)
        self.pr._SAFE_LAYOUT.reset(self.safe_token)

    def test_the_evidence_wording_that_means_a_wrong_figure(self):
        names = self.pr.evidence_names_wrong_figure
        wrong = ["Gross salary should be Rs.8,16,150, not Rs.10,53,610.",
                 "PPE working note is incorrect", "Expected rent should be Rs.1,17,500, not Rs.85,000",
                 "student: 7,61,024 instead of 7,96,500", "Surcharge applied at 25%, not 15%"]
        right = ["Gross salary carried forward from the student's own figure is applied correctly",
                 "Correct treatment", "", "Format and headings present"]
        for text in wrong:
            self.assertTrue(names([Criterion(description="d", awarded=1, max=2, met=False, evidence=text)], [0]), text)
        for text in right:
            self.assertFalse(names([Criterion(description="d", awarded=1, max=2, met=False, evidence=text)], [0]), text)

    def _glyphs(self, *, ca, verdict, score):
        import fitz
        from unittest.mock import patch
        report = GradeReport(total_score=1, max_total=2, overall_remarks="", whole_total=ca,
                             questions=[QuestionMark(qid="Q2", score=1, max_score=2, page=1,
                                                     y_fraction=.2, attempted=True)])
        token = self.pr._RENDER_REPORT.set(report)
        try:
            with fitz.open() as doc:
                doc.new_page(width=595, height=500)
                line = {"lid": "P1L1", "page": 1, "x0": 40, "x1": 180, "y0": 100, "y1": 118, "text": "10,03,610"}
                with patch.object(self.pr, "_draw_tick", wraps=self.pr._draw_tick) as tick, \
                     patch.object(self.pr, "_draw_cross", wraps=self.pr._draw_cross) as cross:
                    self.assertTrue(self.pr._draw_sheet_verdict(doc, "Q2", line, verdict, score=score))
                return tick.call_count, cross.call_count
        finally:
            self.pr._RENDER_REPORT.reset(token)

    def test_on_a_ca_paper_a_wrong_step_with_part_marks_is_crossed(self):
        self.assertEqual(self._glyphs(ca=True, verdict="wrong", score=1.25), (0, 1))
        self.assertEqual(self._glyphs(ca=True, verdict="partial", score=1.25), (1, 0), "a fair partial stays a tick")
        self.assertEqual(self._glyphs(ca=True, verdict="correct", score=2), (1, 0))

    def test_other_categories_keep_one_tick_for_any_positive_award(self):
        self.assertEqual(self._glyphs(ca=False, verdict="wrong", score=1.25), (1, 0))
        self.assertEqual(self._glyphs(ca=False, verdict="partial", score=0), (0, 1))


class TheScoreIsWrittenOnce(unittest.TestCase):
    """Auditing f767db4c: "Q4c marks allotted twice on page 3 and 4"; the margin
    printed "Q4(c) 1/2" on both pages of one answer."""

    def test_a_note_on_another_page_names_its_question_without_the_score(self):
        import fitz
        import pdf_renderer as pr
        report = GradeReport(total_score=1, max_total=2, overall_remarks="", evidence_contract=True,
                             questions=[QuestionMark(qid="Q4(c)", score=1, max_score=2, page=1,
                                                     y_fraction=.2, attempted=True)])
        from unittest.mock import patch
        token = pr._RENDER_REPORT.set(report)
        drawn: dict[int, list[str]] = {}
        real = pr._draw_handwritten_remark

        def capture(page, x, y, text, **kwargs):
            drawn.setdefault(page.number, []).append(text)
            return real(page, x, y, text, **kwargs)
        try:
            with fitz.open() as doc, patch.object(pr, "_draw_handwritten_remark", capture):
                for _ in range(2):
                    doc.new_page(width=700, height=800)
                pr._draw_strip_entry(doc[0], "Q4(c)", 1, 2, "No conclusion recommending continued skepticism.", 200)
                pr._draw_strip_entry(doc[1], "Q4(c)", 1, 2, "Additional procedures are missing.", 200,
                                     show_score=False, label_only=True)
        finally:
            pr._RENDER_REPORT.reset(token)
        first, second = " | ".join(drawn.get(0, [])), " | ".join(drawn.get(1, []))
        self.assertIn("1/2", first)
        self.assertNotIn("1/2", second)
        self.assertTrue(second.startswith("Q4"), second)
        self.assertIn("Additional procedures are missing.", second)
        rows = [r for r in report.render_manifest if r.get("role") == "feedback"]
        self.assertEqual([r["margin_score"] for r in rows], [True, False])
        self.assertEqual([r["label_only"] for r in rows], [False, True])

    def test_the_question_pages_put_the_score_beside_the_total_once(self):
        import inspect
        import pdf_renderer as pr
        source = inspect.getsource(pr)
        self.assertIn("give_score = not score_shown and reason_row['page'] == score_page", source)
        self.assertIn("show_score=not score_shown, label_only=score_shown", source)


class NoWrittenPageIsLeftBlank(unittest.TestCase):
    """Business Laws 9e74a23c "some pages are not checked"; Auditing f5b09984 "second
    last page is not checked, no marks, not even zero"; Taxation 4536f8b8 "no comment
    is provided for the extra attempted question"."""

    def test_unclaimed_pages_get_a_note_and_a_ca_copy_names_them_for_the_faculty(self):
        import fitz
        from unittest.mock import patch
        import pdf_renderer as pr
        report = GradeReport(total_score=4, max_total=10, overall_remarks="", evidence_contract=True,
                             whole_total=True,
                             questions=[QuestionMark(qid="Q1", score=4, max_score=10, page=1,
                                                     y_fraction=.2, attempted=True)],
                             uncounted_choice=[{"qid": "Q3", "page": 3, "rule": "first_attempted"}])
        report.render_manifest = [{"qid": "Q1", "role": "step", "page": 1, "bbox": [10, 10, 20, 20]}]

        def rows(page, n):
            return {f"P{page}L{i}": {"lid": f"P{page}L{i}", "page": page, "x0": 40, "x1": 300,
                                     "y0": 100 + 30 * i, "y1": 118 + 30 * i,
                                     "text": f"Answer line {i} on page {page}"} for i in range(n)}
        by_lid = {**rows(1, 5), **rows(2, 6), **rows(3, 4), **rows(4, 1)}
        notes: dict[int, str] = {}
        real = pr._draw_strip_entry

        def capture(page, qid, *args, **kwargs):
            notes[page.number + 1] = kwargs.get("note", "")
            return real(page, qid, *args, **kwargs)
        token = pr._RENDER_REPORT.set(report)
        try:
            with fitz.open() as doc, patch.object(pr, "_draw_strip_entry", capture):
                for n in range(4):
                    doc.new_page(width=700, height=800)
                    pr._CONTENT_W[(id(doc), n + 1)] = 480.0      # a 220pt margin strip, as on a real copy
                noted = pr._note_pages_nobody_marked(doc, by_lid)
                for n in range(4):
                    pr._CONTENT_W.pop((id(doc), n + 1), None)
        finally:
            pr._RENDER_REPORT.reset(token)
        self.assertEqual(noted, [2, 3], "page 1 is marked, page 4 has one stray line")
        self.assertIn("could not be matched to a question", notes[2])
        self.assertIn("Q3 not counted", notes[3])
        self.assertIn("attempted first", notes[3])
        self.assertTrue(any("Page 2 has writing that no question was matched to" in r
                            for r in report.review_reasons))
        self.assertFalse(any("Page 3" in r for r in report.review_reasons), "an extra attempt is not a fault")


class MarginNotes(unittest.TestCase):
    def test_overlapping_margin_notes_are_repaired_in_every_category(self):
        import inspect
        import pipeline
        source = inspect.getsource(pipeline)
        start = source.index("repairable_layout_codes = {")
        general = source[start: source.index("}", start)]
        self.assertIn('"feedback-overlap"', general, "repaired for CA too, not only commerce")


if __name__ == "__main__":
    unittest.main()
