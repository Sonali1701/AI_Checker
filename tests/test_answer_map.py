# -*- coding: utf-8 -*-
"""OCR answer mapping is navigation evidence and never invents a blank."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from answer_map import answer_map_prompt_block, build_answer_map
from exam_package import build_exam_package
from grader import MarksItem, MarksScheme
from mathpix import LineBox, PageOCR

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


scheme = MarksScheme(total=15, items=[MarksItem(qid="Q1.a", max=5),
                                      MarksItem(qid="Q1.b", max=5),
                                      MarksItem(qid="Q2", max=5)])
package = build_exam_package(
    exam_type="school", student_class="Class 10", subject="Science", level="10",
    paper="Term 1", attempt="2026", applicable_law_date=None, total_marks=15,
    question_paper=b"paper", answer_key=b"key", key_source="upload",
    marks_scheme=scheme, scheme_source="text",
)
pages = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
    LineBox(line_id="P1L1", text="Q.1 (a)", bbox=(0, 0, 100, 20), page=1),
    LineBox(line_id="P1L2", text="student working", bbox=(0, 30, 300, 50), page=1),
])]
mapped = build_answer_map(package, pages)
by_qid = {item.qid: item for item in mapped.locations}
check("the exact official Q1(a) sub-part is located",
      by_qid["Q1.a"].status == "located" and by_qid["Q1.a"].pages == (1,), by_qid)
check("Q1(a) does not falsely locate sibling Q1(b)",
      by_qid["Q1.b"].status == "unresolved", by_qid)
check("missing Q2 label is unresolved, not blank", by_qid["Q2"].status == "unresolved")
prompt = answer_map_prompt_block(mapped)
check("provider is told unresolved never means blank", "never means blank" in prompt)
check("unresolved qid is visible to the grader", "Q2" in prompt)

# Exact Mathpix lines from the 25-mark Economics sheet: ``N.)`` is the student's
# normal label form, and the handwritten 7 was OCR'd as a second 1 between 6 and 8.
sequence_scheme = MarksScheme(
    total=8,
    items=[MarksItem(qid=f"Q{number}", max=1) for number in range(1, 9)],
)
sequence_package = build_exam_package(
    exam_type="school", student_class="Class 11", subject="Economics", level="11",
    paper="Unit Test", attempt="", applicable_law_date=None, total_marks=8,
    question_paper=b"economics-paper", answer_key=b"economics-key",
    key_source="upload", marks_scheme=sequence_scheme, scheme_source="ai_verified",
    question_graph_proof="dual-provider-proof",
)
seen_numbers = [1, 2, 3, 4, 5, 6, 1, 8]
sequence_pages = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
    LineBox(line_id=f"P1L{number}", text=f"{seen}.) answer {number}",
            bbox=(0, number * 30, 300, number * 30 + 20), page=1)
    for number, seen in enumerate(seen_numbers, start=1)
])]
sequence_map = build_answer_map(sequence_package, sequence_pages)
sequence_by_qid = {item.qid: item for item in sequence_map.locations}
check("Mathpix N.) labels map to the official graph",
      all(sequence_by_qid[f"Q{number}"].status == "located"
          for number in range(1, 9)), sequence_by_qid)
check("a duplicated OCR 1 bracketed by 6 and 8 is repaired to Q7",
      sequence_by_qid["Q7"].label_line_ids == ("P1L7",), sequence_by_qid["Q7"])
check("the real Q1 label is not duplicated by the repaired Q7",
      sequence_by_qid["Q1"].label_line_ids == ("P1L1",), sequence_by_qid["Q1"])

subpart_scheme = MarksScheme(total=9, items=[
    MarksItem(qid="Q5", max=1), MarksItem(qid="Q14", max=2),
    MarksItem(qid="Q15", max=2), MarksItem(qid="Q16.i", max=1),
    MarksItem(qid="Q16.ii", max=1), MarksItem(qid="Q16.iii", max=2),
])
subpart_package = build_exam_package(
    exam_type="school", student_class="Class 11", subject="Economics", level="11",
    paper="Unit Test", attempt="", applicable_law_date=None, total_marks=9,
    question_paper=b"subpart-paper", answer_key=b"subpart-key", key_source="upload",
    marks_scheme=subpart_scheme, scheme_source="ai_verified",
    question_graph_proof="dual-provider-proof",
)
subpart_pages = [
    PageOCR(page=1, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P1L1", text="5.) earlier answer", bbox=(0, 0, 300, 20), page=1),
        LineBox(line_id="P1L2", text="16.) Given", bbox=(0, 30, 300, 50), page=1),
        LineBox(line_id="P1L3", text="(i) equilibrium", bbox=(0, 60, 300, 80), page=1),
        LineBox(line_id="P1L4", text="(ii) income spent", bbox=(0, 90, 300, 110), page=1),
    ]),
    PageOCR(page=2, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P2L1", text="(iii) friend suggestion", bbox=(0, 0, 300, 20), page=2),
        LineBox(line_id="P2L2", text="14.) primary data", bbox=(0, 30, 300, 50), page=2),
    ]),
    PageOCR(page=3, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P3L1", text="(5.) variable system", bbox=(0, 0, 300, 20), page=3),
    ]),
]
subpart_map = build_answer_map(subpart_package, subpart_pages)
subpart_by_qid = {item.qid: item for item in subpart_map.locations}
check("standalone roman labels inherit the active official parent across pages",
      [subpart_by_qid[f"Q16.{roman}"].label_line_ids for roman in ("i", "ii", "iii")]
      == [("P1L3",), ("P1L4",), ("P2L1",)], subpart_by_qid)
check("an OCR-dropped leading 1 after Q14 is repaired from Q5 to Q15",
      subpart_by_qid["Q15"].label_line_ids == ("P3L1",), subpart_by_qid["Q15"])
check("the genuine earlier Q5 label remains Q5",
      subpart_by_qid["Q5"].label_line_ids == ("P1L1",), subpart_by_qid["Q5"])

# Exact second Mathpix reading from the live sheet.  The handwritten ``16)`` is the
# first label on photographed page 2, but OCR drops the leading 1 and returns ``(6.)``.
aggregate_scheme = MarksScheme(
    total=16,
    items=[MarksItem(qid=f"Q{number}", max=1) for number in range(1, 17)],
)
aggregate_package = build_exam_package(
    exam_type="school", student_class="Class 11", subject="Economics", level="11",
    paper="Unit Test", attempt="", applicable_law_date=None, total_marks=16,
    question_paper=b"aggregate-paper", answer_key=b"aggregate-key",
    key_source="upload", marks_scheme=aggregate_scheme, scheme_source="ai_verified",
    question_graph_proof="dual-provider-proof",
)
aggregate_pages = [
    PageOCR(page=1, image_width=1000, image_height=1400, lines=[
        LineBox(line_id=f"P1L{number}", text=f"{number}.) answer",
                bbox=(0, number * 30, 300, number * 30 + 20), page=1)
        for number in range(1, 14)
    ]),
    PageOCR(page=2, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P2L2", text="(6.)", bbox=(0, 30, 80, 50), page=2),
        LineBox(line_id="P2L3", text="given working", bbox=(90, 30, 400, 50), page=2),
    ]),
    PageOCR(page=3, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P3L1", text="14.) answer", bbox=(0, 30, 300, 50), page=3),
    ]),
    PageOCR(page=4, image_width=1000, image_height=1400, lines=[
        LineBox(line_id="P4L1", text="15.) answer", bbox=(0, 30, 300, 50), page=4),
    ]),
]
aggregate_map = build_answer_map(aggregate_package, aggregate_pages)
aggregate_by_qid = {item.qid: item for item in aggregate_map.locations}
check("a page-opening OCR (6.) after Q10-Q13 is repaired to aggregate Q16",
      aggregate_by_qid["Q16"].label_line_ids == ("P2L2",), aggregate_by_qid["Q16"])
check("the genuine page-one Q6 label remains attached only to Q6",
      aggregate_by_qid["Q6"].label_line_ids == ("P1L6",), aggregate_by_qid["Q6"])

same_page_lines = list(aggregate_pages[0].lines) + [
    LineBox(line_id="P1L14", text="(6.) repeated note",
            bbox=(0, 450, 300, 470), page=1)
]
same_page_map = build_answer_map(
    aggregate_package,
    [PageOCR(page=1, image_width=1000, image_height=1400, lines=same_page_lines)],
)
same_page_by_qid = {item.qid: item for item in same_page_map.locations}
check("an ordinary same-page duplicate Q6 is not promoted to Q16",
      same_page_by_qid["Q16"].status == "unresolved", same_page_by_qid["Q16"])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
