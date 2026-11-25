"""Offline commerce regressions for missing/cancelled evidence and examiner routing."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from commerce_feedback import apply_commerce_feedback_guards  # noqa: E402


def criterion(award, maximum, lid, quote="", kind="written", description="Method", evidence=""):
    return NS(awarded=award, max=maximum, met=award >= maximum, step_line_id=lid,
              anchor_quote=quote, evidence_kind=kind, description=description, evidence=evidence)


def question(qid="Q10", criteria=(), *, maximum=None, score=None, anchor="P1L1",
             remark="", objective=False, attempted=True):
    cs = list(criteria)
    return NS(qid=qid, criteria=cs, max_score=(sum(c.max for c in cs) if maximum is None else maximum),
              score=(sum(c.awarded for c in cs) if score is None else score),
              anchor_line_id=anchor, remark=remark, objective=objective, attempted=attempted)


def line(lid, text, *, cancelled=False):
    return NS(line_id=lid, text=text, cancelled=cancelled)


def fixture(questions, lines=(), regions=None):
    qs = list(questions)
    report = NS(questions=qs, total_score=sum(q.score for q in qs),
                max_total=sum(q.max_score for q in qs), answer_regions=regions or {})
    return report, [NS(lines=list(lines))]


def apply(report, pages, subject="Core Maths", category="commerce", **kwargs):
    return apply_commerce_feedback_guards(report, pages, category=category, subject=subject, **kwargs)


class CommerceFeedbackTests(unittest.TestCase):
    def test_cancelled_method_cannot_pay_while_live_steps_keep_their_marks(self):
        cancelled = criterion(1, 1, "P1L1", "x = -2")
        live = criterion(1, 1, "P1L2", "answer = 4", description="Final answer")
        q = question(criteria=[cancelled, live], anchor="P1L2")
        report, pages = fixture([q], [line("P1L1", "x = -2", cancelled=True), line("P1L2", "answer = 4")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        result = apply(report, pages)
        self.assertEqual((cancelled.awarded, live.awarded, q.score, report.total_score), (0, 1, 1, 1))
        self.assertEqual(report.max_total, 2, "an evidence guard must not change the official denominator")
        self.assertEqual(cancelled.evidence_kind, "missing")
        self.assertIn("Q10", result.verification_qids)

    def test_a_final_live_rewrite_preserves_credit_and_replaces_cancelled_anchor(self):
        c = criterion(1, 1, "P1L1", "x = -2")
        q = question(criteria=[c], anchor="P1L2")
        report, pages = fixture([q], [line("P1L1", "x = -2", cancelled=True), line("P1L2", "x = −2")],
                                {"Q10": {"line_ids": ["P1L1", "P1L2"]}})
        result = apply(report, pages)
        self.assertEqual((c.awarded, c.step_line_id, q.score), (1, "P1L2", 1))
        self.assertIn("final live rewrite", result.verification_reasons["Q10"])

    def test_several_live_matches_require_the_examiner_without_deleting_live_credit(self):
        c = criterion(1, 1, "P1L1", "x = -2")
        report, pages = fixture([question(criteria=[c])], [line("P1L1", "x = -2", cancelled=True),
                                line("P1L2", "x = -2"), line("P1L3", "x = -2")],
                                {"q10": {"line_ids": ["P1L1", "P1L2", "P1L3"]}})
        result = apply(report, pages)
        self.assertEqual(c.awarded, 1)
        self.assertEqual(c.step_line_id, "P1L1", "do not guess which live occurrence is final")
        self.assertIn("several live lines", result.verification_reasons["Q10"])

    def test_live_quote_after_question_label_and_before_full_stop_is_not_lost(self):
        c = criterion(1, 1, "P1L1", "answer = 4")
        report, pages = fixture([question(criteria=[c], anchor="P1L2")],
                                [line("P1L1", "answer = 4", cancelled=True),
                                 line("P1L2", "Q10 answer = 4.")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        apply(report, pages)
        self.assertEqual((c.awarded, c.step_line_id), (1, "P1L2"))

    def test_another_questions_live_text_cannot_rescue_cancelled_evidence(self):
        c = criterion(1, 1, "P1L1", "answer = 4")
        other = question("Q11", [criterion(1, 1, "P1L2", "answer = 4")], anchor="P1L2")
        report, pages = fixture([question(criteria=[c]), other],
                                [line("P1L1", "answer = 4", cancelled=True), line("P1L2", "answer = 4")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}, "q11": {"line_ids": ["P1L2"]}})
        apply(report, pages)
        self.assertEqual(c.awarded, 0)
        self.assertEqual(other.score, 1)

    def test_symbol_difference_is_not_mathematical_equivalence(self):
        c = criterion(1, 1, "P1L1", "x < 2")
        report, pages = fixture([question(criteria=[c], anchor="P1L2")],
                                [line("P1L1", "x < 2", cancelled=True), line("P1L2", "x > 2")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        result = apply(report, pages)
        self.assertEqual(c.awarded, 0)
        self.assertIn("inequalities", result.verification_reasons["Q10"])

    def test_a_bare_figure_does_not_match_a_different_number(self):
        c = criterion(1, 1, "P1L1", "2")
        report, pages = fixture([question(criteria=[c], anchor="P1L2")],
                                [line("P1L1", "2", cancelled=True), line("P1L2", "x = 12")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        apply(report, pages)
        self.assertEqual(c.awarded, 0)

    def test_a_bare_figure_does_not_match_a_decimal_with_that_integer_part(self):
        c = criterion(1, 1, "P1L1", "2")
        report, pages = fixture([question(criteria=[c], anchor="P1L2")],
                                [line("P1L1", "2", cancelled=True), line("P1L2", "x = 2.5")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        apply(report, pages)
        self.assertEqual(c.awarded, 0)

    def test_missing_method_credit_is_removed_even_when_it_was_partial(self):
        method = criterion(.5, 1, "P1L1", kind="missing")
        answer = criterion(1, 1, "P1L2", "answer = 4", description="Final answer")
        q = question("Q9", [method, answer])
        report, pages = fixture([q], [line("P1L1", "Q9"), line("P1L2", "answer = 4")])
        result = apply(report, pages)
        self.assertEqual((method.awarded, answer.awarded, q.score), (0, 1, 1))
        self.assertIn("omitted step", result.verification_reasons["Q9"])

    def test_correct_one_mark_value_is_verified_without_regex_upgrading(self):
        c = criterion(.5, 1, "P1L1", "answer = 4", description="Final answer", evidence="No working shown")
        q = question("Q15.i", [c], remark="No working shown")
        report, pages = fixture([q], [line("P1L1", "answer = 4")])
        result = apply(report, pages)
        self.assertEqual(q.score, .5, "the second examiner must decide whether the value is correct")
        self.assertIn("entire part", result.verification_reasons["Q15.i"])

    def test_missing_invented_half_method_is_not_accepted_as_one_mark_credit(self):
        method = criterion(.5, .5, "", kind="missing", description="Working")
        answer = criterion(.5, .5, "P1L1", "answer = 4", description="Final answer")
        q = question("Q15.i", [method, answer])
        report, pages = fixture([q], [line("P1L1", "answer = 4")])
        result = apply(report, pages)
        self.assertEqual((method.awarded, answer.awarded, q.score), (0, .5, .5))
        self.assertIn("do not invent a half-mark", result.verification_reasons["Q15.i"])

    def test_genuinely_partial_method_keeps_its_score(self):
        c = criterion(.5, 1, "P1L1", "x = 3", evidence="Wrong substitution")
        q = question("Q15.i", [c], remark="Wrong substitution")
        report, pages = fixture([q], [line("P1L1", "x = 3")])
        result = apply(report, pages)
        self.assertEqual(q.score, .5)
        self.assertNotIn("entire part", result.verification_reasons["Q15.i"])

    def test_authoritative_one_mark_allocation_requests_correct_reassessment(self):
        q = question("Q15.i", [criterion(.5, 2, "P1L1", "4")], remark="No working shown")
        report, pages = fixture([q], [line("P1L1", "4")])
        package = NS(questions=[NS(qid="Q15.i", max_marks=1)])
        result = apply(report, pages, exam_package=package)
        self.assertIn("entire part", result.verification_reasons["Q15.i"])
        self.assertEqual(q.max_score, 2, "official reconciliation belongs to the pipeline")

    def test_invented_one_mark_split_becomes_one_row_that_can_receive_the_full_mark(self):
        method = criterion(.5, .5, "", kind="missing", description="Working")
        answer = criterion(.5, .5, "P1L1", "answer = 4", description="Final answer")
        q = question("Q15.i", [method, answer])
        report, pages = fixture([q], [line("P1L1", "answer = 4")])
        package = NS(questions=[NS(qid="Q15.i", max_marks=1, question_type="numeric", criteria=[],
                                  prompt_text="Find the value of x.")])
        result = apply(report, pages, exam_package=package)
        self.assertEqual(len(q.criteria), 1)
        merged = q.criteria[0]
        self.assertEqual((merged.max, merged.awarded, merged.step_line_id), (1, .5, "P1L1"))
        self.assertEqual(merged.anchor_quote, "answer = 4")
        self.assertEqual(q.score, .5, "normalizing the deliverable is not a judgement of correctness")
        self.assertIn("Find the value of x", result.verification_reasons["Q15.i"])
        # Simulate a visual examiner confirming the correct complete answer.
        merged.awarded = 1
        merged.met = True
        merged.evidence = ""
        q.score = report.total_score = 1
        again = apply(report, pages, exam_package=package)
        self.assertEqual(q.score, 1)
        self.assertFalse(again.changed, "confirmed full credit must not be perpetually removed")

    def test_explicit_official_half_method_half_result_rubric_is_preserved(self):
        method = criterion(.5, .5, "P1L1", "x + x = 4", description="Working")
        answer = criterion(.5, .5, "P1L2", "x = 2", description="Final answer")
        q = question("Q15.i", [method, answer])
        report, pages = fixture([q], [line("P1L1", "x + x = 4"), line("P1L2", "x = 2")])
        package = NS(questions=[NS(qid="Q15.i", max_marks=1, question_type="method", criteria=[
            NS(criterion_id="method", max_marks=.5), NS(criterion_id="result", max_marks=.5)])])
        apply(report, pages, exam_package=package)
        self.assertEqual(q.criteria, [method, answer])
        self.assertEqual([c.max for c in q.criteria], [.5, .5])

    def test_proof_part_is_normalized_for_examiner_without_inventing_correctness(self):
        method = criterion(.5, .5, "P1L1", "x + x = 4", description="Working")
        answer = criterion(0, .5, "", kind="missing", description="Final equality")
        q = question("Q15.i", [method, answer])
        report, pages = fixture([q], [line("P1L1", "x + x = 4")])
        package = NS(questions=[NS(qid="Q15.i", max_marks=1, question_type="method", criteria=[],
                                  prompt_text="Prove the identity with working.")])
        result = apply(report, pages, exam_package=package)
        self.assertEqual((len(q.criteria), q.criteria[0].max, q.score), (1, 1, .5))
        self.assertIn("Prove the identity", result.verification_reasons["Q15.i"])
        self.assertIn("assess that working instead", result.verification_reasons["Q15.i"])

    def test_no_official_maximum_does_not_collapse_model_criteria(self):
        cs = [criterion(.5, .5, "P1L1", "x + x = 4"), criterion(.5, .5, "P1L2", "x = 2")]
        q = question("Q15.i", cs)
        report, pages = fixture([q], [line("P1L1", "x + x = 4"), line("P1L2", "x = 2")])
        apply(report, pages)
        self.assertEqual(q.criteria, cs)

    def test_existing_fixed_rubric_metadata_prevents_split_normalization(self):
        cs = [criterion(.5, .5, "P1L1", "x + x = 4"), criterion(.5, .5, "P1L2", "x = 2")]
        q = question("Q15.i", cs)
        report, pages = fixture([q], [line("P1L1", "x + x = 4"), line("P1L2", "x = 2")])
        report.fixed_rubric_rules = {"q15.i": [{"criterion_id": "method", "max_marks": .5}]}
        package = NS(questions=[NS(qid="Q15.i", max_marks=1, question_type="numeric", criteria=[])])
        apply(report, pages, exam_package=package)
        self.assertEqual(q.criteria, cs)

    def test_missing_or_fragmented_math_correction_goes_to_existing_examiner(self):
        q = question("Q13", [criterion(1, 2, "P1L1", "a = 2")], remark="Is vague. Misses.")
        report, pages = fixture([q], [line("P1L1", "a = 2")])
        result = apply(report, pages)
        self.assertIn("complete correction", result.verification_reasons["Q13"])
        self.assertIn("Q13", result.math_verification_qids)

    def test_accounts_format_is_reviewed_even_if_fully_awarded(self):
        q = question("Q12", [criterion(6, 6, "P1L1", "Capital account")])
        report, pages = fixture([q], [line("P1L1", "Capital account")])
        result = apply(report, pages, subject="Accountancy")
        self.assertIn("carried-forward continuation", result.verification_reasons["Q12"])
        self.assertEqual(q.score, 6, "format judgement requires the page image")

    def test_brief_economics_deduction_is_reviewed_without_assuming_correctness(self):
        q = question("Q2", [criterion(2, 3, "P1L1", "Industry")], remark="Lacks explanation")
        report, pages = fixture([q], [line("P1L1", "Industry")])
        result = apply(report, pages, subject="Economics")
        self.assertIn("distinct valid points", result.verification_reasons["Q2"])
        self.assertIn("FIRST N live points", result.verification_reasons["Q2"])
        self.assertIn("including wrong points", result.verification_reasons["Q2"])
        self.assertEqual(q.score, 2)

    def test_objective_accounts_do_not_get_format_verification(self):
        q = question("Q1", maximum=1, score=1, objective=True)
        report, pages = fixture([q], [line("P1L1", "Q1 A")])
        result = apply(report, pages, subject="Accountancy")
        self.assertEqual(result.verification_qids, [])

    def test_no_criteria_cancelled_answer_is_zeroed_but_can_be_restored_by_examiner(self):
        q = question("Q10", maximum=2, score=2)
        report, pages = fixture([q], [line("P1L1", "answer = 4", cancelled=True)])
        result = apply(report, pages)
        self.assertEqual((q.score, report.total_score), (0, 0))
        self.assertTrue(q.attempted, "an image reviewer must still be able to find and award a live rewrite")
        self.assertIn("Q10", result.verification_qids)

    def test_independently_read_live_objective_is_not_zeroed_for_stale_ocr_anchor(self):
        q = question("Q1", maximum=1, score=1, objective=True)
        report, pages = fixture([q], [line("P1L1", "A", cancelled=True)])
        report.objective_visual_verified_qids = ["Q1"]
        result = apply(report, pages, subject="Accountancy")
        self.assertEqual(q.score, 1)
        self.assertEqual(result.verification_qids, [])

    def test_no_ocr_does_not_invent_cancellation(self):
        q = question(criteria=[criterion(1, 1, "P1L1", "answer = 4")])
        report, pages = fixture([q])
        result = apply(report, [])
        self.assertEqual(q.score, 1)
        self.assertFalse(result.changed)

    def test_noncommerce_reports_are_untouched(self):
        c = criterion(1, 1, "P1L1", "4", kind="missing")
        report, pages = fixture([question(criteria=[c])], [line("P1L1", "4", cancelled=True)])
        result = apply(report, pages, category="ca")
        self.assertEqual(c.awarded, 1)
        self.assertEqual(result.verification_qids, [])

    def test_repeated_guard_does_not_remove_a_valid_live_step(self):
        c = criterion(1, 1, "P1L1", "x = -2")
        q = question(criteria=[c], anchor="P1L2")
        report, pages = fixture([q], [line("P1L1", "x = -2", cancelled=True), line("P1L2", "x = -2")],
                                {"q10": {"line_ids": ["P1L1", "P1L2"]}})
        apply(report, pages)
        again = apply(report, pages)
        self.assertEqual((q.score, c.awarded, c.step_line_id), (1, 1, "P1L2"))
        self.assertFalse(again.changed)


if __name__ == "__main__":
    unittest.main()
