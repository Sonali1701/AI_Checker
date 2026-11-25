"""Live run of 4 Nov 2026 (NEEV Class 9 Hindi, Complete Subjective Test-01, 80 marks, 317 copies).

Section "a" heads ten questions with "[10 x 2 = 20]" and then tags each of them "[1 M]", so the paper's
own tags add up to 70 against its stated 80. The reading that follows the headings and lands on 80 passed
the marks stage, and preflight then refused every copy for having no 1-mark question. Each copy was
checked and paid for first, and all 317 came back "error occurred" with nothing uploaded.

Tags that contradict the paper's own total cannot refute a scheme that meets it, but only where every
question is tagged: where the tags are a subset of the paper, an untagged question explains the gap and
the CA protections below must still hold. No network.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (MarksItem, MarksScheme, printed_tag_sum, scheme_contradicts_paper,
                    scheme_tag_shortfall)

HINDI = "\n".join(["Hindi", "Maximum Marks - 80"]
                  + [f"Q{i} textbook question\n[1 M]" for i in range(1, 11)]
                  + [f"Q{i} grammar question\n[2 M]" for i in range(11, 21)]
                  + [f"Q{i} short answer\n[4 M]" for i in range(21, 25)]
                  + [f"Q{i} long answer\n[6 M]" for i in range(25, 27)]
                  + [f"Q{i} writing\n[4 M]" for i in range(27, 30)])
# What the headings give, and what the portal's total says: the ten tagged 1s are worth 2.
HEADINGS = [2.0] * 20 + [4.0] * 4 + [6.0] * 2 + [4.0] * 3
TAGS = [1.0] * 10 + [2.0] * 10 + [4.0] * 4 + [6.0] * 2 + [4.0] * 3


def scheme(maxima, choice_groups=()):
    return MarksScheme(total=round(sum(maxima), 2),
                       items=[MarksItem(qid=f"Q{i}", max=value) for i, value in enumerate(maxima, 1)],
                       choice_groups=list(choice_groups))


def test_the_fixture_reads_like_the_real_paper():
    assert printed_tag_sum(HINDI) == (70.0, 29) and sum(HEADINGS) == 80


def test_the_paper_that_contradicts_its_own_total_no_longer_refuses_the_reading():
    assert scheme_tag_shortfall(scheme(HEADINGS), HINDI, 80.0) == {}
    assert scheme_contradicts_paper(scheme(HEADINGS), HINDI, 80.0) == ""


def test_a_reading_that_follows_the_tags_instead_is_still_fine():
    assert scheme_contradicts_paper(scheme(TAGS), HINDI, 80.0) == ""


@pytest.mark.parametrize("maxima,why", [
    ([2.0] * 20 + [4.0] * 4 + [6.0] * 2 + [3.0] * 3, "does not reach the official total"),
    ([4.0] * 5 + [2.0] * 10 + [4.0] * 4 + [6.0] * 2 + [4.0] * 3, "leaves fewer rows than the paper tags"),
])
def test_a_scheme_the_paper_can_still_refute_is_refused(maxima, why):
    assert scheme_tag_shortfall(scheme(maxima), HINDI, 80.0).get(1.0) == 10, why


def test_without_an_official_total_the_tags_still_rule():
    assert scheme_tag_shortfall(scheme(HEADINGS), HINDI, None) == {1.0: 10}


# The CA Inter Taxation paper: 14 x "(2 Marks)" and 2 x "(1 Mark)" against its objective questions
# only, on a 100-mark paper. Its tags are a subset, so the gap to 100 is explained by the untagged
# subjective questions and a scheme pricing the objectives at 1.5 is still refused (2026-08-27).
TAX = "\n".join(["Total Marks: 100"]
                + [f"{i}. Objective question?\n(2 Marks)" for i in range(1, 8)]
                + ["8. Last objective.\n(1 Mark)"]
                + [f"{i}. GST objective?\n(2 Marks)" for i in range(1, 8)]
                + ["8. Last GST objective.\n(1 Mark)"])
SUBJECTIVE = [14.0, 5.5, 3.5, 5.5, 4.5, 6.0]


def test_a_paper_whose_tags_are_a_subset_still_checks_every_tagged_value():
    invented = scheme([1.5] * 14 + [1.0] * 2 + SUBJECTIVE + [38.0])
    assert sum(float(item.max) for item in invented.items) == 100
    assert scheme_tag_shortfall(invented, TAX, 100.0) == {2.0: 14}


def test_the_measured_disjoint_scheme_is_still_caught():
    # The CA Final Auditing paper prints 2, 4 and 5-mark questions and a run built its scheme out of
    # 3, 3.5 and 6.5, scoring a 29-mark copy at 85. Its tags outrun the total, so the count test stays
    # out of it and total disjointness is what catches it.
    auditing = "\n".join([f"{i}. Question?\n({value} Marks)" for i, value in
                          enumerate([2, 2, 4, 4, 5, 5, 2, 4], 1)])
    invented = scheme([3.0, 3.5, 6.5, 3.0, 3.5])
    assert scheme_tag_shortfall(invented, auditing, 20.0) == {}
    assert "not one question" in scheme_contradicts_paper(invented, auditing, 20.0)


def test_a_paper_whose_tags_agree_with_its_total_still_refuses_a_missing_value():
    agreeing = "\n".join(["Maximum Marks - 20"] + [f"Q{i} question\n[2 M]" for i in range(1, 11)])
    assert scheme_tag_shortfall(scheme([4.0] * 5), agreeing, 20.0) == {2.0: 10}
