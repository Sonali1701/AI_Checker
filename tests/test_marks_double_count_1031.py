"""31 Oct 2026: two correct readings refused as "marks problems". The mark tags were read twice: a section
heading's tag when a title shares its line or runs long, and a "[2 + 2 + 3 + 3 = 10 Marks]" over the
"[2 M]" tags of the same four parts. The layouts are those of the real papers (Parishram 2.0 English
Test-03, Victory Infinity Economics Test-02); no network.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import extraction_reconciles_with_tags, printed_tag_sum, scheme_contradicts_paper

ENGLISH = ("English - Parishram 2027 (Class 12 CBSE)\nMaximum marks - 80\nQ1\nSECTION A\n(Extract-Based Questions) (24 Marks)\n"
           + "a question [1 M]\n" * 24
           + "Q5\nSECTION B\n(Short Answer Type Questions) (20 Marks)\n" + "a question [2 M]\n" * 10
           + "Q6\nSECTION C\n(Long Answer & Competency Based Questions)\n(36 Marks)\n" + "a question [6 M]\n" * 6)
ECONOMICS = ("Maximum Marks-10\nSECTION-C [10 Marks]\nQ1\nanswer the following questions: [2 + 2 + 3 + 3 =\n10 Marks]\n"
             "(a) one\n[2 M]\n(b) two\n[2 M]\n(c) three\n[3 M]\n(d) four\n[3 M]\n")


def scheme(*values):
    return SimpleNamespace(items=[SimpleNamespace(qid=f"Q{n}", max=value) for n, value in enumerate(values, 1)], choice_groups=[])


def test_a_section_tag_sharing_its_line_with_a_title_is_not_counted_again():
    assert printed_tag_sum(ENGLISH)[0] == 80


def test_a_long_section_title_is_still_a_title():
    text = "SECTION C\n(Long Answer & Competency Based Questions)\n(36 Marks)\n" + "a question [6 M]\n" * 6
    assert printed_tag_sum(text)[0] == 36


def test_a_spelled_out_sum_over_its_own_parts_is_counted_once():
    assert printed_tag_sum(ECONOMICS)[0] == 10


def test_a_spelled_out_sum_on_one_line_is_counted_once():
    text = "the following: [2 + 2 + 3 + 3 = 10 Marks]\n(a)\n[2 M]\n(b)\n[2 M]\n(c)\n[3 M]\n(d)\n[3 M]\n"
    assert printed_tag_sum(text)[0] == 10


def test_every_block_of_a_three_question_paper_is_counted_once():
    block = "[2 + 2 + 3 + 3 = 10 Marks]\n[2 M]\n[2 M]\n[3 M]\n[3 M]\n"
    assert printed_tag_sum(block * 3)[0] == 30


def test_the_right_readings_are_no_longer_refused():
    assert extraction_reconciles_with_tags(scheme(*[1] * 24, *[2] * 10, *[6] * 6), ENGLISH, 80.0, False)
    assert extraction_reconciles_with_tags(scheme(2, 2, 3, 3), ECONOMICS, 10.0, False)
    assert not scheme_contradicts_paper(scheme(*[1] * 24, *[2] * 10, *[6] * 6), ENGLISH, 80.0)
    assert not scheme_contradicts_paper(scheme(2, 2, 3, 3), ECONOMICS, 10.0)


def test_a_wrong_reading_is_still_refused():
    assert not extraction_reconciles_with_tags(scheme(*[1] * 20, *[2] * 10, *[6] * 6), ENGLISH, 80.0, False)
    assert not extraction_reconciles_with_tags(scheme(2, 2, 3), ECONOMICS, 10.0, False)


def test_a_sum_over_untagged_questions_still_prices_them():
    assert printed_tag_sum("GENERAL MCQs (2 + 2 + 1 = 5 Marks)\nQ1\nQ2\nQ3\n")[0] == 5


def test_a_sum_whose_parts_are_not_the_next_tags_still_counts():
    assert printed_tag_sum("block (2 + 2 + 1 = 5 Marks)\nQ1\nQ2\nQ3\nnext (4 Marks)\nthen (6 Marks)\n")[0] == 15


def test_a_section_whose_questions_do_not_add_up_keeps_its_own_tag():
    assert printed_tag_sum("SECTION A\n(Objective) (10 Marks)\na question [3 M]\n")[0] == 13


def test_a_real_question_under_a_heading_is_not_taken_for_a_title():
    text = "SECTION A\nQ1. What is 2 + 3 and why? (2 Marks)\nanother (2 Marks)\n"
    assert printed_tag_sum(text)[0] == 4
