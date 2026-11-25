# -*- coding: utf-8 -*-
"""Teaching exams: what the re-run of 21 Oct 2026 showed after the rules of 15 Oct.

KVS PRT Mock 01 came down from 37.75 to 34 of 40 (evaluators 28.5): three theory
answers still had 4 of 4 where the evaluators gave 3 to 3.5, and "farm to plate" was
priced as one 1.5-mark step. EMRS TGT Maths read 32 of 60 (evaluators 36) only
because errors cancelled: a correct alpha = beta solution to Q1 was refused as "product
of roots instead of the required discriminant" (1 of 4), and Q9's misread final answer
kept 3 of 4 because the key priced that answer at 1 mark (evaluator 1 of 4).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import teaching_key as tk  # noqa: E402


def _rules() -> str:
    return " ".join(categories.get("teaching").prompt.extra_rules.split())


def test_a_different_valid_method_is_never_a_deduction():
    assert "A method that differs from the key's is never a fault" in _rules()


def test_full_marks_on_a_theory_answer_are_the_exception():
    rules = _rules()
    assert "Full marks on a theory answer are the exception" in rules
    assert "A complete and correct answer that is plain or short earns 3 to 3.5 of 4" in rules


def test_a_brief_prt_maths_solution_is_three_of_four():
    assert "a correct but brief solution earns 3 of 4" in _rules()


def test_the_key_prices_a_short_numerical_answer_at_half_and_splits_a_process():
    prompt = " ".join(tk.TEACHING_KEY_SYSTEM_PROMPT.split())
    assert "the final answer carries 1.5 to 2 of the marks" in prompt
    assert "never the whole journey as one step" in prompt
    assert tk.TEACHING_KEY_VERSION == "2026-10-23.1"


def test_every_frozen_step_carries_its_teaching_rule():
    sheet = tk.ModelAnswerKey(questions=[tk.KeyQuestion(
        qid="Q1", max_marks=4, model_answer="k = ±2√6",
        steps=[tk.KeyStep(description="States D = b²−4ac = 0", marks=1, scoring_kind="method"),
               tk.KeyStep(description="k = 2√6", marks=1.5, scoring_kind="numeric"),
               tk.KeyStep(description="Explains ploughing", marks=1, scoring_kind="theory"),
               tk.KeyStep(description="Format", marks=0.5, scoring_kind="format")])])
    rows = tk.as_key_rubric_sheet(sheet).questions[0].criteria
    assert "Any valid method earns this step" in rows[0].marking_rule
    assert "sum and product of roots" in rows[0].marking_rule
    assert "whichever valid method" in rows[1].marking_rule
    assert "a point only named earns half" in rows[2].marking_rule
    assert rows[3].marking_rule == ""
    assert all(len(rule) <= 300 for rule in tk.STEP_RULES.values())
