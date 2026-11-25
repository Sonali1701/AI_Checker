# -*- coding: utf-8 -*-
"""The exam category the operator picks reaches every place that decides by it:
the grading config, the free marks readings, the marks context, the exam package
department, the job record, the budget, the mark size and the second examiner.

Before categories, an explicit exam type was ignored on the free marks path
(detect_marks_scheme with cfg=None fell back to guessing from the class text), and
the operator could not say "this is a Vidyapeeth paper" at all.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import categories  # noqa: E402
import costs  # noqa: E402
import pipeline  # noqa: E402


def cfg(category="", exam_type="", cls="Class 12", subject="Chemistry"):
    return pipeline.cfg_from_request("claude", "", subject, google_token="", task_id="",
                                     student_class=cls, exam_type=exam_type, category=category)


def src(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


class ConfigCarriesTheCategory(unittest.TestCase):
    def test_explicit_category(self):
        c = cfg("vidyapeeth", cls="Class 9", subject="Social Science")
        self.assertEqual((c.category, c.category_source, c.exam_type), ("vidyapeeth", "explicit", "school"))

    def test_explicit_category_beats_the_class_text(self):
        self.assertEqual(cfg("ca", cls="Class 9", subject="Economics").exam_type, "professional")
        self.assertEqual(cfg("school", cls="CA Inter", subject="Taxation").exam_type, "school")

    def test_auto_detection_is_recorded(self):
        c = cfg(cls="CA Inter", subject="Taxation")
        self.assertEqual((c.category, c.category_source, c.exam_type), ("ca", "auto", "professional"))
        self.assertEqual(cfg(cls="Class 11", subject="Accountancy").category, "commerce")

    def test_unknown_category_is_an_error(self):
        with self.assertRaises(categories.UnknownCategory):
            cfg("engineering")


class SettingsFollowTheCategory(unittest.TestCase):
    def test_a_commerce_stream_english_paper_gets_the_commerce_settings(self):
        c = cfg("commerce", cls="Class 12", subject="English")
        self.assertEqual(pipeline.mark_scale_for(c.exam_type, "English", c.category), 0.8)
        self.assertEqual(costs.budget_for(None, exam_type=c.exam_type, subject="English",
                                          category=c.category), 18.0)
        self.assertTrue(pipeline.moderation_applies(c, "English"))

    def test_ca_settings(self):
        c = cfg("ca", cls="Class 12", subject="Accounting")
        self.assertEqual(pipeline.mark_scale_for(c.exam_type, "Accounting", c.category), 0.6)
        self.assertTrue(pipeline.moderation_applies(c, "Accounting"))

    def test_vidyapeeth_settings_equal_school_today(self):
        vp, sch = cfg("vidyapeeth", subject="Physics"), cfg("school", subject="Physics")
        for c in (vp, sch):
            self.assertEqual(pipeline.mark_scale_for(c.exam_type, "Physics", c.category), 1.0)
            self.assertEqual(costs.budget_for(None, exam_type=c.exam_type, subject="Physics",
                                              category=c.category), 12.0)
            self.assertFalse(pipeline.moderation_applies(c, "Physics"))

    def test_budget_policy_takes_the_category(self):
        from budgeting import BudgetPolicy
        policy = BudgetPolicy.for_copy(total_marks=80, exam_type="school", subject="English",
                                       category="commerce")
        self.assertEqual((policy.cap_inr, policy.target_inr), (18.0, 17.0))

    def test_the_budget_override_still_wins(self):
        os.environ["AICHECKER_BUDGET_INR"] = "40"
        try:
            self.assertEqual(costs.budget_for(None, category="school"), 40.0)
        finally:
            del os.environ["AICHECKER_BUDGET_INR"]


class ServerWiring(unittest.TestCase):
    def test_category_helper(self):
        import server
        from fastapi import HTTPException
        self.assertEqual(server._category_or_400("", "professional"), ("", "professional"))
        self.assertEqual(server._category_or_400("vp", ""), ("vidyapeeth", "school"))
        self.assertEqual(server._category_or_400("CA", "school"), ("ca", "professional"))
        for bad in ("engineering", "design"):
            with self.assertRaises(HTTPException) as ctx:
                server._category_or_400(bad, "")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_config_lists_the_categories(self):
        import server
        ids = [c["id"] for c in server.config()["categories"]]
        self.assertEqual(sorted(ids), sorted(categories.REGISTRY))

    def test_marks_reading_is_bound_to_the_category(self):
        import server
        a = json.loads(server._marks_extraction_context(cfg("vidyapeeth"), "Class 12", "Chemistry"))
        b = json.loads(server._marks_extraction_context(cfg("school"), "Class 12", "Chemistry"))
        self.assertEqual((a["category"], b["category"]), ("vidyapeeth", "school"))

    def test_every_grading_endpoint_accepts_the_category(self):
        text = src("server.py")
        self.assertEqual(text.count('    category: str = Form(""),'), 5)
        self.assertEqual(text.count("category, exam_type = _category_or_400(category, exam_type)"), 5)

    def test_the_free_marks_readings_use_the_chosen_exam_type(self):
        text = src("server.py")
        for marker in ('_log_error("pre-queue deterministic marks validation failed", exc)',
                       '_log_error("marks-scheme deterministic extraction failed", exc)'):
            block = text[:text.index(marker)]
            block = block[block.rindex("detect_marks_scheme("):]
            self.assertIn("exam_type=cfg.exam_type", block)

    def test_detect_marks_scheme_honours_an_exam_type_without_a_config(self):
        import inspect
        self.assertIn("exam_type", inspect.signature(pipeline.detect_marks_scheme).parameters)

    def test_only_an_explicit_category_sets_the_package_department(self):
        text = src("server.py")
        self.assertIn('department=(categories.get(cfg.category).department\n'
                      '                        if cfg.category and cfg.category_source == "explicit" else ""),',
                      text.replace("\r\n", "\n"))
        import exam_package
        import inspect
        self.assertIn("department", inspect.signature(exam_package.build_exam_package).parameters)

    def test_the_job_records_its_category(self):
        text = src("server.py")
        self.assertIn('"category": cfg.category,', text)


class EachCategoryOwnsItsRules(unittest.TestCase):
    """A rule written into one category's file reaches that category's papers only."""

    def _with(self, cid, **changes):
        from dataclasses import replace
        original = categories.REGISTRY[cid]
        patched = original
        for field, value in changes.items():
            patched = replace(patched, **{field: value})
        categories.REGISTRY[cid] = patched
        self.addCleanup(categories.REGISTRY.__setitem__, cid, original)

    def test_a_category_rule_reaches_only_its_own_prompt(self):
        import grader
        from dataclasses import replace
        rule = "1. Read the Hint and Solution PDF's final boxed answer as the key."
        self._with("vidyapeeth", prompt=replace(categories.REGISTRY["vidyapeeth"].prompt, extra_rules=rule))
        vp = grader.build_system_prompt("Physics", "Class 9", "school", category="vidyapeeth")
        school = grader.build_system_prompt("Physics", "Class 9", "school", category="school")
        self.assertIn(rule, vp)
        self.assertNotIn(rule, school)
        self.assertEqual(school, grader.build_system_prompt("Physics", "Class 9", "school"))

    def test_only_ca_commerce_and_vidyapeeth_have_rules_of_their_own(self):
        # Build 85: the CA live check (2026-10-06) and the commerce SME review
        # (2026-10-05). School, Vidyapeeth and design papers are unchanged.
        with_rules = {c.id for c in categories.REGISTRY.values() if c.prompt.extra_rules}
        self.assertEqual(with_rules, {"ca", "commerce", "vidyapeeth", "teaching", "innovation",
                                      "gurukul"})
        for c in categories.REGISTRY.values():
            if c.id == "commerce":
                self.assertIn("printed-award-mismatch", c.review.extra_hold_codes)
                self.assertIn("commerce-point-order-unverified", c.review.extra_hold_codes)
            elif c.id == "vidyapeeth":
                self.assertEqual(c.review.extra_hold_codes, frozenset({
                    "mcq-without-key-letter", "mark-on-another-row"}))
            elif c.id == "ca":     # the MCQ shared-row fixes of the 29 Oct 2026 SME sheets
                self.assertEqual(c.review.extra_hold_codes, frozenset({
                    "mark-on-another-row", "objective-answers-share-a-row"}))
            else:
                self.assertEqual(c.review.extra_hold_codes, frozenset(), c.id)
            self.assertEqual(c.review.warn_only_codes,
                             frozenset({"citation-credited-not-given"}) if c.id == "ca"
                             else frozenset(), c.id)
        # School of Innovation (13 Oct 2026): typed submissions, no faculty marks to
        # calibrate against yet, so every copy is a first draft for the reviewer.
        # Gurukul diagnostic tests (20 Nov 2026) likewise, until the teachers' marks.
        self.assertEqual({c.id for c in categories.REGISTRY.values()
                          if not c.review.auto_release}, {"ca", "design", "innovation", "gurukul"})

    def test_a_category_hold_reaches_the_release_gate(self):
        import preflight
        from dataclasses import replace
        self._with("commerce", review=replace(categories.REGISTRY["commerce"].review,
                                              extra_hold_codes=frozenset({"ledger-unbalanced"}),
                                              warn_only_codes=frozenset({"map-needs-teacher"})))
        issue = preflight.Issue(severity="block", code="ledger-unbalanced", detail="x")
        map_issue = preflight.Issue(severity="block", code="map-needs-teacher", detail="y")
        self.assertEqual(preflight.release_blocking([issue, map_issue], "commerce"), [issue])
        self.assertEqual(preflight.release_blocking([issue, map_issue], "school"), [map_issue])
        self.assertEqual(preflight.release_blocking([issue, map_issue]), [map_issue])
        self.assertEqual(preflight.advisory([issue, map_issue], "commerce"), [map_issue])

    def test_the_grader_and_release_decision_receive_the_category(self):
        import inspect
        import grader
        import strict_grading
        self.assertIn("category", inspect.signature(grader.grade_answer_sheet).parameters)
        self.assertIn("category", inspect.signature(strict_grading.automatic_release_decision).parameters)
        text = src("pipeline.py")
        self.assertIn("        cache_ttl=cfg.cache_ttl,\n        category=_category_of(cfg),\n", text)
        self.assertIn("blocked = release_blocking(issues, _category_of(cfg))", text)

    def test_design_release_follows_its_category(self):
        import design_grading
        self.assertFalse(categories.get("design").review.auto_release)
        if not os.environ.get("AICHECKER_DESIGN_RELEASE"):
            self.assertFalse(design_grading.DESIGN_AUTO_RELEASE)


class UserInterface(unittest.TestCase):
    def test_one_category_select(self):
        html = src(os.path.join("static", "index.html"))
        self.assertIn('id="paperType"', html)
        self.assertNotIn('id="examType"', html)
        for cid in categories.REGISTRY:
            self.assertIn(f'<option value="{cid}">', html)
        self.assertIn('<option value="">Auto-detect', html)

    def test_the_form_sends_the_category(self):
        js = src(os.path.join("static", "app.js"))
        self.assertIn('fd.append("category", category);', js)
        self.assertNotIn('$("examType")', js)
        self.assertIn('category: $("paperType").value', js)


if __name__ == "__main__":
    unittest.main()
