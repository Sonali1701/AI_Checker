# -*- coding: utf-8 -*-
"""A state-board paper's MCQs reach the deterministic scorer.

Found on the commerce state-wise batch of 2026-11-17. Bihar Board Class 11
Economics: Section A is 50 one-mark MCQs, and not one of them was scored from the
official key. Two independent faults had to line up:

1. The marks scheme labels its rows by section - "A.Q1" - and the classifier only
   matched a bare number, so no row was ever typed objective.
2. The key numbers its blocks "Sol. 1." and states the answer behind a Devanagari
   label, "सही उत्तर: (b)", which none of the four known key shapes matched. It also
   changes shape at question 10, putting header and answer on one line.

With no official answer and no objective type, the grader's own `is_correct` claim
stood unchecked: the copy was credited 46 of 50 where the evaluator's own arithmetic
implies about 19, Q47 was credited against a plainly wrong letter, and Q50 was
credited although the student wrote nothing at all. The release gate then held the
copy - "no visible answer mark was recorded" on every Section A row - because an
objective row that is never typed objective is never anchored or evidenced either.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exam_package import (  # noqa: E402
    _section_and_number,
    _official_objective_answers,
    _question_nodes,
)


def _item(qid: str, maximum: float):
    return types.SimpleNamespace(qid=qid, max=maximum, description="",
                                 question_type="unknown", correct_answer="")


def _scheme(items):
    return types.SimpleNamespace(items=items)


BIHAR_KEY = """
Sol. 1.
सही उत्तर: (b) मानव व्यवहार से
हहिंदी में सही उत्तर की व्याख्या:
अर्थशास्त्र यह अध्ययन करता है ...
Sol. 2.
सही उत्तर: (c) मूल्य ससद्ाांत
Sol. 10. सही उत्तर: (d) ववकससत देश
Sol. 11. सही उत्तर: (a) वस्तु से प्राप्त सांतुष्टि
"""


class TheKeyShapeIsRead(unittest.TestCase):
    def test_answer_on_the_line_after_the_header(self):
        out = _official_objective_answers(BIHAR_KEY, "")
        self.assertEqual(out.get(1), "B")
        self.assertEqual(out.get(2), "C")

    def test_answer_on_the_header_line_itself(self):
        """The Bihar Economics key switches to this shape at question 10."""
        out = _official_objective_answers(BIHAR_KEY, "")
        self.assertEqual(out.get(10), "D")
        self.assertEqual(out.get(11), "A")

    def test_an_explanation_cannot_answer_the_question(self):
        """Only the head of a block is read, so later prose cannot overrule it."""
        key = ("Sol. 1.\nसही उत्तर: (b) ठीक\n" + ("पाठ " * 80) +
               "\nयह विकल्प: (d) गलत है\n")
        self.assertEqual(_official_objective_answers(key, "").get(1), "B")

    def test_a_label_without_a_letter_is_not_an_answer(self):
        """The explanation label ends at its colon and must be skipped."""
        key = "Sol. 3.\nहहिंदी में सही उत्तर की व्याख्या:\nसही उत्तर: (c) ठीक\n"
        self.assertEqual(_official_objective_answers(key, "").get(3), "C")


class SectionLabelledRowsAreClassified(unittest.TestCase):
    def test_either_label_order_is_recognised(self):
        """The marks reading is an AI call and its labelling is not stable: the same
        Bihar Economics paper came back "A.Q1" on 17 Nov and "QA1" on 18 Nov."""
        for label in ("A.Q1", "A.1", "AQ1", "QA1", "QA.1"):
            self.assertEqual(_section_and_number(label), ("A", 1), label)
        self.assertEqual(_section_and_number("QB14"), ("B", 14))

    def test_a_bare_question_label_is_not_a_section_row(self):
        """"Q1"'s Q is not a section letter; matching it would shadow real numbering."""
        self.assertIsNone(_section_and_number("Q1"))
        self.assertIsNone(_section_and_number("Q50"))

    def test_the_matching_section_is_typed_objective(self):
        key = "".join(f"Sol. {n}.\nसही उत्तर: (b) x\n" for n in range(1, 11))
        nodes = _question_nodes(
            _scheme([_item(f"A.Q{n}", 1.0) for n in range(1, 11)]),
            question_paper_text="", answer_key_text=key)
        objective = [n for n in nodes if n.question_type == "objective"]
        self.assertEqual(len(objective), 10)
        self.assertTrue(all(n.objective_answer == "B" for n in objective))

    def test_another_section_does_not_take_those_letters(self):
        """Section B's 1-14 sit inside the key's 1-50 but are not its questions."""
        key = "".join(f"Sol. {n}.\nसही उत्तर: (b) x\n" for n in range(1, 51))
        items = ([_item(f"A.Q{n}", 1.0) for n in range(1, 51)]
                 + [_item(f"B.Q{n}", 2.0) for n in range(1, 15)])
        nodes = _question_nodes(_scheme(items), question_paper_text="",
                                answer_key_text=key)
        typed = {n.qid for n in nodes if n.question_type == "objective"}
        self.assertEqual(len(typed), 50)
        self.assertTrue(all(q.startswith("A") for q in typed),
                        "a short-answer section took the MCQ section's key")

    def test_two_sections_matching_exactly_is_ambiguous_so_neither_is_taken(self):
        key = "".join(f"Sol. {n}.\nसही उत्तर: (b) x\n" for n in range(1, 6))
        items = ([_item(f"A.Q{n}", 1.0) for n in range(1, 6)]
                 + [_item(f"B.Q{n}", 1.0) for n in range(1, 6)])
        nodes = _question_nodes(_scheme(items), question_paper_text="",
                                answer_key_text=key)
        self.assertEqual([n for n in nodes if n.question_type == "objective"], [])

    def test_the_q_first_label_shape_also_reaches_the_key(self):
        """The shape that regressed the fix on its first re-run."""
        key = "".join(f"Sol. {n}.\nसही उत्तर: (b) x\n" for n in range(1, 51))
        items = ([_item(f"QA{n}", 1.0) for n in range(1, 51)]
                 + [_item(f"QB{n}", 2.0) for n in range(1, 16)])
        nodes = _question_nodes(_scheme(items), question_paper_text="",
                                answer_key_text=key)
        typed = {n.qid for n in nodes if n.question_type == "objective"}
        self.assertEqual(len(typed), 50)
        self.assertTrue(all(q.startswith("QA") for q in typed))

    def test_a_long_question_never_takes_a_key_letter(self):
        """The existing marks guard still applies to section-labelled rows."""
        key = "".join(f"Sol. {n}.\nसही उत्तर: (b) x\n" for n in range(1, 6))
        nodes = _question_nodes(
            _scheme([_item(f"A.Q{n}", 5.0) for n in range(1, 6)]),
            question_paper_text="", answer_key_text=key)
        self.assertEqual([n for n in nodes if n.question_type == "objective"], [])


if __name__ == "__main__":
    unittest.main()
