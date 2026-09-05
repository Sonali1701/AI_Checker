# -*- coding: utf-8 -*-
"""Reading the marks scheme off CBSE-style section headers.

The 70-mark Chemistry paper prints NO per-question "(N Marks)" tag anywhere, so
printed_tag_sum finds nothing and every tag-based check downstream is vacuous —
which is why four AI extractions of it returned 83, 81, 78 and 73 marks, none of
them right and no two alike. The section headers state the same scheme three times
over and never move.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import marks_scheme_from_section_headers as read

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# The real header text, verbatim from the paper's text layer.
CHEM = (
    "SECTION-A Multiple Choice Type Questions (1-5) (5 × 1 = 5) Product 'A' ... "
    "SECTION-B Very Short Answer Type Questions (6-10) (5× 2 = 10) The major ... "
    "SECTION-C Short Answer Type Questions (11 - 15)(5 × 5 = 25) (a) In the ... "
    "SECTION - D Case Based Questions (16-17) (2 x 5 = 10) Read the passage ... "
    "SECTION - E Long Answer Type Questions (18-21) (4 × 5 = 20) Write structures ..."
)

print("\nthe Chemistry paper this was written for")
s = read(CHEM, 70.0)
check("a scheme is returned", s is not None)
if s:
    check("21 questions", len(s.items) == 21, f"{len(s.items)}")
    check("total is exactly 70", abs(s.total - 70.0) < 0.01, f"{s.total}")
    by = {i.qid: i.max for i in s.items}
    check("MCQs are 1 mark", all(by[f"Q{n}"] == 1 for n in range(1, 6)))
    check("section B is 2 marks", all(by[f"Q{n}"] == 2 for n in range(6, 11)))
    check("sections C-E are 5 marks", all(by[f"Q{n}"] == 5 for n in range(11, 22)))
    check("no phantom Q22", "Q22" not in by)

print("\nit works with no official total to check against")
s2 = read(CHEM, None)
check("still reads 70 from the headers alone", s2 is not None and abs(s2.total - 70) < 0.01)

print("\nit is deterministic")
check("same answer twice", read(CHEM, 70.0).total == read(CHEM, 70.0).total)

# --------------------------------------------------------------------------
# A wrong scheme is worse than none: it is injected into the grading prompt and
# then FORCED onto the report. Every one of these must abstain.
print("\nabstains rather than guesses")
check("contradicts the official total",
      read(CHEM, 50.0) is None)
check("range and multiplication disagree",
      read("Questions (1-5) (7 × 1 = 7)", None) is None,
      "range spans 5, arithmetic claims 7")
check("multiplication does not add up",
      read("Questions (1-5) (5 × 1 = 9)", None) is None)
check("two sections claim the same question",
      read("(1-5) (5 × 1 = 5) ... (5-9) (5 × 2 = 10)", None) is None)
check("a gap in the numbering",
      read("(1-5) (5 × 1 = 5) ... (8-9) (2 × 2 = 4)", None) is None,
      "Q6 and Q7 belong to a header we failed to read")
check("does not start at Q1",
      read("(3-7) (5 × 1 = 5)", None) is None)
check("a single section is not enough signal",
      read("(1-5) (5 × 1 = 5)", None) is None)
check("no headers at all", read("Just some prose about alkyl halides.", None) is None)
check("empty text", read("", None) is None)
check("zero marks per question", read("(1-5) (5 × 0 = 0) ... (6-9) (4 × 2 = 8)", None) is None)

print("\nformatting the papers actually use")
check("no space before the bracket",
      read("(1-5)(5 × 1 = 5) x (6-10)(5 × 2 = 10)", 15.0) is not None)
check("ASCII x instead of multiplication sign",
      read("(1-5) (5 x 1 = 5) y (6-10) (5 x 2 = 10)", 15.0) is not None)
check("half marks per question",
      (read("(1-4) (4 x 0.5 = 2) z (5-8) (4 x 1 = 4)", 6.0) or type("", (), {"total": 0})).total == 6.0)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
