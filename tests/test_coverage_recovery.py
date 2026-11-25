"""Recover omitted question judgments without zero-filling or regrading accepted rows."""
from __future__ import annotations

import json
import base64
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from PIL import Image
import grader
import pipeline
import mathpix
from orient import normalize_orientation
from answer_map import build_answer_map
from budgeting import BudgetPolicy
from costs import record_usage_event
from exam_package import build_exam_package
from grader import GradeReport, QuestionMark, Criterion, MarksScheme, MarksItem, AuditItem
from mathpix import PageOCR, LineBox
from strict_grading import StrictGradeError, reconcile_report_to_package


def fixture(qids=None):
    qids = qids or ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6.i", "Q6.ii"]
    specs = list(zip(qids, ([1, 1, 1, 3, 5, 1, 1] + [1] * len(qids))[:len(qids)]))
    scheme = MarksScheme(total=sum(m for q, m in specs),
                         items=[MarksItem(qid=q, max=m) for q, m in specs])
    pkg = build_exam_package(exam_type="school", student_class="10", subject="Maths",
        level="10", paper="", attempt="", applicable_law_date=None,
        total_marks=scheme.total, question_paper=b"paper", answer_key=b"key",
        key_source="upload", marks_scheme=scheme, scheme_source="text")
    doc = fitz.open()
    pages, rows = [], []
    answers = ["x = 2", "y = 3", "z = 4", "Sum = 9", "Area = 12", "Voltage = 6", "Current = 2"]
    answers += [f"Result = {i+20}" for i in range(max(0, len(qids)-len(answers)))]
    for offset in range(0, len(specs), 3):
        page = doc.new_page(width=595, height=842)
        pno = len(pages) + 1
        lines = []
        for local, (qid, maximum) in enumerate(specs[offset:offset+3]):
            answer = answers[offset + local]
            y = 100 + local * 170
            label_id, answer_id = f"P{pno}L{2*local+1}", f"P{pno}L{2*local+2}"
            page.insert_text((60, y), qid)
            page.insert_text((60, y + 35), answer)
            lines += [LineBox(label_id, qid, (60, y-15, 180, y+3), pno),
                      LineBox(answer_id, answer, (60, y+20, 220, y+38), pno)]
            rows.append(QuestionMark(qid=qid, score=maximum, max_score=maximum,
                page=pno, y_fraction=y/842, anchor_line_id=label_id, mark_style="single",
                criteria=[Criterion(description="Correct result", awarded=maximum,
                    max=maximum, met=True, anchor_quote=answer, step_line_id=answer_id)]))
        pages.append(PageOCR(pno, 595, 842, lines))
    source = doc.tobytes()
    images = [p.get_pixmap().tobytes("png") for p in doc]
    doc.close()
    report = GradeReport(questions=rows[:1], total_score=1, max_total=scheme.total,
                         overall_remarks="")
    cfg = pipeline.ProviderConfig(provider="claude", model="claude-haiku-4-5",
        google_token="test", task_id="same-task", exam_type="school",
        grading_contract="exam_package_v2", exam_package=pkg)
    return source, images, pages, rows, report, cfg, scheme


class CoverageRecoveryTests(unittest.TestCase):
    def helper(self, report, cfg, scheme, images, *, usage=None, cap=15):
        usage = {} if usage is None else usage
        common = dict(student_pngs=images, marks_scheme=scheme, usage_out=usage,
                      model=cfg.model, task_id=cfg.task_id, google_token="test",
                      subject="Maths", student_class="10")
        return pipeline._complete_missing_questions(report, cfg=cfg, common=common,
            policy=BudgetPolicy.for_copy(total_marks=scheme.total,
            exam_type="school"), hard_cap=cap, usd_to_inr=85, use_mathpix=True,
            stage=lambda *a: None)

    def test_reported_seven_question_case_completes_verifies_and_renders(self):
        source, images, pages, rows, report, cfg, scheme = fixture()
        calls = []
        def grade(**kwargs):
            scope = kwargs.get("question_qids")
            calls.append(scope)
            self.assertFalse(kwargs["reconcile_output"])
            self.assertEqual(len(kwargs["student_pngs"]), 3)
            self.assertEqual(kwargs["task_id"], "same-task")
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 12000 if scope else 21000,
                "output_tokens": 3600 if scope else 600}, stage="retry" if scope else "primary")
            chosen = rows[:1] if scope is None else [q for q in rows if q.qid in scope]
            return report.model_copy(deep=True, update={"questions": [q.model_copy(deep=True) for q in chosen]})
        def audit(current, **kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                "input_tokens": 14000, "output_tokens": 1600}, stage="verify")
            return [AuditItem(row=i, correct=maximum, evidence_status="verified",
                step_line_id=q.criteria[index].step_line_id, anchor_quote=q.criteria[index].anchor_quote)
                for i, (q, index, maximum) in enumerate(grader._audit_rows(
                    current, True, kwargs.get("selected_qids")))]
        # This fixture supplies complete OCR; reread adapters have separate tests.
        with patch.object(pipeline, "ocr_all_pages", return_value=pages) as ocr, \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(pipeline, "claude_grade", side_effect=grade), \
             patch.object(pipeline, "claude_audit", side_effect=audit) as verifier:
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
                ak_text="Award the stated maximum for each correct result.", marks_scheme=scheme,
                cfg=cfg, student_class="10", subject="Maths", total_marks=13, use_mathpix=True,
                qp_text="Maximum Marks: 13\n" + "\n".join(f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
        self.assertEqual(calls, [None, ["Q2", "Q3", "Q4", "Q5", "Q6.i", "Q6.ii"]])
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(verifier.call_count, 1)
        self.assertTrue(result["release"], result["issues"])
        # Under the Rs.12 school cap (2026-09-17): Haiku primary + recovery, the
        # ChatGPT second examiner, three OCR pages. The lower bound pins that every
        # call was billed; it was 10 when the second examiner was Sonnet.
        self.assertLessEqual(result["cost"].total_inr, 12)
        self.assertGreater(result["cost"].total_inr, 5)
        self.assertEqual(result["report"].total_score, 13)
        self.assertEqual(result["report"].coverage_recovered_qids, [q.qid for q in rows[1:]])
        self.assertEqual([q.qid for q in result["report"].questions], [q.qid for q in rows])
        self.assertEqual({r["qid"] for r in result["report"].render_manifest if r["role"] in {"step", "answer"}},
                         {q.qid for q in rows})
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertEqual(report.questions[0].score, 1)

    def test_complete_report_makes_no_extra_calls(self):
        _, images, _, rows, report, cfg, scheme = fixture()
        report.questions = rows
        with patch.object(pipeline, "claude_grade") as grade:
            self.assertIs(self.helper(report, cfg, scheme, images), report)
        grade.assert_not_called()

    def test_recovered_full_marks_require_independent_verification(self):
        from selective_verification import verification_candidates
        _, _, _, rows, report, _, _ = fixture(["Q1", "Q2"])
        report.questions = rows
        report.coverage_recovered_qids = ["Q2"]
        candidates = verification_candidates(report)
        recovered = next(q for q in candidates if q.qid == "Q2")
        self.assertTrue(recovered.mandatory)
        self.assertIn("question-recovered-after-primary-omission", recovered.reasons)

    def test_unknown_primary_id_is_not_rehomed(self):
        _, images, _, rows, report, cfg, scheme = fixture()
        report.questions.append(rows[0].model_copy(update={"qid": "Q99"}))
        with patch.object(pipeline, "claude_grade") as grade, self.assertRaises(StrictGradeError):
            self.helper(report, cfg, scheme, images)
        grade.assert_not_called()

    def test_a_question_read_twice_keeps_one_row(self):
        # Build 75: one answer over two pages came back twice and the whole copy was
        # refused (Akshat's Maths copy, 2026-09-30). The fuller reading is kept.
        _, images, _, rows, report, cfg, scheme = fixture()
        report.questions.append(rows[0].model_copy(update={"qid": "Q1"}))
        pipeline._index_question_decisions(report, cfg.exam_package)
        self.assertEqual([q.qid for q in report.questions].count("Q1"), 1)

    def test_recovery_does_not_accept_missing_duplicate_or_wrong_rows(self):
        for returned in ([], ["Q1"], ["Q2", "Q2"]):
            with self.subTest(returned=returned):
                _, images, _, rows, report, cfg, scheme = fixture()
                bad = report.model_copy(update={"questions": [rows[0].model_copy(update={"qid": qid}) for qid in returned]})
                with patch.object(pipeline, "claude_grade", return_value=bad) as grade:
                    with self.assertRaisesRegex(StrictGradeError, "exactly one decision"):
                        self.helper(report, cfg, scheme, images)
                self.assertEqual(grade.call_count, 1)
                self.assertEqual([q.qid for q in report.questions], ["Q1"])

    def test_verification_reserve_is_protected_before_dispatch(self):
        _, images, _, _, report, cfg, scheme = fixture()
        with patch.object(pipeline, "claude_grade") as grade:
            with self.assertRaisesRegex(StrictGradeError, "protecting the full verification"):
                self.helper(report, cfg, scheme, images, cap=2)
        grade.assert_not_called()

    def test_actual_recovery_usage_is_checked_before_the_next_call(self):
        _, images, _, rows, report, cfg, scheme = fixture([f"Q{i}" for i in range(1, 11)])
        usage = {}
        def expensive(**kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 50000, "output_tokens": 50000}, stage="retry")
            return report.model_copy(update={"questions": rows[1:9]})
        with patch.object(pipeline, "claude_grade", side_effect=expensive) as grade, \
             patch.object(pipeline.CallPlan, "quote_inr", return_value=.1):
            with self.assertRaisesRegex(StrictGradeError, "protecting the full verification"):
                self.helper(report, cfg, scheme, images, usage=usage, cap=25)
        self.assertEqual(grade.call_count, 1)
        self.assertEqual(usage["_events"][0]["stage"], "retry")

    def test_strict_omitted_sibling_is_never_filled_as_a_blank(self):
        _, _, pages, _, report, cfg, _ = fixture(["Q1.a", "Q1.b"])
        report.evidence_contract = True
        with self.assertRaisesRegex(StrictGradeError, "omitted package"):
            reconcile_report_to_package(report, cfg.exam_package, build_answer_map(cfg.exam_package, pages))

    def test_focused_provider_requests_restrict_output_to_the_requested_qid(self):
        _, images, _, rows, _, _, scheme = fixture()
        response_json = json.dumps({"questions": [rows[1].model_dump()]})
        usage = {}
        with patch.object(grader, "_claude_call", return_value={"stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": response_json}]}) as call:
            grader.grade_answer_sheet([], images, answer_key_text="Key", google_token="test", task_id="same-task",
                marks_scheme=scheme, reconcile_output=False, question_qids=["Q2"], usage_out=usage)
        request = call.call_args.kwargs["request"]
        schema = request["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["questions"]["type"], "object")
        self.assertEqual(schema["properties"]["questions"]["required"], ["Q2"])
        self.assertNotIn("qid", schema["$defs"]["QuestionMark"]["properties"])
        last = request["messages"][0]["content"][-1]["text"]
        self.assertIn("Return exactly 1 question", last)
        self.assertIn("Q2: official maximum 1", last)
        self.assertNotIn("Q1:", last)
        self.assertEqual(usage["_events"][0]["stage"], "retry")

    def test_large_omission_uses_bounded_complete_batches(self):
        _, images, _, rows, report, cfg, scheme = fixture([f"Q{i}" for i in range(1, 11)])
        def grade(**kwargs):
            return report.model_copy(update={"questions": [q for q in rows if q.qid in kwargs["question_qids"]]})
        with patch.object(pipeline, "claude_grade", side_effect=grade) as call:
            self.helper(report, cfg, scheme, images, cap=40)
        self.assertEqual([c.kwargs["question_qids"] for c in call.call_args_list],
                         [[q.qid for q in rows[1:9]], ["Q10"]])
        self.assertEqual([q.qid for q in report.questions], [q.qid for q in rows])

    def test_all_batches_are_reserved_before_spending_on_the_first(self):
        _, images, _, _, report, cfg, scheme = fixture([f"Q{i}" for i in range(1, 11)])
        with patch.object(pipeline, "claude_grade") as call, \
             patch.object(pipeline, "_coverage_verification_reserve", return_value=2), \
             patch.object(pipeline.CallPlan, "quote_inr", return_value=5):
            with self.assertRaisesRegex(StrictGradeError, "all remaining questions"):
                self.helper(report, cfg, scheme, images, cap=10)
        call.assert_not_called()

    def test_small_primary_request_requires_the_entire_question_set(self):
        _, images, _, rows, _, _, scheme = fixture()
        response = {"questions": {q.qid: q.model_dump(exclude={"qid"}) for q in rows}}
        with patch.object(grader, "_claude_call", return_value={"stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(response)}]}) as call:
            result = grader.grade_answer_sheet([], images, answer_key_text="Key", google_token="test",
                task_id="same-task", marks_scheme=scheme, reconcile_output=False)
        schema = call.call_args.kwargs["request"]["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["questions"]["required"], [q.qid for q in rows])
        self.assertEqual([q.qid for q in result.questions], [q.qid for q in rows])
        self.assertEqual([q.score for q in result.questions], [q.score for q in rows])

    def test_keyed_question_cannot_change_its_official_id(self):
        with self.assertRaisesRegex(ValueError, "conflicting question ID"):
            grader.parse_grade_report(json.dumps({"questions": {"Q2": {"qid": "Q1", "score": 1}}}))

    def test_rotated_copy_through_real_ocr_grading_audit_and_renderer_adapters(self):
        # Only remote responses are simulated. Rotation, OCR request construction,
        # provider prompts, parsers, accounting, moderation and rendering all run.
        _, images, pages, rows, _, cfg, scheme = fixture()
        source = fitz.open()
        for image, angle in zip(images, [90, 180, 270]):
            rotated = Image.open(BytesIO(image)).rotate(angle, expand=True)
            data = BytesIO()
            rotated.save(data, format="PNG")
            page = source.new_page(width=rotated.width, height=rotated.height)
            page.insert_image(page.rect, stream=data.getvalue())
        source_bytes = source.tobytes()
        source.close()
        oriented, applied, changed = normalize_orientation(source_bytes, "copy.pdf",
            probe=lambda pngs: [270, 180, 90])
        self.assertTrue(changed)
        self.assertEqual(applied, [270, 180, 90])
        ocr_count = 0
        def ocr_response(*args, request, **kwargs):
            nonlocal ocr_count
            self.assertTrue(request.get("include_line_data"))
            self.assertFalse(request.get("include_word_data"))
            self.assertEqual(request["auto_rotate_confidence_threshold"], 1)
            image = Image.open(BytesIO(base64.b64decode(request["src"].split(",", 1)[1])))
            self.assertLess(image.width, image.height)
            page = pages[ocr_count]
            ocr_count += 1
            sx, sy = image.width / page.image_width, image.height / page.image_height
            return {"line_data": [{"type": "text", "text": line.text,
                "cnt": [[line.bbox[0]*sx, line.bbox[1]*sy], [line.bbox[2]*sx, line.bbox[3]*sy]],
                "confidence": .99} for line in page.lines]}
        def serial_ocr(images, **kwargs):
            return mathpix.ocr_all_pages(images, max_workers=1, **kwargs)
        calls = []
        def model_response(*args, request, model, **kwargs):
            schema = request["output_config"]["format"]["schema"]
            self.assertEqual(kwargs["task_id"], "same-task")
            if "questions" in schema["properties"]:
                calls.append("grade")
                self.assertEqual(schema["properties"]["questions"]["required"], [q.qid for q in rows])
                qprops = schema["$defs"]["QuestionMark"]["properties"]
                cprops = schema["$defs"]["Criterion"]["properties"]
                response_rows = {}
                for q in rows:
                    value = q.model_dump(include=set(qprops))
                    value["criteria"] = [c.model_dump(include=set(cprops)) for c in q.criteria]
                    response_rows[q.qid] = value
                response = {"questions": response_rows}
                usage = {"input_tokens": 21000, "output_tokens": 4200}
            else:
                calls.append("verify")
                self.assertNotEqual(model, cfg.model)
                response = {"items": [AuditItem(row=i, correct=q.score, evidence_status="verified",
                    step_line_id=q.criteria[0].step_line_id,
                    anchor_quote=q.criteria[0].anchor_quote).model_dump() for i, q in enumerate(rows)]}
                usage = {"input_tokens": 14000, "output_tokens": 1600}
            self.assertLessEqual(usage["output_tokens"], request["max_tokens"])
            return {"stop_reason": "end_turn", "usage": usage,
                    "content": [{"type": "text", "text": json.dumps(response)}]}
        def gpt_response(*args, request, model, **kwargs):
            # The second examiner runs on the proxy's ChatGPT route (2026-09-17):
            # a native chat.completions body and reply.
            calls.append("verify")
            self.assertEqual(kwargs["task_id"], "same-task")
            self.assertNotEqual(model, cfg.model)
            self.assertEqual(request["messages"][0]["role"], "system")
            self.assertIs(request["response_format"]["json_schema"]["strict"], True)
            response = {"items": [AuditItem(row=i, correct=q.score, evidence_status="verified",
                step_line_id=q.criteria[0].step_line_id,
                anchor_quote=q.criteria[0].anchor_quote).model_dump() for i, q in enumerate(rows)]}
            self.assertLessEqual(1600, request["max_completion_tokens"])
            return {"choices": [{"message": {"role": "assistant", "content": json.dumps(response)}}],
                    "usage": {"prompt_tokens": 14000, "completion_tokens": 1600}}
        prior = {}
        record_usage_event(prior, {"provider": "gemini", "model": "gemini-2.5-flash",
            "input_tokens": 4000, "output_tokens": 200}, stage="orientation")
        with patch.object(mathpix.pw_access, "mathpix_ocr", side_effect=ocr_response), \
             patch.object(pipeline, "ocr_all_pages", side_effect=serial_ocr), \
             patch.object(grader, "_claude_call", side_effect=model_response), \
             patch.object(grader, "_gpt_call", side_effect=gpt_response):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=oriented, filename="copy.pdf", ak_pngs=[],
                ak_text="Award each correct result its question maximum.", marks_scheme=scheme,
                cfg=cfg, student_class="10", subject="Maths", total_marks=13, use_mathpix=True,
                prior_usage=prior,
                qp_text="Maximum Marks: 13\n" + "\n".join(f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
        self.assertEqual(ocr_count, 3)
        self.assertEqual(calls, ["grade", "verify"])
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].coverage_recovered_qids, [])
        self.assertLess(result["cost"].total_inr, 15)
        self.assertEqual({m["qid"] for m in result["report"].render_manifest if m["role"] in {"step", "answer"}},
                         {q.qid for q in rows})


if __name__ == "__main__":
    unittest.main()
