"""Removing the rupee cap must cover dispatch, repairs and final PDF release."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import pipeline
import server
import test_ca_extraction as ca
from budgeting import CallPlan, Stage
from costs import compute_cost, record_usage_event
from grader import AuditItem
from strict_grading import StrictGradeError, automatic_release_decision
from test_coverage_recovery import fixture


class UncappedGradingTests(unittest.TestCase):
    def test_capped_quote_is_an_estimate_and_primary_was_never_dispatched(self):
        source, images, pages, rows, report, cfg, scheme = fixture()
        original_quote = CallPlan.quote_inr
        def quote(plan, *args):
            return 17.38 if plan.stage == Stage.PRIMARY else original_quote(plan, *args)
        with patch.object(CallPlan, "quote_inr", quote), \
             patch.object(pipeline, "ocr_all_pages", return_value=pages) as ocr, \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(pipeline, "claude_grade") as grade:
            with self.assertRaises(StrictGradeError) as caught:
                pipeline.grade_prepared(qp_pngs=[], sa_pngs=images, ak_pngs=[],
                    ak_text="Use the official result.", marks_scheme=scheme,
                    cfg=cfg, total_marks=13, use_mathpix=True)
        self.assertEqual(caught.exception.code, "PRIMARY_BUDGET_RESERVATION_DENIED")
        self.assertIn("17.38", str(caught.exception))
        self.assertIn("not a charge", str(caught.exception))
        grade.assert_not_called()
        ocr.assert_called_once()

    def run_expensive_copy(self, *, unresolved=False):
        source, images, pages, rows, report, cfg, scheme = fixture()
        cfg.enforce_budget = False
        # Even an explicitly small old cap must not override the disabled switch.
        cfg.budget_cap_inr = 1
        calls, audits = [], []
        def grade(**kwargs):
            scope = kwargs.get("question_qids")
            calls.append(scope)
            record_usage_event(kwargs["usage_out"], {
                "provider": "claude", "model": kwargs["model"],
                "input_tokens": 500000, "output_tokens": 5000,
            }, stage="retry" if scope else "primary")
            chosen = rows[:1] if scope is None else [q for q in rows if q.qid in scope]
            return report.model_copy(deep=True, update={"questions": [q.model_copy(deep=True) for q in chosen]})
        def audit(current, **kwargs):
            self.assertEqual(kwargs["model"], pipeline.verifier_model_for(
                cfg, getattr(cfg.exam_package, "subject", "") or ""))
            only = kwargs.get("only_rows")
            audits.append(only)
            record_usage_event(kwargs["usage_out"], {
                "provider": "claude", "model": kwargs["model"],
                "input_tokens": 200000, "output_tokens": 4000,
            }, stage="verification_repair" if only else "verify")
            items = [AuditItem(row=i, correct=maximum, evidence_status="verified",
                step_line_id=q.criteria[index].step_line_id, anchor_quote=q.criteria[index].anchor_quote)
                for i, (q, index, maximum) in enumerate(grader._audit_rows(
                    current, True, kwargs.get("selected_qids")))]
            if unresolved:
                return []
            if only is None:
                items[-1].evidence_status = "uncertain"
                return items
            return [item for item in items if item.row in only]
        with patch.object(pipeline, "ocr_all_pages", return_value=pages), \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(pipeline, "_estimated_primary_input_tokens", return_value=500000), \
             patch.object(pipeline, "claude_grade", side_effect=grade), \
             patch.object(pipeline, "claude_audit", side_effect=audit):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
                ak_text="Award the stated maximum for each correct result.", marks_scheme=scheme,
                cfg=cfg, student_class="10", subject="Maths", total_marks=13, use_mathpix=True,
                qp_text="Maximum Marks: 13\n" + "\n".join(f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
        return result, calls, audits

    def test_high_cost_copy_completes_missing_questions_verifies_repairs_and_releases(self):
        result, calls, audits = self.run_expensive_copy()
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].total_score, 13)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(audits), 2)
        self.assertIsNone(audits[0])
        self.assertEqual(len(audits[1]), 1)
        self.assertGreater(result["cost"].total_inr, 100)
        self.assertTrue(result["verification_complete"])
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertIsNone(result["budget"]["cap_inr"])
        self.assertFalse(result["budget"]["enforced"])
        self.assertEqual(result["budget"]["actual_inr"], result["cost"].total_inr)
        self.assertIsNone(result["budget"]["within_cap"])
        json.dumps(result["budget"], allow_nan=False)

    def test_uncertified_marks_are_released_and_flagged_not_destroyed(self):
        """A second examiner that certifies nothing is a note on the copy.

        POLICY 2026-09-16. This used to raise VERIFICATION_INCOMPLETE and lose the
        whole copy — the marking, the OCR and the money already spent on it — over a
        CHECK on marks that were already decided and evidenced.
        """
        result, _calls, _audits = self.run_expensive_copy(unresolved=True)
        self.assertTrue(result["release"], result["blocking"])
        self.assertFalse(result["verification_complete"])
        self.assertTrue(any(issue.code == "not-certified" for issue in result["issues"]),
                        [str(issue) for issue in result["issues"]])
        self.assertEqual(result["report"].total_score, 13)

    def test_release_gate_separates_cost_limits_from_quality(self):
        _, _, _, rows, report, cfg, scheme = fixture()
        report.questions = rows
        report.total_score = scheme.total
        cost = compute_cost({"provider": "claude", "model": cfg.model,
                             "input_tokens": 1000000, "output_tokens": 15000})
        common = dict(package=cfg.exam_package, report=report, preflight_issues=[], cost=cost,
                      mandatory_verification_complete=True)
        capped = automatic_release_decision(**common, budget_cap_inr=15)
        self.assertIn("budget-exceeded", {i.code for i in capped.blockers})
        uncapped = automatic_release_decision(**common, budget_cap_inr=None)
        self.assertTrue(uncapped.release, uncapped.blockers)
        # An uncertified copy is released and the teacher is told (policy 2026-09-16);
        # the marks are the first examiner's, which is a note, not a wrong score.
        common["mandatory_verification_complete"] = False
        uncertified = automatic_release_decision(**common, budget_cap_inr=None)
        self.assertTrue(uncertified.release, uncertified.blockers)
        self.assertIn("verification-incomplete", {i.code for i in uncertified.warnings})
        common["mandatory_verification_complete"] = True
        report.maxima_rescaled = True
        self.assertIn("maxima-rescaled", {i.code for i in
            automatic_release_decision(**common, budget_cap_inr=None).blockers})

    def test_production_queue_treats_the_department_cap_as_a_target_not_a_gate(self):
        flow = ca.ProviderFlowTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        received = []
        with patch.object(server, "_run_job", side_effect=received.append):
            flow.test_real_adapters_parse_verify_and_queue_the_complete_graph()
        self.assertEqual(len(received), 1)
        cfg = received[0]["cfg"]
        self.assertFalse(cfg.enforce_budget)         # a held copy earns nobody anything
        self.assertIsNone(cfg.budget_cap_inr)
        self.assertTrue(cfg.is_strict)
        self.assertFalse(server.config()["budget_cap_enabled"])


if __name__ == "__main__":
    unittest.main()
