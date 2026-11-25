# -*- coding: utf-8 -*-
"""Two readers that name unlabelled OR alternatives differently still agree.

The Parishram Class 12 Accountancy unit test (2026-09-17) prints "Q10 ... OR ..."
four times with no label on either alternative. Claude returned Q10 and Q10.OR
(Q10_OR, Q10-OR and Q10or on other runs), Gemini Q10.alt1 and Q10.alt2, for the
same nine fixed rows and four choose-one-of-two groups at 3, 3, 4 and 6 marks; the
comparison by name blocked the paper as "did not return the same graph".
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import MarksChoiceGroup, MarksChoiceOption, MarksItem, MarksScheme  # noqa: E402
from marks_attestation import reconcile_marks_schemes, schemes_agree  # noqa: E402


def _scheme(rows, groups):
    items = [MarksItem(qid=q, max=float(m), description="") for q, m in rows]
    marks = {q: float(m) for q, m in rows}
    choice = [MarksChoiceGroup(group_id=gid, select_count=1,
                               contribution_marks=sum(marks[q] for q in options[0]),
                               options=[MarksChoiceOption(option_id=f"{gid}-{i}", question_ids=list(qids))
                                        for i, qids in enumerate(options)])
              for gid, options in groups]
    return MarksScheme(total=sum(m for _, m in rows), items=items, choice_groups=choice)


FIXED = [(f"Q{i}", 1) for i in range(1, 10)]
PAIRS = [("Q10", 3), ("Q11", 3), ("Q12", 4), ("Q13", 6)]


def _claude(suffix=".OR"):
    rows = FIXED + [r for q, mx in PAIRS for r in ((q, mx), (q + suffix, mx))]
    groups = [(f"choice-{q.lower()}", [[q], [q + suffix]]) for q, _ in PAIRS]
    return _scheme(rows, groups)


def _gemini(marks=None):
    marks = marks or dict(PAIRS)
    rows = FIXED + [r for q, _ in PAIRS for r in ((q + ".alt1", marks[q]), (q + ".alt2", marks[q]))]
    groups = [(f"choice-{i}", [[q + ".alt1"], [q + ".alt2"]]) for i, (q, _) in enumerate(PAIRS, 1)]
    return _scheme(rows, groups)


class AlignmentTests(unittest.TestCase):
    def test_differently_named_alternatives_agree_and_keep_the_primary_names(self):
        for suffix in (".OR", "_OR", "-OR", "or"):
            primary = _claude(suffix)
            self.assertTrue(schemes_agree(primary, _gemini()), suffix)
            consensus = reconcile_marks_schemes(primary, _gemini())
            self.assertEqual([i.qid for i in consensus.items], [i.qid for i in primary.items])
        self.assertTrue(schemes_agree(_gemini(), _claude()))

    def test_a_different_mark_on_an_alternative_is_still_a_disagreement(self):
        self.assertFalse(schemes_agree(_claude(), _gemini(marks={"Q10": 3, "Q11": 3, "Q12": 4, "Q13": 5})))

    def test_a_different_selection_rule_is_still_a_disagreement(self):
        other = _gemini()
        other.choice_groups[0].options.append(
            MarksChoiceOption(option_id="x", question_ids=["Q1"]))
        self.assertFalse(schemes_agree(_claude(), other))

    def test_a_missing_group_is_still_a_disagreement(self):
        other = _gemini()
        other.choice_groups = other.choice_groups[:3]
        self.assertFalse(schemes_agree(_claude(), other))

    def test_multi_part_alternatives_align_option_by_option(self):
        primary = _scheme(FIXED + [("Q10.a", 2), ("Q10.b", 1), ("Q10.OR", 3)],
                          [("g", [["Q10.a", "Q10.b"], ["Q10.OR"]])])
        verifier = _scheme(FIXED + [("Q10.alt1.a", 2), ("Q10.alt1.b", 1), ("Q10.alt2", 3)],
                           [("c", [["Q10.alt1.a", "Q10.alt1.b"], ["Q10.alt2"]])])
        self.assertTrue(schemes_agree(primary, verifier))
        swapped = _scheme(FIXED + [("Q10.alt1.a", 1), ("Q10.alt1.b", 2), ("Q10.alt2", 3)],
                          [("c", [["Q10.alt1.a", "Q10.alt1.b"], ["Q10.alt2"]])])
        self.assertFalse(schemes_agree(primary, swapped))

    def test_a_label_spelt_differently_still_agrees_when_the_marks_agree(self):
        # A name is not a mark (operator's rule, 2026-09-17): (i)/(ii) for the
        # paper's (a)/(b) is the same structure, and the primary's names are kept.
        primary = _scheme(FIXED + [("Q10.a", 2), ("Q10.b", 1), ("Q10.OR", 3)],
                          [("g", [["Q10.a", "Q10.b"], ["Q10.OR"]])])
        roman = _scheme(FIXED + [("Q10.i", 2), ("Q10.ii", 1), ("Q10.alt2", 3)],
                        [("c", [["Q10.i", "Q10.ii"], ["Q10.alt2"]])])
        self.assertTrue(schemes_agree(primary, roman))
        self.assertEqual([i.qid for i in reconcile_marks_schemes(primary, roman).items],
                         [i.qid for i in primary.items])

    def test_fixed_rows_spelt_differently_align_in_paper_order(self):
        # An MCQ list named Q1.1 ... Q1.9 by one reader and Q1 ... Q9 by the other.
        primary = _claude()
        other = _scheme([(f"Q1.{i}", 1) for i in range(1, 10)]
                        + [r for q, mx in PAIRS for r in ((q + ".alt1", mx), (q + ".alt2", mx))],
                        [(f"c{i}", [[q + ".alt1"], [q + ".alt2"]]) for i, (q, _) in enumerate(PAIRS, 1)])
        self.assertTrue(schemes_agree(primary, other))
        dropped = _scheme([(f"Q1.{i}", 1) for i in range(1, 9)]
                          + [r for q, mx in PAIRS for r in ((q + ".alt1", mx), (q + ".alt2", mx))],
                          [(f"c{i}", [[q + ".alt1"], [q + ".alt2"]]) for i, (q, _) in enumerate(PAIRS, 1)])
        self.assertFalse(schemes_agree(primary, dropped))
        heavier = _scheme([(f"Q1.{i}", 1 if i < 9 else 2) for i in range(1, 10)]
                          + [r for q, mx in PAIRS for r in ((q + ".alt1", mx), (q + ".alt2", mx))],
                          [(f"c{i}", [[q + ".alt1"], [q + ".alt2"]]) for i, (q, _) in enumerate(PAIRS, 1)])
        self.assertFalse(schemes_agree(primary, heavier))


if __name__ == "__main__":
    unittest.main()
