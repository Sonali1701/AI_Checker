"""A same-sheet comparison is two columns, even without a notebook fold."""
import fitz
import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR
from teacher_layout import TeacherLayout


def comparison_fixture():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # OCR ordering alternates between columns; one later left-hand row is merged
    # too widely. The surviving right-hand point must not end on that left row.
    rows = [
        ("P1L1", "Capitalism follows private ownership", (80, 140, 302, 166)),
        ("P1L2", "Socialism follows collective ownership", (307, 149, 520, 175)),
        ("P1L3", "and focuses on profit.", (78, 175, 292, 201)),
        ("P1L4", "another left-column claim about welfare", (78, 204, 466, 230)),
    ]
    for _, text, box in rows:
        page.insert_text((box[0], box[1] + 14), text, fontsize=9)
    ocr = [PageOCR(1, 595, 842, [LineBox(lid, text, box, 1) for lid, text, box in rows])]
    criteria = [Criterion(description="Private ownership", awarded=1, max=1, met=True,
                          step_line_id="P1L1", anchor_quote=rows[0][1]),
                Criterion(description="Collective ownership", awarded=1, max=1, met=True,
                          step_line_id="P1L2", anchor_quote=rows[1][1])]
    question = QuestionMark(qid="Q9", score=2, max_score=2, page=1,
                            anchor_line_id="P1L3", y_fraction=.2, criteria=criteria,
                            mark_style="per_step")
    report = GradeReport(total_score=2, max_total=2, questions=[question],
                         overall_remarks="", section_totals=[], evidence_contract=True)
    return doc, ocr, report


def test_wrapping_never_crosses_a_same_sheet_comparison_column():
    doc, ocr, report = comparison_fixture()
    layout = TeacherLayout(doc, report, ocr)
    # This is a single sheet. The fix cannot rely on detected notebook folds.
    layout.folds = {}
    left, right = layout.lines["P1L1"], layout.lines["P1L2"]
    assert not layout.same_column(left, right)
    assert not layout.same_column(right, layout.lines["P1L4"])
    assert layout.point_end(right, {"P1L1"}).lid == "P1L2"
    assert layout.point_end(left, {"P1L2"}).lid == "P1L3"
    assert layout.edge_for(left) <= right.rect.x0 - 2
    assert layout.edge_for(layout.lines["P1L3"]) <= right.rect.x0 - 2
    doc.close()


def test_side_by_side_steps_keep_separate_marks_and_unchanged_awards():
    doc, ocr, report = comparison_fixture()
    layout = TeacherLayout(doc, report, ocr)
    layout.folds = {}
    layout.build()
    steps = [row for row in report.render_manifest if row["role"] == "step"]
    assert len(steps) == 2
    right = next(row for row in steps if row["criteria"] == [1])
    assert right["line_id"] == right["end_line_id"] == "P1L2"
    assert right["anchor_bbox"][0] >= 300
    assert [row["score"] for row in steps] == [1, 1]
    assert report.total_score == report.questions[0].score == 2
    doc.close()
