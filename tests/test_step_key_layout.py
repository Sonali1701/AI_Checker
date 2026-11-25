"""A dense digital solution's detached marks must retain their visual context."""
import sys
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import pipeline
from rubrics import KEY_LAYOUT_NOTE, key_requires_visual_layout
from test_coverage_recovery import fixture


def annotated_key():
    # Like the reported PDF: red labels are separate PDF objects added after prose.
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Q1(a) External confirmations")
        for i in range(5):
            page.insert_textbox(fitz.Rect(40, 70 + i * 120, 470, 150 + i * 120),
                ("Required audit procedure and its application to the facts. " * 4), fontsize=10)
        for i in range(5):
            page.insert_text((480, 110 + i * 120), "1 Marks", color=(1, 0, 0))
        return doc.tobytes(), page.get_pixmap().tobytes("png")


class StepKeyLayoutTests(unittest.TestCase):
    def test_dense_pdf_with_five_separate_red_allocations_keeps_images(self):
        source, png = annotated_key()
        text = grader.pdf_text_layer(source, "solution.pdf")
        self.assertGreater(len(text), pipeline._TEXT_CHARS_PER_PAGE)
        self.assertEqual(text.count("1 Marks"), 5)
        self.assertTrue(pipeline._prefer_text_over_images([png], text)[1])
        images, prepared, dropped = pipeline._prepare_answer_key([png], None, text)
        self.assertFalse(dropped)
        self.assertEqual(images, [png])
        self.assertIn(text, prepared)
        self.assertIn(KEY_LAYOUT_NOTE, prepared)

    def test_group_labels_and_fractional_allocations_require_layout(self):
        for label in ("1 marks\neach point", "1 Marks all points", "0.5 marks",
                      "2 marks", "Explain the following (1 mark each point)"):
            with self.subTest(label=label):
                self.assertTrue(key_requires_visual_layout("Solution prose. " * 70 + "\n" + label))

    def test_plain_prose_still_uses_text_and_sparse_keys_keep_images(self):
        text = "Award credit for the correct explanation. " * 70
        self.assertEqual(pipeline._prepare_answer_key([b"image"], None, text), ([], text.strip(), True))
        images, prepared, dropped = pipeline._prepare_answer_key([b"image"], None, "short")
        self.assertEqual(images, [b"image"])
        self.assertFalse(dropped)
        self.assertIn("short", prepared)
        self.assertEqual(pipeline._prepare_answer_key([], "Explicit rubric", "PDF prose"),
                         ([], "Explicit rubric", False))

    def test_the_verifier_receives_key_images_separate_from_student_evidence(self):
        _, png = annotated_key()
        report = grader.GradeReport(questions=[], total_score=0, max_total=0, overall_remarks="")
        with patch.object(grader, "_claude_call", return_value={
                "content": [{"type": "text", "text": "[]"}], "usage": {}}) as call:
            grader.audit_deductions(report, google_token="test", task_id="test",
                answer_key_pngs=[png], student_evidence_pngs=[png])
        parts = call.call_args.kwargs["request"]["messages"][0]["content"]
        image_indices = [i for i, part in enumerate(parts) if part.get("type") == "image"]
        self.assertEqual(len(image_indices), 2)
        self.assertIn(KEY_LAYOUT_NOTE, parts[image_indices[0] - 1]["text"])
        self.assertIn("STUDENT ANSWER PAGE", parts[image_indices[1] - 1]["text"])
        self.assertEqual(call.call_args.kwargs["count"], 2)

    def test_recovery_reservation_includes_retained_key_images(self):
        _, images, _, _, report, cfg, _ = fixture()
        common = dict(student_pngs=images, qp_text="Paper", answer_key_text="Key")
        base = pipeline._coverage_verification_reserve(report, [], cfg=cfg,
                                                      common=common, usd_to_inr=85)
        with_key = pipeline._coverage_verification_reserve(report, [], cfg=cfg,
            common={**common, "answer_key_pngs": [images[0]] * 11}, usd_to_inr=85)
        self.assertGreater(with_key, base)

    def test_scanned_booklet_verification_stays_below_request_limit(self):
        buffer = io.BytesIO()
        Image.effect_noise((1000, 1400), 24).convert("RGB").save(buffer, format="PNG")
        png = buffer.getvalue()
        self.assertGreater(len(png) * 28 * 4 / 3, 32_000_000)
        report = grader.GradeReport(questions=[], total_score=0, max_total=0, overall_remarks="")
        with patch.object(grader, "_claude_call", return_value={
                "content": [{"type": "text", "text": "[]"}], "usage": {}}) as call:
            grader.audit_deductions(report, google_token="test", task_id="test",
                answer_key_pngs=[png] * 11, student_evidence_pngs=[png] * 17)
            payload = json.dumps(call.call_args.kwargs["request"])
            self.assertLess(len(payload.encode("utf-8")), 32_000_000)
            self.assertEqual(payload.count("image/jpeg"), 28)
            self.assertEqual(call.call_args.kwargs["count"], 28)


if __name__ == "__main__":
    unittest.main()
