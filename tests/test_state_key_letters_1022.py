# -*- coding: utf-8 -*-
"""An MCQ key letter only scores a single MCQ (Maharashtra HSC OCM, 22 Oct 2026).

The paper numbers its sections I to V; its key numbers Section I's five MCQs 1 to 5.
Read as Q1 to Q5, the sections took the MCQs' letters: the 4-mark one-word Section
II was scored 0/4 "Correct answer is (B)" on three copies whose students had 3 of 4
right, and the five-MCQ Section I was given 5/5 from a single tick.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exam_package import _question_nodes  # noqa: E402
from grader import MarksItem, MarksScheme  # noqa: E402

KEY = "Answer Key\nQ1\nB\nQ2\nB\nQ3\nB\nQ4\nB\nQ5\nB\n"


def _types(items):
    scheme = MarksScheme(total=sum(i.max for i in items), items=items)
    return {n.qid: (n.question_type, n.objective_answer) for n in
            _question_nodes(scheme, question_paper_text="", answer_key_text=KEY)}


class KeyLettersOnlyScoreOneMcq(unittest.TestCase):
    def test_sections_numbered_like_mcqs_keep_their_own_type(self):
        nodes = _types([MarksItem(qid="Q1", max=5, question_type="objective"),
                        MarksItem(qid="Q2", max=4, question_type="theory"),
                        MarksItem(qid="Q3", max=6, question_type="theory")])
        self.assertEqual(nodes["Q1"], ("objective", None))   # a block of five MCQs
        self.assertEqual(nodes["Q2"], ("theory", None))
        self.assertEqual(nodes["Q3"], ("theory", None))

    def test_single_mcqs_still_take_the_key_letter(self):
        nodes = _types([MarksItem(qid=f"Q{i}", max=1) for i in range(1, 6)])
        self.assertEqual({q: t for q, t in nodes.items()},
                         {f"Q{i}": ("objective", "B") for i in range(1, 6)})
        two_marks = _types([MarksItem(qid="Q1", max=2)])          # a CA MCQ
        self.assertEqual(two_marks["Q1"], ("objective", "B"))

    def test_a_larger_mcq_the_marks_reading_typed_with_its_letter_keeps_it(self):
        nodes = _types([MarksItem(qid="Q1", max=4, question_type="objective", correct_answer="C")])
        self.assertEqual(nodes["Q1"], ("objective", "B"))


class HalfMarkSteps(unittest.TestCase):
    """MP Board Core Math rows 71 and 72: Q8's 2 marks split as 0.67/0.67/0.66 showed
    "0.67" circled on the copy and "1.34/2" as the mark."""

    def _question(self, steps):
        from grader import Criterion, QuestionMark
        criteria = [Criterion(description=f"step {i}", awarded=a, max=m, met=a >= m)
                    for i, (a, m) in enumerate(steps)]
        return QuestionMark(qid="Q8", score=sum(a for a, _ in steps), max_score=2,
                            criteria=criteria, attempted=True, page=1, y_fraction=0.5)

    def test_thirds_are_re_split_in_half_marks(self):
        from strict_grading import _half_mark_steps
        question = self._question([(0.67, 0.67), (0.67, 0.67), (0.0, 0.66)])
        steps = _half_mark_steps(question, list(question.criteria), "Q8")
        self.assertEqual([(c.awarded, c.max) for c in steps], [(1.0, 1.0), (0.5, 0.5), (0.0, 0.5)])
        self.assertEqual(sum(c.max for c in steps), 2)
        self.assertTrue(all(c.met for c in steps[:2]) and not steps[2].met)

    def test_half_mark_steps_are_left_alone(self):
        from strict_grading import _half_mark_steps
        question = self._question([(1.0, 1.0), (0.5, 1.0)])
        before = [(c.awarded, c.max) for c in question.criteria]
        steps = _half_mark_steps(question, list(question.criteria), "Q8")
        self.assertEqual([(c.awarded, c.max) for c in steps], before)


if __name__ == "__main__":
    unittest.main()
