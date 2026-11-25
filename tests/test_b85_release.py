# -*- coding: utf-8 -*-
"""Build 85: what the live CA check of 2026-10-06 and the commerce SME review of
2026-10-05 changed in the release rule, the cover remark and the marking rules.

- CA: 5 of 14 copies within 3 marks of a careful examiner, five released copies 4.5
  to 12 marks out. Every CA copy now goes to faculty review, marked, with its PDF.
- CA: a missing section number never costs marks (SME, 2026-09-23), so credit for a
  provision stated without its number no longer holds a copy.
- CA: all 14 cover remarks were the code's one line fallback; the SMEs asked for an
  overall remark with a sentence on presentation and exam writing on every copy.
- CA: the "not counted" note told a student the BEST answers were counted beside a
  better answer the first attempted rule had left out.
- Commerce: brief but correct Economics and Business Studies points keep their marks,
  and an account drawn out of format earns nothing. The second examiner gets the same
  rules, or it raises what they take away.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import categories  # noqa: E402
import grader  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
from answer_map import AnswerLocation, AnswerMap  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from strict_grading import reconcile_report_to_package  # noqa: E402
from wording import uncounted_note  # noqa: E402


def _cfg(student_class, subject, exam_type=""):
    return pipeline.cfg_from_request("claude", "", subject, google_token="", task_id="",
                                     student_class=student_class, exam_type=exam_type)


class CategoryReleaseRule(unittest.TestCase):
    def test_every_ca_copy_goes_to_faculty_review_with_the_reason(self):
        cfg = _cfg("CA Inter", "Auditing")
        self.assertEqual(cfg.category, "ca")
        reason = pipeline._category_review_hold(cfg)
        self.assertIn("faculty review", reason)

    def test_school_and_commerce_copies_still_release_automatically(self):
        for cls, subject in (("Class 12", "Accountancy"), ("Class 10", "Physics")):
            self.assertIsNone(pipeline._category_review_hold(_cfg(cls, subject)))
        self.assertIsNone(pipeline._category_review_hold(pipeline.ProviderConfig()))

    def test_a_provision_without_its_section_number_does_not_hold_a_ca_copy(self):
        self.assertNotIn("citation-credited-not-given", preflight.hold_codes("ca"))
        self.assertIn("citation-credited-not-given", preflight.hold_codes("school"))
        issue = preflight.Issue("block", "citation-credited-not-given", "Q1.b step paid")
        self.assertEqual(preflight.release_blocking([issue], "ca"), [])
        self.assertEqual(preflight.advisory([issue], "ca"), [issue])


class UncountedChoiceNote(unittest.TestCase):
    def test_the_note_names_the_rule_that_left_the_answer_out(self):
        self.assertEqual(uncounted_note({"rule": "first_attempted"}),
                         "Only the answers attempted first in this section were counted.")
        self.assertEqual(uncounted_note({"rule": "best_score"}),
                         "The best answers in this section were counted.")
        self.assertEqual(uncounted_note({}), "The best answers in this section were counted.")

    def test_a_ca_paper_records_the_first_attempted_rule(self):
        from test_nested_choices import SCHEME, make_package, make_report
        package = make_package()
        order = ([row.qid for row in SCHEME.items if row.qid.startswith("Q1.")]
                 + ["Q6.c2", "Q6.a", "Q6.b", "Q6.c1.i", "Q6.c1.ii"]
                 + [row.qid for row in SCHEME.items
                    if row.qid.startswith(("Q2.", "Q3.", "Q4.", "Q5."))])
        answer_map = AnswerMap(package_id=package.package_id, locations=tuple(
            AnswerLocation(qid=qid, status="located", first_position=(1, i + 1))
            for i, qid in enumerate(order)))
        result = reconcile_report_to_package(make_report(), package, answer_map=answer_map)
        rules = {str(x.get("qid")): x.get("rule") for x in result.uncounted_choice}
        self.assertTrue(rules)
        self.assertTrue(any(qid.startswith("Q5.") for qid in rules))
        self.assertEqual(set(rules.values()), {"first_attempted"})

    def test_the_page_says_the_first_answers_were_counted(self):
        from test_teacher_layout import _build, _c, _q, _report, _sheet
        rows = [("P1L1", "Ans 3(a) Section 117 compensation", 60, 400),
                ("P1L2", "Ans 4(b) holder in due course", 60, 400)]
        doc, ocr = _sheet(rows)
        report = _report([_q("Q3.a", 3, 6, 1, "P1L1", [_c(3, 6, "P1L1")])])
        report.uncounted_choice = [{"qid": "Q4.b", "page": 1, "anchor_line_id": "P1L2",
                                    "rule": "first_attempted"}]
        out, _ = _build(doc, ocr, report)
        text = " ".join(out[0].get_text().split())
        self.assertIn("not counted", text)
        self.assertIn("attempted first", text)
        self.assertNotIn("best answers", text)


def _report_with(questions):
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions),
                       overall_remarks="", questions=questions)


class CoverRemark(unittest.TestCase):
    def _compose(self, report, professional, reply=None, fail=False):
        sent = {}

        def fake_call(token, **kwargs):
            if fail:
                raise RuntimeError("proxy down")
            sent.update(kwargs)
            return {"reply": reply}

        with patch.object(grader, "_claude_call", fake_call), \
                patch.object(grader, "_claude_text", lambda response: response["reply"]), \
                patch.object(grader, "_claude_usage", lambda response, model: {}), \
                patch.object(grader, "record_usage_event", lambda *a, **k: None):
            text = grader._compose_overall_remarks(
                report, model="m", google_token="", task_id="t", filename="f",
                subject="Auditing", usage={}, professional=professional)
        return text, sent

    def test_a_ca_copy_gets_a_remark_even_with_full_marks(self):
        report = _report_with([QuestionMark(qid="Q1.a", score=5, max_score=5, page=1,
                                            y_fraction=.2)])
        text, sent = self._compose(report, True, reply="Well done on the planning answer. "
                                   "Underline the key words of each point.")
        self.assertIn("Underline", text)
        message = sent["request"]["messages"][0]["content"][0]["text"]
        self.assertIn("CA, CMA or CS", message)
        self.assertIn("No marks were lost.", message)

    def test_a_school_copy_with_full_marks_keeps_no_remark(self):
        report = _report_with([QuestionMark(qid="Q1", score=5, max_score=5, page=1,
                                            y_fraction=.2)])
        text, sent = self._compose(report, False, reply="unused")
        self.assertEqual(text, "")
        self.assertEqual(sent, {})

    def test_the_fallback_still_carries_a_presentation_sentence(self):
        report = _report_with([QuestionMark(qid="Q3.b", score=1, max_score=2.5, page=1,
                                            y_fraction=.2,
                                            remark="Conclude that the preconditions are absent.")])
        text, _ = self._compose(report, True, fail=True)
        self.assertIn("preconditions", text)
        self.assertIn("working notes", text)

    def test_the_remark_names_questions_as_the_paper_prints_them(self):
        report = _report_with([QuestionMark(qid="Q3.b", score=1, max_score=2.5, page=1,
                                            y_fraction=.2, remark="Give a conclusion.")])
        _, sent = self._compose(report, True, reply="ok")
        message = sent["request"]["messages"][0]["content"][0]["text"]
        self.assertIn("Q3(b) 1/2.5", message)


class CategoryRulesReachEveryExaminer(unittest.TestCase):
    def test_the_grader_gets_each_category_its_own_rules(self):
        commerce = grader.build_system_prompt("Economics", "Class 12", "school",
                                              category="commerce")
        ca = grader.build_system_prompt("Auditing", "CA Inter", "professional", category="ca")
        school = grader.build_system_prompt("Physics", "Class 10", "school", category="school")
        self.assertIn("Economics and Business Studies theory", commerce)
        self.assertIn("Accountancy format", commerce)
        self.assertNotIn("Accountancy format", ca)
        self.assertIn("Case based theory", ca)
        self.assertNotIn("RULES FOR THIS EXAM CATEGORY", school)

    def test_the_second_examiner_gets_them_too(self):
        report = _report_with([QuestionMark(qid="Q13", score=2, max_score=6, page=1,
                                            y_fraction=.2, remark="Format not followed.")])
        rules = categories.get("commerce").prompt.extra_rules
        prompt = grader.build_audit_prompt(report, "key", "transcript", category_rules=rules)
        self.assertIn("RULES FOR THIS EXAM CATEGORY", prompt)
        self.assertIn("Accountancy format", prompt)
        self.assertLess(prompt.index("Accountancy format"),
                        prompt.index("Return the verdicts as JSON"))
        self.assertNotIn("RULES FOR THIS EXAM CATEGORY",
                         grader.build_audit_prompt(report, "key", "transcript"))

    def test_the_pipeline_passes_rules_only_where_a_category_has_them(self):
        commerce = pipeline._category_rules_kwarg(_cfg("Class 12", "Accountancy"))
        self.assertIn("Accountancy format", commerce["category_rules"])
        self.assertEqual(pipeline._category_rules_kwarg(_cfg("Class 10", "Physics")), {})
        self.assertEqual(pipeline._category_rules_kwarg(pipeline.ProviderConfig()), {})


if __name__ == "__main__":
    unittest.main()
