# -*- coding: utf-8 -*-
"""CA faculty feedback of 17-18 Oct 2026 on rows 9, 10, 39, 40 and 41 of the 10 Oct
tool-checked copies, in code.

Questions the student never wrote were zeroed on other answers' writing and took an
"answer any N" place from a real answer; a page photographed twice was marked twice;
unattempted questions were described in the grader's words instead of "not
attempted"; a remark said nothing valid was written beside 5.5 of 7.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import preflight  # noqa: E402
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402


def _q(qid, score, max_score, lines, awards=None):
    awards = awards if awards is not None else [0.0] * len(lines)
    share = max_score / max(1, len(lines))
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=5, y_fraction=0.3,
                        anchor_line_id=lines[0] if lines else "", attempted=True, mark_style="per_step",
                        remark="No answer to Section 117 opening provision appears on supplied pages.",
                        criteria=[Criterion(description=f"step {i}", awarded=a, max=share, met=a >= share,
                                            step_line_id=line) for i, (line, a) in enumerate(zip(lines, awards))])


def _report(questions, regions, **extra):
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions), overall_remarks="",
                       questions=questions, answer_regions=regions, **extra)


class PhantomAttempts(unittest.TestCase):
    """Business Laws 23a44745: Q3(a) and Q3(b) crossed at 0 on the student's "4 b)"
    answer. Corporate Laws 1e3c181a: Q4(a) zeroed on the first lines of "Answer 1(a)"."""

    def test_a_zero_on_another_answers_lines_is_a_phantom(self):
        regions = {"q4.b": {"qid": "Q4(b)", "line_ids": ["P5L1", "P5L2", "P5L3", "P5L4"],
                            "label_line_ids": ["P5L1"]}}
        report = _report([_q("Q3(a)", 0, 6, ["P5L2", "P5L3"]), _q("Q3(b)", 0, 4, ["P5L4"])], regions)
        found = preflight.phantom_attempts(report)
        self.assertEqual(sorted(found), ["q3.a", "q3.b"])
        self.assertIn("belongs to Q4(b)", found["q3.a"])

    def test_a_part_zeroed_inside_answer_1a_is_a_phantom(self):
        regions = {"q1.a": {"qid": "Q1(a)", "line_ids": ["P4L1", "P4L2", "P4L3", "P5L1"]}}
        report = _report([_q("Q1(a)", 4.5, 5, ["P4L2", "P4L3"], [2.5, 2.0]), _q("Q4(a)", 0, 5, ["P4L2"])], regions)
        self.assertEqual(list(preflight.phantom_attempts(report)), ["q4.a"])

    def test_a_written_wrong_answer_is_not_a_phantom(self):
        regions = {"q3.a": {"qid": "Q3(a)", "line_ids": ["P6L1", "P6L2", "P6L3"]},
                   "q2.b": {"qid": "Q2(b)", "line_ids": ["P5L1", "P5L2"]}}
        report = _report([_q("Q3(a)", 0, 6, ["P6L1", "P6L2"])], regions)
        self.assertEqual(preflight.phantom_attempts(report), {}, "its own writing, a real 0 with a reason")

    def test_marks_parts_of_one_question_and_mcqs_are_left_alone(self):
        regions = {"q4": {"qid": "Q4", "line_ids": ["P5L1", "P5L2", "P5L3"]},
                   "q2": {"qid": "Q2", "line_ids": ["P1L1", "P1L2"]}}
        report = _report([_q("Q4(a)", 0, 5, ["P5L2"]),                    # inside its own parent
                          _q("Q3(a)", 0.5, 6, ["P5L2"], [0.5]),           # earned marks: credit checks
                          _q("Q1", 0, 2, ["P1L1"])], regions)              # MCQ sized
        self.assertEqual(preflight.phantom_attempts(report), {})

    def test_nothing_cited_decides_nothing(self):
        regions = {"q2": {"qid": "Q2", "line_ids": ["P1L1"]}}
        q = _q("Q3", 0, 6, [])
        self.assertEqual(preflight.phantom_attempts(_report([q], regions)), {})

    def _choice_paper(self):
        from exam_package import ChoiceGroup, ChoiceOption, build_exam_package
        scheme = MarksScheme(total=15, items=[MarksItem(qid="Q1", max=5), MarksItem(qid="Q3", max=5),
                                              MarksItem(qid="Q4", max=5)])
        group = ChoiceGroup(group_id="choice-1", select_count=1, selection_policy="first_attempted",
                            contribution_marks=5,
                            options=[ChoiceOption(option_id="a", question_ids=["Q3"]),
                                     ChoiceOption(option_id="b", question_ids=["Q4"])])
        return build_exam_package(
            exam_type="professional", student_class="CA Foundation", subject="Business Laws",
            level="Foundation", paper="Paper 2", attempt="Sep 2026", applicable_law_date=None,
            total_marks=10, question_paper=b"paper", answer_key=b"key", key_source="upload",
            marks_scheme=scheme, scheme_source="text", choice_groups=[group])

    def _phantom_report(self):
        regions = {"q1": {"qid": "Q1", "line_ids": ["P1L1", "P1L2"]},
                   "q4": {"qid": "Q4", "line_ids": ["P5L1", "P5L2", "P5L3"]}}
        q1 = _q("Q1", 3, 5, ["P1L1", "P1L2"], [1.5, 1.5])
        q3 = _q("Q3", 0, 5, ["P5L2", "P5L3"])
        q4 = _q("Q4", 3, 5, ["P5L2", "P5L3"], [1.5, 1.5])
        return _report([q1, q3, q4], regions)

    def test_the_phantom_no_longer_takes_the_place_of_the_written_answer(self):
        import pipeline
        from strict_grading import reconcile_report_to_package
        paper = self._choice_paper()
        before = reconcile_report_to_package(self._phantom_report(), paper)
        self.assertEqual([q.qid for q in before.questions], ["Q1", "Q3"], "the fault as delivered")
        report = self._phantom_report()
        cleared = pipeline._clear_phantom_attempts(report, NS(category="ca"))
        self.assertEqual(cleared, ["q3"])
        q3 = next(q for q in report.questions if q.qid == "Q3")
        self.assertFalse(q3.attempted)
        self.assertEqual((q3.score, q3.criteria, q3.remark, q3.anchor_line_id), (0.0, [], "Not attempted", ""))
        after = reconcile_report_to_package(report, paper)
        self.assertEqual([q.qid for q in after.questions], ["Q1", "Q4"])
        self.assertEqual(after.total_score, 6)

    def test_other_categories_are_unchanged_for_now(self):
        import pipeline
        report = self._phantom_report()
        self.assertEqual(pipeline._clear_phantom_attempts(report, NS(category="school")), [])
        self.assertTrue(report.questions[1].attempted)

    def test_it_runs_before_the_blank_recheck_after_the_regrade_and_before_the_choice_rule(self):
        import inspect
        import pipeline
        source = inspect.getsource(pipeline)
        clear = "_clear_phantom_attempts(report, cfg)"
        self.assertLess(source.index(clear), source.index("report = _recheck_written_blanks("))
        regrade = source.index("report = _regrade_invalid_questions(\n            report, cfg=cfg")
        self.assertIn(clear, source[regrade: regrade + 400])
        final = source.index("report, cfg.exam_package, answer_map=answer_map, finalize_choices=True)")
        self.assertIn(clear, source[final - 600: final])


class CreditWithoutAnAnswer(unittest.TestCase):
    """Business Laws f87b56cc: Q1(a) was paid 2 marks with no Q1(a) on the copy."""

    def test_it_is_found_and_sent_back_for_marking_on_ca_papers(self):
        import inspect
        import pipeline
        regions = {"q1.b": {"qid": "Q1(b)", "line_ids": ["P1L1", "P1L2", "P1L3"]}}
        report = _report([_q("Q1(b)", 7, 7, ["P1L1", "P1L2"], [3.5, 3.5]),
                          _q("Q1(a)", 2, 7, ["P1L3"], [2.0])], regions)
        found = preflight.credit_without_own_answer(report)
        self.assertEqual(list(found), ["q1.a"])
        self.assertIn("every cited line belongs to q1.b", found["q1.a"])
        source = inspect.getsource(pipeline._regrade_invalid_questions)
        self.assertIn('"CREDIT_WITHOUT_OWN_ANSWER"', source)
        self.assertIn('StrictGradeError("REPEATED_PAGE", why)', source)
        self.assertIn('if _category_of(cfg) == "ca":', source)


def _noisy(words, every=6):
    """The same writing read again: every sixth word misread."""
    return [w if i % every else w[::-1] for i, w in enumerate(words)]


_PAGE1 = ("As per the Negotiable Instruments Act 1881 section 138 when one person draws a cheque "
          "for the purpose to discharge the debt wholly or partly but not applicable when given for "
          "moral obligation illegal consideration security or gift if cheque gets dishonour due to "
          "insufficient amount in bank then section 138 can be apply but there are some conditions "
          "present the cheque within validity date drawer fails to meet payment after fifteen days").split()
_PAGE3 = ("As per the Companies Act 2013 section 2(20) company means a company incorporated under this "
          "act or any previous company law having features like separate legal entity common seal "
          "artificial person perpetual succession conclusion after above provision Flower Fans Limited "
          "will not be affected by outgoing or incoming of any members it based on perpetual succession").split()


class RepeatedPages(unittest.TestCase):
    """Business Laws 23a44745: pages 1 and 2 are two photos of one page."""

    def test_two_reads_of_one_page_are_a_repeat(self):
        from repeated_pages import repeated_pages
        pages = {1: [" ".join(_PAGE1)], 2: [" ".join(_noisy(_PAGE1))], 3: [" ".join(_PAGE3)]}
        self.assertEqual(repeated_pages(pages), {2: 1})

    def test_different_pages_and_short_pages_are_not(self):
        from repeated_pages import repeated_pages
        self.assertEqual(repeated_pages({1: [" ".join(_PAGE1)], 2: [" ".join(_PAGE3)]}), {})
        short = " ".join(_PAGE1[:12])
        self.assertEqual(repeated_pages({1: [short], 2: [short]}), {}, "too little writing to tell")
        half = " ".join(_PAGE1[: len(_PAGE1) // 2])
        self.assertEqual(repeated_pages({1: [" ".join(_PAGE1)], 2: [half]}), {}, "a partial photo is left alone")

    def _ocr(self):
        def page(n, words):
            return NS(page=n, lines=[NS(line_id=f"P{n}L{i}", text=" ".join(words[i * 10:(i + 1) * 10]),
                                        kind="text", printed=False, cancelled=False) for i in range(6)])
        return [page(1, _PAGE1), page(2, _noisy(_PAGE1)), page(3, _PAGE3)]

    def test_the_grader_is_told_and_paying_both_photos_is_sent_back(self):
        from repeated_pages import ocr_repeated_pages, paid_twice, prompt_block
        ocr = self._ocr()
        self.assertEqual(ocr_repeated_pages(ocr), {2: 1})
        self.assertIn("page 2 repeats page 1", prompt_block({2: 1}))
        self.assertEqual(prompt_block({}), "")
        twice = _report([_q("Q1(a)", 4.5, 7, ["P1L1", "P2L5"], [3.5, 1.0])], {})
        once = _report([_q("Q1(a)", 3.5, 7, ["P1L1", "P1L5"], [3.5, 0.0])], {})
        self.assertIn("q1.a", paid_twice(twice, ocr))
        self.assertEqual(paid_twice(once, ocr), {})

    def test_the_repeated_page_is_noted_as_repeated(self):
        import fitz
        from unittest.mock import patch
        import pdf_renderer as pr
        report = GradeReport(total_score=4, max_total=7, overall_remarks="", evidence_contract=True,
                             whole_total=True,
                             questions=[QuestionMark(qid="Q1(a)", score=4, max_score=7, page=1,
                                                     y_fraction=.2, attempted=True)])
        report.render_manifest = [{"qid": "Q1(a)", "role": "step", "page": 1, "bbox": [10, 10, 20, 20]}]
        by_lid = {}
        for n, words in ((1, _PAGE1), (2, _noisy(_PAGE1))):
            for i in range(6):
                by_lid[f"P{n}L{i}"] = {"lid": f"P{n}L{i}", "page": n, "x0": 40, "x1": 300,
                                       "y0": 100 + 30 * i, "y1": 118 + 30 * i,
                                       "text": " ".join(words[i * 10:(i + 1) * 10])}
        notes: dict[int, str] = {}
        real = pr._draw_strip_entry

        def capture(page, qid, *args, **kwargs):
            notes[page.number + 1] = kwargs.get("note", "")
            return real(page, qid, *args, **kwargs)
        token = pr._RENDER_REPORT.set(report)
        try:
            with fitz.open() as doc, patch.object(pr, "_draw_strip_entry", capture):
                for n in range(2):
                    doc.new_page(width=700, height=800)
                    pr._CONTENT_W[(id(doc), n + 1)] = 480.0
                noted = pr._note_pages_nobody_marked(doc, by_lid)
                for n in range(2):
                    pr._CONTENT_W.pop((id(doc), n + 1), None)
        finally:
            pr._RENDER_REPORT.reset(token)
        self.assertEqual(noted, [2])
        self.assertEqual(notes[2], "Repeated page. The same writing is evaluated on page 1.")
        self.assertFalse(report.review_reasons, "a repeat that carries no marks needs no faculty note")


class NotAttemptedOnTheCover(unittest.TestCase):
    """Business Laws f87b56cc: "Q2(c): No Supreme Court significance answer supplied"
    was to read "Unattempted, no answer provided for Q2(c), 0/6 marks"."""

    def _report(self, ca):
        return GradeReport(total_score=4, max_total=12, overall_remarks="", whole_total=ca, questions=[
            QuestionMark(qid="Q2(b)", score=4, max_score=6, page=1, y_fraction=.2, attempted=True,
                         remark="Indoor management rule not explained."),
            QuestionMark(qid="Q2(c)", score=0, max_score=6, page=1, y_fraction=.2, attempted=False,
                         remark="No Supreme Court significance answer supplied."),
            QuestionMark(qid="Q3(a)", score=0, max_score=6, page=1, y_fraction=.2, attempted=False, remark="")])

    def test_a_ca_copy_says_not_attempted_in_one_form(self):
        import pdf_renderer as pr
        report = self._report(True)
        lines = [pr._strip_lost_text(q, report) for q in pr._report_strip_lost(report)]
        self.assertEqual(lines[0], f"{pr._label('Q2(b)', report)}: Indoor management rule not explained.")
        self.assertEqual(lines[1], f"{pr._label('Q2(c)', report)}: Not attempted. No answer was provided, 0/6 marks.")
        self.assertEqual(len(lines), 3, "a blank part with no remark is listed too")
        for line in lines:
            self.assertNotIn(";", line)

    def test_other_categories_keep_the_graders_remark(self):
        import pdf_renderer as pr
        report = self._report(False)
        lines = [pr._strip_lost_text(q, report) for q in pr._report_strip_lost(report)]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("No Supreme Court significance answer supplied."))


class StepMarksOnCaCopies(unittest.TestCase):
    """Corporate Laws 1e3c181a: "Step marking missing in Q 1(a)"."""

    def test_a_ca_answer_of_several_steps_is_marked_step_by_step(self):
        import inspect
        import pdf_renderer as pr
        source = inspect.getsource(pr._render_question_marks)
        rule = source.index('if not is_obj and len(criteria) > 1 and bool(getattr(render_report, "whole_total", False)):')
        self.assertLess(source.index('elif mark_style in ("single", "whole"):'), rule, "it overrides the single style")
        self.assertLess(rule, source.index("use_step_marks = ("))


class RemarksThatDenyCredit(unittest.TestCase):
    """Business Laws 23a44745 Q2(a): 5.5 of 7 and "No valid characteristic is stated"."""

    def test_the_teacher_is_told_without_holding_the_copy(self):
        q = QuestionMark(qid="Q2(a)", score=5.5, max_score=7, page=3, y_fraction=.2, attempted=True,
                         remark="No valid characteristic is stated.")
        fair = QuestionMark(qid="Q1(c)", score=3, max_score=6, page=3, y_fraction=.2, attempted=True,
                            remark="Part (ii) not attempted.")
        issues = preflight._check_remark_denies_credit(_report([q, fair], {}))
        self.assertEqual([(i.code, i.severity) for i in issues], [("remark-denies-credit", "warn")])
        self.assertIn("Q2(a)", issues[0].detail)
        self.assertNotIn("remark-denies-credit", preflight.SCORE_INTEGRITY_CODES)


class TheCaRules(unittest.TestCase):
    def setUp(self):
        self.rules = " ".join(categories.get("ca").prompt.extra_rules.split())

    def test_unwritten_questions_are_not_attempted_and_never_marked_on_other_writing(self):
        self.assertIn('returned attempted=false with no marks, and its remark is "Not attempted"', self.rules)
        self.assertIn("It is never marked on another answer's writing", self.rules)
        self.assertIn("takes one of the N places", self.rules)

    def test_feedback_says_what_was_right_then_what_is_missing(self):
        self.assertIn("says first what the student got right", self.rules)
        self.assertIn('never write "no valid characteristic is stated"', self.rules)
        self.assertIn("A missing section number is never the only reason given", self.rules)

    def test_a_contradictory_conclusion_and_a_repeated_page(self):
        self.assertIn("A conclusion that contradicts the provision", self.rules)
        self.assertIn("The same page photographed twice is marked once", self.rules)


if __name__ == "__main__":
    unittest.main()
