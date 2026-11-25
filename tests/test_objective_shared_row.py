# -*- coding: utf-8 -*-
"""Two MCQs marked on one answer line hold a commerce copy.

UP Board Class 11 Economics (21 Oct 2026, released to the student): the evidence for
MCQ 2 and MCQ 3 was both located on the "Ans 3rd (D)" line. MCQ 2's cross with
"Correct answer is (D)" was drawn on the student's right answer D, and the "Ans 2nd"
line had no mark. Each mark sat on the row it cited, so the wrong-row check passed.
The anchors below are that copy's own render manifest.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import preflight  # noqa: E402
from grading_quality import objective_answers_sharing_a_row  # noqa: E402

UP_ECONOMICS_P3 = {
    "Q1.1": [36, 160, 405, 205],
    "Q1.2": [39, 247, 454, 296],
    "Q1.3": [39, 247, 454, 296],
    "Q1.4": [33, 294, 481, 341],
    "Q1.5": [36, 342, 419, 384],
    "Q1.6": [33, 388, 440, 433],
}


def _report(anchors, page=3):
    questions = [NS(qid=qid, objective=True, attempted=True) for qid in anchors]
    manifest = [{"qid": qid, "role": "answer", "page": page, "anchor_bbox": box,
                 "bbox": [box[2] + 8, box[1], box[2] + 60, box[3]]}
                for qid, box in anchors.items()]
    return NS(questions=questions, render_manifest=manifest)


class ObjectiveAnswersSharingARow(unittest.TestCase):
    def test_two_mcqs_on_one_line_are_found(self):
        issues = objective_answers_sharing_a_row(_report(UP_ECONOMICS_P3))
        self.assertEqual([i.code for i in issues], ["objective-answers-share-a-row"])
        self.assertIn("Q1.2 and Q1.3", issues[0].detail)

    def test_it_holds_a_commerce_copy(self):
        self.assertIn("objective-answers-share-a-row", preflight.hold_codes("commerce"))
        self.assertIn("mark-on-another-row", preflight.hold_codes("commerce"))

    def test_one_answer_per_line_passes(self):
        anchors = dict(UP_ECONOMICS_P3, **{"Q1.2": [39, 205, 450, 247]})
        self.assertEqual(objective_answers_sharing_a_row(_report(anchors)), [])

    def test_two_columns_of_one_line_pass(self):
        # "1. A ... 6. C" printed side by side cite different halves of the line.
        anchors = {"Q1.1": [40, 160, 200, 205], "Q1.6": [300, 160, 470, 205],
                   "Q1.2": [40, 205, 200, 250], "Q1.7": [300, 205, 470, 250]}
        self.assertEqual(objective_answers_sharing_a_row(_report(anchors)), [])

    def test_a_block_cited_for_several_mcqs_is_left_to_the_row_check(self):
        block = [30, 160, 470, 400]
        anchors = {"Q1": block, "Q2": block, "Q3": [30, 410, 470, 455],
                   "Q4": [30, 455, 470, 500], "Q5": [30, 500, 470, 545]}
        self.assertEqual(objective_answers_sharing_a_row(_report(anchors)), [])

    def test_written_answers_are_not_checked(self):
        report = _report(UP_ECONOMICS_P3)
        for q in report.questions:
            q.objective = False
        self.assertEqual(objective_answers_sharing_a_row(report), [])


if __name__ == "__main__":
    unittest.main()
