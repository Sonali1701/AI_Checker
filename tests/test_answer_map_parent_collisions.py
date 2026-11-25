"""Question ownership survives OCR list/roman/option collisions on real copies."""
from types import SimpleNamespace

import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

from answer_map import build_answer_map
from mathpix import LineBox, PageOCR


def package(qids=None):
    qids = qids or [f"Q{n}" for n in range(1, 16)]
    return SimpleNamespace(
        package_id="source-bound-test-paper",
        questions=[SimpleNamespace(
            qid=qid, question_type="objective" if qid in {
                f"Q{n}" for n in range(1, 7)} else "theory") for qid in qids],
    )


def page(number, texts):
    return PageOCR(page=number, image_width=1000, image_height=1400, lines=[
        LineBox(line_id=f"P{number}L{i}", text=text,
                bbox=(20, i * 30, 800, i * 30 + 20), page=number)
        for i, text in enumerate(texts, 1)
    ])


def mapped(pages, qids=None):
    return {row.qid: row for row in build_answer_map(package(qids), pages).locations}


def test_roman_ii_misread_as_11_stays_under_its_explicit_answer():
    rows = mapped([page(1, [
        "Answer 7", "Soviet system means government system",
        "(i) government rule and social welfare.",
        "(11) It focus in society, equality etc.",
        "Answer 8", "Planned development",
    ]), page(2, ["Answer-11", "In 1985 Asean established."])])
    assert "P1L4" in rows["Q7"].line_ids
    assert "P1L4" in rows["Q7"].uncertain_line_ids
    assert rows["Q11"].pages == (2,)
    assert rows["Q11"].label_line_ids == ("P2L1",)
    assert "P1L4" not in rows["Q11"].line_ids


def test_numbered_cause_does_not_move_long_answer_to_earlier_mcq():
    rows = mapped([page(1, ["Answer 1", "(B) Jawaharlal Nehru"]),
                   page(2, [
                       "Answer - 15", "Three major reasons for collapse",
                       "(1) The Gorbachev reforms",
                       "people thought reforms were slow",
                       "(d) Every country want independence",
                       "Low economy because of nuclear weapon spending",
                   ])])
    assert rows["Q1"].pages == (1,)
    assert rows["Q15"].line_ids == tuple(f"P2L{i}" for i in range(1, 7))
    assert "P2L3" in rows["Q15"].uncertain_line_ids


def test_long_prose_bullet_misread_as_mcq_number_stays_in_descriptive_answer():
    rows = mapped([page(1, [
        "Answer 1", "(B) Jawaharlal Nehru", "Answer - (14)",
        "Kashmir conflict", "1. On the basis of Sindh river, for water both sides",
        "disputed water sharing", "Answer 15", "Soviet collapse",
    ])])
    assert rows["Q1"].line_ids == ("P1L1", "P1L2")
    assert rows["Q14"].line_ids == ("P1L3", "P1L4", "P1L5", "P1L6")
    assert "P1L5" in rows["Q14"].uncertain_line_ids


def test_short_bare_objective_value_and_option_return_remain_headings():
    rows = mapped([page(1, [
        "Answer 1", "Nehru", "Answer 15", "long answer",
        "1.) Jawaharlal Nehru", "Answer 15", "long answer again",
        "1.) (B) Jawaharlal Nehru is the correct value",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P1L5", "P1L8")


def test_restarted_passage_option_list_keeps_aggregate_parent_and_uncertainty():
    rows = mapped([page(1, [
        "Q1 (B) Jawaharlal Nehru", "Q2 (C) Andhra Pradesh",
        "Q3 (A) 1949", "Q4 (D) Berlin Wall",
    ]), page(2, [
        "Q13", "1)(C) Shaping a united yet diverse nation",
        "2)(B) Due to continental size and extreme diversity",
        "3)(B) Sardar Vallabhbhai Patel", "4)(B) The Muslim league",
        "4)(B) The Muslim league", "Q14", "Kashmir conflict",
    ])])
    for number in range(1, 5):
        assert rows[f"Q{number}"].pages == (1,)
    assert rows["Q13"].line_ids == tuple(f"P2L{i}" for i in range(1, 7))
    assert rows["Q13"].uncertain_line_ids == tuple(f"P2L{i}" for i in range(2, 7))


def test_section_boundary_preserves_restarted_objective_list():
    rows = mapped([page(1, [
        "Q1 (B) old response", "Q2 (C) old response", "Q3 (A) old response",
        "Q13", "Passage answer", "Section-A",
        "1)(C) new response", "2)(B) new response", "3)(B) new response",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P1L7")
    assert rows["Q2"].label_line_ids == ("P1L2", "P1L8")
    assert rows["Q3"].label_line_ids == ("P1L3", "P1L9")


def test_page_opening_option_list_and_explicit_return_are_preserved():
    rows = mapped([page(1, [
        "Q1 (B) old response", "Q2 (C) old response", "Q3 (A) old response",
        "Q13", "Passage answer",
    ]), page(2, [
        "1)(C) revised response", "2)(B) revised response", "3)(B) revised response",
        "Q13", "Passage answer", "Question (1) (B) explicit return",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P2L1", "P2L6")
    assert rows["Q2"].label_line_ids == ("P1L2", "P2L2")
    assert rows["Q3"].label_line_ids == ("P1L3", "P2L3")


def test_option_sequence_without_explicit_standalone_proof_keeps_labels():
    rows = mapped([page(1, [
        "Q13", "1)(C) one", "2)(B) two", "3)(B) three",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L2",)
    assert rows["Q2"].label_line_ids == ("P1L3",)
    assert rows["Q3"].label_line_ids == ("P1L4",)


def test_mcq_option_b_misread_as_8_does_not_steal_passage_subparts():
    rows = mapped([page(1, [
        "Answer 3", "1950", "Answer 4", "Berlin Wall",
        "Answer 8", "Planned development", "Ans-13", "Ans. (2)", "(c) United diverse nation",
        "(ii) (2)", "(8) Due to its continental size and extreme diversity.",
        "(3)", "(B) Sardar vallabhai patel.",
        "(4)", "(B) muslim league.",
        "Answer - (14)", "Kashmir conflict",
    ])])
    assert rows["Q8"].line_ids == ("P1L5", "P1L6")
    assert rows["Q3"].line_ids == ("P1L1", "P1L2")
    assert rows["Q4"].line_ids == ("P1L3", "P1L4")
    assert rows["Q13"].line_ids == tuple(f"P1L{i}" for i in range(7, 16))
    assert rows["Q14"].label_line_ids == ("P1L16",)


def test_explicit_return_to_an_earlier_question_remains_a_real_heading():
    rows = mapped([page(1, [
        "Answer 1", "first answer", "Answer 15", "long answer",
        "Question (1)", "explicit revised answer",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P1L5")
    assert rows["Q1"].line_ids == ("P1L1", "P1L2", "P1L5", "P1L6")
    assert rows["Q15"].line_ids == ("P1L3", "P1L4")


def test_bare_out_of_order_question_heading_is_preserved():
    rows = mapped([page(1, [
        "Answer 15", "long answer", "11.) ASEAN established in 1967",
        "economic growth and peace", "Answer 11", "another explicit attempt",
    ])])
    assert rows["Q11"].label_line_ids == ("P1L3", "P1L5")
    assert rows["Q15"].line_ids == ("P1L1", "P1L2")


def test_unique_parenthesized_question_is_not_suppressed_without_other_heading():
    rows = mapped([page(1, ["Answer 15", "long answer", "(11) ASEAN answer"])])
    assert rows["Q11"].label_line_ids == ("P1L3",)


def test_page_opening_number_keeps_existing_heading_evidence():
    rows = mapped([page(1, ["Answer 1", "Nehru", "Answer 15", "long answer"]),
                   page(2, ["(1) a separately numbered return", "more writing"])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P2L1")


def test_fresh_page_option_answer_after_theory_keeps_its_explicit_objective():
    rows = mapped([page(1, [
        "Q1 (B) old response", "Answer15", "long descriptive answer",
    ]), page(2, [
        "(1) (B) Jawaharlal Nehru is the correct historical leader",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L1", "P2L1")


def test_known_official_child_number_retains_child_context_not_objective_parent():
    rows = mapped([page(1, [
        "Answer 1", "(B) objective response", "Answer 15",
        "(1) first official subpart", "working for that subpart",
    ])], ["Q1", "Q15.1", "Q15.2"])
    assert rows["Q1"].line_ids == ("P1L1", "P1L2")
    assert rows["Q15.1"].label_line_ids == ("P1L4",)
    assert rows["Q15.1"].line_ids == ("P1L4", "P1L5")
    assert "P1L4" in rows["Q15.1"].uncertain_line_ids
    assert rows["Q15.2"].status == "unresolved"


def test_section_boundary_ends_the_explicit_parent_context():
    rows = mapped([page(1, [
        "Answer 15", "long answer", "Section - A", "(1) objective return",
        "Answer 1", "another explicit response",
    ])])
    assert rows["Q1"].label_line_ids == ("P1L4", "P1L5")


def test_cancelled_explicit_heading_cannot_supply_false_collision_proof():
    p = page(1, ["Answer 7", "(11) genuine question answer", "Answer 11"])
    p.lines[-1].cancelled = True
    rows = mapped([p])
    assert rows["Q11"].label_line_ids == ("P1L2",)


def test_late_table_column_above_next_explicit_heading_keeps_previous_owner():
    p = PageOCR(page=1, image_width=1200, image_height=1500, lines=[
        LineBox(line_id="P1L1", text="Answer-9", bbox=(400, 100, 550, 140), page=1),
        LineBox(line_id="P1L2", text="Capitalism", bbox=(150, 160, 400, 200), page=1),
        LineBox(line_id="P1L3", text="private ownership and profit", bbox=(150, 210, 560, 260), page=1),
        LineBox(line_id="P1L4", text="Section-C Answer: 10", bbox=(400, 400, 650, 450), page=1),
        LineBox(line_id="P1L5", text="Socialism", bbox=(680, 160, 950, 200), page=1),
        LineBox(line_id="P1L6", text="public welfare and equality", bbox=(680, 210, 1100, 260), page=1),
        LineBox(line_id="P1L7", text="united yet diverse nation", bbox=(150, 490, 1100, 550), page=1),
    ])
    rows = mapped([p])
    assert rows["Q9"].line_ids == ("P1L1", "P1L2", "P1L3", "P1L5", "P1L6")
    assert rows["Q10"].line_ids == ("P1L4", "P1L7")
    assert [l.line_id for l in p.lines] == [f"P1L{i}" for i in range(1, 8)]


def test_late_text_below_or_overlapping_next_heading_is_not_moved():
    p = PageOCR(page=1, image_width=1200, image_height=1500, lines=[
        LineBox(line_id="P1L1", text="Answer 9", bbox=(400, 100, 550, 140), page=1),
        LineBox(line_id="P1L2", text="left answer", bbox=(100, 200, 500, 460), page=1),
        LineBox(line_id="P1L3", text="Answer 10", bbox=(400, 400, 650, 440), page=1),
        LineBox(line_id="P1L4", text="right text overlaps heading", bbox=(700, 380, 1100, 420), page=1),
        LineBox(line_id="P1L5", text="right text below heading", bbox=(700, 450, 1100, 490), page=1),
    ])
    rows = mapped([p])
    assert rows["Q10"].line_ids == ("P1L3", "P1L4", "P1L5")
