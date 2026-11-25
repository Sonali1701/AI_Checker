# -*- coding: utf-8 -*-
"""Uday Bihar Board Class 11 Commerce Test-01 against the faculty marks (10 Oct 2026).

- The Bihar faculty want points explained: three correct bare characteristics got
  1.5/3 where the tool gave 3/3, and an accounting equation written as final totals
  only got 0/4 where the tool gave 3.5/4. The CBSE SME's commerce rule 1 says the
  opposite, so the Bihar rules go out only with a paper that names Bihar Board.
- Accountancy 343f6e52: one photo of two notebook pages; the OCR stopped 40% of the
  way down the right page, so Q11 (answered; 2/3 from the faculty) had no OCR line and
  went out as not attempted. The foot of that page is now read again.
- The second examiner found that Q11 and gave it marks, but a ruling on a row recorded
  as not attempted cannot be applied, and the 0 stood without a hold.
"""
import os
import sys
from types import SimpleNamespace

import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
from categories import commerce  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402

BIHAR_FOOTER = "Q1. Who wrote 'Principles of Economics'?\nUday Bihar Board 11th Commerce 2027 (English + Hindi Medium)"


def test_bihar_board_is_read_from_the_paper_not_the_state_name():
    assert commerce.paper_board(BIHAR_FOOTER) == "bihar"
    assert commerce.paper_board("Bihar School Examination Board, Patna") == "bihar"
    assert commerce.paper_board("BSEB Annual Examination 2027") == "bihar"
    # A CBSE paper that only mentions the state is not a Bihar Board paper.
    assert commerce.paper_board("Q4. Name two rivers of Bihar and their tributaries.") == ""
    assert commerce.paper_board("") == ""


def test_bihar_rules_go_out_only_with_a_bihar_commerce_paper():
    import server
    cfg = SimpleNamespace(category="commerce", answer_language="", leniency="standard")
    text = server._with_marking_settings(BIHAR_FOOTER, cfg)
    assert text.startswith(BIHAR_FOOTER)
    assert "BIHAR BOARD MARKING" in text and "replace commerce rule 1" in text
    # A CBSE commerce paper is sent unchanged.
    cbse = "Q1. State any three features of business. (3)"
    assert server._with_marking_settings(cbse, cfg) == cbse
    # Another category never gets them, even on a Bihar paper.
    school = SimpleNamespace(category="school", answer_language="", leniency="standard")
    assert server._with_marking_settings(BIHAR_FOOTER, school) == BIHAR_FOOTER


def test_commerce_rule_1_names_its_bihar_exception():
    rules = categories.get("commerce").prompt.extra_rules
    assert "Never deduct solely because a correct point is brief" in rules
    assert "NOT on a Bihar, UP, MP or Maharashtra" in rules
    bihar = commerce.BIHAR_BOARD_RULES
    for needle in ("earns HALF its", "transaction-wise working earn",
                   "needs its narration", "Never deduct for spelling"):
        assert needle in bihar, needle


def _line(n, text, x0, y0, x1, y1, page=1):
    return LineBox(line_id=f"P{page}L{n}", text=text, bbox=(x0, y0, x1, y1), page=page)


def _spread_343f6e52():
    # The measured layout: 2084 x 1507, left page read to its foot, right page to 607.
    left = [_line(i + 1, f"left {i}", 40, 150 + 100 * i, 1000, 190 + 100 * i) for i in range(13)]
    right = [_line(14 + i, f"right {i}", 1120, 150 + 60 * i, 2000, 190 + 60 * i) for i in range(8)]
    right[-1].bbox = (1205, 567, 1417, 607)
    left[-1].bbox = (105, 1439, 1082, 1499)
    return PageOCR(page=1, image_width=2084, image_height=1507, lines=left + right)


def test_the_unread_foot_of_a_facing_page_is_found():
    page = _spread_343f6e52()
    crops = pipeline._unread_bands(page, 2084, 1507)
    assert len(crops) == 1
    x0, y0, x1, y1 = crops[0]
    assert 1000 <= x0 <= 1120 and y0 == 607 and x1 == 2084 and y1 == 1507
    # A portrait page is never split, and a spread read to both feet needs nothing.
    assert pipeline._unread_bands(page, 1000, 1400) == []
    full = _spread_343f6e52()
    for i, line in enumerate(full.lines[13:]):
        line.bbox = (1120, 150 + 170 * i, 2000, 190 + 170 * i)
    assert pipeline._unread_bands(full, 2084, 1507) == []


def test_the_foot_is_read_again_and_its_lines_follow_that_page(monkeypatch):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (2084, 1507), "white").save(buf, format="PNG")
    page = _spread_343f6e52()
    seen = {}

    def fake_ocr(png, number, **_):
        seen["size"] = Image.open(BytesIO(png)).size
        return PageOCR(page=number, image_width=seen["size"][0], image_height=seen["size"][1],
                       lines=[_line(1, "Drawing - Any cash or assets withdrawn", 50, 60, 900, 110),
                              _line(2, "11. Definition of Accounting Equation", 60, 240, 950, 290)])

    monkeypatch.setattr(pipeline, "ocr_page", fake_ocr)
    monkeypatch.setattr(pipeline, "mark_struck_lines", lambda *a, **k: None)
    cfg = SimpleNamespace(google_token="t", task_id="x")
    pipeline._read_unread_bands([buf.getvalue()], [page], cfg=cfg, filename="c.pdf")
    assert seen["size"][1] == 1507 - 607
    texts = [line.text for line in page.lines]
    assert texts[-2:] == ["Drawing - Any cash or assets withdrawn",
                          "11. Definition of Accounting Equation"]
    added = page.lines[-1]
    assert added.line_id == "P1L23"            # after the 21 lines already read
    assert added.bbox[1] == 607 + 240 and added.bbox[0] > 1000
    assert len({line.line_id for line in page.lines}) == len(page.lines)


def _q(qid, attempted, score=0.0):
    return SimpleNamespace(qid=qid, attempted=attempted, score=score, max_score=3.0, criteria=[])


def test_a_skipped_stretch_inside_a_facing_page_is_read_in_order(monkeypatch):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (2084, 1507), "white").save(buf, format="PNG")
    # The left page of 343f6e52: "Accountancy", "Section - A", then nothing until Q5
    # at 617 (Q1 to Q4 unread); the right page read to its foot.
    left = [_line(1, "Accountancy", 405, 7, 635, 57), _line(2, "Section - A", 474, 169, 650, 198)]
    left += [_line(3 + i, f"left {i}", 40, 617 + 95 * i, 1000, 655 + 95 * i) for i in range(9)]
    right = [_line(12 + i, f"right {i}", 1120, 150 + 140 * i, 2000, 190 + 140 * i) for i in range(10)]
    page = PageOCR(page=1, image_width=2084, image_height=1507, lines=left + right)
    crops = pipeline._unread_bands(page, 2084, 1507)
    assert crops == [(0, 198, crops[0][2], 617)]

    def fake_ocr(png, number, **_):
        return PageOCR(page=number, image_width=1, image_height=1,
                       lines=[_line(1, "1. (B) Luca Pacioli", 50, 20, 400, 60),
                              _line(2, "4. (B) Real A/c", 50, 330, 400, 370)])

    monkeypatch.setattr(pipeline, "ocr_page", fake_ocr)
    monkeypatch.setattr(pipeline, "mark_struck_lines", lambda *a, **k: None)
    pipeline._read_unread_bands([buf.getvalue()], [page], cfg=SimpleNamespace(
        google_token="t", task_id="x"), filename="c.pdf")
    texts = [line.text for line in page.lines]
    assert texts[:5] == ["Accountancy", "Section - A", "1. (B) Luca Pacioli",
                         "4. (B) Real A/c", "left 0"]
    assert page.lines[2].bbox[1] == 198 + 20
    assert len({line.line_id for line in page.lines}) == len(page.lines)


def test_an_answer_the_examiner_found_holds_the_copy():
    q8a, q8b, q11a, q11b = (_q("Q8.alt1", True, 2), _q("Q8.alt2", False),
                            _q("Q11.alt1", False), _q("Q11.alt2", False))
    report = SimpleNamespace(questions=[q8a, q8b, q11a, q11b])
    rows = [(q8a, -1, 2.0), (q8b, -1, 2.0), (q11a, -1, 3.0), (q11b, -1, 3.0)]
    group = lambda gid, a, b: SimpleNamespace(options=[SimpleNamespace(question_ids=[a]),
                                                       SimpleNamespace(question_ids=[b])])
    package = SimpleNamespace(choice_groups=[group("8", "Q8.alt1", "Q8.alt2"),
                                             group("11", "Q11.alt1", "Q11.alt2")])
    unverified = {1: "row 1 awards an answer still recorded as unattempted",
                  2: "row 2 awards an answer still recorded as unattempted",
                  3: "row 3 has not been verified against readable student evidence"}
    # Q8.alt2 is the alternative not taken while Q8.alt1 was answered: not a miss.
    assert pipeline._examiner_found_answers(report, rows, unverified, package) == ["Q11.alt1"]
    assert pipeline._examiner_found_answers(report, rows, {}, package) == []

    held = SimpleNamespace(review_reasons=["Q11.alt1 was marked not attempted, but the "
                                           "second examiner found an answer on the page"])
    issues = preflight._check_review_reasons(held)
    assert [i.code for i in preflight.release_blocking(issues, "commerce")] == ["faculty-review"]


def test_a_mark_beside_the_last_row_of_the_photo_stays_on_the_page():
    import fitz
    import teacher_layout
    from grader import Criterion, GradeReport, QuestionMark
    W, H, PX = 595.0, 842.0, 2.0
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    lines = []
    # Q9's answer: rows down to the foot of the photo, the last one running to the
    # paper's edge, so a pair cannot sit after it.
    for i, (text, x1) in enumerate([("9. What are liabilities?", 300),
                                    ("Liabilities are financial obligations or debts", 585),
                                    ("that a business owes to external parties", 585)]):
        y_base = 790 + 24 * i
        page.insert_text((60, y_base), text, fontsize=14, color=(0.1, 0.1, 0.35))
        page.draw_rect(fitz.Rect(60, y_base - 11, x1, y_base + 3), color=None,
                       fill=(0.15, 0.15, 0.4), fill_opacity=0.35)
        lines.append(LineBox(f"P1L{i + 1}", text, (60 * PX, (y_base - 13) * PX, x1 * PX,
                                                     (y_base + 4) * PX), 1))
    ocr = [PageOCR(1, int(W * PX), int(H * PX), lines)]
    crit = Criterion(description="Debts owed to outsiders", awarded=2, max=2, met=True,
                     step_line_id="P1L3", anchor_quote="")
    q = QuestionMark(qid="Q9", score=2, max_score=2, page=1, anchor_line_id="P1L3",
                     y_fraction=0.0, criteria=[crit])
    report = GradeReport(total_score=2, max_total=2, overall_remarks="Good work.",
                         section_totals=[], questions=[q], evidence_contract=True)
    pdf = teacher_layout.build_teacher_pdf(doc, report, ocr)
    out = fitz.open(stream=pdf, filetype="pdf")
    marks = [r for r in report.render_manifest if r["role"] in ("answer", "step")]
    assert marks
    for row in marks:
        assert out[row["page"] - 1].rect.contains(fitz.Rect(row["bbox"])), row


def test_an_unanswered_choice_mcq_is_not_held_as_credited():
    # "Answer any 15 of 20": Q16 to Q20 were never answered and are not on the report.
    q1 = SimpleNamespace(qid="Q1", score=1.0, max_score=1.0, attempted=True, criteria=[])
    q6 = SimpleNamespace(qid="Q6", score=1.0, max_score=1.0, attempted=True, criteria=[])
    q7 = SimpleNamespace(qid="Q7", score=0.0, max_score=1.0, attempted=False, criteria=[])
    report = SimpleNamespace(questions=[q1, q6, q7], false_blank_qids=[],
                             objective_unconfirmed_qids=["Q6", "Q7", "Q16", "Q17", "Q20"])
    issues = [i for i in preflight._check_coverage(report) if i.code.startswith("objective")]
    assert [i.code for i in issues] == ["objective-unconfirmed"]
    text = str(issues[0])
    assert "Q6" in text and not any(q in text for q in ("Q7", "Q16", "Q17", "Q20"))


def test_an_mcq_row_in_maths_delimiters_or_with_a_garbled_label_is_its_own():
    # Accountancy 832fcb86: "2 - B" read "\( \Omega-B \)", "3 - C" read "\( 3-c \)".
    texts = ["Choose the correct option:-", "Q1 - (1)", r"\( \Omega-B \)", r"\( 3-c \)",
             r"\( 4-B \)", r"\( 5-B \)", r"\( 6-B \)", "7 - financial", "Answer No. - 8"]
    page = PageOCR(1, 1000, 1400, [_line(i + 1, t, 50, 100 + 40 * i, 400, 130 + 40 * i)
                                   for i, t in enumerate(texts)])
    questions = [SimpleNamespace(qid=f"Q{n}", objective=True, attempted=True, score=1.0,
                                 max_score=1.0, anchor_line_id=f"P1L{n + 1}", correct_option="B")
                 for n in range(1, 7)]
    report = SimpleNamespace(questions=questions, objective_visual_verified_qids=[])
    assert preflight._check_objective_anchor_ownership(report, [page]) == []
    # A row labelled with another number is never taken for this question's.
    questions[2].anchor_line_id = "P1L5"            # Q3 on "\( 4-B \)"
    issues = preflight._check_objective_anchor_ownership(report, [page])
    assert {i.code for i in issues} >= {"objective-anchor-mismatch"}
    assert any("Q3 is anchored to P1L5" in str(i) for i in issues)


def test_one_journal_entry_inside_a_table_block_may_be_two_rows_tall():
    from presentation_layout import BLOCK_ENTRY_ROW, TIGHT_ROW, row_box_limit, valid_visual_box
    page = PageOCR(2, 1240, 1755, [])
    journal = LineBox("P2D12", r"\begin{tabular}...", (69, 627, 1200, 1707), 2, kind="table")
    prose = LineBox("P2L3", "Capital is the amount invested", (69, 300, 1200, 345), 2)
    assert row_box_limit(journal) == BLOCK_ENTRY_ROW and row_box_limit(prose) == TIGHT_ROW
    assert row_box_limit({"lid": "P2D12"}) == BLOCK_ENTRY_ROW
    entry = [0.06, 0.40, 0.95, 0.51]            # Jan 2 entry with its narration: 11%
    assert valid_visual_box(page, journal, entry)
    assert not valid_visual_box(page, journal, [0.06, 0.40, 0.95, 0.60])   # two entries
    # A prose row's box must hold that row: one drawn rows below it names another.
    assert not valid_visual_box(page, prose, [0.06, 0.30, 0.95, 0.36])


def test_an_mcq_table_gives_each_question_its_own_row():
    import fitz
    import pdf_renderer as pr
    assert [pr._objective_item_number(q) for q in ("Q1.1", "Q1.4", "Q1.10", "Q7")] == [1, 4, 10, 7]
    assert pr._objective_lane_key("Q1.2") < pr._objective_lane_key("Q1.10")
    assert pr._row_number("Q. 4. & (A) & Primary Data") == 4
    rows_text = ["Q. 1 & (A) & A. Morsholl", "Q. 2. & (B) & They are searce",
                 "A. 8. & (D) & All of the above", "Q. 4. & (A) & Primary Data"]
    text = ("\\begin{tabular}[t]{|l|l|l|}\n"
            + "".join(f"\\hline {row} \\\\\n" for row in rows_text) + "\\hline\n\\end{tabular}")
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    block = {"lid": "P1D7", "page": 1, "text": text, "x0": 30, "y0": 240, "x1": 500, "y1": 340}
    rows = pr._array_rows(doc, block, {})
    assert [r["_array_row"] for r in rows] == [0, 1, 2, 3]
    assert "begin" not in rows[0]["text"] and "Morsholl" in rows[0]["text"]
    # Four MCQs cite the block: the third row is Q1.3's although OCR read "A. 8.".
    questions = [SimpleNamespace(qid=f"Q1.{n}", objective=True, attempted=True, score=1.0,
                                 max_score=1.0, anchor_line_id="P1D7", correct_option="A")
                 for n in range(1, 5)]
    token = pr._RENDER_REPORT.set(SimpleNamespace(questions=questions, answer_regions={},
                                                  objective_visual_verified_qids=[]))
    try:
        picked = [pr._objective_row(doc, q, block, {"P1D7": block}) for q in questions]
    finally:
        pr._RENDER_REPORT.reset(token)
    assert [p["_array_row"] if p else None for p in picked] == [0, 1, 2, 3]


def test_a_lone_option_on_the_answer_row_is_the_option_not_a_label():
    page = PageOCR(1, 1000, 1400, [_line(1, "(A)", 50, 100, 120, 130), _line(2, "1)", 50, 140, 90, 170)])
    q1 = SimpleNamespace(qid="Q1.1", objective=True, score=1.0, anchor_line_id="P1L1",
                         chosen_option="A")
    q2 = SimpleNamespace(qid="Q1.2", objective=True, score=1.0, anchor_line_id="P1L2",
                         chosen_option="B")
    issues = preflight._check_bare_objective(SimpleNamespace(questions=[q1, q2]), [page])
    assert len(issues) == 1 and "Q1.2" in str(issues[0]) and "Q1.1" not in str(issues[0])
