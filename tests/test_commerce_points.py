"""Commerce feedback: first live points, concise credit, and source-bound review."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import categories
import grader
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark
from mathpix import LineBox, PageOCR
from rubrics import any_n_marks_contract


def fixture(*, question="Explain any two advantages of planning.", awards=(0, 1, 1),
            maxima=(1, 1, 0), cancelled=()):
    lines = [LineBox("P1L1", "Q5", (0, 0, 500, 20), 1)]
    lines += [LineBox(f"P1L{i+2}", f"{i+1}) Student point {i+1}",
                      (0, 40 + i*40, 500, 60 + i*40), 1, cancelled=i in cancelled)
              for i in range(3)]
    criteria = [Criterion(description=f"Point {i+1}", max=mx, awarded=score,
                           met=mx > 0 and score == mx, step_line_id=f"P1L{i+2}")
                for i, (score, mx) in enumerate(zip(awards, maxima))]
    q = QuestionMark(qid="Q5", score=sum(awards), max_score=2, page=1,
                     y_fraction=.2, criteria=criteria)
    report = GradeReport(total_score=sum(awards), max_total=2, overall_remarks="", questions=[q])
    report.answer_regions = {"q5": {"line_ids": [line.line_id for line in lines],
                                    "label_line_ids": ["P1L1"]}}
    package = NS(department="commerce", subject="Business Studies",
                 questions=[NS(qid="Q5", prompt_text=question, question_type="theory")])
    return report, [PageOCR(1, 600, 800, lines)], package


class CommercePromptPolicy(unittest.TestCase):
    def test_first_n_contract_is_scoped_and_preserves_best_or(self):
        for subject in ("Business Studies", "Economics", "BST", "OCM", "SP"):
            prompt = grader.build_system_prompt(subject, "Class 12", "school", category="commerce")
            self.assertIn("FIRST N live points", prompt)
            self.assertNotIn("credit the N BEST valid points", prompt)
            self.assertIn("Never deduct solely because a correct point is brief", prompt)
            self.assertNotIn("Brief wording alone costs", prompt)
            contract = any_n_marks_contract("commerce", subject)
            self.assertIn("FIRST N live points", contract)
            self.assertNotIn("Choose the N best valid attempted points", contract)
        self.assertEqual(categories.get("commerce").choice.selection_policy, "best_score")
        for category, subject in (("school", "Economics"), ("ca", "Auditing"),
                                  ("commerce", "English")):
            self.assertIn("Choose the N best valid attempted points",
                          any_n_marks_contract(category, subject))

    def test_provider_checklist_and_recovery_use_the_scoped_contract(self):
        scheme = MarksScheme(total=2, items=[MarksItem(qid="Q5", max=2)])
        for requested in (None, ["Q5"]):
            text, _ = grader.grading_coverage_block(scheme, requested,
                                                  category="commerce", subject="BST")
            self.assertIn("FIRST N live points", text)
            self.assertNotIn("Choose the N best valid attempted points", text)
        other, _ = grader.grading_coverage_block(scheme)
        self.assertIn("Choose the N best valid attempted points", other)

    def test_second_examiner_receives_complete_feedback_recheck(self):
        report, _, _ = fixture()
        report.commerce_feedback_verification_reasons = {
            "Q5": ["Verify the first two points in writing order."],
            "Q6": ["This unrelated question is not selected."],
        }
        report.commerce_point_order_issues = ["Q5: Verify first-N source order.",
                                              "Q6: Verify an unrelated point group."]
        prompt = grader.build_audit_prompt(report, "official key", "live transcript",
                                          include_awards=True, selected_qids={grader._norm_qid("Q5")},
                                          category_rules=categories.get("commerce").prompt.extra_rules)
        self.assertIn("Verify the first two points in writing order", prompt)
        self.assertIn("Read the original page images", prompt)
        self.assertIn("complete verified correction", prompt)
        self.assertIn("FIRST N", prompt)
        self.assertIn("FIRST-N ORDER CERTIFICATE Q5", prompt)
        self.assertIn("commerce_point_order_verified=true on EVERY requested mark row", prompt)
        self.assertNotIn("FIRST-N ORDER CERTIFICATE Q6", prompt)
        self.assertNotIn("This unrelated question", prompt)

    def test_server_review_fields_are_not_in_provider_schema(self):
        schema = grader._grade_report_output_schema()
        self.assertNotIn("commerce_feedback_verification_reasons", schema["properties"])
        self.assertNotIn("commerce_point_order_issues", schema["properties"])
        self.assertNotIn("verified_commerce_point_order_qids", schema["properties"])

    def test_only_explicit_json_true_counts_as_an_audit_point_order_confirmation(self):
        values = [None, False, "true", 1, True]
        items = grader.parse_audit(json.dumps({"items": [
            {"row": i, "correct": 1, "evidence_status": "verified",
             **({"commerce_point_order_verified": value} if value is not None else {})}
            for i, value in enumerate(values)]}))
        self.assertEqual([item.commerce_point_order_verified for item in items],
                         [False, False, False, False, True])

    def test_first_examiner_cannot_fabricate_point_order_certificate(self):
        report, _, _ = fixture()
        data = report.model_dump()
        data["verified_commerce_point_order_qids"] = ["Q5"]
        parsed = grader.parse_grade_report(json.dumps(data))
        self.assertEqual(parsed.verified_commerce_point_order_qids, [])

    def test_maths_recheck_and_small_correct_answer_rules(self):
        prompt = grader.build_system_prompt("Applied Mathematics", "Class 12", "school",
                                            category="commerce")
        for phrase in ("powers, subscripts", "recompute the exact live expression",
                       "verified corrected expression", "at most one mark", "Cancelled attempts"):
            self.assertIn(phrase, prompt)


class FirstPointEvidenceGuard(unittest.TestCase):
    def test_later_correct_point_cannot_replace_first_wrong_point(self):
        report, pages, package = fixture(maxima=(0, 1, 1))
        before = report.model_dump()
        issues = grader.enforce_commerce_point_order(report, pages, package, category="commerce")
        self.assertEqual(issues[0]["code"], "commerce-point-order-unverified")
        self.assertIn("Point 3 was scored", issues[0]["detail"])
        self.assertEqual(report.total_score, before["total_score"])
        self.assertEqual([(c.max, c.awarded) for c in report.questions[0].criteria],
                         [(0, 0), (1, 1), (1, 1)])
        self.assertTrue(report.commerce_point_order_issues)
        self.assertTrue(report.commerce_feedback_verification_reasons["Q5"])

    def test_wrong_first_point_is_checked_and_extra_point_is_not_scored(self):
        report, pages, package = fixture(awards=(0, 1, 0))
        self.assertEqual(grader.enforce_commerce_point_order(report, pages, package), [])
        self.assertEqual([c.step_line_id for c in report.questions[0].criteria], ["P1L2", "P1L3"])
        self.assertEqual(report.total_score, 1)

    def test_missing_first_point_decision_requires_review(self):
        report, pages, package = fixture(awards=(0, 1, 0))
        report.questions[0].criteria = report.questions[0].criteria[1:]
        issues = grader.enforce_commerce_point_order(report, pages, package)
        self.assertIn("no grading decision", issues[0]["detail"])

    def test_unreadable_list_and_unbound_anchor_require_review(self):
        report, pages, package = fixture()
        for line in pages[0].lines[1:]:
            line.text = "Paragraph without a list boundary"
        self.assertTrue(grader.enforce_commerce_point_order(report, pages, package))
        report, pages, package = fixture(awards=(0, 1, 0))
        report.questions[0].criteria[0].step_line_id = "P99L1"
        self.assertTrue(grader.enforce_commerce_point_order(report, pages, package))

    def test_multiple_required_groups_are_not_collapsed(self):
        report, pages, package = fixture(question="Give two advantages and two disadvantages.")
        issues = grader.enforce_commerce_point_order(report, pages, package)
        self.assertIn("multiple point groups", issues[0]["detail"])

    def test_explicit_image_certificate_resolves_source_group_ambiguity(self):
        for prompt, change in (
            ("Give two advantages and two disadvantages.", lambda pages: None),
            ("Explain any two advantages of planning.",
             lambda pages: setattr(pages[0].lines[2], "text", "(i) A nested explanation")),
            ("Explain any two advantages of planning.",
             lambda pages: [setattr(line, "text", "Unnumbered handwritten point")
                            for line in pages[0].lines[1:]]),
            ("Explain any two advantages of planning.",
             lambda pages: setattr(pages[0].lines[2], "text", "1) Restarted explanation")),
        ):
            with self.subTest(prompt=prompt):
                report, pages, package = fixture(question=prompt, awards=(0, 1, 0))
                change(pages)
                self.assertTrue(grader.enforce_commerce_point_order(report, pages, package))
                report.verified_commerce_point_order_qids = ["5"]
                self.assertEqual(grader.enforce_commerce_point_order(report, pages, package), [])
                self.assertEqual(report.commerce_point_order_issues, [])

    def test_image_certificate_cannot_override_concrete_later_point_scoring(self):
        report, pages, package = fixture(maxima=(0, 1, 1))
        report.verified_commerce_point_order_qids = ["Q5"]
        self.assertIn("Point 3 was scored", grader.enforce_commerce_point_order(
            report, pages, package)[0]["detail"])

    def test_image_certificate_does_not_invent_official_count_or_answer_ownership(self):
        for prompt, reason in (("", "unavailable"),
                               ("Discuss any two staffing considerations.", "cannot be safely resolved"),
                               ("Give zero advantages and two disadvantages.", "invalid")):
            # A numeric invalid count must still be rejected even alongside a valid group.
            if "zero" in prompt:
                prompt = prompt.replace("zero", "0")
            report, pages, package = fixture(question=prompt)
            report.verified_commerce_point_order_qids = ["Q5"]
            self.assertIn(reason, grader.enforce_commerce_point_order(report, pages, package)[0]["detail"])
        report, pages, package = fixture(question="Give two advantages and two disadvantages.")
        report.verified_commerce_point_order_qids = ["Q5"]
        report.answer_regions = {}
        self.assertTrue(grader.enforce_commerce_point_order(report, pages, package))

    def test_nested_labels_are_not_mistaken_for_separate_top_level_points(self):
        report, pages, package = fixture(awards=(0, 1, 0))
        pages[0].lines[2].text = "(i) A nested explanation"
        self.assertIn("nested points", grader.enforce_commerce_point_order(
            report, pages, package)[0]["detail"])

    def test_cancelled_work_never_defines_the_live_point_order(self):
        report, pages, package = fixture(awards=(0, 1, 1), maxima=(0, 1, 1), cancelled=(0,))
        report.questions[0].criteria = report.questions[0].criteria[1:]
        self.assertEqual(grader.enforce_commerce_point_order(report, pages, package), [])

    def test_school_and_ca_keep_their_existing_point_policy(self):
        for category in ("school", "ca"):
            report, pages, package = fixture()
            before = report.model_dump()
            self.assertEqual(grader.enforce_commerce_point_order(report, pages, package,
                                                                category=category), [])
            self.assertEqual(report.model_dump(), before)

    def test_uncapped_question_is_not_given_an_invented_count(self):
        report, pages, package = fixture(question="Explain the importance of planning.")
        self.assertEqual(grader.enforce_commerce_point_order(report, pages, package), [])

    def test_unsupported_counted_requirement_is_held_instead_of_guessed(self):
        report, pages, package = fixture(question="Discuss any two staffing considerations.")
        self.assertIn("cannot be safely resolved", grader.enforce_commerce_point_order(
            report, pages, package)[0]["detail"])


class CommerceAliasRouting(unittest.TestCase):
    def test_theory_abbreviations_route_by_class_without_substring_false_positives(self):
        for subject in ("BST", "OCM", "SP", "Secretarial Practice"):
            self.assertEqual(categories.resolve(student_class="Class 12", subject=subject).id,
                             "commerce")
            self.assertEqual(categories.resolve(student_class="Class 9", subject=subject).id,
                             "school")
        self.assertFalse(categories.subjects.is_commerce_subject("Spanish"))

    def test_maths_needs_explicit_commerce_stream_or_operator_category(self):
        for subject in ("Maths", "Core Mathematics", "Applied Mathematics"):
            self.assertEqual(categories.resolve(student_class="Class 12 Commerce", subject=subject).id,
                             "commerce")
            self.assertEqual(categories.resolve(student_class="Class 12", subject=subject).id,
                             "school")
            self.assertEqual(categories.resolve(category="commerce", student_class="Class 12",
                                                 subject=subject).id, "commerce")
            self.assertEqual(categories.resolve(category="school", student_class="Class 12 Commerce",
                                                 subject=subject).id, "school")


if __name__ == "__main__":
    unittest.main()
