# -*- coding: utf-8 -*-
"""Exam categories (CA, Commerce 11-12, School board, PW Vidyapeeth, Design).

The user asked for the grading logic to be separated by category because each has
different requirements. These pin the resolver, the per-category settings, and that
the category package reproduces the app's older classifiers exactly, so moving the
callers onto it changes no mark.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import categories  # noqa: E402
from categories import subjects  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MATRIX = [
    ("CA Inter", "Taxation"), ("CA Foundation", "Accounting"), ("CA Final", "FR"),
    ("CMA Inter", "Costing"), ("CA", "Business Laws"), ("CA Group 1", "Audit"),
    ("Class 11", "Accountancy"), ("Class 12", "Business Studies"), ("XII", "Economics"),
    ("12th", "Applied Mathematics"), ("Class 12", "English"), ("Class 9", "Economics"),
    ("Class 9", "Social Science"), ("Class 10", "Physics"), ("Class 12", "Chemistry"),
    ("Class 10", "Hindi"), ("", "Accountancy"), ("", ""), ("Class 8", "Maths"),
    ("Class 11", "Marketing"), ("Class 12", "Banking"), ("Class 12", "Strategic Management"),
    ("Class 7", "Science"), ("Class 12", "Legal Studies"), ("ISC Class 12", "English Literature"),
]


class Resolver(unittest.TestCase):
    def test_explicit_choice_wins_over_everything(self):
        r = categories.resolve(category="vidyapeeth", exam_type="professional",
                               student_class="CA Inter", subject="Taxation")
        self.assertEqual((r.id, r.source), ("vidyapeeth", "explicit"))

    def test_aliases(self):
        for alias, want in (("professional", "ca"), ("CMA", "ca"), ("Board", "school"),
                            ("CBSE", "school"), ("vp", "vidyapeeth"), ("Drawing", "design")):
            self.assertEqual(categories.resolve(category=alias).id, want, alias)

    def test_an_unknown_category_is_an_error_not_a_default(self):
        with self.assertRaises(categories.UnknownCategory):
            categories.resolve(category="engineering")

    def test_legacy_exam_type(self):
        self.assertEqual(categories.resolve(exam_type="professional", student_class="Class 9").id, "ca")
        # An explicit "school" from an old client rules CA out, as it always did.
        forced = categories.resolve(exam_type="school", student_class="CA Inter", subject="Taxation")
        self.assertNotEqual(forced.id, "ca")
        self.assertEqual(forced.category.legacy_exam_type, "school")

    def test_design_paper_type(self):
        self.assertEqual(categories.resolve(paper_type="design").id, "design")

    def test_auto_detection(self):
        cases = {
            ("CA Inter", "Taxation"): "ca", ("CA", "Business Laws"): "ca",
            ("CMA Gr II", "Costing"): "ca",
            ("Class 11", "Business Studies"): "commerce", ("XII", "Accountancy"): "commerce",
            ("", "Accountancy"): "commerce",
            ("Class 9", "Social Science"): "school", ("Class 9", "Economics"): "school",
            ("Class 12", "English"): "school", ("Class 10", "Physics"): "school",
        }
        for (cls, subj), want in cases.items():
            self.assertEqual(categories.resolve(student_class=cls, subject=subj).id, want, (cls, subj))

    def test_vidyapeeth_and_design_are_never_guessed(self):
        for cls, subj in MATRIX:
            self.assertNotIn(categories.resolve(student_class=cls, subject=subj).id,
                             ("vidyapeeth", "design"))
            self.assertNotIn(categories.resolve(student_class="Radiant 2.0 Vidyapeeth " + cls,
                                                subject=subj).id, ("vidyapeeth", "design"))

    def test_stored_jobs(self):
        self.assertEqual(categories.for_job({"category": "vidyapeeth"}).id, "vidyapeeth")
        self.assertEqual(categories.for_job({"mode": "design"}).id, "design")
        self.assertEqual(categories.for_job({"exam_package": {"department": "commerce"}}).id, "commerce")
        self.assertEqual(categories.for_job({"exam_type": "professional"}).id, "ca")
        self.assertEqual(categories.for_job({}).id, "school")
        self.assertEqual(categories.for_job({"category": "gone"}).id, "school")

    def test_ui_choices_list_every_category(self):
        ids = [c["id"] for c in categories.ui_choices()]
        self.assertEqual(sorted(ids), sorted(["ca", "commerce", "school", "vidyapeeth", "design",
                                          "teaching", "innovation", "gurukul"]))
        for choice in categories.ui_choices():
            self.assertNotRegex(choice["hint"], r"[;–—]| - ")


class Settings(unittest.TestCase):
    def test_every_category_is_complete(self):
        for c in categories.REGISTRY.values():
            self.assertIn(c.department, ("school", "commerce", "ca"))
            self.assertIn(c.legacy_exam_type, ("school", "professional"))
            self.assertIn(c.pipeline, ("written", "design"))
            self.assertIn(c.choice.selection_policy, ("first_attempted", "best_score"))
            self.assertGreater(c.cost.budget_inr, 0)
            self.assertGreater(c.render.mark_scale, 0)

    def test_the_differences_between_categories(self):
        ca, com, sch, vp, des = (categories.REGISTRY[k] for k in
                                 ("ca", "commerce", "school", "vidyapeeth", "design"))
        self.assertEqual(ca.choice.selection_policy, "first_attempted")
        self.assertEqual({c.choice.selection_policy for c in (com, sch, vp)}, {"best_score"})
        self.assertTrue(ca.render.whole_total)
        self.assertFalse(ca.marks.try_regex)
        self.assertTrue(com.examiner.always_moderate and ca.examiner.always_moderate)
        self.assertFalse(sch.examiner.always_moderate)
        self.assertFalse(des.review.auto_release)
        self.assertEqual(des.pipeline, "design")
        self.assertEqual(vp.department, "school")

    def test_the_package_imports_nothing_from_the_app(self):
        for name in os.listdir(os.path.join(ROOT, "categories")):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(ROOT, "categories", name), encoding="utf-8").read())
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for mod in mods:
                    self.assertNotIn(mod.split(".")[0], ("grader", "pipeline", "server", "costs",
                                                         "exam_package", "strict_grading"), name)


class SameAsTheOlderClassifiers(unittest.TestCase):
    """Moving callers onto the category package must change nothing."""

    def test_subject_rules(self):
        import costs
        import exam_package
        import grader
        import language_marks
        for cls, subj in MATRIX:
            self.assertEqual(subjects.is_reasoning_subject(subj), grader._is_reasoning_subject(subj), subj)
            self.assertEqual(subjects.is_commerce_subject(subj), grader._is_commerce_subject(subj), subj)
            self.assertEqual(subjects.is_cost_commerce_subject(subj),
                             costs.is_professional_or_commerce(subject=subj), subj)
            self.assertEqual(subjects.is_language_paper(subj), language_marks.is_language_paper(subj), subj)
            for et in ("", "school", "professional"):
                self.assertEqual(subjects.package_department(et, cls, subj),
                                 exam_package.department_for(et, cls, subj), (et, cls, subj))

    def test_ca_detection_matches_the_grader(self):
        import grader
        for cls, subj in MATRIX:
            self.assertEqual(categories.is_ca_text(cls, subj),
                             grader.detect_exam_type(cls, subj) == "professional", (cls, subj))

    def test_settings_match_today_for_every_auto_detected_paper(self):
        import costs
        import pipeline
        for cls, subj in MATRIX:
            for et in ("", "school", "professional"):
                res = categories.resolve(exam_type=et, student_class=cls, subject=subj)
                legacy = pipeline.cfg_from_request("claude", "", subj, google_token="", task_id="",
                                                   student_class=cls, exam_type=et).exam_type
                self.assertEqual(res.category.legacy_exam_type, legacy, (et, cls, subj))
                self.assertEqual(categories.mark_scale(res.category, subj),
                                 pipeline.mark_scale_for(legacy, subj), (et, cls, subj))
                self.assertEqual(categories.budget_inr(res.category, subj),
                                 costs.budget_for(None, exam_type=legacy, subject=subj), (et, cls, subj))
                cfg = pipeline.cfg_from_request("claude", "", subj, google_token="", task_id="",
                                                student_class=cls, exam_type=et)
                self.assertEqual(categories.always_moderate(res.category, subj),
                                 pipeline.moderation_applies(cfg, subj), (et, cls, subj))

    def test_choice_policy_matches_the_package_rule(self):
        from types import SimpleNamespace as NS
        import strict_grading
        for dept in ("ca", "commerce", "school"):
            for printed in ("first_attempted", "best_score"):
                want = strict_grading.effective_selection_policy(
                    NS(selection_policy=printed), NS(department=dept))
                self.assertEqual(categories.selection_policy(None, printed, dept), want)
                self.assertEqual(categories.selection_policy(categories.REGISTRY[dept], printed), want)


if __name__ == "__main__":
    unittest.main()
