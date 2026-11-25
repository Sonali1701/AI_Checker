"""Missing OCR headings and a partially completed verifier must fit bounded repair."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import pipeline
from answer_map import build_answer_map
from budgeting import CallPlan, Stage
from costs import record_usage_event
from exam_package import normalize_qid
from grader import AuditItem, Criterion, audit_repair_rows, build_audit_prompt
from grading_quality import initialize_evidence, prepare_evidence, apply_verified_anchors
from strict_grading import StrictGradeError
from test_coverage_recovery import fixture


def missing_headings():
    source, images, pages, questions, report, cfg, scheme = fixture()
    questions[3].criteria = [questions[3].criteria[0].model_copy(update={"max": n, "awarded": n})
                             for n in (1, 2)]
    questions[4].criteria = [questions[4].criteria[0].model_copy(update={"max": n, "awarded": n})
                             for n in (2, 1, 1, 1)]
    for page in pages:
        for line in page.lines:
            if line.text in {"Q4", "Q6.i", "Q6.ii"}:
                line.text = "[unreadable label]"  # The source PDF still has the true label.
    report.questions = questions
    report.total_score = scheme.total
    return source, images, pages, questions, report, cfg, scheme


def answers(report):
    return [AuditItem(row=row, qid=q.qid, correct=maximum, evidence_status="verified",
            step_line_id=q.criteria[idx].step_line_id, anchor_quote=q.criteria[idx].anchor_quote)
            for row, (q, idx, maximum) in enumerate(grader._audit_rows(report, True))]


class TargetedVerificationTests(unittest.TestCase):
    def prepared(self):
        data = missing_headings()
        _, _, pages, _, report, cfg, _ = data
        initialize_evidence(report, cfg.exam_package, build_answer_map(cfg.exam_package, pages))
        prepare_evidence(report, pages)
        return data

    def test_missing_headings_split_continuations_without_stealing_other_evidence(self):
        _, _, pages, questions, report, _, _ = self.prepared()
        seen = set()
        for q in questions:
            region = report.answer_regions[normalize_qid(q.qid)]
            self.assertIn(q.criteria[0].step_line_id, region["line_ids"])
            self.assertFalse(seen.intersection(region["line_ids"]))
            seen.update(region["line_ids"])
        for qid in ("q4", "q6.i", "q6.ii"):
            self.assertTrue(report.answer_regions[qid]["ownership_proposed"])
        prompt = build_audit_prompt(report, include_awards=True)
        self.assertIn("OWNERSHIP NEEDS VISUAL VERIFICATION", prompt)
        self.assertNotIn("(location unresolved)", prompt)
        apply_verified_anchors(report, answers(report), pages, include_awards=True, selected_qids=None)

    def test_missing_answer_cannot_take_another_questions_known_label_or_step(self):
        _, _, pages, questions, report, cfg, _ = missing_headings()
        questions[3].anchor_line_id = questions[2].anchor_line_id
        for criterion in questions[3].criteria:
            criterion.step_line_id = questions[2].criteria[0].step_line_id
        initialize_evidence(report, cfg.exam_package, build_answer_map(cfg.exam_package, pages))
        before = list(report.answer_regions["q3"]["line_ids"])
        prepare_evidence(report, pages)
        self.assertEqual(report.answer_regions["q4"]["line_ids"], [])
        self.assertEqual(report.answer_regions["q3"]["line_ids"], before)

    def test_uncertain_duplicate_and_missing_decisions_are_all_repair_targets(self):
        _, _, _, _, report, _, _ = self.prepared()
        items = answers(report)
        items[8].evidence_status = "uncertain"
        items = items[:-1] + [items[3].model_copy()]
        gaps = audit_repair_rows(report, items, include_awards=True)
        self.assertEqual(set(gaps), {3, 8, 10})
        self.assertIn("not been verified", gaps[8])

    def test_repair_prompt_preserves_global_row_id_and_excludes_completed_rows(self):
        _, _, _, _, report, _, _ = self.prepared()
        prompt = build_audit_prompt(report, include_awards=True, only_rows={8}, repair_rows={8: "uncertain"})
        self.assertIn("  #8 [Q5; criterion 3]", prompt)
        self.assertNotIn("  #0 ", prompt)
        self.assertNotIn("  #9 ", prompt)
        self.assertIn("ORIGINAL row numbers", prompt)

    def test_repaired_full_credit_can_replace_deduction_with_empty_reason(self):
        first = [AuditItem(row=8, correct=0, reason="Area formula omitted")]
        fixed = [AuditItem(row=8, correct=1, reason="", evidence_status="verified")]
        merged = pipeline._merge_audit_verdicts(first, fixed, only_rows={8})
        self.assertEqual(merged[0].correct, 1)
        with self.assertRaisesRegex(StrictGradeError, "unrequested"):
            pipeline._merge_audit_verdicts(first, [AuditItem(row=0, correct=1)], only_rows={8})
        with self.assertRaisesRegex(StrictGradeError, "duplicate"):
            pipeline._merge_audit_verdicts(first, fixed * 2, only_rows={8})

    def test_repair_reservation_fits_output_ceiling_and_refuses_underfunded_call(self):
        _, _, _, _, report, _, _ = self.prepared()
        args = dict(report=report, selected_qids=None, only_rows={8}, prompt="x" * 7000,
                    page_count=1, model="claude-sonnet-4-6", usd_to_inr=94)
        full = pipeline._fit_verification_repair(**args, available_inr=10)
        minimum = CallPlan(stage=Stage.VERIFY, provider="claude", model=full.model,
            max_input_tokens=full.max_input_tokens, max_output_tokens=560)
        allowance = (full.quote_inr(94) + minimum.quote_inr(94)) / 2
        fitted = pipeline._fit_verification_repair(**args, available_inr=allowance)
        self.assertLess(fitted.max_output_tokens, full.max_output_tokens)
        self.assertGreaterEqual(fitted.max_output_tokens, 560)
        self.assertLessEqual(fitted.quote_inr(94), allowance)
        with self.assertRaisesRegex(StrictGradeError, "No repair call was charged"):
            pipeline._fit_verification_repair(**args, available_inr=minimum.quote_inr(94) - .1)

    def test_the_request_constrains_the_original_repair_row(self):
        _, _, _, _, report, _, _ = self.prepared()
        response = json.dumps({"items": [answers(report)[8].model_dump()]})
        with patch.object(grader, "_claude_call", return_value={
            "usage": {"input_tokens": 100, "output_tokens": 100},
            "content": [{"type": "text", "text": response}]}) as call:
            grader.audit_deductions(report, google_token="test", task_id="test", include_awards=True,
                only_rows={8}, repair_rows={8: "uncertain"})
        schema = call.call_args.kwargs["request"]["output_config"]["format"]["schema"]
        self.assertEqual(schema["$defs"]["AuditItem"]["properties"]["row"]["enum"], [8])

    def test_three_page_eleven_row_copy_repairs_only_uncertain_row_and_releases_under_cap(self):
        source, _, pages, _, primary, cfg, scheme = missing_headings()
        observed = []
        requests = []
        def grade(**kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 19027, "output_tokens": 1494}, stage="primary")
            return primary.model_copy(deep=True)
        def audit(report, **kwargs):
            only = kwargs.get("only_rows")
            observed.append(only)
            requests.append(kwargs)
            items = answers(report)
            if only is None:
                items[8].evidence_status = "uncertain"
                usage = {"input_tokens": 17000, "output_tokens": 1900}
            else:
                items = [items[8]]
                usage = {"input_tokens": 6500, "output_tokens": 210}
            record_usage_event(kwargs["usage_out"], dict(usage, provider="claude", model=kwargs["model"]), stage="verify")
            return items
        with patch.object(pipeline, "ocr_all_pages", return_value=pages), \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(pipeline, "claude_grade", side_effect=grade), \
             patch.object(pipeline, "claude_audit", side_effect=audit):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
                ak_text="Award the stated maximum for each correct result.", marks_scheme=scheme,
                cfg=cfg, student_class="10", subject="Maths", total_marks=13, use_mathpix=True,
                qp_text="Maximum Marks: 13\n" + "\n".join(
                    f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in primary.questions))
        self.assertEqual(observed, [None, {8}])
        # Assertions outside the mocked adapter cannot be swallowed by the
        # pipeline's retry exception handler. GPT reasoning shares this ceiling.
        self.assertEqual(len(requests[0]["student_evidence_pngs"]), 3)
        repair = requests[1]
        self.assertEqual(len(repair["student_evidence_pngs"]), 1)
        self.assertIn("P2L4", repair["ocr_transcript"])
        self.assertNotIn("P1L", repair["ocr_transcript"])
        self.assertNotIn("P3L", repair["ocr_transcript"])
        self.assertLessEqual(repair["max_output_tokens"], pipeline._evidence_output_budget(
            result["report"], repair.get("selected_qids"), repair=True,
            only_rows={8}, model=repair["model"]))
        criterion = result["report"].questions[4].criteria[3]
        self.assertEqual(result["report"].verified_step_anchors["q5:3"],
                         [criterion.step_line_id, criterion.anchor_quote])
        self.assertTrue(result["release"], result["issues"])
        self.assertLess(result["cost"].total_inr, 15)
        self.assertEqual(result["report"].total_score, 13)
        self.assertEqual({m["qid"] for m in result["report"].render_manifest if m["role"] in {"step", "answer"}},
                         {q.qid for q in primary.questions})


if __name__ == "__main__":
    unittest.main()
