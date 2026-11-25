# -*- coding: utf-8 -*-
"""Parishram Humanities (Vidyapeeth, Class 12 Arts) review of 10 Oct 2026, in code.

Four reviewers compared 15 tool-checked copies with the faculty's own per-question
marks. The rubric was re-invented on every copy (as on CA), short answers were overpaid
in History, Political Science, Sociology and Hindi, correct point answers were
underpaid in Economics and Geography, and remarks never named what to add.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import grader  # noqa: E402
import key_rubric  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import MarksItem, MarksScheme  # noqa: E402
from key_rubric import (KeyRubricSheet, QuestionSteps, StepItem, complete_step_rubric,  # noqa: E402
                        key_rubric_applies, rubric_flavor)


def _package():
    scheme = MarksScheme(total=6, items=[MarksItem(qid="Q7", max=2), MarksItem(qid="Q10", max=4)])
    return build_exam_package(
        exam_type="school", student_class="Class 12", subject="Political Science", level="Class 12",
        paper="Infinity Test 10", attempt="2026", applicable_law_date=None, total_marks=6,
        question_paper=b"paper", answer_key=b"key", key_source="upload", marks_scheme=scheme,
        scheme_source="ai_verified")


class ArtsRubricIsSetOncePerPaper(unittest.TestCase):
    def test_only_ca_uses_the_key_rubric(self):
        # Vidyapeeth measured worse with steps from the model answer (see key_rubric_applies).
        self.assertFalse(key_rubric_applies(SimpleNamespace(exam_type="school", category="vidyapeeth")))
        self.assertFalse(key_rubric_applies(SimpleNamespace(exam_type="school", category="vidyapeeth"),
                                            "History"))
        # Maths and Science take the key's worked steps (14 Oct 2026).
        self.assertTrue(key_rubric_applies(SimpleNamespace(exam_type="school", category="vidyapeeth"),
                                           "Maths"))
        self.assertFalse(key_rubric_applies(SimpleNamespace(exam_type="school", category="school"),
                                            "Maths"))
        self.assertTrue(key_rubric_applies(SimpleNamespace(exam_type="professional", category="ca")))
        self.assertFalse(key_rubric_applies(SimpleNamespace(exam_type="school", category="school")))
        self.assertEqual(rubric_flavor(SimpleNamespace(exam_type="professional", category="ca")), "")
        self.assertEqual(rubric_flavor(SimpleNamespace(exam_type="school", category="vidyapeeth")), "vidyapeeth")

    def test_the_school_prompt_and_the_faculty_rules_reach_the_reader(self):
        seen = {}

        def reader(*args, **kwargs):
            seen.update(kwargs)
            return KeyRubricSheet(questions=[
                QuestionSteps(qid="Q7", criteria=[
                    StepItem(criterion_id="Q7.s1", description="Definition", max_marks=1),
                    StepItem(criterion_id="Q7.s2", description="Feature explained", max_marks=1)]),
                QuestionSteps(qid="Q10", criteria=[
                    StepItem(criterion_id="Q10.s1", description="Partition", max_marks=1.25),
                    StepItem(criterion_id="Q10.s2", description="Princely states", max_marks=2.75)])])
        updated, changed = complete_step_rubric(
            _package(), b"%PDF key", "key", google_token="t", task_id="k", reader=reader,
            cache_dir=Path(tempfile.mkdtemp()), flavor="vidyapeeth", category_rules="RULES X")
        self.assertEqual(seen.get("flavor"), "vidyapeeth")
        self.assertEqual(seen.get("category_rules"), "RULES X")
        self.assertEqual(changed, ["Q7"], "Arts steps are half marks: 1.25 + 2.75 is refused")

    def test_an_arts_cache_never_collides_with_the_ca_cache(self):
        package = _package()
        self.assertNotEqual(key_rubric.cache_key(b"k", package), key_rubric.cache_key(b"k", package, "vidyapeeth"))

    def test_the_school_prompt_is_point_wise_not_icai(self):
        prompt = key_rubric.SCHOOL_KEY_RUBRIC_SYSTEM_PROMPT
        self.assertNotIn("ICAI", prompt)
        self.assertIn("its points are examples, not the only right answers", prompt)
        self.assertIn("any other valid, relevant point earns the step", prompt)
        self.assertIn("multiples of 0.5", prompt)
        self.assertIn("ICAI", key_rubric.KEY_RUBRIC_SYSTEM_PROMPT, "the CA prompt is unchanged")


class InventoryRetry(unittest.TestCase):
    """Run 4: a proxy reset lost one copy's inventory, so it was graded from labels alone."""

    def _take(self, *errors):
        import answer_inventory
        calls = []

        def reader(*args, **kwargs):
            calls.append(1)
            raise errors[min(len(calls), len(errors)) - 1]

        with mock.patch.object(answer_inventory.time, "sleep"):
            result = answer_inventory.take_answer_inventory(
                package=_package(), answer_map=None, ocr_pages=[object()], student_pngs=[b"p"],
                google_token="t", task_id="k", reader=reader)
        return result, len(calls)

    def test_a_dropped_connection_is_retried_once(self):
        reset = requests.exceptions.ConnectionError("Connection aborted. 10054")
        self.assertEqual(self._take(reset, reset), (None, 2))

    def test_a_timeout_is_not_retried_it_may_have_run(self):
        self.assertEqual(self._take(requests.exceptions.ReadTimeout("read timed out")), (None, 1))

    def test_other_failures_are_not_retried(self):
        self.assertEqual(self._take(ValueError("bad json")), (None, 1))


class HeldQuestionStepsFitItsMarks(unittest.TestCase):
    """Political Science Q14 (5c57fae6): after two readings the steps were valid one by
    one but totalled 7 of 6, and the final check threw the whole copy away."""

    SPEC = SimpleNamespace(qid="Q14", max_marks=6.0, question_type="descriptive", criteria=[])

    def _question(self, score, steps):
        from grader import Criterion, QuestionMark
        return QuestionMark(qid="Q14", score=score, max_score=6, page=4, y_fraction=0.2,
                            mark_style="per_step",
                            criteria=[Criterion(description=f"reason {n}", awarded=a, max=m,
                                                met=a >= m, evidence="")
                                      for n, (a, m) in enumerate(steps)])

    def _clamp(self, question):
        from pipeline import _clamp_to_valid_marks
        from strict_grading import validate_question_marks
        _clamp_to_valid_marks(question, 6)
        validate_question_marks(question, self.SPEC)
        return [(c.awarded, c.max) for c in question.criteria]

    def test_unawarded_room_is_trimmed_first(self):
        question = self._question(5, [(2, 3), (2, 2), (1, 2)])
        self.assertEqual(self._clamp(question), [(2, 2), (2, 2), (1, 2)])
        self.assertEqual(question.score, 5)

    def test_awards_above_the_marks_come_off_the_last_step(self):
        question = self._question(7, [(3, 3), (2, 2), (2, 2)])
        self.assertEqual(self._clamp(question), [(3, 3), (2, 2), (1, 1)])
        self.assertEqual(question.score, 6)

    def test_a_short_breakdown_gets_a_zero_step(self):
        question = self._question(3, [(2, 2), (1, 2)])
        self.assertEqual(self._clamp(question), [(2, 2), (1, 2), (0, 2)])
        self.assertEqual(question.score, 3)


class FacultyLevels(unittest.TestCase):
    def setUp(self):
        self.rules = " ".join(categories.get("vidyapeeth").prompt.extra_rules.split())

    def test_named_only_and_restated_points_earn_nothing_extra(self):
        self.assertIn("a step whose own evidence says the point is missing", self.rules)
        self.assertIn("a line that restates the question", self.rules)
        self.assertIn("never quarters", self.rules)

    def test_economics_and_geography_point_answers_are_paid_in_full(self):
        self.assertIn("Do not take 0.25 or 0.5 for \"lacks detail\"", self.rules)
        self.assertIn("correct answer with numbers only: 2 of 4", self.rules)

    def test_subject_levels_from_the_faculty(self):
        for anchor in ("one-line definition and bare features gets 1", "capped at 2.5 of 6",
                       "none of the key's arguments gets 0.5", "1.5 of 2 on an extract part",
                       "capped at 3 of 5"):
            self.assertIn(anchor, self.rules, anchor)

    def test_history_and_english_second_examiner_raises_only_misreads(self):
        # 69 remaining copies: the second examiner's raises put History +1.8 and English +1.4.
        self.assertIn("On a History or English paper the second examiner never raises a mark "
                      "for depth, detail or completeness", self.rules)

    def test_remarks_must_say_what_to_add(self):
        self.assertIn("Every remark on a descriptive answer says what to add", self.rules)

    def test_the_rules_reach_the_grading_prompt_for_every_arts_subject(self):
        for subject in ("History", "Economics", "Hindi", "Geography"):
            prompt = " ".join(grader.build_system_prompt(subject, "Class 12", "school",
                                                         category="vidyapeeth").split())
            self.assertIn("Every remark on a descriptive answer says what to add", prompt, subject)


if __name__ == "__main__":
    unittest.main()
