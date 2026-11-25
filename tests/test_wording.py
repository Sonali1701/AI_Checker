# -*- coding: utf-8 -*-
"""The student never sees a semicolon or a dash on a checked copy.

Reviewer rule, 2026-09-16: "don't use ; and dash as it looks like AI". Remarks are
short plain sentences, the way a teacher writes in a margin.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wording import plain_report, plain_sentences, wording_problems  # noqa: E402


def test_semicolon_ends_the_sentence():
    assert plain_sentences("Points only named; not explained") == "Points only named. Not explained."


def test_dashes_become_commas_and_hyphenated_words_lose_the_hyphen():
    assert plain_sentences("Only two points - third missing") == "Only two points, third missing."
    assert plain_sentences("Only two points — third missing") == "Only two points, third missing."
    assert plain_sentences("U-shaped valleys are glacial; not fold mountains.") == \
        "U shaped valleys are glacial. Not fold mountains."
    assert plain_sentences("flat-topped hills") == "Flat topped hills."


def test_numeric_range_is_spelt_out():
    assert plain_sentences("Write 1-2 lines on each point.") == "Write 1 to 2 lines on each point."


def test_ans_label_becomes_a_sentence():
    assert plain_sentences("Ans: C") == "Correct answer is (C)."
    assert plain_sentences("Ans: (b)") == "Correct answer is (B)."


def test_clean_text_is_left_alone_apart_from_the_full_stop():
    text = "Types named with examples, but how each plain is formed is not explained."
    assert plain_sentences(text) == text
    assert plain_sentences("Not attempted") == "Not attempted."
    assert plain_sentences("") == ""


def test_wording_problems_names_each_violation():
    assert wording_problems("a; b") == ["semicolon"]
    assert wording_problems("a - b") == ["dash"]
    assert wording_problems("a – b; c") == ["semicolon", "dash"]
    assert wording_problems("Correct answer is (C) 25 years.") == []


def test_plain_report_rewrites_every_remark_in_place():
    class Q:
        def __init__(self, remark):
            self.remark = remark

    class R:
        questions = [Q("named; not explained"), Q(""), Q("1-2 lines")]
        overall_remarks = "Good work - revise Q7; attempt every part"

    r = R()
    plain_report(r)
    assert [q.remark for q in r.questions] == ["Named. Not explained.", "", "1 to 2 lines."]
    assert r.overall_remarks == "Good work, revise Q7. Attempt every part."
    for q in r.questions:
        assert wording_problems(q.remark) == []
    assert wording_problems(r.overall_remarks) == []


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL {name}: {exc}")
    print("all passed" if not failed else f"{failed} failed")
    raise SystemExit(1 if failed else 0)


def test_a_formula_keeps_its_minus_and_an_em_dash_between_words_is_a_comma():
    """PW Skills feedback, 23 Oct 2026: "Use =A1-TODAY()" reached the learner as "Use =A1
    TODAY()", and "feature engineering\u2014those sections" lost its break."""
    from wording import plain_sentences, wording_problems
    out = plain_sentences("Formula reverses subtraction. Use =A1-TODAY() for days remaining.")
    assert out == "Formula reverses subtraction. Use =A1-TODAY() for days remaining." and not wording_problems(out)
    assert plain_sentences("Should be =SUM(B2:B10)-C2.") == "Should be =SUM(B2:B10)-C2."
    assert plain_sentences("Good feature engineering\u2014those sections show thought") == \
        "Good feature engineering, those sections show thought."
    assert plain_sentences("Range 0\u20134 seconds") == "Range 0 to 4 seconds."
    assert plain_sentences("Only two points - third missing") == "Only two points, third missing."
