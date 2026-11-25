# -*- coding: utf-8 -*-
"""Bihar Board commerce SME feedback (sheet "Copy Checking Project", rows 26 to 41,
21 Oct 2026).

* Accountancy Q9: liabilities defined as amounts "owned" to outsiders were credited;
  the answer is wrong (the word reverses the meaning).
* Economics: three named characteristics, one written as a longer sentence, got 2/3;
  the faculty give 1.5/3.
* Remarks the students could not understand: "One-line explanation receives
  two-thirds treatment under the Bihar Board rule", "explicit answer text controls
  the conflicting letter".
* English: spelling mistakes were not pointed out.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
from categories.commerce import BIHAR_BOARD_RULES, COMMERCE_RULES, board_rules  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from language_marks import WordSlip, WordSlipSheet, spelling_sentence, verify_word_slips  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from wording import plain_report, strip_rule_talk  # noqa: E402


def test_a_word_that_reverses_an_accounting_meaning_is_a_wrong_answer():
    assert "owned" in COMMERCE_RULES and "Liabilities are amounts owed, not owned." in COMMERCE_RULES
    assert COMMERCE_RULES in categories.get("commerce").prompt.extra_rules
    # The English "never deduct for spelling" rule says it is for English only.
    assert "This is for English only" in BIHAR_BOARD_RULES
    assert board_rules("Uday Bihar Board 11th Commerce 2027") == BIHAR_BOARD_RULES


def test_a_longer_sentence_is_not_an_explanation():
    assert "A longer sentence\n   is not an explanation" in BIHAR_BOARD_RULES
    assert "never 2/3" in BIHAR_BOARD_RULES


def test_remarks_never_cite_the_marking_rules():
    assert strip_rule_talk("One-line explanation receives two-thirds treatment under the "
                           "Bihar Board rule.") == "Explain each point in one or two lines."
    assert strip_rule_talk("B means 'Were'. The written 'Did' is wrong before 'playing'; explicit "
                           "answer text controls the conflicting letter.") == (
        "B means 'Were'. The written 'Did' is wrong before 'playing'.")
    assert strip_rule_talk("C means fourteen and a half months, not years. Explicit written "
                           "duration controls the conflicting letter.") == (
        "C means fourteen and a half months, not years.")
    # Ordinary remarks, and the word "rule" in a subject sense, are left alone.
    for kept in ("Characteristic is stated without explaining what an aggregate of facts means.",
                 "Use the rule of three to find the cost.", "Journal entry needs narration."):
        assert strip_rule_talk(kept) == kept
    report = GradeReport(total_score=1, max_total=3, overall_remarks="Review Q2 under the Bihar Board rule.",
                         questions=[QuestionMark(qid="Q2", score=2, max_score=3, page=1, y_fraction=.5,
                                                 remark="One-line explanation receives two-thirds "
                                                        "treatment under the Bihar Board rule.")])
    plain_report(report)
    assert report.questions[0].remark == "Explain each point in one or two lines."
    assert "Bihar" not in report.overall_remarks


def _line(lid, text, row):
    y = 100 + 60 * row
    return LineBox(lid, text, (100, y, 1100, y + 40), 1)


def test_every_confirmed_spelling_slip_is_named_for_the_student():
    lines = [_line("P1L1", "Explainations of the lines", 0), _line("P1L2", "I recieve the letter", 1)]
    page = PageOCR(1, 1200, 1700, lines, [])          # no word boxes: nothing can be underlined
    report = GradeReport(total_score=2, max_total=2, overall_remarks="", evidence_contract=True,
                         questions=[QuestionMark(qid="SB1", score=2, max_score=2, page=1, y_fraction=.1,
                                                 anchor_line_id="P1L1", criteria=[])],
                         answer_regions={"sb1": {"qid": "SB1", "line_ids": ["P1L1", "P1L2"]}})
    sheet = WordSlipSheet(items=[
        WordSlip(line_id="P1L1", written="Explainations", correct="Explanations", confidence=.97),
        WordSlip(line_id="P1L2", written="recieve", correct="receive", confidence=.97),
        # The OCR read "Explainations": a different word, so this reading is not confirmed.
        WordSlip(line_id="P1L1", written="Explaination", correct="Explanation", confidence=.99)])
    notes: list = []
    marks, dropped = verify_word_slips(sheet, [page], report, notes_out=notes)
    assert marks == []                                   # no box, no underline
    assert [n["written"] for n in notes] == ["Explainations", "recieve"]
    assert spelling_sentence(notes) == (
        "Check these words: write Explanations, not Explainations and receive, not recieve.")
    assert spelling_sentence([]) == ""
