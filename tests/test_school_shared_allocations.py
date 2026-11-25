"""School section totals, shared clauses, and a single source-bound residual.

Transport replies are mocked; the real request, strict parsing, total calculation,
API attestation and queue checks run. No equal clause split is an official maximum.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader
import pipeline
import server
import test_ca_extraction as ca


PAPER = """Class 12th. Total: 40 Marks.
Q1 to Q10 are 1-mark Multiple Choice Questions.
Q11 and Q12 are 3-mark Short Answer Questions.
Q13 to Q15 are 4-mark Short Answer Questions.
Q16 and Q17 are 6-mark Long Answer Questions.
All questions are compulsory.
""" + "\n".join(f"Q{i}. Explain the required concept." for i in range(1, 14)) + """
Q14. Read the case.
(a) Identify the three broad pillars of the New Economic Policy.
(b) Explain any two major industrial liberalisation measures.
Q15. Explain land reforms and their partial success.
Q16. (a) Identify deficient demand and state two monetary measures.
(b) Derive the savings function algebraically. (2 Marks)
Q17. (a) Critically evaluate privatisation and disinvestment.
(b) Discuss the major common goals of Indian Five-Year Plans.
"""
EXPECTED = {**{f"Q{i}": 1 for i in range(1, 11)}, "Q11": 3, "Q12": 3,
            "Q13": 4, "Q14": 4, "Q15": 4, "Q16.a": 4, "Q16.b": 2, "Q17": 6}
GOOD = {"items": [{"qid": qid, "max": maximum} for qid, maximum in EXPECTED.items()],
        "choice_groups": [], "allocation_issues": []}

# Actual History paper boundaries: its cover prices the complete source-based
# question; the three numbered requirements have no individual mark tags.
HISTORY_PAPER = """Class 12th. Total: 40 Marks.
All 13 questions are compulsory.
Section A: Questions 1 to 6 are Multiple Choice Questions carrying 1 mark each.
Section B: Questions 7 to 9 carry 3 marks each.
Section C: Questions 10 and 11 carry 8 marks each.
Section D: Question 12 is a Source-based Question carrying 4 marks and consists
of three sub-questions.
Section E: Question 13 is a Map-based Question carrying 5 marks.
""" + "\n".join(f"Q{i}. Explain the required concept." for i in range(1, 12)) + """
Q12. Read the passage about medieval travellers and answer the following:
1. Name any one medieval traveller whose account is important.
2. Mention any one aspect of Indian society that medieval travellers described.
3. Why should historians critically examine travellers' accounts? Explain.
Q13. Locate and label the five historical sites on the map.
"""
HISTORY_EXPECTED = {**{f"Q{i}": 1 for i in range(1, 7)},
                    **{f"Q{i}": 3 for i in range(7, 10)},
                    "Q10": 8, "Q11": 8, "Q12": 4, "Q13": 5}
HISTORY_GOOD = {"items": [{"qid": qid, "max": maximum}
                          for qid, maximum in HISTORY_EXPECTED.items()],
                "choice_groups": [], "allocation_issues": []}


class SchoolSharedAllocationTests(unittest.TestCase):
    def test_real_extractor_keeps_shared_parents_and_explicit_two_mark_part(self):
        reply = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(GOOD)}]}
        with patch.object(grader, "_claude_call", return_value=reply):
            scheme = grader.extract_marks_scheme([], google_token="mock", task_id="offline",
                student_class="12th", subject="Economics", exam_type="board", total_marks=40,
                paper_text=PAPER, require_complete=True)
        self.assertEqual({row.qid: row.max for row in scheme.items}, EXPECTED)
        self.assertEqual(scheme.total, 40)
        self.assertFalse(scheme.choice_groups)
        self.assertNotIn("Q17.a", EXPECTED)
        self.assertNotIn("Q14.b", EXPECTED)

    def test_unresolved_tags_still_fail_without_bypassing_strict_parser(self):
        unresolved = copy.deepcopy(GOOD)
        unresolved["allocation_issues"] = ["Q16.b: printed tag unreadable"]
        with self.assertRaisesRegex(grader.MarksExtractionError, "Q16.b"):
            grader.parse_strict_marks_scheme(json.dumps(unresolved))

    def test_shared_allocation_graph_can_be_attested_and_queued(self):
        flow = ca.ProviderFlowTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.reply = copy.deepcopy(GOOD)
        form = dict(flow.form, student_class="12th", subject="Economics", exam_type="board",
                    total_marks="40")
        with patch.object(server, "pdf_text_layer", return_value=PAPER), \
             patch.object(pipeline, "pdf_text_layer", return_value=PAPER):
            result = flow.post("/api/detect-marks", form,
                               {"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf")})
            self.assertEqual(result["method"], "ai_verified", result)
            self.assertEqual(result["effective_total"], 40)
            self.assertEqual({row["qid"]: row["max"] for row in result["items"]}, EXPECTED)
            self.assertTrue(result["marks_attestation"])
            flow.post("/api/evaluate", {**form, "key_source": "upload",
                "marks_items": json.dumps(result["items"]), "marks_source": result["method"],
                "marks_attestation": result["marks_attestation"]},
                {"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf"),
                 "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
                 "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})

    def test_source_based_numbered_requirements_keep_one_attested_four_mark_award(self):
        flow = ca.ProviderFlowTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.reply = copy.deepcopy(HISTORY_GOOD)
        form = dict(flow.form, student_class="12th", subject="History", exam_type="board",
                    total_marks="40")
        with patch.object(server, "pdf_text_layer", return_value=HISTORY_PAPER), \
             patch.object(pipeline, "pdf_text_layer", return_value=HISTORY_PAPER):
            result = flow.post("/api/detect-marks", form,
                               {"question_paper": ("history.pdf", b"%PDF-history", "application/pdf")})
            self.assertEqual(result["method"], "ai_verified", result)
            self.assertEqual(result["effective_total"], 40)
            self.assertEqual({row["qid"]: row["max"] for row in result["items"]}, HISTORY_EXPECTED)
            self.assertEqual([(row["qid"], row["max"]) for row in result["items"]
                              if row["qid"].startswith("Q12")], [("Q12", 4.0)])
            self.assertTrue(result["marks_attestation"])
            flow.post("/api/evaluate", {**form, "key_source": "upload",
                "marks_items": json.dumps(result["items"]), "marks_source": result["method"],
                "marks_attestation": result["marks_attestation"]},
                {"question_paper": ("history.pdf", b"%PDF-history", "application/pdf"),
                 "answer_key": ("history-key.pdf", b"%PDF-key", "application/pdf"),
                 "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})


if __name__ == "__main__":
    unittest.main()
