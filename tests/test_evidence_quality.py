"""Production evidence, rubric and rendered-coverage regressions."""
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from PIL import Image
from anchors import resolve_steps
from answer_map import build_answer_map
from exam_package import build_exam_package, package_prompt_block
from grader import (Criterion, GradeReport, QuestionMark, MarksItem, MarksScheme,
                    AuditItem, audit_completion_errors, parse_audit)
from grading_quality import (initialize_evidence, prepare_evidence,
                             check_render_manifest, apply_verified_anchors)
from mathpix import (LineBox, PageOCR, WordBox, ocr_page, pageocr_from_dict,
                     pageocr_to_dict, word_target_bbox, synthesize_diagram_regions)
from pdf_renderer import build_evaluated_pdf

# A legacy-contract suite: marks stay inside the sheet and within reach of their
# evidence row, and checking plans are drawn. The teacher layout (the product
# default) has its own contract in test_teacher_layout.py.
import pdf_renderer as _pr
_LAYOUT_BEFORE = _pr.TEACHER_LAYOUT


def setUpModule():
    _pr.TEACHER_LAYOUT = False


def tearDownModule():
    _pr.TEACHER_LAYOUT = _LAYOUT_BEFORE

from strict_grading import reconcile_report_to_package, StrictGradeError


def package(qids=("Q1",), key="", rubric=None):
    scheme = MarksScheme(total=2 * len(qids), items=[MarksItem(qid=q, max=2) for q in qids])
    return build_exam_package(exam_type="school", student_class="10", subject="Maths",
                             level="10", paper="", attempt="", applicable_law_date=None,
                             total_marks=scheme.total, question_paper=b"paper", answer_key=b"key",
                             key_source="upload", marks_scheme=scheme, scheme_source="text",
                             answer_key_text=key, step_rubric=rubric)


def fixture(missing_quote=False):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    texts = ["Q1", "x = 2", "answer = 4"]
    ocr = PageOCR(1, 595, 842, [LineBox(f"P1L{i+1}", t, (60, 85+i*50, 210, 103+i*50), 1)
                                   for i, t in enumerate(texts)])
    for i, text in enumerate(texts):
        pg.insert_text((60, 100+i*50), text)
    pdf = doc.tobytes()
    doc.close()
    criteria = [Criterion(description="Substitution", awarded=1, max=1, met=True,
                          step_line_id="P1L2", anchor_quote="x = 2"),
                Criterion(description="Result", awarded=1, max=1, met=True,
                          step_line_id="P1L3", anchor_quote="" if missing_quote else "answer = 4")]
    report = GradeReport(total_score=2, max_total=2, overall_remarks="",
                         questions=[QuestionMark(qid="Q1", score=2, max_score=2, page=1,
                                                 y_fraction=.1, anchor_line_id="P1L1",
                                                 mark_style="per_step", criteria=criteria)])
    pkg = package()
    amap = build_answer_map(pkg, [ocr])
    initialize_evidence(report, pkg, amap)
    prepare_evidence(report, [ocr])
    return pdf, report, [ocr], pkg


class EvidenceQualityTests(unittest.TestCase):
    def test_production_pipeline_repairs_anchor_and_releases_real_pdf(self):
        import pipeline
        from costs import record_usage_event
        source, primary, ocr, pkg = fixture(missing_quote=True)
        primary.evidence_contract = False
        cfg = pipeline.ProviderConfig(provider="claude", model="claude-haiku-4-5",
                                      google_token="test", task_id="test", exam_type="school",
                                      grading_contract="exam_package_v2", exam_package=pkg)
        def grade(**kwargs):
            self.assertFalse(kwargs["reconcile_output"])
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                               "input_tokens": 100, "output_tokens": 100}, stage="grading")
            return primary.model_copy(deep=True)
        def audit(report, **kwargs):
            self.assertIn("Q1", report.placement_risk_qids)
            self.assertTrue(kwargs["student_evidence_pngs"])
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                               "input_tokens": 100, "output_tokens": 100}, stage="verification")
            return [AuditItem(row=0, correct=1, evidence_status="verified", step_line_id="P1L2", anchor_quote="x = 2"),
                    AuditItem(row=1, correct=1, evidence_status="verified", step_line_id="P1L3", anchor_quote="answer = 4")]
        scheme = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=2)])
        with patch.object(pipeline, "ocr_all_pages", return_value=ocr), \
                patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
                patch.object(pipeline, "claude_grade", side_effect=grade), \
                patch.object(pipeline, "claude_audit", side_effect=audit):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
                ak_text="Q1: x = 2. Hence answer = 4. Award one mark for each correct step.",
                marks_scheme=scheme, cfg=cfg, student_class="10", subject="Maths", total_marks=2, use_mathpix=True,
                qp_text="Maximum Marks: 2\nQ1. Calculate the answer. (2 Marks)")
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].total_score, 2)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))

    def test_fixed_rubric_import_and_weight_enforcement(self):
        pkg = package(key="Q1\nValid method [1 mark]\nCorrect result [1 mark]")
        self.assertEqual(len(pkg.questions[0].criteria), 2)
        self.assertIn("q1.c1", package_prompt_block(pkg))
        _, report, _, _ = fixture()
        for c, spec in zip(report.questions[0].criteria, pkg.questions[0].criteria):
            c.rubric_criterion_id = spec.criterion_id
        reconcile_report_to_package(report, pkg)
        report.questions[0].criteria[0].max = 1.5
        with self.assertRaisesRegex(StrictGradeError, "immutable"):
            reconcile_report_to_package(report, pkg)

    def test_bad_rubric_rejected_without_inventing_allocations(self):
        self.assertEqual(package(key="Q1\nAnswer is 100 [2]\nTotal 2 marks").questions[0].criteria, [])
        rubric = {"version": 1, "questions": [{"qid": "Q1", "criteria": [
            {"criterion_id": "a", "description": "Method", "max_marks": 3}]}]}
        with self.assertRaisesRegex(ValueError, "sum exactly"):
            package(rubric=rubric)

    def test_ocr_uses_supported_line_request_and_keeps_confidence_and_diagrams(self):
        buf = BytesIO()
        Image.new("RGB", (500, 700), "white").save(buf, format="PNG")
        response = {"line_data": [
            {"type": "text", "text": "x = 200", "cnt": [[20, 50], [250, 70]], "confidence": .61},
            {"type": "diagram", "cnt": [[20, 100], [450, 500]]}]}
        def service_response(*args, request, **kwargs):
            if request.get("include_line_data") and request.get("include_word_data"):
                return {"error": "Requesting both line data and word data is not supported."}
            return response
        with patch("mathpix.pw_access.mathpix_ocr", side_effect=service_response) as mocked:
            result = ocr_page(buf.getvalue(), 1, google_token="test", task_id="test")
        self.assertEqual(mocked.call_count, 1)
        self.assertTrue(mocked.call_args.kwargs["request"]["include_line_data"])
        self.assertFalse(mocked.call_args.kwargs["request"].get("include_word_data", False))
        self.assertEqual(result.lines[0].confidence, .61)
        self.assertEqual(result.lines[0].bbox, (20, 50, 250, 70))
        self.assertEqual(result.words, [])
        restored = pageocr_from_dict(pageocr_to_dict(result))
        self.assertEqual(restored.lines[1].kind, "diagram")
        self.assertEqual(restored.lines[1].line_id, "P1D2")
        self.assertIsNone(word_target_bbox(restored, restored.lines[0], "200"))

    def test_cached_word_contours_survive_roundtrip(self):
        result = PageOCR(1, 500, 700,
            [LineBox("P1L1", "x = 200", (20, 50, 250, 70), 1, confidence=.61)],
            [WordBox("200", (210, 50, 250, 70), .95)])
        restored = pageocr_from_dict(pageocr_to_dict(result))
        self.assertEqual(word_target_bbox(restored, restored.lines[0], "200"), (210, 50, 250, 70))

    def test_repeated_words_are_not_guessed(self):
        line = LineBox("P1L1", "100 100", (0, 0, 200, 20), 1)
        page = PageOCR(1, 200, 300, [line], [WordBox("100", (0, 0, 30, 20)), WordBox("100", (150, 0, 200, 20))])
        self.assertIsNone(word_target_bbox(page, line, "100"))
        self.assertEqual(word_target_bbox(page, line, "100 100"), (0, 0, 200, 20))

    def test_answer_mapping_owns_continuations_and_excludes_cancelled_work(self):
        pages = [PageOCR(1, 500, 700, [LineBox("P1L1", "Q1", (10, 10, 60, 30), 1),
                                     LineBox("P1L2", "x = 2", (10, 40, 100, 60), 1)]),
                 PageOCR(2, 500, 700, [LineBox("P2L1", "answer = 4", (10, 10, 100, 30), 2, confidence=.5),
                                     LineBox("P2L2", "old attempt", (10, 35, 100, 55), 2, cancelled=True),
                                     LineBox("P2L3", "Q2", (10, 80, 60, 100), 2)])]
        amap = build_answer_map(package(("Q1", "Q2")), pages)
        q1 = amap.locations[0]
        self.assertEqual(q1.pages, (1, 2))
        self.assertIn("P2L1", q1.line_ids)
        self.assertNotIn("P2L2", q1.line_ids)
        self.assertNotIn("P2L3", q1.line_ids)
        self.assertEqual(q1.confidence, .5)

    def test_wrong_existing_anchor_is_repaired_from_actual_quote(self):
        c = Criterion(description="Method", awarded=1, max=1, met=True,
                      anchor_quote="x = 2", step_line_id="P1L2")
        rows = [{"lid": "P1L1", "text": "x = 2"}, {"lid": "P1L2", "text": "answer = 4"}]
        resolved = resolve_steps([c], rows, model_anchor_ok=lambda *a: True, require_evidence=True)
        self.assertEqual(resolved[0].line_id, "P1L1")
        c.anchor_quote = "2"
        resolved = resolve_steps([c], [{"lid": "P1L1", "text": "200"}],
                                 model_anchor_ok=lambda *a: True, require_evidence=True)
        self.assertFalse(resolved[0].placed)

    def test_two_mark_answer_has_each_step_and_a_total(self):
        source, report, ocr, _ = fixture()
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(len([r for r in report.render_manifest if r["role"] == "step"]), 2)
        self.assertTrue(report.render_manifest_pdf_sha256)

    def test_wrong_step_keeps_its_cross_and_margin_zero_without_a_circled_zero(self):
        source, report, ocr, _ = fixture()
        q = report.questions[0]
        q.score = report.total_score = 1
        q.criteria[1].awarded = 0
        q.criteria[1].met = False
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        row = next(r for r in report.render_manifest if r["role"] == "step" and r["score"] == 0)
        self.assertEqual(row["line_id"], "P1L3")
        self.assertIsNone(row["numeral_bbox"])
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            self.assertEqual(doc[0].get_textbox(fitz.Rect(row["bbox"])).strip(), "")
            margin = fitz.Rect(report.render_page_layouts["1"]["margin_bbox"])
            self.assertIn("1/2", "".join(doc[0].get_textbox(margin).split()))
            self.assertNotIn("1+0=1", "".join(doc[0].get_textbox(margin).split()))
            self.assertFalse(any(r.get("question_total") for r in report.render_manifest))

    def test_final_numeral_position_is_checked_after_pinning_to_evidence_row(self):
        source, report, ocr, _ = fixture()
        baseline = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        row = next(r for r in report.render_manifest if r["role"] == "step")
        box = fitz.Rect(row["numeral_bbox"])
        with fitz.open(stream=baseline, filetype="pdf") as doc:
            header = doc[0].rect.height - 842
        left = report.render_page_layouts["1"]["content_bbox"][0]
        x, y = (box.x0 + box.x1) / 2 - left, (box.y0 + box.y1) / 2 - header
        # A dark notebook edge crosses the location selected on a blank scan.
        with fitz.open(stream=source, filetype="pdf") as doc:
            doc[0].draw_line((x, y - 26), (x, y + 26), width=3, color=(.1, .1, .1))
            source = doc.tobytes()
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(report.total_score, 2)
        self.assertEqual(len([r for r in report.render_manifest if r["role"] == "step"]), 2)

    def test_a_step_without_a_quote_is_ticked_on_the_line_it_cites(self):
        # Since the commerce SME review of 29 Oct 2026, in every category.
        source, report, ocr, _ = fixture(missing_quote=True)
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual({r["line_id"] for r in report.render_manifest if r["role"] == "step"}, {"P1L2", "P1L3"})

    def test_repair_layout_preserves_step_marking(self):
        source, report, ocr, _ = fixture()
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr, safe_layout=True)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(len([r for r in report.render_manifest if r["role"] == "step"]), 2)

    def test_uncertain_verdict_cannot_certify_marks(self):
        _, report, _, _ = fixture()
        verdicts = [AuditItem(row=i, correct=1, evidence_status="uncertain") for i in range(2)]
        self.assertTrue(audit_completion_errors(report, verdicts, include_awards=True))
        parsed = parse_audit(json.dumps({"items": [{"row": 0, "correct": 1,
            "evidence_status": "verified", "step_line_id": "P1L2", "anchor_quote": "x = 2"}]}))
        self.assertEqual(parsed[0].step_line_id, "P1L2")

    def test_certificate_rejects_other_pdf_and_cross_question_anchor(self):
        source, report, ocr, _ = fixture()
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf + b"x")[0].code, "render-certificate-mismatch")
        bad = [AuditItem(row=0, correct=1, evidence_status="verified", step_line_id="P9L1")]
        with self.assertRaisesRegex(StrictGradeError, "outside"):
            apply_verified_anchors(report, bad, ocr, include_awards=True, selected_qids=None)

    def test_missing_work_does_not_cross_correct_neighbour(self):
        import pdf_renderer
        source, report, ocr, _ = fixture()
        q = report.questions[0]
        q.score, report.total_score = 1, 1
        q.criteria[1].awarded = 0
        q.criteria[1].met = False
        q.criteria[1].evidence_kind = "missing"
        q.remark = "Units missing"
        with patch.object(pdf_renderer, "_draw_cross", wraps=pdf_renderer._draw_cross) as cross:
            pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(cross.call_count, 0)
        self.assertEqual(check_render_manifest(report, pdf), [])

    def test_prose_continuation_is_marked_without_duplicate_score(self):
        source, report, ocr, pkg = fixture()
        doc = fitz.open(stream=source, filetype="pdf")
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 100), "Explanation continues here")
        source = doc.tobytes()
        doc.close()
        ocr.append(PageOCR(2, 595, 842, [LineBox("P2L1", "Explanation continues here", (60, 85, 250, 103), 2)]))
        report.questions[0].mark_style = "single"
        initialize_evidence(report, pkg, build_answer_map(pkg, ocr))
        prepare_evidence(report, ocr)
        pdf = build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(len([r for r in report.render_manifest if r.get("question_total")]), 1)
        self.assertFalse(any(r["role"] == "continuation" for r in report.render_manifest))
        self.assertTrue(any(r.get('question_total') and r['page'] == 2 for r in report.render_manifest))

    def test_fixed_quarter_marks_survive_moderation(self):
        from grader import apply_audit
        _, report, _, _ = fixture()
        c = report.questions[0].criteria[0]
        c.rubric_criterion_id = "method"
        report.fixed_rubric_rules = {"q1": [{"criterion_id": "method", "mark_increment": .25}]}
        verdicts = [AuditItem(row=0, correct=.75, reason="Substitution sign should be positive",
                             evidence_status="verified", step_line_id="P1L2", anchor_quote="x = 2"),
                    AuditItem(row=1, correct=1, evidence_status="verified", step_line_id="P1L3", anchor_quote="answer = 4")]
        self.assertEqual(audit_completion_errors(report, verdicts, include_awards=True), [])
        apply_audit(report, verdicts, include_awards=True)
        self.assertEqual(report.questions[0].score, 1.75)


if __name__ == "__main__":
    unittest.main()
