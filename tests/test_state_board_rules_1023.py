# -*- coding: utf-8 -*-
"""UP Board, MP Board and Maharashtra HSC commerce papers get their faculty's marking.

Checked against the faculty's own copies on 21 and 22 Oct 2026 (sheet rows 42 to
101): the tool, on CBSE rule 1, was 2.4 marks above them on average in Economics and
Business Studies, and released copies were up to 6.5 marks over.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from categories import commerce  # noqa: E402

FOOTERS = {
    "UP Board": "Uday UP Board 11th Commerce 2027 (English + Hindi Medium)",
    "MP Board": "Parishram Pro MP Board 12th Commerce 2027 (Hindi Medium)",
    "Maharashtra HSC": "Parishram Maharashtra HSC Commerce 2027 (English Medium)",
}


def test_each_board_is_named_from_the_papers_own_footer():
    for board, footer in FOOTERS.items():
        assert commerce.paper_board("Q1. State three features. (3)\n" + footer) == board
    assert commerce.paper_board("UDAY PRO Maharashtra Board 11th Commerce") == "Maharashtra HSC"
    assert commerce.paper_board("Uday Bihar Board 11th Commerce 2027") == "bihar"
    # A state's name in a question is not its board.
    assert commerce.paper_board("Q4. Name two crops grown in Maharashtra and UP.") == ""
    assert commerce.paper_board("CBSE Class 12 Business Studies") == ""


def test_the_rules_name_the_faculty_standard_and_the_board():
    rules = commerce.board_rules(FOOTERS["UP Board"])
    assert "names UP Board" in rules and "replace" in rules and "commerce rule 1" in rules
    for needle in ("earns HALF its share", "two definitions with no basis of difference",
                   "both axes drawn and named", "the meaning AND an example",
                   "Privatisation", "matches the key's own steps", '"sudden" for "abrupt"',
                   "never name these rules"):
        assert needle in rules, needle
    # Bihar keeps its own block, English rules and all.
    assert commerce.board_rules("Uday Bihar Board 11th Commerce") == commerce.BIHAR_BOARD_RULES


def test_the_server_sends_them_only_with_a_commerce_paper():
    cfg = SimpleNamespace(category="commerce", answer_language="", leniency="standard")
    text = server._with_marking_settings("Q1. Explain. (3)\n" + FOOTERS["MP Board"], cfg)
    assert "STATE BOARD MARKING (this question paper names MP Board)" in text
    school = SimpleNamespace(category="school", answer_language="", leniency="standard")
    paper = "Q1. Explain. (3)\n" + FOOTERS["MP Board"]
    assert server._with_marking_settings(paper, school) == paper
