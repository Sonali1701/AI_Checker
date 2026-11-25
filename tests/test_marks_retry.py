"""The question-wise marks are read ONCE, by Claude, and a busy Claude is recoverable."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
import pipeline
import server
import pw_access
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore


class SingleReadingTests(unittest.TestCase):
    def setUp(self):
        self.store = MarksAttestationStore()
        self.scheme = MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=2)])
        self.calls = []
        self.busy = False
        self.patch = patch.multiple(
            server, _require_allowed=lambda token: token or "user-one",
            _MARKS_ATTESTATIONS=self.store, JOBS={}, _persist_job=lambda *a: None,
            pdf_text_layer=lambda *a: "Maximum Marks: 3", paper_total_from_text=lambda *a: 3,
            _paper_text_layer_complete=lambda *a: True, paper_has_choice=lambda *a: False,
            detect_marks_scheme=self.detect, _run_job=lambda *a: None,
            _log_error=lambda *a: None,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.form = dict(provider="claude", model="", student_class="Class 11",
                         subject="Physics", exam_type="school", total_marks="3", mode="ai")

    def detect(self, data, filename, cfg, *args, **kwargs):
        if cfg is None:
            return None, "none"
        self.calls.append((cfg.provider, cfg.model))
        if self.busy:
            raise pw_access.PWAccessError("provider busy", status_code=429, provider="claude")
        return self.scheme.model_copy(deep=True), "ai"

    def post(self, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                return await client.post(path, **kwargs)
        return asyncio.run(send())

    def request(self, **form):
        response = self.post("/api/detect-marks", data={**self.form, **form},
                             headers={"Authorization": "user-one"},
                             files={"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def evaluate(self, result):
        return self.post("/api/evaluate", headers={"Authorization": "user-one"},
            data={**self.form, "key_source": "upload", "marks_items": json.dumps(result["items"]),
                  "marks_source": result["method"], "marks_attestation": result["marks_attestation"]},
            files={"question_paper": ("paper.pdf", b"%PDF-paper", "application/pdf"),
                   "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
                   "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})

    def test_one_claude_reading_verifies_and_grading_can_start(self):
        result = self.request(provider="gemini")  # an old form value is ignored
        self.assertEqual(result["method"], "ai_verified", result)
        self.assertTrue(result["marks_attestation"])
        self.assertEqual(self.calls, [(server.CLAUDE, pipeline._BUDGET_GRADING_MODEL)])
        [record] = self.store._records.values()
        self.assertEqual(record.extractors, (f"claude:{pipeline._BUDGET_GRADING_MODEL}",))
        self.assertEqual(self.evaluate(result).status_code, 200)
        self.request()  # a re-click is served from the cache
        self.assertEqual(len(self.calls), 1)

    def test_a_busy_claude_is_reported_and_never_authorizes_grading(self):
        self.busy = True
        result = self.request()
        self.assertEqual(result["method"], "ai_extraction_failed")
        self.assertEqual(result["failure_code"], "extractor_rate_limited")
        self.assertEqual(result["retry_after_seconds"], 60)
        self.assertEqual(result["marks_attestation"], "")
        self.assertIn("Claude", result["verification_detail"])
        self.assertNotIn("Gemini", result["verification_detail"])
        self.assertEqual(self.evaluate(result).status_code, 409)
        self.assertFalse(server.JOBS)

    def test_retry_after_a_busy_reading_reads_once_more(self):
        self.busy = True
        self.request()
        self.busy = False
        result = self.request()
        self.assertEqual(result["method"], "ai_verified")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.evaluate(result).status_code, 200)

    def test_capacity_error_keeps_machine_readable_reason(self):
        class Reply:
            status_code = 429
            headers = {"Retry-After": "20"}
        with self.assertRaises(pw_access.PWAccessError) as caught:
            pw_access._raise_proxy_error("claude", Reply())
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, 20)


if __name__ == "__main__":
    unittest.main()
