"""Offline extraction/queue behavior for one six-mark essay among four topics.

The provider reply is a fixture. These tests verify that the real request explains
exclusive topics, the strict parser retains their identities, and the normal API
calculates 40 rather than double-counting or dividing the six-mark award.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import pipeline
import server
import test_ca_extraction as ca
from marks_attestation import effective_marks_total


PAPER = """Class 12 Hindi. Maximum Marks: 40.
Q1-Q10 each 1 mark; Q11-Q15 each 2 marks; Q16-Q17 each 3 marks.
Q18-Q19 each 4 marks. Q20 carries 6 marks.
Q20. Write approximately 120 words on any ONE of the following four topics:
(क) A cold night.
(ख) A family celebration.
(ग) Falling water levels.
(घ) A meeting in a dream.
"""
MAXIMA = {**{f"Q{i}": 1 for i in range(1, 11)},
          **{f"Q{i}": 2 for i in range(11, 16)},
          "Q16": 3, "Q17": 3, "Q18": 4, "Q19": 4,
          **{f"Q20.alt{i}": 6 for i in range(1, 5)}}
GOOD = {
    "items": [{"qid": qid, "max": maximum} for qid, maximum in MAXIMA.items()],
    "choice_groups": [{"group_id": "q20-topics", "select_count": 1,
                       "options": [{"option_id": label, "question_ids": [f"Q20.alt{i}"]}
                                   for i, label in enumerate(("क", "ख", "ग", "घ"), 1)]}],
    "allocation_issues": [],
}


class LanguageTopicChoiceTests(unittest.TestCase):
    def test_extraction_request_and_parser_preserve_four_full_award_alternatives(self):
        reply = {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps(GOOD)}]}
        with patch.object(grader, "_claude_call", return_value=reply) as call:
            scheme = grader.extract_marks_scheme([], google_token="mock", task_id="offline",
                student_class="12", subject="Hindi", exam_type="school", total_marks=40,
                paper_text=PAPER, require_complete=True)
        prompt = call.call_args.kwargs["request"]["system"]
        self.assertIn("WRITE ON ANY ONE OF SEVERAL TOPICS", prompt)
        self.assertIn("Q20.alt1, Q20.alt2, Q20.alt3 and Q20.alt4", prompt)
        self.assertEqual({item.qid: item.max for item in scheme.items}, MAXIMA)
        self.assertNotIn("Q20", MAXIMA)
        self.assertEqual(scheme.total, 58)
        self.assertEqual(effective_marks_total(scheme), 40)
        self.assertEqual(scheme.choice_groups[0].contribution_marks, 6)
        self.assertEqual(scheme.choice_groups[0].select_count, 1)

    def test_shared_q20_membership_is_rejected_without_inventing_topic_ids(self):
        bad = copy.deepcopy(GOOD)
        bad["items"] = [row for row in bad["items"] if not row["qid"].startswith("Q20.")]
        bad["items"].append({"qid": "Q20", "max": 6})
        for option in bad["choice_groups"][0]["options"]:
            option["question_ids"] = ["Q20"]
        with self.assertRaisesRegex(grader.MarksExtractionError, "share questions"):
            grader.parse_strict_marks_scheme(json.dumps(bad))

    def test_normal_api_attests_and_queues_only_the_effective_forty_mark_graph(self):
        flow = ca.ProviderFlowTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.reply = copy.deepcopy(GOOD)
        form = dict(flow.form, student_class="12", subject="Hindi", exam_type="school",
                    total_marks="40")
        with patch.object(server, "pdf_text_layer", return_value=PAPER), \
             patch.object(pipeline, "pdf_text_layer", return_value=PAPER):
            detected = flow.post("/api/detect-marks", form,
                                {"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf")})
            self.assertEqual(detected["method"], "ai_verified", detected)
            self.assertEqual(detected["total"], 58)
            self.assertEqual(detected["effective_total"], 40)
            self.assertTrue(detected["marks_attestation"])
            flow.post("/api/evaluate", {**form, "key_source": "upload",
                "marks_items": json.dumps(detected["items"]), "marks_source": detected["method"],
                "marks_attestation": detected["marks_attestation"]},
                {"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf"),
                 "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
                 "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})

    def test_missing_selection_rule_cannot_unlock_automatic_grading(self):
        flow = ca.ProviderFlowTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.reply = copy.deepcopy(GOOD)
        flow.reply["choice_groups"] = []
        form = dict(flow.form, student_class="12", subject="Hindi", exam_type="school",
                    total_marks="40")
        with patch.object(server, "pdf_text_layer", return_value=PAPER), \
             patch.object(pipeline, "pdf_text_layer", return_value=PAPER):
            detected = flow.post("/api/detect-marks", form,
                                {"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf")})
        self.assertNotEqual(detected["method"], "ai_verified")
        self.assertFalse(detected["marks_attestation"])


if __name__ == "__main__":
    unittest.main()
