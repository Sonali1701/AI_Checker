"""A positive award uses one tick; an attempted zero uses one cross."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
import pdf_renderer as pr
from grader import Criterion, GradeReport, QuestionMark


class PartialCreditGlyphTests(unittest.TestCase):
    def setUp(self):
        self.report_token = pr._RENDER_REPORT.set(None)
        self.safe_token = pr._SAFE_LAYOUT.set(False)
        pr._MARK_BOXES.clear()
        pr._MARK_INK.clear()
        pr._CONTENT_W.clear()
        pr._SHEET_RIGHT.clear()

    def tearDown(self):
        pr._RENDER_REPORT.reset(self.report_token)
        pr._SAFE_LAYOUT.reset(self.safe_token)

    @staticmethod
    def line(lid="P1L1", y=100, text="Student answer"):
        return {"lid": lid, "page": 1, "x0": 40, "x1": 180,
                "y0": y, "y1": y + 18, "text": text}

    @staticmethod
    def sheet():
        document = fitz.open()
        document.new_page(width=595, height=500)
        return document

    def test_certified_partial_and_full_verdicts_use_only_a_tick(self):
        for verdict, score, wrong in (
                ("correct", None, False), ("partial", None, False), ("wrong", None, True),
                ("wrong", .5, False), ("partial", 0, True), ("correct", 0, True)):
            with self.subTest(verdict=verdict, score=score), self.sheet() as document:
                pr._MARK_BOXES.clear()
                with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                     patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                    self.assertTrue(pr._draw_sheet_verdict(
                        document, "Q1", self.line(), verdict, score=score))
                self.assertEqual(tick.call_count, int(not wrong))
                self.assertEqual(cross.call_count, int(wrong))
                self.assertTrue(document[0].get_drawings())

    def test_mcq_partial_full_and_zero_use_one_matching_glyph(self):
        for score in (.5, 1, 0):
            with self.subTest(score=score), self.sheet() as document:
                pr._MARK_BOXES.clear()
                pr._MARK_INK.clear()
                line = self.line()
                with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                     patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                    self.assertTrue(pr._draw_mcq_verdict(
                        document, document[0], "Q1", line, {line["lid"]: line},
                        score, 1, 12, "Q1"))
                self.assertEqual(tick.call_count, int(score > 0))
                self.assertEqual(cross.call_count, int(score == 0))
                text = document[0].get_text().strip()
                self.assertEqual(text, f"{score:g}" if score > 0 else "")

    def test_partial_verdict_metadata_and_score_are_preserved(self):
        question = QuestionMark(qid="Q1", score=.5, max_score=1,
                                page=1, y_fraction=.2, attempted=True)
        report = GradeReport(total_score=.5, max_total=1, overall_remarks="",
                             questions=[question], evidence_contract=True)
        token = pr._RENDER_REPORT.set(report)
        try:
            with self.sheet() as document:
                self.assertTrue(pr._draw_sheet_verdict(
                    document, "Q1", self.line(), "partial", score=.5))
            self.assertEqual(report.render_manifest[0]["verdict"], "partial")
            self.assertEqual(report.render_manifest[0]["score"], .5)
            self.assertEqual(question.score, .5)
        finally:
            pr._RENDER_REPORT.reset(token)

    def test_numerical_partial_full_and_zero_steps_keep_their_awards(self):
        for score in (.5, 2, 0):
            with self.subTest(score=score), self.sheet() as document:
                pr._MARK_BOXES.clear()
                pr._MARK_INK.clear()
                lines = [self.line(text="x = 2"),
                         self.line("P1L2", 180, "y = 1")]
                question = QuestionMark(
                    qid="Q1", score=score + 1, max_score=4, page=1,
                    y_fraction=.2, attempted=True, mark_style="per_step",
                    criteria=[
                        Criterion(description="First calculation", awarded=score,
                                  max=2, met=score == 2, step_line_id="P1L1",
                                  anchor_quote="x = 2"),
                        Criterion(description="Second calculation", awarded=1,
                                  max=2, met=False, step_line_id="P1L2",
                                  anchor_quote="y = 1"),
                    ])
                with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                     patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                    pr._render_question_marks(document, question, lines,
                        {line["lid"]: line for line in lines}, {})
                self.assertEqual(tick.call_count, 1 + int(score > 0))
                self.assertEqual(cross.call_count, int(score == 0))
                self.assertEqual(question.score, score + 1)
                if score > 0:
                    self.assertIn(f"{score:g}", document[0].get_text().splitlines())

    def test_whole_descriptive_partial_full_and_zero_use_one_matching_glyph(self):
        for score in (.5, 2, 0):
            with self.subTest(score=score), self.sheet() as document:
                pr._MARK_BOXES.clear()
                pr._MARK_INK.clear()
                line = self.line()
                question = QuestionMark(qid="Q1", score=score, max_score=2,
                    page=1, y_fraction=.2, attempted=True, mark_style="single")
                with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                     patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                    pr._render_question_marks(document, question, [line],
                        {line["lid"]: line}, {})
                self.assertEqual(tick.call_count, int(score > 0))
                self.assertEqual(cross.call_count, int(score == 0))
                self.assertEqual(question.score, score)
                if 0 < score < 2:
                    self.assertIn(f"{score:g}", document[0].get_text().splitlines())

    def test_unattempted_zero_answer_has_no_invented_glyph(self):
        with self.sheet() as document:
            question = QuestionMark(qid="Q1", score=0, max_score=2,
                page=1, y_fraction=.2, attempted=False, mark_style="single")
            with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                 patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                pr._render_question_marks(document, question, [], {}, {})
            self.assertEqual((tick.call_count, cross.call_count), (0, 0))

    def test_zero_criterion_does_not_add_a_cross_to_positive_whole_answer_cluster(self):
        with self.sheet() as document:
            lines = [self.line(text="First point"),
                     self.line("P1L2", 180, "Last point")]
            question = QuestionMark(qid="Q1", score=.5, max_score=2,
                page=1, y_fraction=.2, attempted=True, mark_style="single",
                criteria=[
                    Criterion(description="First point", awarded=.5, max=1,
                              met=False, step_line_id="P1L1"),
                    Criterion(description="Last point", awarded=0, max=1,
                              met=False, step_line_id="P1L2"),
                ])
            with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as tick, \
                 patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as cross:
                pr._render_question_marks(document, question, lines,
                    {line["lid"]: line for line in lines}, {})
            # The score keeps its one tick and never shares a row with a cross. Since
            # 2026-10-09 the score sits on the answer's last row that is not wrong, so
            # the wrong last point is crossed on its OWN row (Business Laws reviewer:
            # "cross is not there for wrong point"; test_reviewer_business_laws).
            self.assertEqual((tick.call_count, cross.call_count), (1, 1))
            tick_y = tick.call_args.args[2]
            cross_y = cross.call_args.args[2]
            self.assertGreater(abs(cross_y - tick_y), 30)
            self.assertEqual(question.score, .5)


if __name__ == "__main__":
    unittest.main()
