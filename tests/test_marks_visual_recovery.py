"""Original page evidence, bounded automatic recovery, and scanned-paper attestation.

Only provider transport replies are simulated; both adapters, schemas, parsing,
graph comparison, API proof issuance and evaluation queue validation run normally.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz
import grader
import pipeline
import pw_access
import server
from paper_structure import ca_paper_structure
import test_ca_extraction as ca
from test_ca_extraction import GOOD, PAPER


def general_reading():
    reading = copy.deepcopy(GOOD)
    reading["items"] = [{"qid": qid, "max": maximum} for qid, maximum in reading["items"].items()]
    reading["choice_groups"] = [dict(group, group_id=gid)
                                for gid, group in reading["choice_groups"].items()]
    return reading


def page_png():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), "Maximum Marks: 3\nQ1. Explain X. (1 Mark)\nQ2. Explain Y. (2 Marks)")
        return page.get_pixmap().tobytes("png")


class VisualRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.flow = ca.ProviderFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.png = page_png()
        p = patch.object(pipeline, "pdf_or_image_to_pngs", return_value=[self.png])
        p.start()
        self.addCleanup(p.stop)

    def assert_ready(self, result):
        self.assertEqual(result["method"], "ai_verified", result)
        self.assertEqual((result["total"], result["effective_total"]), (118, 100))
        self.flow.post("/api/evaluate", {**self.flow.form, "key_source": "upload",
            "marks_items": json.dumps(result["items"]), "marks_source": result["method"],
            "marks_attestation": result["marks_attestation"]},
            {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf"),
             "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
             "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})

    def test_boundary_veto_recovers_automatically_from_original_images(self):
        def reply(*args, **kwargs):
            recovery = kwargs["request"]["output_config"]["format"]["schema"]["properties"]["items"]["type"] == "array"
            self.flow.reply = general_reading() if recovery else dict(GOOD, structure_ok=False)
            return self.flow.claude(*args, **kwargs)
        with patch.object(grader, "_claude_call", reply):
            result = self.flow.detect()
        self.assert_ready(result)
        self.assertEqual([provider for provider, _ in self.flow.calls], ["claude", "claude"])
        self.assertEqual(len({call["task_id"] for _, call in self.flow.calls}), 1)
        for provider, call in self.flow.calls:
            parts = call["request"]["messages"][0]["content"]
            self.assertTrue(any(part.get("type") == "image" for part in parts))
        recovery_request = self.flow.calls[-1][1]["request"]
        text = json.dumps(recovery_request)
        self.assertIn("VISUAL ALLOCATION RECHECK", text)
        self.assertNotIn("SOURCE ALLOCATION REGISTRY", text)
        self.assertNotIn("CROSS-CHECK", text)
        self.assertNotIn("structure_ok", recovery_request["output_config"]["format"]["schema"]["properties"])

    def test_recovery_is_bounded_and_preserves_specific_unresolved_question(self):
        self.flow.reply = dict(GOOD, allocation_issues=["Q5.a.remaining: printed tag is unreadable"])
        result = self.flow.detect()
        self.assertFalse(result["marks_attestation"])
        self.assertIn("Q5.a.remaining", result["verification_detail"])
        self.assertEqual(len(self.flow.calls), 2)  # one reading + one bounded recovery

    def test_provider_rate_limit_does_not_trigger_extra_paid_recovery(self):
        def busy(*args, **kwargs):
            self.flow.calls.append(("claude", kwargs))
            raise pw_access.PWAccessError("busy", status_code=429, provider="claude")
        with patch.object(grader, "_claude_call", busy):
            result = self.flow.detect()
        self.assertEqual(result["failure_code"], "extractor_rate_limited")
        self.assertIn("Claude", result["verification_detail"])
        self.assertFalse(result["marks_attestation"])
        self.assertEqual(len(self.flow.calls), 1)

    def test_primary_truncation_also_gets_one_visual_recovery(self):
        def reply(*args, **kwargs):
            self.flow.truncated = not self.flow.calls
            self.flow.reply = general_reading()
            return self.flow.claude(*args, **kwargs)
        with patch.object(grader, "_claude_call", reply):
            result = self.flow.detect()
        self.assert_ready(result)
        self.assertEqual([p for p, _ in self.flow.calls], ["claude", "claude"])

    def test_scanned_mixed_and_image_papers_receive_visual_proofs_and_cache(self):
        real_text, real_images = grader.pdf_text_layer, grader.pdf_or_image_to_pngs
        for kind in ("scan", "mixed", "image"):
            with self.subTest(kind=kind):
                if kind == "image":
                    data, name, expected_pages = self.png, "paper.png", 1
                else:
                    with fitz.open() as doc:
                        if kind == "mixed":
                            doc.new_page().insert_text((40, 70), "Cover: Maximum Marks: 3")
                        page = doc.new_page()
                        page.insert_image(page.rect, stream=self.png)
                        page.set_rotation(90)
                        data, name, expected_pages = doc.tobytes(), "paper.pdf", len(doc)
                self.flow.calls.clear()
                self.flow.reply = {"allocation_issues": [], "choice_groups": [],
                                   "items": [{"qid": "Q1", "max": 1}, {"qid": "Q2", "max": 2}]}
                form = dict(self.flow.form, total_marks="3")
                with patch.object(server, "pdf_text_layer", real_text), \
                     patch.object(pipeline, "pdf_text_layer", real_text), \
                     patch.object(pipeline, "pdf_or_image_to_pngs", real_images), \
                     patch.object(server, "_paper_text_layer_complete", return_value=False):
                    result = self.flow.post("/api/detect-marks", form,
                                            {"question_paper": (name, data, "application/pdf")})
                    self.assertEqual(result["method"], "ai_verified", result)
                    self.assertEqual(result["effective_total"], 3)
                    self.flow.post("/api/detect-marks", form,
                                   {"question_paper": (name, data, "application/pdf")})
                    self.assertEqual(len(self.flow.calls), 1)  # the re-click is cached
                    for _, call in self.flow.calls:
                        parts = call["request"]["messages"][0]["content"]
                        self.assertEqual(sum(part.get("type") == "image" for part in parts),
                                         expected_pages)


class RegistryBoundaryTests(unittest.TestCase):
    def test_a_mark_before_the_question_does_not_hide_its_requirement(self):
        text = PAPER.replace("(A) Determine and communicate matters. (5 Marks)",
                             "(A) (5 Marks) Determine and communicate matters.")
        registry = ca_paper_structure(text)
        self.assertIsNotNone(registry)
        unit = next(unit for unit in registry.units if unit.qid == "Q6.a")
        self.assertEqual(unit.maximum, 5)
        self.assertIn("Determine and communicate matters", unit.source)

    def test_mcq_excerpt_ends_before_the_next_case_narrative(self):
        text = PAPER.replace("4.\nMCQ question 4", "Case Scenario - II\nA shared new case.\n1.\nA narrative fact.\n4.\nMCQ question 4")
        registry = ca_paper_structure(text)
        self.assertIsNotNone(registry)
        previous = next(unit.source for unit in registry.units if unit.qid == "Q3")
        self.assertNotIn("shared new case", previous)
        self.assertIn("Fourth", previous)


if __name__ == "__main__":
    unittest.main()
