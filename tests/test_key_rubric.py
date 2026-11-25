# -*- coding: utf-8 -*-
"""The key's step marks are set once per paper and frozen for every copy (key_rubric.py).

MEASURED 2026-10-09 (35 CA copies against SME-approved marks): the grader invented a
different split of the same question on every copy (Corporate Law Q3(a): 2+3, 2+2+1,
1+1+1+2), because rubrics.rubric_rows reads no step marks from a CA key whose marks are
margin notes. The reply that sets the scheme is untrusted: only a question whose steps
are positive quarter marks with unique ids adding up to its maximum is kept.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exam_package import build_exam_package  # noqa: E402
from grader import MarksItem, MarksScheme  # noqa: E402
import key_rubric  # noqa: E402
from key_rubric import (KeyRubricSheet, QuestionSteps, StepItem, complete_step_rubric,  # noqa: E402
                        verify_key_rubric)


def _package():
    scheme = MarksScheme(total=12, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q6.c2", max=2),
                                          MarksItem(qid="Q4.a.i", max=1.5), MarksItem(qid="Q2", max=7.5)])
    package = build_exam_package(
        exam_type="professional", student_class="CA Intermediate", subject="Auditing and Ethics",
        level="CA Intermediate", paper="MTS 01", attempt="2026", applicable_law_date=None,
        total_marks=12, question_paper=b"paper", answer_key=b"key", key_source="upload",
        marks_scheme=scheme, scheme_source="ai_verified")
    q1 = next(q for q in package.questions if q.qid == "Q1")
    q1.question_type = "objective"
    q1.objective_answer = "B"
    return package


def _steps(qid, weights):
    return [StepItem(criterion_id=f"{qid}.s{i + 1}", description=f"Point {i + 1}", max_marks=w,
                     source_quote="Standards on Auditing") for i, w in enumerate(weights)]


SHEET = KeyRubricSheet(questions=[
    QuestionSteps(qid="Q6.c2", printed=True, criteria=_steps("Q6.c2", [0.5, 0.5, 0.5, 0.5])),
    QuestionSteps(qid="Q4.a.i", printed=True, criteria=_steps("Q4.a.i", [0.25] * 6)),
    QuestionSteps(qid="Q2", printed=False, criteria=_steps("Q2", [2, 2, 2])),   # 6, not 7.5
    QuestionSteps(qid="Q1", printed=True, criteria=_steps("Q1", [1])),          # objective: ignored
])


class Verify(unittest.TestCase):
    def test_only_schemes_that_add_up_are_kept(self):
        accepted, refused = verify_key_rubric(SHEET, _package())
        self.assertEqual(sorted(accepted), ["q4.a.i", "q6.c.2"])
        self.assertEqual([c.max_marks for c in accepted["q6.c.2"]], [0.5] * 4)
        self.assertTrue(any("Q2: steps add up to 6" in r for r in refused), refused)

    def test_odd_weights_and_repeated_ids_are_refused(self):
        bad = KeyRubricSheet(questions=[
            QuestionSteps(qid="Q6.c2", criteria=_steps("Q6.c2", [0.3, 0.7, 1])),
            QuestionSteps(qid="Q4.a.i", criteria=[StepItem(criterion_id="x", description="a", max_marks=0.75),
                                                   StepItem(criterion_id="X", description="b", max_marks=0.75)])])
        accepted, refused = verify_key_rubric(bad, _package())
        self.assertEqual(accepted, {})
        self.assertEqual(len(refused), 2)


class Complete(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())
        self.calls = 0

    def reader(self, *args, **kwargs):
        self.calls += 1
        return SHEET

    def test_the_package_carries_the_frozen_steps_and_is_rehashed(self):
        package = _package()
        updated, changed = complete_step_rubric(package, b"%PDF key", "key text", google_token="t",
                                                task_id="k", reader=self.reader, cache_dir=self.cache)
        self.assertEqual(sorted(changed), ["Q4.a.i", "Q6.c2"])
        by = {q.qid: q for q in updated.questions}
        self.assertEqual(len(by["Q6.c2"].criteria), 4)
        self.assertEqual(by["Q2"].criteria, [], "a scheme that does not add up stays unfixed")
        self.assertEqual(by["Q1"].criteria, [])
        self.assertNotEqual(updated.package_id, package.package_id)
        self.assertEqual(updated.package_id, updated.computed_id())

    def test_every_later_batch_of_the_paper_reuses_the_same_scheme(self):
        first, _ = complete_step_rubric(_package(), b"%PDF key", "key", google_token="t", task_id="k",
                                        reader=self.reader, cache_dir=self.cache)
        second, _ = complete_step_rubric(_package(), b"%PDF key", "key", google_token="t", task_id="k",
                                         reader=self.reader, cache_dir=self.cache)
        self.assertEqual(self.calls, 1, "the key is read once, then cached")
        self.assertEqual(first.package_id, second.package_id)

    def test_a_different_key_is_read_again(self):
        complete_step_rubric(_package(), b"%PDF key", "key", google_token="t", task_id="k",
                             reader=self.reader, cache_dir=self.cache)
        complete_step_rubric(_package(), b"%PDF other key", "key", google_token="t", task_id="k",
                             reader=self.reader, cache_dir=self.cache)
        self.assertEqual(self.calls, 2)

    def test_a_failed_reading_leaves_the_package_as_it_was(self):
        def boom(*args, **kwargs):
            raise RuntimeError("proxy 502")
        package = _package()
        updated, changed = complete_step_rubric(package, b"%PDF key", "key", google_token="t",
                                                task_id="k", reader=boom, cache_dir=self.cache)
        self.assertIs(updated, package)
        self.assertEqual(changed, [])

    def test_no_key_no_call(self):
        package = _package()
        updated, changed = complete_step_rubric(package, None, "", google_token="t", task_id="k",
                                                reader=self.reader, cache_dir=self.cache)
        self.assertEqual((self.calls, changed), (0, []))

    def test_the_prompt_names_the_rules_the_smes_gave(self):
        prompt = key_rubric.KEY_RUBRIC_SYSTEM_PROMPT
        self.assertIn("each point", prompt)
        self.assertIn("MUST add up EXACTLY", prompt)
        self.assertIn("a right rate on a wrong base earns nothing", prompt)


if __name__ == "__main__":
    unittest.main()
