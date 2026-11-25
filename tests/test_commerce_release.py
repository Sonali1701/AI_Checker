"""Commerce SME feedback: the rendered marks must agree with the awards.

Uses synthetic PDFs and saved OCR objects only; no OCR service or model calls.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fitz
import pdf_renderer as pr
from grading_quality import apply_verified_anchors, check_render_manifest
from preflight import advisory, check_copy, release_blocking
from strict_grading import automatic_release_decision
from test_checking_plan import section_fixture
from test_evidence_quality import fixture


def replace_numeral(pdf, report, row, text):
    """Alter actual PDF text, preserving the score circle and expected award."""
    box = fitz.Rect(row["numeral_bbox"])
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[row["page"] - 1]
        page.add_redact_annot(box + (-1, -1, 1, 1), fill=False)
        page.apply_redactions(images=0, graphics=0, text=0)
        page.insert_text((box.x0 + 2, box.y0 + min(12, box.height - 2)), text,
                         fontsize=min(10, box.height - 4), color=(.9, 0, 0))
        assert page.get_textbox(box + (-1, -1, 1, 1)).strip() == text
        altered = doc.tobytes()
    # The checker must inspect the final PDF even when its bookkeeping matches.
    report.render_manifest_pdf_sha256 = hashlib.sha256(altered).hexdigest()
    return altered


class CommerceRenderedMarks(unittest.TestCase):
    def test_one_awarded_but_two_printed_is_held_after_complete_preflight(self):
        for teacher in (False, True):
            with self.subTest(teacher_layout=teacher), patch.object(pr, "TEACHER_LAYOUT", teacher):
                source, report, ocr, package = fixture()
                pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
                self.assertEqual(check_copy(report, pdf_bytes=pdf, ocr_pages=ocr), [])
                row = next(r for r in report.render_manifest
                           if r["role"] == "step" and r.get("numeral_bbox"))
                self.assertEqual(row["score"], 1)
                pdf = replace_numeral(pdf, report, row, "2")
                issues = check_copy(report, pdf_bytes=pdf, ocr_pages=ocr)
                held = {i.code for i in release_blocking(issues, "commerce")}
                self.assertIn("printed-award-mismatch", held)
                decision = automatic_release_decision(
                    package=package, report=report, preflight_issues=issues,
                    cost=None, budget_cap_inr=None, mandatory_verification_complete=True,
                    category="commerce")
                self.assertFalse(decision.release)
                self.assertIn("printed-award-mismatch", {i.code for i in decision.blockers})
                self.assertEqual(report.questions[0].criteria[0].awarded, 1)
                # This is the Commerce SME policy; other exam categories keep theirs.
                for category in ("school", "vidyapeeth", "ca"):
                    self.assertEqual(release_blocking(issues, category), [])
                    self.assertIn("printed-award-mismatch",
                                  {i.code for i in advisory(issues, category)})

    def test_swapped_step_awards_cannot_hide_behind_a_correct_question_total(self):
        source, report, ocr, _ = fixture()
        q = report.questions[0]
        q.criteria[0].awarded, q.criteria[0].met = .5, False
        q.criteria[1].awarded = q.criteria[1].max = 1.5
        q.max_score = report.max_total = 2.5
        with patch.object(pr, "TEACHER_LAYOUT", False):
            pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        steps = [r for r in report.render_manifest if r["role"] == "step"]
        self.assertEqual(len(steps), 2)
        steps[0]["score"], steps[1]["score"] = steps[1]["score"], steps[0]["score"]
        self.assertEqual(sum(r["score"] for r in steps), q.score)
        issues = check_render_manifest(report, pdf)
        self.assertIn("step-award-mismatch",
                      {i.code for i in release_blocking(issues, "commerce")})

    def test_missing_question_total_in_margin_is_held(self):
        source, report, ocr, _ = fixture()
        pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        report.render_manifest = [r for r in report.render_manifest if not r.get("margin_score")]
        issues = check_render_manifest(report, pdf)
        self.assertIn("margin-score-not-marked",
                      {i.code for i in release_blocking(issues, "commerce")})

    def test_incorrect_circled_question_total_is_held(self):
        source, report, ocr, _ = fixture()
        report.questions[0].mark_style = "single"
        with patch.object(pr, "TEACHER_LAYOUT", False):
            pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        row = next(r for r in report.render_manifest if r.get("question_total"))
        pdf = replace_numeral(pdf, report, row, "3")
        issues = check_render_manifest(report, pdf)
        self.assertIn("printed-award-mismatch",
                      {i.code for i in release_blocking(issues, "commerce")})

    def test_unverified_first_point_order_reaches_release_gate_without_render_contract(self):
        _, report, ocr, _ = fixture()
        report.evidence_contract = False
        report.commerce_point_order_issues = ["Q4: the written order of five points is unresolved."]
        issues = check_copy(report, ocr_pages=ocr)
        held = release_blocking(issues, "commerce")
        self.assertIn("commerce-point-order-unverified", {i.code for i in held})
        self.assertEqual(release_blocking(issues, "school"), [])

    def test_marks_away_from_their_evidence_are_held(self):
        source, report, ocr, _ = fixture()
        pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        row = next(r for r in report.render_manifest if r["role"] == "step")
        row["anchor_bbox"] = [20, 600, 50, 620]
        issues = check_render_manifest(report, pdf)
        self.assertIn("mark-away-from-evidence",
                      {i.code for i in release_blocking(issues, "commerce")})

    def test_step_circle_sum_disagreeing_with_question_total_is_held(self):
        source, report, ocr, _ = fixture()
        pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        row = next(r for r in report.render_manifest if r["role"] == "step")
        row["score"] = 2
        issues = check_render_manifest(report, pdf)
        self.assertIn("step-awards-do-not-reconcile",
                      {i.code for i in release_blocking(issues, "commerce")})

    def test_verified_ledger_section_keeps_its_question_total(self):
        source, report, ocr, items = section_fixture()
        apply_verified_anchors(report, items, ocr, include_awards=True, selected_qids=None)
        # Its checking plan routes a ledger to the legacy renderer automatically.
        with patch.object(pr, "TEACHER_LAYOUT", True):
            pdf = pr.build_evaluated_pdf(source, "copy.pdf", [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(release_blocking(check_copy(report, pdf_bytes=pdf, ocr_pages=ocr),
                                         "commerce"), [])
        self.assertTrue(any(r.get("question_total") for r in report.render_manifest))
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            margins = " ".join(doc[int(p) - 1].get_textbox(fitz.Rect(layout["margin_bbox"]))
                               for p, layout in report.render_page_layouts.items())
        self.assertIn("2/2", "".join(margins.split()))


if __name__ == "__main__":
    unittest.main()
