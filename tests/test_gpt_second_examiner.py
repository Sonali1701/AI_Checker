# -*- coding: utf-8 -*-
"""The second examiner on the proxy's ChatGPT deployments (2026-09-17).

* the strict Claude route verifies school copies on Luna and CA/commerce copies on
  Terra; legacy routes keep the stronger-same-provider auditor;
* the audit request is a native chat.completions body: system prompt, the shared
  key material first (prefix caching), strict JSON schema with every property
  required, images as data URLs, low reasoning effort with headroom;
* usage is recorded as provider "gpt" with cached tokens split out and priced;
* nulls the strict schema forces are stripped before the Claude parser runs;
* a missing deployment (404) sends the pass to the Claude fallback;
* the department caps are Rs.12 for school and Rs.18 for CA/commerce.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import costs  # noqa: E402
import grader  # noqa: E402
import pipeline  # noqa: E402
import pw_access  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402


def _report():
    q = QuestionMark(qid="Q7.c", score=1.5, max_score=3, page=1, y_fraction=0.5, anchor_line_id="P1L1",
                     criteria=[Criterion(description="Saptanga", awarded=1, max=1, met=True,
                                         step_line_id="P1L1", anchor_quote="Saptanga theory"),
                               Criterion(description="Bureaucracy", awarded=0.5, max=1, met=False,
                                         step_line_id="P1L2", anchor_quote="Centralised"),
                               Criterion(description="Espionage", awarded=0, max=1, met=False,
                                         step_line_id="", anchor_quote="")])
    return GradeReport(total_score=1.5, max_total=3, overall_remarks="", questions=[q],
                       evidence_contract=True)


def _gpt_reply(items, prompt_tokens=12000, cached=4000, completion=900):
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps({"items": items})}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
                  "prompt_tokens_details": {"cached_tokens": cached}},
    }


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAFklEQVR4nGP8z4AATAxYwKggXQUBAKcPAgPeWZG7AAAAAElFTkSuQmCC")


class RoutingTests(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(provider="claude", model="claude-haiku-4-5", grading_contract="exam_package_v2")
        base.update(kw)
        return pipeline.ProviderConfig(**base)

    def test_school_and_ca_copies_both_verify_on_luna(self):
        # 2026-09-19: Terra cost Rs.20 a CA copy and moved zero marks on the
        # sixteen-copy batch; Luna is a tenth of the price for the same tokens.
        self.assertEqual(pipeline.verifier_model_for(self._cfg(), "Social Science"),
                         pipeline._SCHOOL_SECOND_EXAMINER)
        self.assertEqual(pipeline.verifier_model_for(self._cfg(exam_type="professional"), "Accounting"),
                         pipeline._PROFESSIONAL_SECOND_EXAMINER)
        self.assertEqual(pipeline.verifier_model_for(self._cfg(), "Business Studies"),
                         pipeline._PROFESSIONAL_SECOND_EXAMINER)
        self.assertTrue(pipeline._SCHOOL_SECOND_EXAMINER.startswith("gpt-5.6-luna"))
        self.assertTrue(pipeline._PROFESSIONAL_SECOND_EXAMINER.startswith("gpt-5.6-luna"))

    def test_the_ladder_never_lists_one_examiner_twice(self):
        self.assertEqual(pipeline.verifier_ladder_for(self._cfg(exam_type="professional"), "Accounting"),
                         [pipeline._PROFESSIONAL_SECOND_EXAMINER])
        self.assertEqual(pipeline.verifier_ladder_for(self._cfg(), "Social Science"),
                         [pipeline._SCHOOL_SECOND_EXAMINER])
        legacy = self._cfg(grading_contract="legacy")
        self.assertEqual(pipeline.verifier_ladder_for(legacy, "Physics"), [pipeline.audit_model_for(legacy)])

    def test_legacy_routes_keep_the_stronger_same_provider_auditor(self):
        legacy = self._cfg(grading_contract="legacy")
        self.assertEqual(pipeline.verifier_model_for(legacy, "Physics"), pipeline.audit_model_for(legacy))
        # A Gemini config cannot exist any more: it is coerced to Claude on creation,
        # so it gets the Claude strict route's second examiner.
        gemini = pipeline.ProviderConfig(provider="gemini", model="gemini-2.5-flash",
                                         grading_contract="exam_package_v2")
        self.assertTrue(gemini.is_claude)
        self.assertEqual(pipeline.verifier_model_for(gemini, "Physics"),
                         pipeline._SCHOOL_SECOND_EXAMINER)

    def test_only_listed_gpt_deployments_pass_the_startup_check(self):
        self.assertEqual(pipeline.assert_proxy_model("gpt-5.6-terra-ard-content"), "gpt-5.6-terra-ard-content")
        with self.assertRaises(RuntimeError):
            pipeline.assert_proxy_model("gpt-5.6-terra")
        self.assertEqual(pipeline.provider_of("gpt-5.6-luna-ard-content"), pipeline.GPT)
        self.assertEqual(pipeline.provider_of("claude-haiku-4-5"), pipeline.CLAUDE)

    def test_gpt_reasoning_headroom_is_in_the_output_allowance_and_its_quote(self):
        report = _report()
        claude = pipeline._evidence_output_budget(report, None, model="claude-sonnet-4-6")
        gpt = pipeline._evidence_output_budget(report, None, model="gpt-5.6-luna-ard-content")
        self.assertEqual(gpt - claude, grader.GPT_REASONING_HEADROOM)


class PrimaryCeilingTests(unittest.TestCase):
    def test_the_primary_floor_grows_with_the_paper_and_stays_inside_the_ceiling(self):
        from types import SimpleNamespace as NS
        small = NS(questions=[NS(scorable=True, criteria=[]) for _ in range(5)])
        sst = NS(questions=[NS(scorable=True, criteria=[]) for _ in range(5)]
                 + [NS(scorable=True, criteria=[1, 2, 3]) for _ in range(21)])
        huge = NS(questions=[NS(scorable=True, criteria=[1] * 8) for _ in range(40)])
        self.assertEqual(pipeline._primary_output_floor(small), 6000)
        self.assertEqual(pipeline._primary_output_floor(sst), 800 + 150 * 26 + 80 * (5 + 63))
        self.assertEqual(pipeline._primary_output_floor(huge), 15000)
        self.assertEqual(pipeline._primary_output_floor(None), 6000)


class CostTests(unittest.TestCase):
    def test_gpt_deployments_are_priced_and_cached_input_is_a_tenth(self):
        rates = costs._match_rates("gpt-5.6-terra-ard-content")
        self.assertEqual((rates["input"], rates["output"]), (2.0, 12.0))
        self.assertAlmostEqual(rates["cached_input"], 0.2)
        self.assertEqual(costs._match_rates("gpt-5.6-luna-ard-content")["output"], 1.2)
        usage = {}
        costs.record_usage_event(usage, {"provider": "gpt", "model": "gpt-5.6-luna-ard-content",
                                         "input_tokens": 8000, "cached_input_tokens": 4000,
                                         "output_tokens": 1000}, stage="verify")
        cost = costs.compute_cost(usage, usd_to_inr=94.0)
        self.assertTrue(cost.rates_found and cost.usage_attested)
        expected_usd = (8000 * 0.20 + 4000 * 0.02 + 1000 * 1.20) / 1e6
        self.assertAlmostEqual(cost.total_inr, round(expected_usd * 94.0, 4), places=3)

    def test_department_caps(self):
        self.assertEqual(costs.budget_for(30, exam_type="school", subject="Social Science"), 12.0)
        self.assertEqual(costs.budget_for(100, exam_type="school", subject="Physics"), 12.0)
        self.assertEqual(costs.budget_for(25, exam_type="professional", subject="Accounting"), 18.0)
        self.assertEqual(costs.budget_for(100, exam_type="professional", subject="Taxation"), 18.0)
        self.assertEqual(costs.budget_for(50, exam_type="school", subject="Business Studies"), 18.0)


class StrictSchemaTests(unittest.TestCase):
    def test_every_property_is_required_and_optional_ones_are_nullable(self):
        schema = grader._openai_strict_schema(grader._strict_json_schema(
            grader.audit_response_schema(True, None)))

        def walk(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                props = node.get("properties") or {}
                self.assertEqual(sorted(node.get("required") or []), sorted(props))
                self.assertIs(node.get("additionalProperties"), False)
                for sub in props.values():
                    walk(sub)
            for sub in (node.get("anyOf") or []):
                walk(sub)
            if isinstance(node.get("items"), dict):
                walk(node["items"])
            for sub in (node.get("$defs") or {}).values():
                walk(sub)
        walk(schema)
        item = schema["$defs"]["AuditItem"]
        self.assertTrue(grader._nullable(item["properties"]["criterion"]))
        self.assertNotIn("minItems", json.dumps(schema))

    def test_nulls_are_stripped_before_parsing(self):
        self.assertEqual(grader._strip_nulls({"a": None, "b": [{"c": None, "d": 1}], "e": ""}),
                         {"b": [{"d": 1}], "e": ""})


class AuditRequestTests(unittest.TestCase):
    def test_the_audit_runs_as_a_chat_completion_with_the_key_first(self):
        report = _report()
        seen = {}

        def fake_gpt(google_token, **kw):
            seen.update(kw)
            return _gpt_reply([
                {"row": 0, "correct": 1, "reason": "", "evidence_status": "verified",
                 "step_line_id": "P1L1", "anchor_quote": "Saptanga theory", "anchor_bbox": [],
                 "anchor_verdict": "correct", "checking_points": [], "award_scope": "entry",
                 "award_anchor": None, "evidence_kind": "written", "qid": None, "criterion": None},
                {"row": 1, "correct": 0.5, "reason": "Departments not named.", "evidence_status": "verified",
                 "step_line_id": "P1L2", "anchor_quote": "Centralised", "anchor_bbox": None,
                 "anchor_verdict": "partial", "checking_points": None, "award_scope": None,
                 "award_anchor": None, "evidence_kind": None, "qid": "Q7.c", "criterion": 1}])
        usage = {}
        with patch.object(pw_access, "gpt_generate", side_effect=fake_gpt), \
                patch.object(pw_access, "claude_generate") as claude:
            items = grader.audit_deductions(
                report, "gpt-5.6-luna-ard-content", google_token="tok", task_id="t1",
                filename="x.pdf", answer_key_text="key", ocr_transcript="P1L1 Saptanga theory",
                question_paper_text="Q7 c)", student_evidence_pngs=[PNG], answer_key_pngs=[PNG],
                usage_out=usage, include_awards=True, selected_qids={"q7.c"}, max_output_tokens=1200)
        claude.assert_not_called()
        self.assertEqual(seen["model"], "gpt-5.6-luna-ard-content")
        request = seen["request"]
        self.assertEqual(request["messages"][0], {"role": "system", "content": grader.AUDIT_SYSTEM_PROMPT})
        parts = request["messages"][1]["content"]
        kinds = [p["type"] for p in parts]
        # key layout note, key image, then the prompt, then the student page header and image
        self.assertEqual(kinds, ["text", "image_url", "text", "text", "image_url"])
        self.assertRegex(parts[1]["image_url"]["url"], r"^data:image/(jpeg|png);base64,")
        self.assertEqual(request["max_completion_tokens"], 1200 + grader.GPT_REASONING_HEADROOM)
        self.assertEqual(request["reasoning_effort"], grader.GPT_REASONING_EFFORT)
        self.assertIs(request["response_format"]["json_schema"]["strict"], True)
        self.assertNotIn("temperature", request)
        self.assertNotIn("max_tokens", request)
        # usage: cached tokens split out, provider gpt, stage verify
        event = usage["_events"][0]
        self.assertEqual((event["provider"], event["stage"]), ("gpt", "verify"))
        self.assertEqual((event["input_tokens"], event["cached_input_tokens"], event["output_tokens"]),
                         (8000, 4000, 900))
        self.assertTrue(costs.compute_cost(usage).rates_found)
        # the nulls the strict schema forced did not reach the parser
        self.assertEqual([(i.row, i.correct) for i in items], [(0, 1.0), (1, 0.5)])
        self.assertEqual(items[1].reason, "Departments not named.")
        self.assertEqual(items[0].award_scope, "entry")

    def test_a_missing_deployment_falls_back_to_claude(self):
        report = _report()
        usage = {}

        def missing(google_token, **kw):
            raise pw_access.PWAccessError("gpt proxy error 404: DeploymentNotFound",
                                          status_code=404, provider="gpt")
        claude_seen = {}

        def fake_claude(google_token, **kw):
            claude_seen.update(kw)
            return {"content": [{"type": "text", "text": json.dumps({"items": [
                {"row": 0, "correct": 1, "reason": ""}]})}],
                "usage": {"input_tokens": 5000, "output_tokens": 300}}
        with patch.object(pw_access, "gpt_generate", side_effect=missing), \
                patch.object(pw_access, "claude_generate", side_effect=fake_claude):
            items = grader.audit_deductions(
                report, "gpt-5.6-terra-ard-content", google_token="tok", task_id="t1",
                answer_key_text="key", ocr_transcript="P1L1", usage_out=usage,
                include_awards=True, max_output_tokens=800)
        self.assertEqual(claude_seen["model"], grader.GPT_SECOND_EXAMINER_FALLBACK)
        self.assertEqual([e["provider"] for e in usage["_events"]], ["claude"])
        self.assertEqual(items[0].correct, 1.0)

    def test_a_rejected_strict_request_is_retried_plainly_once(self):
        report = _report()
        calls = []

        def picky(google_token, **kw):
            calls.append(kw["request"])
            if "reasoning_effort" in kw["request"]:
                raise pw_access.PWAccessError("gpt proxy error 400: unsupported parameter",
                                              status_code=400, provider="gpt")
            return _gpt_reply([{"row": 0, "correct": 1, "reason": ""}])
        with patch.object(pw_access, "gpt_generate", side_effect=picky), \
                patch.object(pw_access, "claude_generate") as claude:
            grader.audit_deductions(report, "gpt-5.6-luna-ard-content", google_token="tok",
                                    task_id="t1", answer_key_text="k", ocr_transcript="P1L1",
                                    usage_out={}, include_awards=True, max_output_tokens=800)
        claude.assert_not_called()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("reasoning_effort", calls[1])
        self.assertIs(calls[1]["response_format"]["json_schema"]["strict"], False)

    def test_capacity_errors_are_not_swallowed(self):
        def busy(google_token, **kw):
            raise pw_access.PWAccessError("busy", status_code=529, provider="gpt")
        with patch.object(pw_access, "gpt_generate", side_effect=busy), \
                self.assertRaises(pw_access.PWAccessError):
            grader.audit_deductions(_report(), "gpt-5.6-luna-ard-content", google_token="tok",
                                    task_id="t1", usage_out={}, include_awards=True,
                                    max_output_tokens=800)


if __name__ == "__main__":
    unittest.main()
