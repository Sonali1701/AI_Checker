# -*- coding: utf-8 -*-
"""The coverage gate: an answer the marker never read must not pass as a blank.

Taken from the Class 12 Chemistry copy that shipped at 25/70 with TWENTY answered
sub-parts recorded as `attempted: false` — 25 marks, the whole of the student's
score again. Every repair layer in the pipeline reads the model's REPORT; none of
them read the page, so the miss was invisible to all of them while the OCR had the
text the whole time.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (GradeReport, QuestionMark, _answered_question_numbers,
                    _student_label_num, find_unmarked_answers)
from mathpix import LineBox, PageOCR

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def page(num, texts):
    return PageOCR(page=num, image_width=1000, image_height=1400,
                   lines=[LineBox(line_id=f"P{num}L{i}", text=t,
                                  bbox=(0, i * 40, 900, i * 40 + 30), page=num)
                          for i, t in enumerate(texts, start=1)])


# --------------------------------------------------------------------------
print("\nquestion labels a student actually writes")
# Every one of these is a real line from the Chemistry sheet.
for text, want in [
    ("Q-17: (i) 1-bromobutane", 17),
    ("Q -18 →", 18),
    ("18→(ii)", 18),
    ("Q-19→ (i) CH3-CH2-CH2-I", 19),
    ("Q-20→ (i) (A) CH3Br", 20),
    ("Q-21→ (i) A→", 21),
    ("Q-6:", 6),
    ("Q.12", 12),
    ("Q 15", 15),
    ("Answer-2", 2),
    ("Ans 3", 3),
    ("Answer No. 4", 4),
    ("12) Haloarenes are aliphatic hydrocarbons", 12),
    ("17.", 17),
    ("उत्तर 5", 5),
]:
    check(f"label {text!r} -> {want}", _student_label_num(text) == want,
          f"got {_student_label_num(text)}")

# --------------------------------------------------------------------------
print("\nchemistry answers that must NOT read as labels")
# The expensive failure: a false label opens a region and mis-attributes every
# line under it. IUPAC names begin with exactly the digit-and-hyphen shape.
for text in [
    "1-bromobutane",
    "2-chloro-2-methylpropane",
    "3-chloropentane will undergo SN1 reaction faster",
    "2,3-dimethylbutane",
    "1,2-dichloroethane is the answer",
    "2.5 g of NaCl was dissolved",
    "Section-E",
    "C2H5",
    "CH3 - CH2 - NC",
    "(iii)",
]:
    check(f"not a label: {text!r}", _student_label_num(text) is None,
          f"read as Q{_student_label_num(text)}")

# --------------------------------------------------------------------------
print("\nregions: content is attributed to the label above it")
pages = [page(6, [
    "Section-E",
    "Q-17: (i) 1-bromobutane",
    "(ii) Stereo isomerism (optically active)",
    "(iii) Octane",
    "(iv) 1-bromobutane",
    "Q-18 → (i) CH3-CH-CH2-CH3",
    "(ii) chlorobenzene with C2H5",
])]
found = _answered_question_numbers(pages)
check("Q17 has content", found.get(17, 0) >= 3, f"found={found}")
check("Q18 has content", found.get(18, 0) >= 1, f"found={found}")
check("no phantom Q1 from '1-bromobutane'", 1 not in found, f"found={found}")

# --------------------------------------------------------------------------
print("\nthe Chemistry failure itself")
report = GradeReport(
    total_score=0.0, max_total=6.0, overall_remarks="",
    questions=[
        QuestionMark(qid="Q17.i", score=0.0, max_score=1.5, page=6,
                     y_fraction=0.2, remark="", attempted=False),
        QuestionMark(qid="Q17.ii", score=0.0, max_score=1.5, page=6,
                     y_fraction=0.3, remark="", attempted=False),
        QuestionMark(qid="Q18.i", score=1.0, max_score=1.0, page=6,
                     y_fraction=0.5, remark="", attempted=True),
    ],
)
bad = find_unmarked_answers(report, pages)
check("both unread Q17 parts are caught", bad == ["Q17.i", "Q17.ii"], f"got {bad}")

# A question the student really did leave blank must stay quiet, or the gate
# fires on every honestly-unattempted question and nobody trusts it.
blank = GradeReport(
    total_score=0.0, max_total=5.0, overall_remarks="",
    questions=[QuestionMark(qid="Q21.i", score=0.0, max_score=5.0, page=6,
                            y_fraction=0.9, remark="", attempted=False)],
)
check("a genuinely blank question is not flagged",
      find_unmarked_answers(blank, pages) == [],
      f"got {find_unmarked_answers(blank, pages)}")

check("no OCR -> no opinion", find_unmarked_answers(report, None) == [])

# --------------------------------------------------------------------------
print("\na struck-out false start is not an answer")
struck = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
    LineBox(line_id="P1L1", text="Q-9→", bbox=(0, 0, 900, 30), page=1),
    LineBox(line_id="P1L2", text="wrong working here", bbox=(0, 40, 900, 70),
            page=1, cancelled=True),
])]
check("cancelled lines do not count as content",
      _answered_question_numbers(struck).get(9, 0) == 0,
      f"got {_answered_question_numbers(struck)}")


print("")
print("a blank sub-part is not an unread answer")
# The OCR sees that question 1 was answered, never which of 1(a)/(b)/(c). A sub-part
# whose sibling was attempted is explained by that sibling, and flagging it blocked a
# Corporate Laws copy over Q1.c and Q3.b when the student had written 1(a), 1(b) and
# 3(a) and nothing else. Leaving sub-parts blank is ordinary; on a choice paper it is
# the norm.
def _sub(qid, attempted):
    return QuestionMark(qid=qid, score=0.0, max_score=5.0, page=1, y_fraction=0.2,
                        remark="", attempted=attempted)

_sub_ocr = [PageOCR(page=1, image_width=600, image_height=800, lines=[
    LineBox(line_id="P1L0", text="Ans 1) b) As per the provisions",
            bbox=(50, 50, 500, 80), page=1),
    LineBox(line_id="P1L1", text="Ans 3 (a) Service of documents",
            bbox=(50, 90, 500, 120), page=1)])]

_partial = GradeReport(total_score=0, max_total=50, overall_remarks="x", questions=[
    _sub("Q1.a", True), _sub("Q1.b", True), _sub("Q1.c", False),
    _sub("Q3.a", True), _sub("Q3.b", False)])
check("a blank sub-part beside an answered sibling is not flagged",
      find_unmarked_answers(_partial, _sub_ocr) == [],
      f"got {find_unmarked_answers(_partial, _sub_ocr)}")

# The Chemistry copy this check exists for: EVERY row under an answered question
# number marked unattempted, so nothing explains the writing.
_all_blank = GradeReport(total_score=0, max_total=50, overall_remarks="x", questions=[
    _sub("Q1.a", False), _sub("Q1.b", False), _sub("Q3.a", False)])
check("a question whose every sub-part is 'unattempted' still raises",
      find_unmarked_answers(_all_blank, _sub_ocr) == ["Q1.a", "Q1.b", "Q3.a"],
      f"got {find_unmarked_answers(_all_blank, _sub_ocr)}")

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
