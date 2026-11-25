"""Live run of 3 Nov 2026: Subjective Test 03 (Biology), rows Q11.i to Q11.iii and the rule "2 of Q7, Q8, Q9,
Q10, Q11". The page totalled 14, then the server refused the rule ("A choice option refers to an unknown
question") and the test went back to its marks problem. A question named in a rule now stands for its parts.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import parse_strict_marks_scheme
from integrated_workflow import normalize_operator_marks
from marks_attestation import effective_marks_total

ROWS = [("Q1", 1), ("Q2", 1), ("Q3", 1), ("Q4", 1), ("Q5", 1), ("Q6.a", 1), ("Q6.b", 1), ("Q6.c", 1),
        ("Q7", 3), ("Q8", 3), ("Q9", 3), ("Q10", 3), ("Q11.i", 1), ("Q11.ii", 1), ("Q11.iii", 1)]


def entered(*options, select=2, rows=ROWS):
    return {"items": [{"qid": qid, "max": mark} for qid, mark in rows],
            "choices": [{"select": select, "options": [list(option) for option in options]}]}


def server_total(marks):
    """What server._workflow_operator_marks checks, without the attestation."""
    raw = {"items": [{"qid": row["qid"], "max": row["max"], "description": ""} for row in marks["items"]],
           "choice_groups": [{"group_id": f"choice-{n}", "select_count": choice["select"],
                              "options": [{"option_id": f"o{i}", "question_ids": option} for i, option in enumerate(choice["options"], 1)]}
                             for n, choice in enumerate(marks["choices"], 1)]}
    return effective_marks_total(parse_strict_marks_scheme(json.dumps(raw)))


def test_a_question_named_in_a_rule_stands_for_its_parts():
    marks = normalize_operator_marks(entered(["Q7"], ["Q8"], ["Q9"], ["Q10"], ["Q11"]))
    assert marks["choices"][0]["options"][-1] == ["Q11.i", "Q11.ii", "Q11.iii"]
    assert server_total(marks) == 14


def test_the_parts_written_out_still_work():
    marks = normalize_operator_marks(entered(["Q7"], ["Q8"], ["Q9"], ["Q10"], ["Q11.i", "Q11.ii", "Q11.iii"]))
    assert marks["choices"][0]["options"][-1] == ["Q11.i", "Q11.ii", "Q11.iii"] and server_total(marks) == 14


def test_a_row_of_that_name_is_taken_as_itself_not_its_parts():
    rows = [("Q5", 2), ("Q5.a", 1), ("Q6", 3)]
    marks = normalize_operator_marks(entered(["Q5"], ["Q6"], select=1, rows=rows))
    assert marks["choices"][0]["options"] == [["Q5"], ["Q6"]]


def test_a_rule_ignores_letter_case_and_keeps_the_rows_spelling():
    marks = normalize_operator_marks(entered(["q7"], ["Q11"], select=1))
    assert marks["choices"][0]["options"] == [["Q7"], ["Q11.i", "Q11.ii", "Q11.iii"]]


def test_brackets_count_as_parts_and_a_longer_number_does_not():
    rows = [("Q1(a)", 2), ("Q1(b)", 2), ("Q10", 4), ("Q2", 4)]
    marks = normalize_operator_marks(entered(["Q1"], ["Q2"], select=1, rows=rows))
    assert marks["choices"][0]["options"] == [["Q1(a)", "Q1(b)"], ["Q2"]]


def test_a_question_that_is_not_listed_is_named_at_once():
    with pytest.raises(ValueError, match="Q12 is in a choice rule but not among the questions listed"):
        normalize_operator_marks(entered(["Q7"], ["Q12"], select=1))


def test_a_part_written_twice_is_kept_once():
    marks = normalize_operator_marks(entered(["Q7"], ["Q11", "Q11.i"], select=1))
    assert marks["choices"][0]["options"][-1] == ["Q11.i", "Q11.ii", "Q11.iii"]
