"""Offline end-to-end Commerce feedback verification and faculty review."""
from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import grader
import fitz
import pipeline
import pw_access
import requests
import wording
from answer_map import build_answer_map
from costs import record_usage_event
from grader import AuditItem
from grading_quality import initialize_evidence, prepare_evidence
from test_coverage_recovery import fixture
from remark_quality import fragment_problem, remark_quality_problem


LONG_AUDIT_REASON = ("Answer is incomplete. Substitute the positive root into the original equation "
                     "and state that the correct final value of x equals four rather than two.")


def commerce_fixture():
    source, images, pages, questions, report, cfg, scheme = fixture(["Q1", "Q2"])
    cfg.category, cfg.category_source = "commerce", "explicit"
    cfg.enforce_budget = False
    cfg.exam_package.department = "commerce"
    cfg.exam_package.subject = "Core Maths"
    cfg.exam_package.level = "12"
    cfg.exam_package.package_id = cfg.exam_package.computed_id()
    report.questions = questions
    report.total_score = scheme.total
    return source, images, pages, questions, report, cfg, scheme


def theory_fixture():
    source, images, pages, questions, report, cfg, scheme = fixture(["Q1"])
    cfg.category, cfg.category_source = "commerce", "explicit"
    cfg.enforce_budget = False
    cfg.exam_package.department = "commerce"
    cfg.exam_package.subject = "Business Studies"
    cfg.exam_package.level = "12"
    cfg.exam_package.questions[0].question_type = "theory"
    cfg.exam_package.questions[0].prompt_text = "Give one advantage and one disadvantage of planning."
    cfg.exam_package.package_id = cfg.exam_package.computed_id()
    answer = "Planning provides direction. Planning can be costly."
    pages[0].lines[1].text = answer
    pages[0].lines[1].bbox = (60, 120, 360, 138)
    questions[0].criteria[0].anchor_quote = answer
    questions[0].criteria[0].description = "Requested advantage and disadvantage"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 100), "Q1")
    page.insert_text((60, 135), answer)
    source = doc.tobytes()
    images = [page.get_pixmap().tobytes("png")]
    doc.close()
    report.questions = questions
    report.total_score = scheme.total
    return source, images, pages, questions, report, cfg, scheme


def certified_rows(report, kwargs):
    rows = grader._audit_rows(report, kwargs.get("include_awards", True), kwargs.get("selected_qids"))
    only = kwargs.get("only_rows")
    return [AuditItem(row=i, qid=q.qid, criterion=index, correct=float(q.criteria[index].awarded),
                      evidence_status="verified", step_line_id=q.criteria[index].step_line_id,
                      anchor_quote=q.criteria[index].anchor_quote,
                      anchor_verdict="correct" if q.criteria[index].awarded == q.criteria[index].max else "wrong",
                      evidence_kind=q.criteria[index].evidence_kind,
                      reason="" if q.criteria[index].awarded == q.criteria[index].max
                      else q.criteria[index].evidence or "The required method is missing.")
            for i, (q, index, _) in enumerate(rows) if only is None or i in only]


class CommerceGradeSheetTests(unittest.TestCase):
    def run_copy(self, audit_impl=None, *, missing_key=False, cancelled=False,
                 invented_split=False, fixture_factory=commerce_fixture, final_remark=None):
        source, _, pages, _, primary, cfg, scheme = fixture_factory()
        subject = cfg.exam_package.subject
        if invented_split:
            q1 = primary.questions[0]
            result = q1.criteria[0].model_copy(update={"max": .5, "awarded": .5, "met": True})
            method = q1.criteria[0].model_copy(update={"description": "Working", "max": .5,
                "awarded": 0, "met": False, "step_line_id": "", "anchor_quote": "",
                "evidence_kind": "missing", "evidence": "No working shown."})
            q1.criteria = [method, result]
            q1.score = .5
            primary.total_score = sum(q.score for q in primary.questions)
            cfg.exam_package.questions[0].question_type = "numeric"
            cfg.exam_package.questions[0].prompt_text = "Find the value of x."
            cfg.exam_package.package_id = cfg.exam_package.computed_id()
        if cancelled:
            pages[0].lines[1].cancelled = True
        observed = []

        def grade(**kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 1000, "output_tokens": 400}, stage="primary")
            return primary.model_copy(deep=True)

        def audit(current, **kwargs):
            observed.append((current.model_copy(deep=True), kwargs))
            record_usage_event(kwargs["usage_out"], {"provider": "gpt", "model": kwargs["model"],
                "input_tokens": 2000, "output_tokens": 300}, stage="verify")
            return audit_impl(current, kwargs) if audit_impl else certified_rows(current, kwargs)

        with ExitStack() as stack:
            transport = stack.enter_context(patch.object(requests.sessions.Session, "request",
                side_effect=AssertionError("Network prohibited by Commerce pipeline test")))
            blocked = [stack.enter_context(patch.object(owner, name,
                side_effect=AssertionError(f"Unmocked provider adapter: {name}")))
                for owner, name in ((pw_access, "mathpix_ocr"),
                                    (grader, "_claude_call"), (grader, "_gpt_call"))]
            stack.enter_context(patch.object(pipeline, "ocr_all_pages", return_value=pages))
            stack.enter_context(patch.object(pipeline, "_reread_sideways_pages", return_value={}))
            stack.enter_context(patch.object(pipeline, "claude_grade", side_effect=grade))
            audit_mock = stack.enter_context(patch.object(pipeline, "claude_audit", side_effect=audit))
            if final_remark is not None:
                original_wording = wording.finalise_wording
                def inject_final_remark(current, *args, **kwargs):
                    current = original_wording(current, *args, **kwargs)
                    current.questions[0].remark = final_remark
                    return current
                stack.enter_context(patch.object(wording, "finalise_wording", side_effect=inject_final_remark))
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="commerce.pdf",
                ak_pngs=[], ak_text="" if missing_key else "Award one mark for each correct final value.",
                marks_scheme=scheme, cfg=cfg, student_class="Class 12 Commerce", subject=subject,
                total_marks=scheme.total, use_mathpix=True,
                qp_text=f"Maximum Marks: {scheme.total:g}\n" + "\n".join(
                    f"{node.qid}. {node.prompt_text or 'State the answer.'} ({node.max_marks:g} Mark)"
                    for node in cfg.exam_package.questions))
            transport.assert_not_called()
            for provider in blocked:
                provider.assert_not_called()
        return result, observed, audit_mock.call_count

    def test_full_credit_maths_requires_original_image_certification_and_releases(self):
        result, observed, calls = self.run_copy()
        self.assertEqual(calls, 1)
        report, args = observed[0]
        self.assertEqual(set(report.mandatory_verification_qids), {"Q1", "Q2"})
        self.assertEqual(args["selected_qids"], {grader._norm_qid("Q1"), grader._norm_qid("Q2")})
        self.assertTrue(args["student_evidence_pngs"])
        self.assertIn("P1L2", args["ocr_transcript"])
        self.assertIn("powers", report.commerce_feedback_verification_reasons["Q1"][0])
        self.assertIn("Reconstruct and recompute", args["category_rules"])
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].total_score, 2)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))

    def test_missing_key_skips_examiner_and_sends_marked_pdf_to_review(self):
        result, _, calls = self.run_copy(missing_key=True)
        self.assertEqual(calls, 0)
        self.assertFalse(result["release"])
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertTrue(any("Commerce feedback" in reason for reason in result["report"].review_reasons))

    def test_incomplete_first_pass_repairs_only_the_missing_decision(self):
        def audit(current, kwargs):
            rows = certified_rows(current, kwargs)
            return rows[:1] if kwargs.get("only_rows") is None else rows
        result, observed, calls = self.run_copy(audit)
        self.assertEqual(calls, 2)
        self.assertEqual(observed[1][1]["only_rows"], {1})
        self.assertTrue(result["release"], result["issues"])

    def test_uncertain_second_pass_cannot_release_as_certified(self):
        def audit(current, kwargs):
            rows = certified_rows(current, kwargs)
            rows[-1].evidence_status = "uncertain"
            return rows
        result, _, calls = self.run_copy(audit)
        self.assertEqual(calls, 2)
        self.assertFalse(result["release"])
        self.assertTrue(any("Q2" in reason and "independently verified" in reason
                            for reason in result["report"].review_reasons))

    def test_cancelled_award_is_removed_before_the_existing_examiner_reads_it(self):
        result, observed, _ = self.run_copy(cancelled=True)
        q1 = next(q for q in observed[0][0].questions if q.qid == "Q1")
        self.assertEqual(q1.score, 0)
        self.assertEqual(q1.criteria[0].awarded, 0)
        self.assertEqual(q1.criteria[0].evidence_kind, "missing")
        self.assertIn("cancelled", q1.criteria[0].evidence)
        self.assertEqual(result["report"].max_total, 2)

    def test_one_mark_invented_method_split_can_receive_verified_whole_answer_credit(self):
        def audit(current, kwargs):
            items = certified_rows(current, kwargs)
            items[0].correct = 1
            items[0].reason = ""
            items[0].anchor_verdict = "correct"
            return items
        result, observed, calls = self.run_copy(audit, invented_split=True)
        reviewed = observed[0][0].questions[0]
        self.assertEqual((len(reviewed.criteria), reviewed.criteria[0].max, reviewed.score), (1, 1, .5))
        self.assertEqual(calls, 1)
        self.assertEqual(result["report"].questions[0].score, 1)
        self.assertEqual(result["report"].total_score, 2)
        self.assertTrue(result["release"], result["issues"])

    def test_valid_long_audit_correction_preserves_the_concrete_result_and_releases(self):
        self.assertEqual(len(LONG_AUDIT_REASON.split()), 25)
        self.assertEqual(remark_quality_problem(LONG_AUDIT_REASON), "")
        def audit(current, kwargs):
            items = certified_rows(current, kwargs)
            items[0].correct = .5
            items[0].reason = LONG_AUDIT_REASON
            items[0].anchor_verdict = "wrong"
            return items
        result, _, calls = self.run_copy(audit)
        self.assertEqual(calls, 1, "A validated correction should use the existing examiner pass.")
        final = result["report"].questions[0]
        self.assertEqual(final.score, .5)
        self.assertIn("four rather than two", final.remark)
        self.assertEqual(remark_quality_problem(final.remark), "")
        self.assertEqual(fragment_problem(final.remark), "")
        self.assertTrue(result["release"], result["issues"])

    def test_generic_final_feedback_cannot_release_despite_a_valid_audit_certificate(self):
        def audit(current, kwargs):
            items = certified_rows(current, kwargs)
            items[0].correct = .5
            items[0].reason = "Use the positive root; x equals four rather than two."
            items[0].anchor_verdict = "wrong"
            return items
        result, _, calls = self.run_copy(audit, final_remark="Answer is incomplete.")
        self.assertEqual(calls, 1)
        self.assertFalse(result["release"])
        self.assertTrue(any("Q1" in reason for reason in result["report"].review_reasons))

    def test_ambiguous_bst_group_releases_only_with_explicit_first_n_image_certificate(self):
        def audit(current, kwargs):
            items = certified_rows(current, kwargs)
            for item in items:
                item.commerce_point_order_verified = True
            return items
        result, observed, calls = self.run_copy(audit, fixture_factory=theory_fixture)
        self.assertEqual(calls, 1)
        self.assertTrue(observed[0][0].commerce_point_order_issues)
        self.assertEqual(result["report"].verified_commerce_point_order_qids, ["Q1"])
        self.assertEqual(result["report"].commerce_point_order_issues, [])
        self.assertTrue(result["release"], result["issues"])

    def test_ordinary_bst_evidence_certificate_does_not_resolve_first_n_group_order(self):
        result, _, _ = self.run_copy(fixture_factory=theory_fixture)
        self.assertFalse(result["release"])
        self.assertEqual(result["report"].verified_commerce_point_order_qids, [])
        self.assertTrue(result["report"].commerce_point_order_issues)
        self.assertTrue(any(getattr(issue, "code", "") == "commerce-point-order-unverified"
                            for issue in result["issues"]))


class CommercePipelineHooks(unittest.TestCase):
    def prepared(self):
        _, _, pages, _, report, cfg, _ = commerce_fixture()
        initialize_evidence(report, cfg.exam_package, build_answer_map(cfg.exam_package, pages))
        prepare_evidence(report, pages)
        return report, pages, cfg

    def test_missing_award_is_zeroed_before_risk_selection_and_count_is_preserved(self):
        report, pages, cfg = self.prepared()
        report.questions[0].criteria[0].evidence_kind = "missing"
        required = pipeline._prepare_commerce_feedback(report, pages, cfg, "Core Maths")
        self.assertEqual(report.questions[0].score, 0)
        self.assertEqual(report.max_total, 2)
        self.assertEqual(required, {"Q1", "Q2"})
        self.assertEqual(set(report.mandatory_verification_qids), required)

    def test_certification_requires_every_requested_row_for_a_question(self):
        report, _, _ = self.prepared()
        q1 = report.questions[0]
        q1.criteria = [q1.criteria[0].model_copy(update={"awarded": .5, "max": .5}) for _ in range(2)]
        items = [AuditItem(row=0, correct=.5, evidence_status="verified"),
                 AuditItem(row=1, correct=.5, evidence_status="uncertain"),
                 AuditItem(row=2, correct=1, evidence_status="verified")]
        self.assertEqual(pipeline._commerce_certified_questions(report, items, True, None), {"Q2"})

    def test_duplicate_row_cannot_certify_a_question_or_point_order(self):
        report, _, _ = self.prepared()
        items = certified_rows(report, {})
        for item in items:
            item.commerce_point_order_verified = True
        items.append(items[0].model_copy(deep=True))
        self.assertEqual(pipeline._commerce_certified_questions(report, items, True, None), {"Q2"})
        self.assertEqual(pipeline._commerce_point_order_certificates(report, items, True, None), {"Q2"})

    def test_point_order_requires_explicit_confirmation_on_every_expected_row(self):
        report, _, _ = self.prepared()
        q1 = report.questions[0]
        q1.criteria = [q1.criteria[0].model_copy(update={"awarded": .5, "max": .5}) for _ in range(2)]
        items = certified_rows(report, {})
        items[0].commerce_point_order_verified = True
        items[2].commerce_point_order_verified = True
        self.assertEqual(pipeline._commerce_certified_questions(report, items, True, None), {"Q1", "Q2"})
        self.assertEqual(pipeline._commerce_point_order_certificates(report, items, True, None), {"Q2"})
        items[1].commerce_point_order_verified = True
        self.assertEqual(pipeline._commerce_point_order_certificates(report, items, True, None), {"Q1", "Q2"})

    def test_newly_discovered_final_check_is_not_covered_by_an_earlier_certificate(self):
        report, pages, cfg = self.prepared()
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", {"Q1"}, {"Q1"})
        self.assertTrue(any("Q2" in reason for reason in report.review_reasons))

    def test_evidence_correction_after_certification_requires_review(self):
        report, pages, cfg = self.prepared()
        report.questions[0].criteria[0].evidence_kind = "missing"
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", {"Q1", "Q2"}, {"Q1", "Q2"})
        self.assertEqual(report.questions[0].score, 0)
        self.assertTrue(any("Q1" in reason for reason in report.review_reasons))

    def test_late_score_change_invalidates_certificate_digest(self):
        report, pages, cfg = self.prepared()
        required = pipeline._prepare_commerce_feedback(report, pages, cfg, "Core Maths")
        digests = pipeline._commerce_question_digests(report, required)
        report.questions[0].criteria[0].awarded = .5
        report.questions[0].criteria[0].met = False
        report.questions[0].criteria[0].evidence = "Use x = 2."
        report.questions[0].score = .5
        report.total_score = 1.5
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", required, required, digests)
        self.assertTrue(any("Q1" in reason for reason in report.review_reasons))

    def test_alias_migration_preserves_prior_feedback_check_and_valid_certificate(self):
        report, pages, cfg = self.prepared()
        required = pipeline._prepare_commerce_feedback(report, pages, cfg, "Core Maths")
        digests = pipeline._commerce_question_digests(report, required)
        report.commerce_feedback_verification_reasons["1"] = ["Earlier image recheck."]
        report.mandatory_verification_qids = ["1", "2"]
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", {"1", "2"}, {"1", "2"},
            {str(index + 1): digest for index, digest in enumerate(digests.values())})
        self.assertIn("Earlier image recheck.", report.commerce_feedback_verification_reasons["Q1"])
        self.assertNotIn("1", report.commerce_feedback_verification_reasons)
        self.assertEqual(set(report.mandatory_verification_qids), {"Q1", "Q2"})
        self.assertEqual(report.review_reasons, [])

    def test_zero_maximum_choice_candidate_does_not_require_a_release_certificate(self):
        report, pages, cfg = self.prepared()
        report.questions[0].max_score = 0
        report.questions[0].score = 0
        report.questions[0].criteria[0].max = 0
        report.questions[0].criteria[0].awarded = 0
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", {"Q1", "Q2"}, {"Q2"})
        self.assertEqual(report.review_reasons, [])

    def assert_final_feedback_held(self, remark):
        report, pages, cfg = self.prepared()
        q1 = report.questions[0]
        q1.criteria[0].awarded = .5
        q1.criteria[0].met = False
        q1.criteria[0].evidence = remark
        q1.score = .5
        q1.remark = remark
        report.total_score = 1.5
        required = pipeline._prepare_commerce_feedback(report, pages, cfg, "Core Maths")
        digests = pipeline._commerce_question_digests(report, required)
        pipeline._finish_commerce_feedback(report, pages, cfg, "Core Maths", required, required, digests)
        self.assertTrue(any("Q1" in reason for reason in report.review_reasons),
                        "A prior mark certificate cannot authorize weak final student feedback.")

    def test_generic_final_remark_is_held_even_with_a_current_certificate(self):
        self.assert_final_feedback_held("Answer is incomplete.")

    def test_missing_final_remark_is_held_even_with_a_current_certificate(self):
        self.assert_final_feedback_held("")

    def test_fragmented_final_remark_is_held_even_with_a_current_certificate(self):
        self.assert_final_feedback_held("Is vague. Misses.")


if __name__ == "__main__":
    unittest.main()
