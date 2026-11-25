"""Invalid step breakdowns are regraded together before independent verification."""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import pipeline
from answer_map import build_answer_map
from budgeting import BudgetPolicy
from costs import record_usage_event
from grader import Annotation, AuditItem, Criterion
from grading_quality import initialize_evidence
from selective_verification import verification_candidates
from strict_grading import StrictGradeError, reconcile_report_to_package, validate_question_marks
from test_coverage_recovery import fixture
from test_evidence_quality import package as fixed_package
from test_nested_choices import audit_scheme, make_package, make_report


class QuestionRegradingTests(unittest.TestCase):
    def setup_report(self, qids=None):
        data = fixture(qids)
        _, _, pages, rows, report, cfg, _ = data
        cfg.enforce_budget = False
        report.questions = [q.model_copy(deep=True) for q in rows]
        initialize_evidence(report, cfg.exam_package, build_answer_map(cfg.exam_package, pages))
        return data

    def repair(self, report, cfg, scheme, images, *, usage=None, cap=math.inf, key_images=None):
        return pipeline._regrade_invalid_questions(report, cfg=cfg,
            common=dict(student_pngs=images, marks_scheme=scheme, usage_out=usage if usage is not None else {},
                        model=cfg.model, task_id=cfg.task_id, google_token="test",
                        question_paper_pngs=[], answer_key_pngs=key_images,
                        answer_key_text="Official key", qp_text="Official paper",
                        ocr_transcript="Original labelled student evidence", subject="Maths", student_class="10"),
            policy=BudgetPolicy.for_copy(total_marks=scheme.total, exam_type="school"),
            hard_cap=cap, usd_to_inr=85, use_mathpix=True, stage=lambda *a: None)

    def test_valid_decisions_do_not_trigger_a_paid_regrade(self):
        _, images, _, _, report, cfg, scheme = self.setup_report()
        with patch.object(pipeline, "claude_grade") as grade:
            self.assertIs(self.repair(report, cfg, scheme, images), report)
        grade.assert_not_called()

    def test_all_invalid_questions_are_collected_and_valid_rows_preserved(self):
        _, images, _, rows, report, cfg, scheme = self.setup_report()
        report.questions[3].criteria[0].max += 1
        report.questions[4].criteria = []
        report.questions[5].score = 2
        valid = {q.qid: q.model_dump() for i, q in enumerate(report.questions) if i not in (3, 4, 5)}
        with patch.object(pipeline, "claude_grade", return_value=report.model_copy(
                update={"questions": [rows[i] for i in (3, 4, 5)]})) as grade:
            result = self.repair(report, cfg, scheme, images)
        self.assertEqual(grade.call_count, 1)
        self.assertEqual(grade.call_args.kwargs["question_qids"], [rows[i].qid for i in (3, 4, 5)])
        self.assertFalse(grade.call_args.kwargs["reconcile_output"])
        self.assertEqual(grade.call_args.kwargs["student_pngs"], images)
        self.assertEqual(grade.call_args.kwargs["answer_key_text"], "Official key")
        self.assertIn("QUESTION REGRADING REQUIRED", grade.call_args.kwargs["qp_text"])
        self.assertIn("step maxima total 4; the paper allocates 3", grade.call_args.kwargs["qp_text"])
        self.assertEqual(result.regraded_qids, [rows[i].qid for i in (3, 4, 5)])
        self.assertEqual({q.qid: q.model_dump() for q in result.questions if q.qid in valid}, valid)
        reconcile_report_to_package(result, cfg.exam_package)

    def test_actual_ca_graph_keeps_100_after_regrading_q1c_and_an_optional_question(self):
        _, images, _, _, _, cfg, _ = self.setup_report()
        scheme, report = audit_scheme(), make_report()
        cfg.exam_package = make_package(scheme)
        report.evidence_contract = True
        for q in report.questions:
            q.criteria = [Criterion(description="Required audit point", max=q.max_score,
                                    awarded=q.score, met=True)]
        good = {q.qid: q.model_copy(deep=True) for q in report.questions}
        for q in report.questions:
            if q.qid in ("Q1.c", "Q5.a.remaining"):
                q.criteria[0].max += 1
        def regrade(**kwargs):
            return report.model_copy(update={"questions": [good[q] for q in kwargs["question_qids"]]})
        with patch.object(pipeline, "claude_grade", side_effect=regrade) as grade:
            result = self.repair(report, cfg, scheme, images)
        self.assertEqual(grade.call_args.kwargs["question_qids"], ["Q1.c", "Q5.a.remaining"])
        self.assertEqual(len(result.questions), 38)
        result = reconcile_report_to_package(result, cfg.exam_package, finalize_choices=False)
        self.assertEqual(result.max_total, 100)
        self.assertEqual(len(result.questions), 38)

    def test_underallocation_and_nonfinite_or_invalid_awards_are_regraded(self):
        for defect in ("under", "negative", "nan-max", "infinite-score", "step-award"):
            _, images, _, rows, report, cfg, scheme = self.setup_report()
            q = report.questions[4]
            if defect == "under":
                q.criteria[0].max = 4
                q.criteria[0].awarded = q.score = 4
            elif defect == "negative":
                q.criteria[0].awarded = -1
            elif defect == "nan-max":
                q.criteria[0].max = float("nan")
            elif defect == "infinite-score":
                q.score = float("inf")
            else:
                q.criteria[0].awarded = 6
            with self.subTest(defect=defect), patch.object(pipeline, "claude_grade",
                    return_value=report.model_copy(update={"questions": [rows[4]]})) as grade:
                self.repair(report, cfg, scheme, images)
                grade.assert_called_once()
                validate_question_marks(report.questions[4], cfg.exam_package.questions[4])

    def test_fixed_rubric_weights_ids_and_increments_are_not_changed_to_fit(self):
        pkg = fixed_package(rubric={"version": 1, "questions": [{"qid": "Q1", "criteria": [
            {"criterion_id": "method", "description": "Method", "max_marks": 1, "mark_increment": .5},
            {"criterion_id": "result", "description": "Result", "max_marks": 1}]}]})
        _, _, _, rows, _, _, _ = fixture()
        q = rows[0].model_copy(deep=True)
        q.max_score = q.score = 2
        q.criteria = [Criterion(description=c.description, rubric_criterion_id=c.criterion_id,
                               max=c.max_marks, awarded=c.max_marks, met=True) for c in pkg.questions[0].criteria]
        for defect in ("weights", "id", "increment"):
            bad = q.model_copy(deep=True)
            if defect == "weights":
                bad.criteria[0].max, bad.criteria[1].max = 1.5, .5
                bad.criteria[0].awarded = bad.criteria[1].awarded = .5
            elif defect == "id":
                bad.criteria[0].rubric_criterion_id = "invented"
            else:
                bad.criteria[0].awarded = .3
            before = bad.model_dump()
            with self.subTest(defect=defect), self.assertRaises(StrictGradeError):
                validate_question_marks(bad, pkg.questions[0])
            self.assertEqual(bad.model_dump(), before)

    def test_unchanged_failure_is_bounded_and_goes_to_faculty_review(self):
        _, images, _, _, report, cfg, scheme = self.setup_report()
        bad = report.questions[3]
        bad.criteria[0].awarded = -1.0  # a defect the zero-step fallback never repairs
        with patch.object(pipeline, "claude_grade", return_value=report.model_copy(
                deep=True, update={"questions": [bad.model_copy(deep=True)]})) as grade:
            # 2026-09-19: the copy is no longer refused. The best decision is kept
            # and the reason travels with the copy to a faculty reviewer.
            result = self.repair(report, cfg, scheme, images)
        # A text key gets the same bounded pair as a visual key: the primary reading
        # and one stronger alternate reading, and the breakdown is never rescaled.
        self.assertEqual(grade.call_count, 2)
        self.assertEqual([c.kwargs["model"] for c in grade.call_args_list],
                         [cfg.model, pipeline._BUDGET_GRADING_MODEL])
        self.assertTrue(any(bad.qid in reason for reason in result.review_reasons), result.review_reasons)
        self.assertIn(bad.qid, result.regraded_qids)
        self.assertLessEqual(float(next(q for q in result.questions if q.qid == bad.qid).score),
                             float(bad.max_score))

    def test_bad_response_identity_and_truncation_cannot_replace_valid_rows(self):
        for defect in ("wrong", "duplicate", "missing", "truncated"):
            _, images, _, rows, report, cfg, scheme = self.setup_report()
            report.questions[3].criteria[0].max += 1
            snapshot = report.model_dump()
            returned = {"wrong": [rows[0]], "duplicate": [rows[3], rows[3]], "missing": []}.get(defect)
            kwargs = {"side_effect": grader.OutputLimitExceeded("cut off")} if defect == "truncated" else {
                "return_value": report.model_copy(update={"questions": returned})}
            with self.subTest(defect=defect), patch.object(pipeline, "claude_grade", **kwargs) as grade:
                with self.assertRaises(StrictGradeError):
                    self.repair(report, cfg, scheme, images)
                grade.assert_called_once()
                self.assertEqual(report.model_dump(), snapshot)

    def test_multiple_batches_commit_only_after_every_decision_passes(self):
        _, images, _, rows, report, cfg, scheme = self.setup_report([f"Q{i}" for i in range(1, 11)])
        for q in report.questions[:9]:
            q.criteria[0].awarded = -1.0
        snapshot, usage = report.model_dump(), {}
        def grade(**kwargs):
            ids = kwargs["question_qids"]
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 100, "output_tokens": 100}, stage="retry")
            chosen = ([q for q in rows if q.qid in ids] if len(ids) > 1 else [report.questions[8]])
            return report.model_copy(update={"questions": chosen})
        with patch.object(pipeline, "claude_grade", side_effect=grade) as call:
            result = self.repair(report, cfg, scheme, images, usage=usage)
        # Two batches, then one stronger alternate reading of the still-invalid Q9.
        self.assertEqual([c.kwargs["question_qids"] for c in call.call_args_list],
                         [[q.qid for q in rows[:8]], ["Q9"], ["Q9"]])
        self.assertEqual(len(usage["_events"]), 3)
        # The eight valid decisions are committed; Q9 goes to faculty with its reason.
        self.assertTrue(any("Q9" in reason for reason in result.review_reasons), result.review_reasons)
        for q, expected in zip(result.questions[:8], rows[:8]):
            self.assertEqual(q.model_dump(), expected.model_dump())

    def test_visual_key_uses_stronger_model_only_for_still_invalid_rows(self):
        _, images, _, rows, report, cfg, scheme = self.setup_report()
        for q in report.questions[3:5]:
            q.criteria[0].max += 1
        first = report.model_copy(deep=True, update={"questions": [rows[3], report.questions[4]]})
        second = report.model_copy(deep=True, update={"questions": [rows[4]]})
        with patch.object(pipeline, "claude_grade", side_effect=[first, second]) as calls:
            result = self.repair(report, cfg, scheme, images, key_images=images[:1])
        self.assertEqual(calls.call_count, 2)
        self.assertEqual([c.kwargs["model"] for c in calls.call_args_list],
                         [cfg.model, pipeline._BUDGET_GRADING_MODEL])
        self.assertEqual(calls.call_args.kwargs["question_qids"], [rows[4].qid])
        self.assertEqual(calls.call_args.kwargs["answer_key_pngs"], images[:1])
        self.assertIn("First identify EVERY required step", calls.call_args.kwargs["qp_text"])
        self.assertEqual(result.regraded_qids, [rows[3].qid, rows[4].qid])
        for q, expected in zip(result.questions, rows):
            self.assertEqual(q.model_dump(), expected.model_dump())

    def test_alternate_reader_is_bounded_and_an_invalid_breakdown_goes_to_faculty(self):
        _, images, _, _, report, cfg, scheme = self.setup_report()
        report.questions[3].criteria[0].awarded = -1.0
        qid = report.questions[3].qid
        bad = report.model_copy(deep=True, update={"questions": [report.questions[3]]})
        with patch.object(pipeline, "claude_grade", return_value=bad) as calls:
            result = self.repair(report, cfg, scheme, images, key_images=images[:1])
        self.assertEqual(calls.call_count, 2)
        self.assertTrue(any(qid in reason for reason in result.review_reasons), result.review_reasons)
        # The release gate turns the reason into a hold that routes the copy to faculty.
        from preflight import check_copy, release_blocking
        codes = {issue.code for issue in release_blocking(check_copy(result))}
        self.assertIn("faculty-review", codes)

    def test_regraded_full_marks_are_mandatory_for_independent_verification(self):
        _, _, _, _, report, _, _ = self.setup_report()
        report.regraded_qids = ["Q1"]
        report.rubric_unfixed_qids = []
        candidate = next(c for c in verification_candidates(report) if c.qid == "Q1")
        self.assertTrue(candidate.mandatory)
        self.assertIn("question-regraded-after-invalid-breakdown", candidate.reasons)
        schema = grader._grade_report_output_schema(question_qids=["Q1"])
        self.assertNotIn("regraded_qids", schema["properties"])

    def test_any_four_wrong_surplus_does_not_inflate_or_reduce_the_four_scoring_slots(self):
        from exam_package import QuestionNode
        _, _, _, rows, _, _, scheme = self.setup_report()
        question = rows[0].model_copy(deep=True)
        question.qid = "Q6.c2"
        question.score = question.max_score = 4
        question.criteria = [Criterion(description=f"Valid point {i}", max=1, awarded=1, met=True)
                             for i in range(4)] + [
            Criterion(description="Incorrect surplus attempt", max=0, awarded=0, met=False)
            for _ in range(2)]
        spec = QuestionNode(qid=question.qid, max_marks=4)
        validate_question_marks(question, spec)
        self.assertEqual(sum(c.awarded for c in question.criteria), 4)
        for c in question.criteria[-2:]:
            c.max = 1
        with self.assertRaisesRegex(StrictGradeError, "step maxima total 6"):
            validate_question_marks(question, spec)
        prompt, _ = grader.grading_coverage_block(scheme)
        self.assertIn("max=0 and awarded=0", prompt)

    def test_original_annotations_on_regraded_answers_cannot_survive(self):
        _, images, _, rows, report, cfg, scheme = self.setup_report()
        report.questions[3].criteria[0].max += 1
        report.annotations = [Annotation(type="underline", page=rows[3].page,
            target_line_id=rows[3].criteria[0].step_line_id), Annotation(type="underline", page=rows[0].page,
            target_line_id=rows[0].criteria[0].step_line_id), Annotation(type="underline", page=rows[3].page)]
        with patch.object(pipeline, "claude_grade", return_value=report.model_copy(update={"questions": [rows[3]]})):
            self.repair(report, cfg, scheme, images)
        self.assertEqual([a.target_line_id for a in report.annotations], [rows[0].criteria[0].step_line_id])

    def test_explicit_capped_mode_still_reserves_verification_before_regrading(self):
        _, images, _, _, report, cfg, scheme = self.setup_report()
        cfg.enforce_budget = True
        report.questions[3].criteria[0].max += 1
        with patch.object(pipeline, "claude_grade") as grade, self.assertRaises(StrictGradeError):
            self.repair(report, cfg, scheme, images, cap=.01)
        grade.assert_not_called()

    def run_complete_flow(self, *, unresolved=False, annotated_key=False):
        source, images, pages, rows, report, cfg, scheme = self.setup_report()
        key_images = images[:1] if annotated_key else []
        calls, audits = [], []
        def transport(*args, **kwargs):
            request = kwargs["request"]
            scope = request["output_config"]["format"]["schema"]["properties"]["questions"]["required"]
            calls.append(scope)
            if annotated_key:
                self.assertIn("ANSWER KEY PAGE LAYOUT", json.dumps(request))
                content = request["messages"][0]["content"]
                self.assertEqual(sum(b["type"] == "image" for b in content), len(images) + 1)
            chosen = [q.model_copy(deep=True) for q in rows if q.qid in scope]
            if len(calls) == 1:
                chosen[3].criteria[0].max += 1
                chosen[3].criteria[0].awarded = chosen[3].score = 1
                chosen[4].criteria = []
            else:
                self.assertIn("QUESTION REGRADING REQUIRED", json.dumps(request))
            return {"stop_reason": "end_turn", "usage": {"input_tokens": 14000, "output_tokens": 2200},
                    "content": [{"type": "text", "text": json.dumps({"questions": {
                        q.qid: q.model_dump(exclude={"qid"}) for q in chosen}})}]}
        def audit(current, **kwargs):
            audits.append(kwargs["selected_qids"])
            self.assertEqual(kwargs["answer_key_pngs"], key_images)
            if annotated_key:
                self.assertIn("ANSWER KEY PAGE LAYOUT", kwargs["answer_key_text"])
            self.assertNotEqual(kwargs["model"], cfg.model)
            self.assertEqual(current.regraded_qids, ["Q4", "Q5"])
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                "input_tokens": 14000, "output_tokens": 1400}, stage="verify")
            if unresolved:
                return []
            return [AuditItem(row=i, correct=maximum, evidence_status="verified",
                step_line_id=q.criteria[index].step_line_id, anchor_quote=q.criteria[index].anchor_quote)
                for i, (q, index, maximum) in enumerate(grader._audit_rows(current, True, kwargs["selected_qids"]))]
        with patch.object(pipeline, "ocr_all_pages", return_value=pages) as ocr, \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(grader, "_claude_call", side_effect=transport), \
             patch.object(pipeline, "claude_audit", side_effect=audit):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=key_images,
                ak_text=None if annotated_key else "Award the stated maximum for each correct result.",
                ak_text_layer=("Official solution explanation. " * 50 + "\n1 marks\neach point"
                               if annotated_key else ""), marks_scheme=scheme,
                cfg=cfg, student_class="10", subject="Maths", total_marks=13, use_mathpix=True,
                qp_text="Maximum Marks: 13\n" + "\n".join(f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
        self.assertEqual(calls, [[q.qid for q in rows], ["Q4", "Q5"]])
        self.assertEqual(ocr.call_count, 1)
        # An uncertified pass costs one repair round; it does not cost the copy.
        self.assertEqual(len(audits), 2 if unresolved else 1)
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["verification_complete"], not unresolved)
        self.assertEqual(result["report"].total_score, 13)
        self.assertFalse(result["report"].maxima_rescaled)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertFalse(result["budget"]["enforced"])
        self.assertGreater(result["cost"].total_inr, 0)
        self.assertEqual({r["qid"] for r in result["report"].render_manifest if r["role"] in {"answer", "step"}},
                         {q.qid for q in rows})

    def test_complete_flow_regrades_all_bad_rows_verifies_and_releases_pdf(self):
        self.run_complete_flow()

    def test_dense_step_key_images_survive_primary_regrading_and_verification(self):
        self.run_complete_flow(annotated_key=True)

    def test_a_regrade_that_cannot_be_certified_is_released_and_flagged(self):
        """POLICY 2026-09-16: the copy goes out, saying what was not certified.

        It used to raise VERIFICATION_INCOMPLETE, so a correctly regraded copy was
        destroyed because the SECOND examiner's reply fell short — after every mark
        on it had been decided, evidenced and paid for.
        """
        self.run_complete_flow(unresolved=True, annotated_key=True)


if __name__ == "__main__":
    unittest.main()
