# -*- coding: utf-8 -*-
"""The free marks reader on a teaching mock: the numbered instructions are not Q1 to Q4.

Every KVS/NVS/EMRS mock of the "AI Copy cheking feedback" sheet (21 Oct 2026) opens
with "Instructions: 1. This subjective test contains ... 4. Ensure your handwriting is
legible." and then numbers its questions from 1 again. The marks came out right (10 x
4 = 40) but Q1 to Q4 took the instruction lines as their text, and that text is
written into the grading prompt next to each question's maximum.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402

PAPER = """Mock Test - 01
Total Marks: 40
Instructions:
1. This subjective test contains a total of 10 questions, and each question carries 4 marks.
2. All questions are compulsory, there is no overall choice.
3. Show your workings clearly where necessary.
4. Ensure your handwriting is legible.
(Subjective Questions)
1. Comment on the volume and direction of trade at the time of independence. (4 Marks)
2. When does a firm shut down in the short run under perfect competition ? (4 Marks)
3. Explain the law of variable proportions. (4 Marks)
4. Distinguish between stock and flow variables. (4 Marks)
5. What is a production possibility curve? (4 Marks)
"""


def test_the_instruction_lines_are_not_the_questions_text():
    scheme = grader.marks_scheme_from_text([PAPER])
    by_qid = {item.qid: item for item in scheme.items}
    assert [item.qid for item in scheme.items] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert all(item.max == 4.0 for item in scheme.items)
    assert by_qid["Q1"].description.startswith("Comment on the volume and direction of trade")
    assert by_qid["Q4"].description.startswith("Distinguish between stock and flow")
    assert not any("handwriting" in item.description or "subjective test" in item.description
                   for item in scheme.items)


def test_a_paper_numbered_once_keeps_its_text():
    paper = PAPER.split("(Subjective Questions)\n", 1)[1]
    scheme = grader.marks_scheme_from_text([paper])
    assert scheme.items[0].description.startswith("Comment on the volume")
    assert len(scheme.items) == 5


def test_a_hindi_tag_line_is_not_a_questions_text():
    # KVS Biology: the number stands alone on its line and the bilingual question ends
    # with a "(4 अंक)" line; that tag came back as Q1's text.
    paper = ("1. \nExplain the five kingdom classification. \nपाँच जगत वर्गीकरण की व्याख्या कीजिए। \n"
             "(4 अंक) \n2. \nDescribe the structure of a neuron. \n(4 अंक) \n")
    scheme = grader.marks_scheme_from_text([paper])
    assert [(item.qid, item.max) for item in scheme.items] == [("Q1", 4.0), ("Q2", 4.0)]
    assert not any("अंक" in item.description for item in scheme.items)
    # The text under the bare number is the question's text: the answer inventory files
    # each answer by comparing it with this (answer_inventory.py rule 7).
    assert scheme.items[0].description.startswith("Explain the five kingdom classification")
    assert scheme.items[1].description.startswith("Describe the structure of a neuron")


def test_a_sections_product_tag_is_not_counted_again_with_its_questions_tags():
    # UP LT Grade Home Science Mock 1: each section prices itself and every question
    # prints its own marks too. Read twice, the 200-mark paper came to 372 and the
    # right reading (A1-A10 = 8, B1-B10 = 12) was refused as ai_mismatch.
    def section(name, per, n):
        rows = "".join(f"{i}.\nQuestion {name}{i} text.\n{per} Marks\n" for i in range(1, n + 1))
        return f"Section {name} (Short-answer Questions)\n({n} × {per} Marks = {n * per} Marks)\n" + rows
    paper = "Total Marks: 200\n" + section("A", 8, 10) + section("B", 12, 10)
    assert grader.printed_tag_sum(paper)[0] == 200.0
    # Two questions printing the tag at the end of a text line, not on its own line,
    # still leave the section worth its product.
    shorter = paper.replace("Question A3 text.\n8 Marks", "Question A3 text. 8 Marks")
    assert grader.printed_tag_sum(shorter)[0] == 200.0
    # The instructions price the same questions again in words ("10 short-answer
    # questions of 8 marks each"); on build 122 that "each" rule still added 200, so the
    # right reading was refused although the tags now read 200.
    worded = ("Instructions:\n2. Section A features 10 short-answer questions of 8 marks each.\n"
              "3. Section B features 10 long-answer questions of 12 marks each.\n" + shorter)
    rows = ([grader.MarksItem(qid=f"A{i}", max=8) for i in range(1, 11)]
            + [grader.MarksItem(qid=f"B{i}", max=12) for i in range(1, 11)])
    assert grader.each_rule_allowance(rows, worded) == 0.0
    assert grader.extraction_reconciles_with_tags(
        grader.MarksScheme(total=200, items=rows), worded, 200.0, False)


def test_a_second_section_numbered_from_1_keeps_the_first_sections_text():
    # The list is dropped only before the first marks tag: a Section B that numbers
    # from 1 again after tagged questions changes nothing that was read already.
    paper = PAPER.split("(Subjective Questions)\n", 1)[1] + "Section B\n1. Explain GDP. (4 Marks)\n"
    scheme = grader.marks_scheme_from_text([paper])
    assert scheme.items[0].description.startswith("Comment on the volume")
