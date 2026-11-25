"""Replay CA source-reading failures and the model's 74-vs-56 choice arithmetic."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz
import httpx
import grader
import pipeline
import server
from marks_attestation import MarksAttestationStore, MarksAttestationError, effective_marks_total
from paper_structure import ca_paper_structure
from test_nested_choices import audit_scheme, make_package


def paper_text():
    mcqs = "\n".join(f"{i}.\nMCQ question {i}\n(1) First\n(2) Second\n(3) Third\n(4) Fourth"
                     for i in range(1, 16))
    ordinary = "\n".join(f"{i}.\n(A) First requirement (5 Marks)\n"
                          "(B) Second requirement (5 Marks)\n(C) Third requirement (4 Marks)"
                          for i in (2, 3, 4))
    return """Maximum Marks- 100
Part-A comprises 15 Multiple Choice Questions (MCQs).
Part-B comprises 6 questions having 3 parts each which require descriptive type answers.
Part - A
Multiple choice question (MCQ)
(Case Scenario 1 carries 6 Marks)
(Case Scenario 2 carries 10 Marks)
(Case Scenario 3 carries 8 Marks)
General MCQs (3 MCQs × 2 Marks each = 6 Marks)
Directions (1-3) Read the case.
Directions (4-8) Read the case.
Directions (9-12) Read the case.
""" + mcqs + """
Part – B
Question No. 1 is compulsory.
Attempt any four questions from the Rest.
1.
(A) Explain the requirement. (5 Marks)
(B) Two separately marked requirements.
(i) First requirement (3 Marks)
(ii) Second requirement (2 Marks)
(C) Explain the concept. (4 Marks)
""" + ordinary + """
5.
(A) Identify standards for these assignments.
(i) First assignment (1 Mark)
(ii) Second assignment (1 Mark)
Further, explain the difference between the engagements. (3 Marks)
(B) Explain the risk. (5 Marks)
(C) Verify the charges. (4 Marks)
6.
(A) Determine and communicate matters. (5 Marks)
(B) Planning engagement activities.
(i) Identify three preliminary activities.
(ii) List three matters for continuance. (5 Marks)
(C1) Engagement for the financial year 2024-
25.
(i) Discuss with the team. (2 Marks)
(ii) Discuss with management. (2 Marks)
OR
(C2) Planning an NGO audit. (4 Marks)
"""


PAPER = paper_text()
BASE = audit_scheme()
EXPECTED = {**{f"Q{i}": 2.0 for i in range(1, 16)},
            **{row.qid: row.max for row in BASE.items[15:]}}
GOOD = {
    "allocation_issues": [], "items": EXPECTED,
    "choice_groups": {f"G{i}": {key: value for key, value in group.model_dump().items()
                                if key not in ("group_id", "contribution_marks")}
                      for i, group in enumerate(BASE.choice_groups, 1)},
}


class SourceReadingTests(unittest.TestCase):
    def test_printed_registry_keeps_shared_and_unnumbered_allocations(self):
        registry = ca_paper_structure(PAPER)
        self.assertIsNotNone(registry)
        self.assertEqual({unit.qid: unit.maximum for unit in registry.units}, EXPECTED)
        self.assertEqual(sum(unit.maximum for unit in registry.units), 118)
        self.assertEqual(len(registry.choice_instructions), 2)
        self.assertIn("Further", next(u.source for u in registry.units if u.qid == "Q5.a.remaining"))
        shared = next(u for u in registry.units if u.qid == "Q6.b")
        self.assertIn("(i)", shared.source)
        self.assertIn("(ii)", shared.source)
        self.assertEqual(shared.maximum, 5)

    def test_unrecognized_or_incomplete_layout_keeps_general_extractor(self):
        self.assertIsNone(ca_paper_structure("A scanned paper"))
        self.assertIsNone(ca_paper_structure(PAPER.replace("15.\nMCQ", "16.\nMCQ")))
        self.assertIsNone(ca_paper_structure(PAPER.replace("(C2)", "(B)")))

    def test_finite_schema_cannot_omit_or_repeat_a_registered_row(self):
        schema, registry = grader.marks_extraction_contract(PAPER, require_complete=True)
        schema = grader._strict_json_schema(schema)
        self.assertEqual(set(schema["properties"]["items"]["required"]), set(EXPECTED))
        self.assertFalse(schema["properties"]["items"]["additionalProperties"])
        self.assertEqual(schema["properties"]["choice_groups"]["required"], ["G1", "G2"])
        self.assertNotIn("total", schema["properties"])
        self.assertNotIn("contribution_marks", json.dumps(schema))
        self.assertNotIn("structure_ok", json.dumps(schema))
        self.assertIn("allocation_issues", schema["required"])
        self.assertNotIn("MarksItem", schema["$defs"])
        self.assertNotIn("correct_answer", json.dumps(schema))
        self.assertLess(len(json.dumps(schema)), 6000)
        parsed = grader.parse_strict_marks_scheme(json.dumps(GOOD))
        grader.validate_marks_registry(parsed, registry)
        self.assertEqual(parsed.total, 118)

    def test_false_or_missing_structure_confirmation_is_not_accepted(self):
        for confirmation in (False, None):
            bad = copy.deepcopy(GOOD)
            if confirmation is None:
                bad.pop("structure_ok", None)
            else:
                bad["structure_ok"] = confirmation
            with self.assertRaises(grader.MarksExtractionError):
                grader.parse_strict_marks_scheme(json.dumps(bad), require_structure=True)

    def test_recorded_120_mark_error_is_rejected_at_its_actual_source(self):
        # The live log omitted the unnumbered 3 marks and assigned the shared
        # Q6(B) 5 marks to each roman clause, giving 118 - 3 + 5 = 120.
        bad = copy.deepcopy(GOOD)
        del bad["items"]["Q5.a.remaining"]
        del bad["items"]["Q6.b"]
        bad["items"].update({"Q6.b.i": 5, "Q6.b.ii": 5})
        self.assertEqual(sum(bad["items"].values()), 120)
        with self.assertRaises(grader.MarksExtractionError):
            grader.parse_strict_marks_scheme(json.dumps(bad), structure=ca_paper_structure(PAPER))
        bad = copy.deepcopy(GOOD)
        bad["items"]["Q6.b"] = 10
        with self.assertRaisesRegex(grader.MarksExtractionError, "Q6.b: read 10, printed 5"):
            grader.parse_strict_marks_scheme(json.dumps(bad), structure=ca_paper_structure(PAPER))

    def test_nested_arithmetic_ignores_redundant_provider_fields_and_group_order(self):
        for reverse in (False, True):
            reading = copy.deepcopy(GOOD)
            reading["total"] = 999
            reading["choice_groups"]["G1"]["contribution_marks"] = 74
            reading["choice_groups"]["G2"]["contribution_marks"] = "not an allocation"
            if reverse:
                reading["choice_groups"] = dict(reversed(list(reading["choice_groups"].items())))
            scheme = grader.parse_strict_marks_scheme(json.dumps(reading))
            self.assertEqual(scheme.total, 118)
            self.assertEqual(effective_marks_total(scheme), 100)
            self.assertEqual({g.group_id: g.contribution_marks for g in scheme.choice_groups},
                             {"G1": 56, "G2": 4})
            self.assertEqual({row.qid: row.max for row in scheme.items}, EXPECTED)

    def test_general_extraction_derives_totals_without_ca_specific_numbers(self):
        schema, registry = grader.marks_extraction_contract("Unknown layout", require_complete=True)
        self.assertIsNone(registry)
        self.assertNotIn("contribution_marks", json.dumps(schema))
        reading = {"items": [{"qid": "Q1", "max": 1.5}, {"qid": "Q2", "max": 2.5},
                             {"qid": "Q3", "max": 2.5}, {"qid": "Q4", "max": 2.5}],
                   "choice_groups": [{"group_id": "optional", "select_count": 2,
                       "options": [{"option_id": qid, "question_ids": [qid]}
                                   for qid in ("Q2", "Q3", "Q4")]}]}
        scheme = grader.parse_strict_marks_scheme(json.dumps(reading))
        self.assertEqual((scheme.total, effective_marks_total(scheme)), (9, 6.5))
        self.assertEqual(scheme.choice_groups[0].contribution_marks, 5)

    def test_arithmetic_does_not_fix_invalid_graphs_or_missing_facts(self):
        for defect in ("missing-count", "wrong-count", "unknown", "duplicate", "crossing", "unequal"):
            with self.subTest(defect=defect):
                reading = copy.deepcopy(GOOD)
                outer, inner = reading["choice_groups"].values()
                if defect == "missing-count":
                    del outer["select_count"]
                elif defect == "wrong-count":
                    outer["select_count"] = 6
                elif defect == "unknown":
                    outer["options"][0]["question_ids"].append("Q99")
                elif defect == "duplicate":
                    outer["options"][0]["question_ids"].append("Q2.a")
                elif defect == "crossing":
                    inner["options"][1]["question_ids"] = ["Q2.c"]
                else:
                    reading["items"]["Q2.a"] = 6
                with self.assertRaises(ValueError):
                    grader.parse_strict_marks_scheme(json.dumps(reading))

    def test_duplicate_or_invalid_maxima_cannot_disappear_during_arithmetic(self):
        for rows in ([{"qid": "Q2", "max": 2}, {"qid": "q2", "max": 3}],
                     [{"qid": "Q1", "max": float("nan")}],
                     [{"qid": "Q1", "max": float("inf")}],
                     [{"qid": "Q1", "max": 0}], []):
            with self.subTest(rows=rows), self.assertRaises(grader.MarksExtractionError):
                grader.parse_strict_marks_scheme(json.dumps({"items": rows}))

    def test_stored_authority_cannot_bypass_contribution_validation(self):
        for index, invalid in ((0, 74), (1, 8)):
            scheme = audit_scheme()
            scheme.choice_groups[index].contribution_marks = invalid
            with self.assertRaises(MarksAttestationError):
                effective_marks_total(scheme)
            self.assertIn("choice-contribution-mismatch",
                          {issue.code for issue in make_package(scheme).blockers()})

    def test_single_column_paragraphs_and_marks_keep_reading_order(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((35, 50), "Q5. First short question")
            page.insert_text((480, 80), "(3 Marks)")
            page.insert_text((35, 110), "Q6. " + "Long question across the page. " * 3, fontsize=9)
            page.insert_text((480, 140), "(5 Marks)")
            page.insert_text((35, 170), "Q7. Next question")
            text = grader.pdf_text_layer(doc.tobytes(), "paper.pdf")
        self.assertLess(text.index("(3 Marks)"), text.index("Q6."))
        self.assertLess(text.index("(5 Marks)"), text.index("Q7."))

    def test_true_columns_with_a_full_width_heading_stay_separate(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((130, 30), "OFFICIAL ANSWER KEY FOR BOTH COLUMNS", fontsize=12)
            page.insert_text((35, 65), "Q5")
            page.insert_text((325, 65), "Q6")
            page.insert_text((35, 100), "Correct Answer: B. Explanation for this answer.", fontsize=9)
            page.insert_text((325, 100), "Correct Answer: D. Explanation for this answer.", fontsize=9)
            text = grader.pdf_text_layer(doc.tobytes(), "key.pdf")
        self.assertLess(text.index("Correct Answer: B"), text.index("Q6"))
        self.assertLess(text.index("Q6"), text.index("Correct Answer: D"))

    def test_question_label_precedes_same_baseline_text_in_a_different_font(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((30, 60), "3.", fontname="hebo", fontsize=11)
            page.insert_text((60, 60), "(A) A long question across the full page describing the required explanation.",
                             fontname="tiro", fontsize=11)
            text = grader.pdf_text_layer(doc.tobytes(), "paper.pdf")
        self.assertLess(text.index("3."), text.index("(A)"))


class ProviderFlowTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reply = copy.deepcopy(GOOD)
        self.truncated = False
        self.form = dict(provider="claude", model="", student_class="CA Inter",
                         subject="Auditing", exam_type="professional", total_marks="100", mode="ai")
        patches = [patch.multiple(server, _require_allowed=lambda token: "tester",
                                  _MARKS_ATTESTATIONS=MarksAttestationStore(), JOBS={},
                                  _persist_job=lambda *a: None, _run_job=lambda *a: None,
                                  _paper_text_layer_complete=lambda *a: True,
                                  pdf_text_layer=lambda *a: PAPER, _log_error=lambda *a: None),
                   patch.multiple(pipeline, pdf_text_layer=lambda *a: PAPER,
                                  pdf_or_image_to_pngs=lambda *a: []),
                   patch.object(grader, "_claude_call", self.claude)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def claude(self, *args, **kwargs):
        # The marks are read ONCE, by Claude; there is no second reader.
        self.calls.append(("claude", kwargs))
        return {"stop_reason": "max_tokens" if self.truncated else "end_turn",
                "content": [{"type": "text", "text": json.dumps(self.reply)}]}

    def post(self, path, data, files):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                return await client.post(path, data=data, files=files,
                                         headers={"Authorization": "tester"})
        response = asyncio.run(send())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def detect(self):
        return self.post("/api/detect-marks", self.form,
                         {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf")})

    def test_real_adapters_parse_verify_and_queue_the_complete_graph(self):
        result = self.detect()
        self.assertEqual(result["method"], "ai_verified", result)
        self.assertEqual((result["total"], result["effective_total"]), (118, 100))
        self.assertEqual(len(result["items"]), 38)
        self.assertEqual([provider for provider, _ in self.calls], ["claude"])
        schema = self.calls[0][1]["request"]["output_config"]["format"]["schema"]
        self.assertEqual(set(schema["properties"]["items"]["required"]), set(EXPECTED))
        self.assertNotIn("contribution_marks", json.dumps(schema))
        self.post("/api/evaluate", {**self.form, "key_source": "upload",
            "marks_items": json.dumps(result["items"]), "marks_source": result["method"],
            "marks_attestation": result["marks_attestation"]},
            {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf"),
             "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
             "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})

    def test_recorded_74_instead_of_56_does_not_require_an_ai_repair(self):
        self.reply["total"] = 100
        self.reply["choice_groups"]["G1"]["contribution_marks"] = 74
        self.reply["choice_groups"]["G2"]["contribution_marks"] = 8
        # The recorded contributions are not trusted; the server recomputes 118 -> 100.
        self.test_real_adapters_parse_verify_and_queue_the_complete_graph()

    def test_wrong_select_count_is_not_adjusted_to_reach_100(self):
        self.reply["choice_groups"]["G1"]["select_count"] = 3
        result = self.detect()
        self.assertFalse(result["marks_attestation"])
        self.assertEqual(result["effective_total"], 86)
        self.assertEqual(len(self.calls), 1)

    def test_output_limit_never_substitutes_the_54_mark_regex_table(self):
        self.truncated = True
        with patch.object(server, "marks_scheme_from_pdf", side_effect=AssertionError("unsafe fallback")):
            result = self.detect()
        self.assertEqual(result["failure_code"], "extractor_incomplete_output")
        self.assertEqual(result["items"], [])
        self.assertFalse(result["marks_attestation"])
        self.assertFalse(result["extraction_cached"])
        self.assertIn("Claude stopped", result["verification_detail"])
        self.assertEqual(len(self.calls), 1)
        self.truncated = False
        self.assertEqual(self.detect()["method"], "ai_verified")

    def test_wrong_registered_maximum_blocks_before_independent_verification(self):
        self.reply["items"]["Q6.b"] = 10
        result = self.detect()
        self.assertEqual(result["failure_code"], "extractor_source_mismatch")
        self.assertIn("Q6.b", result["verification_detail"])
        self.assertFalse(result["marks_attestation"])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
