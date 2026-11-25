# -*- coding: utf-8 -*-
"""A choice paper whose reading misses the official total gets one told recheck.

MEASURED 2026-09-28: a Parishram ISC Chemistry paper (marks only in section headers,
one OR in Q19) came back "Fixed questions plus the extracted choice rules do not
exactly equal the official paper total" and could not be graded at all: the reading
counted both Q19 alternatives as compulsory.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline  # noqa: E402
from grader import MarksChoiceGroup, MarksChoiceOption, MarksItem, MarksScheme  # noqa: E402
from marks_attestation import choice_total_gap  # noqa: E402


def _scheme(grouped: bool) -> MarksScheme:
    items = [MarksItem(qid=f"Q{n}", max=5) for n in range(1, 14)]      # 65 fixed
    items += [MarksItem(qid="Q19.alt1", max=5), MarksItem(qid="Q19.alt2", max=5)]
    groups = [MarksChoiceGroup(group_id="choice-q19", select_count=1, contribution_marks=5,
                               options=[MarksChoiceOption(option_id="a", question_ids=["Q19.alt1"]),
                                        MarksChoiceOption(option_id="b", question_ids=["Q19.alt2"])])
              ] if grouped else []
    return MarksScheme(total=75, items=items, choice_groups=groups)


class Gap(unittest.TestCase):
    def test_ungrouped_alternatives_are_named(self):
        gap = choice_total_gap(_scheme(False), 70)
        self.assertIn("one student can score 75", gap)
        self.assertIn("paper total is 70", gap)
        self.assertIn("Q19.alt1", gap)

    def test_a_matching_graph_has_no_gap(self):
        self.assertEqual(choice_total_gap(_scheme(True), 70), "")


class Recheck(unittest.TestCase):
    def run_detect(self, replies):
        calls = []

        def extract(*args, **kwargs):
            calls.append(kwargs)
            return replies[len(calls) - 1].model_copy(deep=True)

        cfg = type("Cfg", (), {"provider": "claude", "exam_type": "school"})()
        with patch.multiple(pipeline, _extract_marks=extract, pdf_text_layer=lambda *a: "Q19 OR",
                            paper_has_choice=lambda *a: True,
                            pdf_or_image_to_pngs=lambda *a: [b"png"]):
            scheme, method = pipeline.detect_marks_scheme(
                b"%PDF", "paper.pdf", cfg, total_marks=70, require_complete=True)
        return scheme, method, calls

    def test_a_wrong_total_buys_one_told_recheck(self):
        scheme, method, calls = self.run_detect([_scheme(False), _scheme(True)])
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1]["visual_recheck"])
        self.assertIn("Q19.alt1", calls[1]["recovery_reason"])
        self.assertEqual(choice_total_gap(scheme, 70), "")
        self.assertEqual(method, "ai")

    def test_a_recheck_that_still_misses_keeps_the_first_and_stops(self):
        scheme, _method, calls = self.run_detect([_scheme(False), _scheme(False)])
        self.assertEqual(len(calls), 2)
        self.assertTrue(choice_total_gap(scheme, 70))

    def test_a_matching_first_reading_costs_one_call(self):
        _scheme_, _method, calls = self.run_detect([_scheme(True)])
        self.assertEqual(len(calls), 1)


# The Parishram ISC Chemistry Test-09 layout: headers for Q1-10 and Q16-24 only.
_CHEM = " ".join(
    ["SECTION - A Multiple Choice Type Questions (1-5) (5 × 1 = 5)"]
    + [f"Q{n} question text" for n in range(1, 6)]
    + ["SECTION - B Very Short Answer Type Questions (6–10) (2 × 5 = 10)"]
    + [f"Q{n} question text" for n in range(6, 16)]
    + ["SECTION - C Long Answer Type Questions (16–20) (5 × 5 = 25)"]
    + [f"Q{n} question text" for n in range(16, 19)] + ["Q19 part one OR part two"]
    + ["Q20 text SECTION - D Case Based Study Questions (21 - 24)(4 × 5 = 20)"]
    + [f"Q{n} case text" for n in range(21, 25)])


class HeaderGap(unittest.TestCase):
    def test_the_entered_total_prices_the_one_block_without_a_header(self):
        from grader import marks_scheme_from_section_headers as read
        for total, per in ((75, 3), (70, 2)):
            scheme = read(_CHEM, total)
            rows = {i.qid: i.max for i in scheme.items}
            self.assertEqual(scheme.total, total)
            self.assertEqual([rows[f"Q{n}"] for n in range(11, 16)], [per] * 5)
            self.assertEqual((rows["Q6"], rows["Q19"], rows["Q24"]), (2, 5, 5))
        self.assertIsNone(read(_CHEM, 60), "60 leaves Q11-15 worth nothing")
        self.assertIsNone(read(_CHEM, 72), "no half-mark split")
        self.assertIsNone(read(_CHEM + " Attempt any four questions.", 75))

    def test_the_refusal_says_which_questions_the_paper_leaves_unpriced(self):
        from grader import section_header_gap
        read = MarksScheme(total=75, items=[MarksItem(qid=f"Q{n}", max=2) for n in range(11, 16)])
        message = section_header_gap(_CHEM, read, 60)
        for part in ("add up to 60", "Q11–Q15", "70 gives 2 marks each", "75 gives 3 marks each"):
            self.assertIn(part, message)
        self.assertEqual(section_header_gap(_CHEM.replace("(6–10) (2 × 5 = 10)",
                                                          "(6–15) (2 × 10 = 20)"), read, 70), "")


if __name__ == "__main__":
    unittest.main()
