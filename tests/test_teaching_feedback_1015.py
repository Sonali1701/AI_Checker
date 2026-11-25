# -*- coding: utf-8 -*-
"""Teaching exams: the SME feedback of 14 Oct 2026 (sheet "AI Copy cheking feedback"),
in code.

KVS PRT Mock 01: the tool gave 37.75 of 40, the six evaluators 28.5. Every theory
answer got 4 of 4 from the tool; the evaluators gave 2 to 3 for brief explanations,
three one line points, missing subject terms (ploughing, levelling, irrigation) and
one aspect of three explained. EMRS TGT Maths: 39 vs 36; a wrong final answer from a
misread strict inequality got 3.5 from the tool and 1 from the evaluator.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import grader  # noqa: E402
import pipeline  # noqa: E402
import teaching_key  # noqa: E402


class TheEvaluatorsLevels(unittest.TestCase):
    def setUp(self):
        self.rules = " ".join(categories.get("teaching").prompt.extra_rules.split())

    def test_a_brief_answer_is_not_full_marks_on_a_teaching_paper(self):
        self.assertIn("this overrides the school rule that a brief correct answer earns full marks", self.rules)
        self.assertIn("three one line points for a 4 mark question earn 2 to 2.5", self.rules)
        self.assertIn("a correct but brief explanation earns 2 to 3", self.rules)

    def test_subject_terms_and_several_aspects(self):
        self.assertIn("ploughing, levelling and irrigation", self.rules)
        self.assertIn("A general description without them earns at most half the step", self.rules)
        self.assertIn("One aspect explained briefly with the others merely named is 2 to 2.5 of 4", self.rules)

    def test_mathematics_and_language_levels(self):
        self.assertIn("The final answer step is never earned by a wrong value", self.rules)
        self.assertIn("1 to 2 of 4", self.rules)
        self.assertIn("working that is hard to follow loses half a mark", self.rules)
        self.assertIn("A word limit printed in the question is part of the task", self.rules)

    def test_the_second_examiner_does_not_raise_a_brief_answer(self):
        self.assertIn("A brief answer stays brief", self.rules)

    def test_the_rules_reach_both_examiners(self):
        prompt = " ".join(grader.build_system_prompt("Science", "PRT", "school", category="teaching").split())
        self.assertIn("three one line points for a 4 mark question earn 2 to 2.5", prompt)
        cfg = SimpleNamespace(exam_type="school", category="teaching", answer_language="", leniency="standard")
        second = " ".join(pipeline._category_rules(cfg).split())
        self.assertIn("A brief answer stays brief", second)


class TheKeyAsksForExplainedPoints(unittest.TestCase):
    def test_the_key_writer_is_told_a_theory_step_needs_an_explanation(self):
        prompt = teaching_key.TEACHING_KEY_SYSTEM_PROMPT
        self.assertIn("named AND explained", prompt)
        self.assertIn("ploughing, levelling, irrigation", prompt)

    def test_old_cached_keys_are_written_again(self):
        self.assertEqual(teaching_key.TEACHING_KEY_VERSION, "2026-10-23.1")
        wanted = [("Q1", 4.0)]
        self.assertNotEqual(teaching_key.cache_key(b"paper", wanted, "Science", "PRT"),
                            teaching_key.cache_key(b"paper", wanted, "Science", "TGT"))


if __name__ == "__main__":
    unittest.main()
