"""Verifier label mistakes must not discard a sound decision or move another answer."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
import pipeline
from anchors import _quote_score
from answer_map import build_answer_map, _student_label_qid
from costs import record_usage_event
from exam_package import build_exam_package
from grader import (AuditItem, Criterion, GradeReport, MarksItem, MarksScheme,
                    QuestionMark, audit_completion_gaps, build_audit_prompt)
from grading_quality import (apply_verified_anchors, initialize_evidence,
                             prepare_evidence, resolve_verified_anchors)
from mathpix import LineBox, PageOCR, is_header_line
from strict_grading import StrictGradeError


def fixture():
    scheme = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=1)])
    package = build_exam_package(exam_type="school", student_class="10", subject="Physics",
        level="10", paper="", attempt="", applicable_law_date=None,
        total_marks=2, question_paper=b"paper", answer_key=b"key", key_source="upload",
        marks_scheme=scheme, scheme_source="text")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 100), "Q1 (c) 50 W")
    page.insert_text((60, 170), "Q2 (b) Vx < Vy")
    source = doc.tobytes()
    doc.close()
    ocr = [PageOCR(1, 595, 842, [
        LineBox("P1L1", r"\(\text{Q1}\) (c) 50 W", (60, 85, 260, 105), 1),
        LineBox("P1L2", r"\(\text{Q2}\) (b) V_{x} < V_{y}", (60, 155, 330, 175), 1),
    ])]
    report = GradeReport(total_score=1, max_total=2, overall_remarks="", questions=[
        QuestionMark(qid="Q1", score=1, max_score=1, page=1, y_fraction=.12,
            anchor_line_id="P1L1", mark_style="single", criteria=[Criterion(
                description="Power", awarded=1, max=1, met=True,
                anchor_quote="50 W", step_line_id="P1L1")]),
        QuestionMark(qid="Q2", score=0, max_score=1, page=1, y_fraction=.2,
            anchor_line_id="P1L2", mark_style="single", remark="Voltage relation reversed",
            criteria=[Criterion(description="Voltage relation", awarded=0, max=1, met=False,
                evidence="student: Vx < Vy; key: Vx > Vy", anchor_quote="Vx < Vy", step_line_id="P1L2")]),
    ])
    initialize_evidence(report, package, build_answer_map(package, ocr))
    prepare_evidence(report, ocr)
    return source, report, ocr, package, scheme


def verdicts(anchor="P1L1", quote="Vx < Vy"):
    return [AuditItem(row=0, qid="Q1", correct=1, evidence_status="verified",
                     step_line_id="P1L1", anchor_quote="50 W"),
            AuditItem(row=1, qid="Q2", correct=0, evidence_status="verified",
                step_line_id=anchor, anchor_quote=quote,
                reason="The voltage measured across X should exceed Y because its resistance is higher.")]


class VerifierAnchorTests(unittest.TestCase):
    def test_wrong_question_id_resolves_only_from_unique_owned_quote(self):
        _, report, ocr, _, _ = fixture()
        before = report.model_copy(deep=True)
        items = verdicts()
        apply_verified_anchors(report, items, ocr, include_awards=True, selected_qids=None)
        self.assertEqual(items[1].step_line_id, "P1L2")
        self.assertEqual(report.verified_step_anchors["q2:0"], ["P1L2", "Vx < Vy"])
        self.assertEqual(report.answer_regions, before.answer_regions)
        self.assertEqual([q.score for q in report.questions], [1, 0])

    def test_brackets_case_and_spacing_are_not_new_line_ids(self):
        _, report, ocr, _, _ = fixture()
        items = verdicts("[p 1 l 2]", "Illegible OCR but visually read")
        apply_verified_anchors(report, items, ocr, include_awards=True, selected_qids=None)
        self.assertEqual(items[1].step_line_id, "P1L2")

    def test_missing_id_is_recovered_before_completion_check(self):
        _, report, ocr, _, _ = fixture()
        items = verdicts("", "Vx < Vy")
        self.assertEqual(resolve_verified_anchors(report, items, ocr,
            include_awards=True, selected_qids=None), {})
        self.assertEqual(audit_completion_gaps(report, items, True), ([], {}))

    def test_quote_only_in_other_answer_never_moves_ownership(self):
        _, report, ocr, _, _ = fixture()
        before = report.model_dump()
        with self.assertRaisesRegex(StrictGradeError, "outside"):
            apply_verified_anchors(report, verdicts(quote="50 W"), ocr,
                                   include_awards=True, selected_qids=None)
        self.assertEqual(report.model_dump(), before)

    def test_ambiguous_owned_quote_does_not_choose_a_line(self):
        _, report, ocr, _, _ = fixture()
        ocr[0].lines.append(LineBox("P1L3", "Vx < Vy", (60, 195, 330, 215), 1))
        report.answer_regions["q2"]["line_ids"].append("P1L3")
        with self.assertRaisesRegex(StrictGradeError, "outside"):
            apply_verified_anchors(report, verdicts(), ocr, include_awards=True, selected_qids=None)

    def test_cancelled_line_cannot_be_certified(self):
        _, report, ocr, _, _ = fixture()
        ocr[0].lines[1].cancelled = True
        with self.assertRaisesRegex(StrictGradeError, "outside"):
            apply_verified_anchors(report, verdicts("P1L2"), ocr, include_awards=True, selected_qids=None)

    def test_ocr_wrappers_and_roman_labels_keep_subparts_separate(self):
        _, _, _, package, _ = fixture()
        base = package.questions[0]
        package = package.model_copy(update={"questions": [
            base.model_copy(update={"qid": "Q6.i"}), base.model_copy(update={"qid": "Q6.ii"})]})
        lines = [r"\(\text{Q6}\)", r"\((i)\)", "x = 2", r"\((ii)\)", "y = 3"]
        ocr = [PageOCR(1, 595, 842, [LineBox(f"P1L{i}", text,
            (60, i*30, 330, i*30+20), 1) for i, text in enumerate(lines, 1)])]
        mapped = {q.qid: q for q in build_answer_map(package, ocr).locations}
        self.assertEqual(mapped["Q6.i"].line_ids, ("P1L2", "P1L3"))
        self.assertEqual(mapped["Q6.ii"].line_ids, ("P1L4", "P1L5"))
        self.assertEqual(_student_label_qid(r"\(\text{Q6.ii}\)"), "q6.ii")
        self.assertEqual(_student_label_qid("Q1.a.1"), "q1.a.1")
        self.assertIsNone(_student_label_qid("2-chloroethane"))
        self.assertIsNone(_student_label_qid("2.5 g of NaCl"))

    def test_inline_answer_is_evidence_bare_heading_is_not(self):
        self.assertTrue(is_header_line("Q2"))
        self.assertTrue(is_header_line(r"\(\text{Q6.ii}\)"))
        self.assertFalse(is_header_line(r"\(\text{Q2}\) (b) V_{x} < V_{y}"))

    def test_math_formatting_does_not_erase_signs_or_inequalities(self):
        self.assertGreater(_quote_score("Vx < Vy", r"\(V_{x} \lt V_{y}\)"), 0)
        self.assertEqual(_quote_score("Vx > Vy", r"\(V_{x} < V_{y}\)"), 0)
        self.assertEqual(_quote_score("2", "200"), 0)
        self.assertEqual(_quote_score("2", "-2"), 0)

    def test_verifier_prompt_supplies_each_questions_allowed_ids(self):
        _, report, _, _, _ = fixture()
        prompt = build_audit_prompt(report, include_awards=True)
        q2 = prompt.split("Q2 (awarded", 1)[1]
        self.assertIn("allowed evidence IDs: P1L2", q2)
        self.assertNotIn("allowed evidence IDs: P1L1", q2)

    def _run_pipeline(self, needs_repair=False):
        source, report, ocr, package, scheme = fixture()
        cfg = pipeline.ProviderConfig(provider="claude", model="claude-haiku-4-5",
            google_token="test", task_id="same-task", exam_type="school",
            grading_contract="exam_package_v2", exam_package=package)
        def grade(**kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                "input_tokens": 14000, "output_tokens": 800}, stage="primary")
            return report.model_copy(deep=True)
        audit_calls = 0
        def audit(current, **kwargs):
            nonlocal audit_calls
            audit_calls += 1
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                "input_tokens": 8000, "output_tokens": 600}, stage="verify")
            if needs_repair and audit_calls == 1:
                return verdicts("P9L99", "No matching quote")
            if needs_repair:
                self.assertIn("P1L2", kwargs["repair_rows"][1])
                self.assertIn("outside", kwargs["repair_rows"][1])
                self.assertEqual(kwargs["only_rows"], {1})
                return verdicts()[1:]
            else:
                self.assertIsNone(kwargs.get("repair_rows"))
            return verdicts()
        with patch.object(pipeline, "ocr_all_pages", return_value=ocr), \
             patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
             patch.object(pipeline, "claude_grade", side_effect=grade), \
             patch.object(pipeline, "claude_audit", side_effect=audit) as verifier:
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
                ak_text="Q1: 50 W. Q2: Vx > Vy because the resistance of X is higher.",
                marks_scheme=scheme, cfg=cfg, student_class="10", subject="Physics", total_marks=2,
                use_mathpix=True, qp_text="Maximum Marks: 2\nQ1. Power (1 Mark)\nQ2. Compare voltages (1 Mark)")
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(verifier.call_count, 2 if needs_repair else 1)
        self.assertEqual(result["report"].total_score, 1)
        self.assertLess(result["cost"].total_inr, 15)
        self.assertTrue(any(m["qid"] == "Q2" and m["line_id"] == "P1L2"
                            for m in result["report"].render_manifest if m["role"] in {"answer", "step"}))

    def test_full_pipeline_recovers_q2_anchor_without_paid_retry(self):
        self._run_pipeline()

    def test_unresolved_anchor_gets_explicit_bounded_verifier_repair(self):
        self._run_pipeline(needs_repair=True)


if __name__ == "__main__":
    unittest.main()
