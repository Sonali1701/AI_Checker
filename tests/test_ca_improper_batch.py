# -*- coding: utf-8 -*-
"""Defects found grading the 16 "improper" CA copies of 2026-09-19.

Every check here reproduces what a real copy or paper did that day:
  * a combined FM + SM paper file prints "Total Marks: 50" twice and is out of 100;
  * "GENERAL MCQs (2 + 2 + 1 = 5 Marks)" priced a block the tag counter could not read;
  * one "(5 Marks)" tag shared by two OR alternatives refused a correct reading;
  * a Haiku breakdown short of (or over) a question's marks held five copies;
  * marks given to a part the student never wrote, cited from another answer;
  * a "≥" in a diagnostic print killed a graded copy on a cp1252 console;
  * /api/detect-marks blocked the whole server while one paper was read;
  * the marks graph was read by Haiku on 100-mark papers and lost their choice rules;
  * the cover opened with "You completed 0 of 8 answers fully."
"""
from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
from exam_package import RubricCriterionSpec  # noqa: E402,F401  (import check only)
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FM_SM = """CA Inter Udesh Regular Sep 2026 (Group-02)
Total Marks: 50
21/07/2026 Total Time: 90 Min
Financial Management
General Instructions
Question 1 in Part B is mandatory and attempt any 2 from rest 3 Questions in Part B.
1. (A) ... (5 Marks)
CA Inter Udesh Regular Sep 2026 (Group-02)
Total Marks: 50
26/06/2026 Total Time: 90 Min
Strategic Management
General Instructions
Question 1 in Part B is mandatory and attempt any 2 from rest 3 Questions in Part B.
1. (A) ... (5 Marks)
"""


def _q(qid, score, max_score, criteria, anchor="", attempted=True, objective=False):
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=1, y_fraction=0.0,
                        anchor_line_id=anchor, attempted=attempted, objective=objective,
                        criteria=criteria)


class CombinedPaperTotal(unittest.TestCase):
    def test_two_papers_in_one_file_add_up(self):
        self.assertEqual(grader.paper_total_from_text(FM_SM), 100.0)

    def test_a_header_repeated_on_every_page_reads_once(self):
        page = ("CA Inter\nTotal Marks: 50\nCost and Management Accounting\n"
                "General Instructions\nAll questions are compulsory.\n1. ...\n")
        self.assertEqual(grader.paper_total_from_text(page * 3), 50.0)

    def test_a_second_total_without_its_own_instructions_is_not_a_paper(self):
        text = ("Total Marks: 100\nTaxation\nGeneral Instructions\nAnswer any two.\n"
                "Q5. The firm's total marks: 40 in the case study below were ...\n")
        self.assertEqual(grader.paper_total_from_text(text), 100.0)


class PrintedTags(unittest.TestCase):
    def test_a_sum_priced_block_is_counted(self):
        text = "CASE STUDY MCQ (2 Marks x 5 = 10 Marks)\nGENERAL MCQs (2 + 2 + 1 = 5 Marks)\n" \
               "(5 Marks)\n(5 Marks)\n(5 Marks)\n(8 Marks)\n(2 Marks)\n(8 Marks)\n(2 Marks)\n"
        self.assertEqual(grader.printed_tag_sum(text)[0], 50.0)

    def test_a_sum_that_does_not_add_up_is_ignored(self):
        self.assertEqual(grader.printed_tag_sum("(2 + 2 + 1 = 6 Marks)\n(5 Marks)\n")[0], 5.0)

    def test_one_tag_shared_by_an_or_pair(self):
        items = [MarksItem(qid=q, max=m) for q, m in
                 [("Q1", 10), ("Q2", 10), ("Q3", 10), ("Q4.B.alt1", 5), ("Q4.B.alt2", 5)]]
        scheme = MarksScheme(total=40, items=items, choice_groups=[{
            "group_id": "g", "select_count": 1, "contribution_marks": 5,
            "options": [{"option_id": "a", "question_ids": ["Q4.B.alt1"]},
                        {"option_id": "b", "question_ids": ["Q4.B.alt2"]}]}])
        text = "Q1 (10 Marks)\nQ2 (10 Marks)\nQ3 (10 Marks)\n(B) X OR Y (5 Marks)\nanswer any\n"
        self.assertTrue(grader.extraction_reconciles_with_tags(scheme, text, 35, True))
        # Without a choice rule the extra alternative is still a mismatch.
        self.assertFalse(grader.extraction_reconciles_with_tags(scheme, text, 35, False))

    def test_the_professional_prompt_names_the_boilerplate_trap(self):
        block = grader.MARKS_SCHEME_PROFESSIONAL_BLOCK
        self.assertIn("SPECIFIC choice instruction overrides generic boilerplate", block)
        self.assertIn("each paper's or section's", block)


class _Spec:
    def __init__(self, qid, max_marks, criteria=()):
        self.qid, self.max_marks, self.criteria = qid, max_marks, list(criteria)


class UncoveredSteps(unittest.TestCase):
    def test_a_short_breakdown_is_completed_at_zero(self):
        q = _q("Q6", 6, 10, [Criterion(description="a", awarded=4, max=5, met=False),
                            Criterion(description="b", awarded=2, max=3.5, met=False)])
        pipeline._record_uncovered_steps({"q6": q}, {"q6": _Spec("Q6", 10)},
                                         {"q6": "Q6: step maxima total 8.5; the paper allocates 10."})
        self.assertEqual(sum(c.max for c in q.criteria), 10)
        self.assertEqual(sum(c.awarded for c in q.criteria), 6)   # the score never rises
        self.assertEqual(q.criteria[-1].awarded, 0)

    def test_over_allocation_loses_only_unawarded_marks(self):
        q = _q("Q3.c", 1.5, 2, [Criterion(description="a", awarded=1, max=1.5, met=False),
                               Criterion(description="b", awarded=0.5, max=1, met=False)])
        pipeline._record_uncovered_steps({"q3.c": q}, {"q3.c": _Spec("Q3.c", 2)},
                                         {"q3.c": "Q3.c: step maxima total 2.5; the paper allocates 2."})
        self.assertEqual(sum(c.max for c in q.criteria), 2)
        self.assertEqual(sum(c.awarded for c in q.criteria), 1.5)

    def test_a_real_over_award_and_a_fixed_rubric_are_left_to_hold(self):
        over = _q("Q1", 3, 2, [Criterion(description="a", awarded=3, max=3, met=True)])
        pipeline._record_uncovered_steps({"q1": over}, {"q1": _Spec("Q1", 2)},
                                         {"q1": "Q1: step maxima total 3; the paper allocates 2."})
        self.assertEqual(over.criteria[0].max, 3)
        fixed = _q("Q2", 1, 5, [Criterion(description="a", awarded=1, max=3, met=False)])
        pipeline._record_uncovered_steps({"q2": fixed}, {"q2": _Spec("Q2", 5, criteria=["x"])},
                                         {"q2": "Q2: step maxima total 3; the paper allocates 5."})
        self.assertEqual(len(fixed.criteria), 1)


class CreditWithoutOwnAnswer(unittest.TestCase):
    def report(self, questions, regions):
        return GradeReport(total_score=sum(q.score for q in questions),
                           max_total=sum(q.max_score for q in questions),
                           overall_remarks="", questions=questions, answer_regions=regions)

    def test_marks_cited_from_another_answer_hold_the_copy(self):
        # CMA MTS-02: QB3.A was never written. Its "evidence" was the Q2(A) table.
        phantom = _q("QB3.A", 6, 8, [Criterion(description="x", awarded=6, max=8, met=False,
                                                step_line_id="P3D1")], anchor="P3D1")
        real = _q("QB2.A", 6, 8, [Criterion(description="y", awarded=6, max=8, met=False,
                                             step_line_id="P3D1")], anchor="P2D39")
        regions = {"qb.2.a": {"qid": "QB2.A", "line_ids": ["P2D39", "P3D1"]}}
        issues = preflight._check_credit_without_own_answer(self.report([phantom, real], regions))
        self.assertEqual([i.code for i in issues], ["credit-without-own-answer"])
        self.assertIn("QB3.A", issues[0].detail)
        self.assertIn("credit-without-own-answer", preflight.SCORE_INTEGRITY_CODES)

    def test_a_shared_mcq_answer_list_is_not_someone_elses_answer(self):
        mcq = _q("SM.Q2", 2, 2, [Criterion(description="x", awarded=2, max=2, met=True,
                                            step_line_id="P1L23")], anchor="P1L23")
        regions = {f"sm.q.1.{k}": {"qid": f"SM.Q1.{k}", "line_ids": ["P1L23"]} for k in "abc"}
        self.assertEqual(preflight._check_credit_without_own_answer(self.report([mcq], regions)), [])

    def test_an_answer_with_its_own_region_is_fine(self):
        q = _q("Q1", 3, 5, [Criterion(description="x", awarded=3, max=5, met=False,
                                       step_line_id="P1L4")], anchor="P1L4")
        regions = {"q1": {"qid": "Q1", "line_ids": ["P1L4"]}}
        self.assertEqual(preflight._check_credit_without_own_answer(self.report([q], regions)), [])


class ConsoleAndServer(unittest.TestCase):
    def test_a_cp1252_console_cannot_kill_a_copy(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        saved = sys.stdout
        try:
            sys.stdout = stream
            pipeline._tolerant_console()
            print("[marks] note: Q4.b ≥ 2 marks ₹ 500", flush=True)   # must not raise
        finally:
            sys.stdout = saved

    def test_marks_reading_never_runs_on_the_event_loop(self):
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        self.assertIn("await run_in_threadpool(\n            detect_marks_scheme,", src)
        self.assertIn("google_token = await run_in_threadpool(_require_allowed, authorization)", src)

    def test_marks_are_read_by_one_fixed_model_for_detect_and_evaluate(self):
        import server
        common = dict(google_token="t", task_id="t", student_class="CA Intermediate",
                      exam_type="professional")
        big = pipeline.cfg_from_request("claude", "", "Taxation", total_marks=100, **common)
        small = pipeline.cfg_from_request("claude", "", "Taxation", total_marks=50, **common)
        self.assertEqual(server._marks_reader_config(big).model, pipeline._BUDGET_GRADING_MODEL)
        # evaluate() builds its context from the size-routed config; it must still match.
        self.assertEqual(server._marks_extraction_context(big, "CA Intermediate", "Taxation"),
                         server._marks_extraction_context(server._marks_reader_config(big),
                                                          "CA Intermediate", "Taxation"))
        self.assertEqual(server._marks_extraction_context(big, "CA Intermediate", "Taxation"),
                         server._marks_extraction_context(small, "CA Intermediate", "Taxation"))


class PointListsAreNotQuestions(unittest.TestCase):
    @staticmethod
    def pages(rows):
        from mathpix import LineBox, PageOCR
        return [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
            LineBox(line_id=f"P1L{i}", text=t, bbox=(0, i * 30, 600, i * 30 + 20), page=1)
            for i, t in enumerate(rows, start=1)])]

    def test_a_restarted_prose_list_inside_an_answer(self):
        import answer_map
        rows = ["Q2 (B) Inventory control is the process of planning", "Basis to be adopted",
                "1). ABC analysis", "2) VED analysis", "3) Re-order level system",
                "4). Economic order Quantity."]
        self.assertEqual(answer_map._restarted_point_lists(self.pages(rows)),
                         {"P1L3", "P1L4", "P1L5", "P1L6"})

    def test_answers_numbered_from_the_top_and_mcq_lists_stay_labels(self):
        import answer_map
        self.assertEqual(answer_map._restarted_point_lists(self.pages(
            ["1.) answer one here", "2.) answer two here", "3.) answer three here"])), set())
        self.assertEqual(answer_map._restarted_point_lists(self.pages(
            ["Q6 Plains are formed", "1) Indica (b)", "2) (c)", "3) Tamil (a)"])), set())


class WidePhotoOfOnePage(unittest.TestCase):
    @staticmethod
    def pdf(width, height, lines):
        import fitz
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (width, height), (252, 252, 250))
        d = ImageDraw.Draw(img)
        for x0, x1, y in lines:
            d.rectangle([x0, y, x1, y + 18], fill=(40, 40, 90))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        doc = fitz.open()
        doc.new_page(width=width, height=height).insert_image(
            fitz.Rect(0, 0, width, height), stream=buf.getvalue())
        return doc.tobytes()

    def test_a_portrait_page_in_a_landscape_frame_is_not_cut(self):
        import fitz
        from orient import split_two_page_spreads
        # Corporate Law copy p4: one notebook page in the middle of a wide photo,
        # every line running through the centre.
        lines = [(560, 1440, 200 + 70 * i) for i in range(14)]
        out, _ = split_two_page_spreads(self.pdf(2000, 1400, lines), "s.pdf")
        self.assertEqual(fitz.open(stream=out, filetype="pdf").page_count, 1)

    def test_a_real_spread_is_still_split(self):
        import fitz
        from orient import split_two_page_spreads
        lines = [(x0, x1, 200 + 90 * i) for i in range(10)
                 for x0, x1 in ((60, 930), (1070, 1940))]
        out, changed = split_two_page_spreads(self.pdf(2000, 1400, lines), "s.pdf")
        self.assertTrue(changed)
        self.assertEqual(fitz.open(stream=out, filetype="pdf").page_count, 2)


class McqRemark(unittest.TestCase):
    def test_a_long_option_is_never_cut_mid_word(self):
        import wording
        long = "Assets that are tangible or otherwise and situated in India or outside India"
        self.assertEqual(wording.mcq_remark("C", {"C": long}), "Correct answer is (C).")
        self.assertEqual(wording.mcq_remark("C", {"C": "25 years"}), "Correct answer is (C) 25 years.")


class SidewaysPageDirection(unittest.TestCase):
    def test_an_upside_down_turn_is_caught_by_its_own_read(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from PIL import Image
        from mathpix import LineBox, PageOCR

        def page(conf, n=2):
            return PageOCR(n, 1000, 1400, [LineBox(f"P{n}L{i}", "x", (60 + 40 * i, 40, 80 + 40 * i, 480), n,
                                                   confidence=conf) for i in range(1, 5)])
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(buf, format="PNG")
        pngs, pages = [buf.getvalue()], [page(None, 1)]
        reads = {270: PageOCR(1, 1000, 1400, [LineBox("P1L1", "garbage", (40, 60, 480, 80), 1, confidence=0.08)]),
                 90: PageOCR(1, 1000, 1400, [LineBox("P1L1", "By balance b/d", (40, 60, 480, 80), 1, confidence=0.93)])}
        calls = []

        def fake_ocr(png, page_num, **kw):
            w, h = Image.open(io.BytesIO(png)).size
            calls.append(len(calls))
            return reads[270] if len(calls) == 1 else reads[90]
        with patch.object(pipeline, "ocr_page", side_effect=fake_ocr),                 patch.object(pipeline, "mark_struck_lines", return_value=0),                 patch.object(grader, "orientation_probe", return_value=[270]),                 patch.object(pipeline, "lines_run_vertically", return_value=(True, 4, 4)):
            rotated = pipeline._reread_sideways_pages(pngs, pages, cfg=NS(google_token="t", task_id="k"),
                                                      filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {1: 90})
        self.assertEqual(pages[0].lines[0].text, "By balance b/d")
        self.assertEqual(len(calls), 2)


class CoverWording(unittest.TestCase):
    def test_no_completed_count_on_the_cover(self):
        src = open(os.path.join(ROOT, "grader.py"), encoding="utf-8").read()
        self.assertNotIn('f"You completed {', src)


if __name__ == "__main__":
    unittest.main()
