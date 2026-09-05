# -*- coding: utf-8 -*-
"""A PW reviewer's line-by-line feedback on the CA Foundation Business Laws copy.

Source: evaluated_47f4b6b6.pdf (Master Test Series-02, Business Laws, 50 marks),
checked against the official question paper and Hint-and-Solutions before each note
was turned into a test.

  Q1a(i)  "extra marks awarded even when provision is wrong, it should 0.5 marks.
           Student has reached the correct conclusion, but the provision/reasoning
           is contradictory and incorrect. Correct conclusion alone should not
           receive 1.5/2. Maximum 0.5/2 should be awarded for the correct
           conclusion."
        -> the tool scored it 1.5/2 and its OWN remark read "Right conclusion. Your
           reasoning contradicts itself." It saw the fault and paid 75% anyway. The
           rule it had only covered a MISSING provision, never a wrong one.

  Q1a(iv) "Step marking is not proper"
  Q1b(i)  "Step marking is not proper"
        -> Q1.b.i printed "1.5+1.5=3/4" beside a page carrying exactly two marks.
           The student's Provision paragraph is wrong (it lists modes of delivery
           instead of s.20 of the Sale of Goods Act, under which property in
           specific goods passes when the contract is made) and carries NO mark at
           all. Two faults behind one complaint: the criteria were worth 3 on a
           4-mark question, so a mark belonged to no step; and the margin advertised
           a breakdown the page could not account for.

  2b      "is checked correctly cross is not there for wrong point"
  Q3b     "The student's wrong/inadequately stated point should be crossed/marked
           clearly. Also mention that only 4 rights/points were required for a
           4-mark answer. The evaluator should not leave all five attempted points
           appearing equally accepted."
        -> Q3.b printed "1+0+1+1=3/4" while the page carried three marks: the zero
           step was drawn nowhere. step_line_id told the model to anchor a step
           "where THIS rubric step is satisfied", and a step worth nothing is
           satisfied nowhere, so it came back empty and the renderer dropped it.

Q1b(ii), Q1c, Q2a(ii), 2c and Q3a were marked "correctly checked" and the total was
confirmed correct, so nothing here may change how those are handled.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import grader
import pdf_renderer as pr
from grader import Criterion, QuestionMark
from preflight import _is_red, _rgb, check_copy

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


BL = grader.build_system_prompt("Business Laws", "CA Foundation", exam_type="professional")

print("== Q1a(i): a wrong provision is not a missing one ==")
check("the rule exists at all",
      "A WRONG provision is not the same as a missing one" in BL)
check("the provision step scores zero",
      "the provision criterion AND the application criterion built on it BOTH score zero" in BL)
check("because an application built on a wrong rule cannot stand",
      "an application cannot be sound when the rule it applies is wrong" in BL)
check("a lone conclusion is capped at a quarter of the part",
      "at most a QUARTER of it" in BL)
check("the reviewer's actual ruling is quoted",
      "maximum 0.5/2" in BL)
check("and the reason is given, not just the rule",
      "Arriving at the right verdict is not the same as knowing the law" in BL)
check("the remark must name the right provision",
      "name what the provision should have been" in BL)
check("the older missing-provision rule survives",
      "Do not award full marks to a bare conclusion with no provision." in BL)

print("\n== Q3b / 2b: 'any N' caps the count and the surplus is marked ==")
check("the cap rule exists",
      '"Explain any N" caps the count' in BL)
check("only the best N count", "credit the N BEST valid points and no more" in BL)
check("a fifth point on a 4-mark question is not a bonus",
      "earns at most 4, and the fifth is not a bonus" in BL)
check("a weak or wrong point gets its own zero criterion",
      "gets its OWN zero-scored criterion anchored ON that point" in BL)
check("so that a cross actually lands beside it", "a ✗ lands beside it" in BL)
check("the reason silence is wrong is stated",
      "an unmarked point on the page reads to the student as one that was accepted" in BL)
check("the remark says how many were required", "Only 4 rights were needed" in BL)
check("point-wise marking is otherwise unchanged",
      "Flowing essays that make 3 of 6 required points get 3/6" in BL)

print("\n== Q1a(iv) / Q1b(i): a step worth nothing still needs a place ==")
anchor = grader.Criterion.model_fields["step_line_id"].description
check("zero steps must be anchored",
      "A step that scored NOTHING still needs an anchor" in anchor)
check("on the wrong line, or where the point belonged",
      "where the student wrote the WRONG thing, or where the missing point" in anchor)
check("and the consequence of not doing so is spelled out",
      "A zero with no anchor is drawn nowhere" in anchor)
check("the reviewer's words are quoted so the rule is not tidied away later",
      "cross is not there for wrong point" in anchor)
check("the existing anchor rules are intact",
      "NEVER use a header line" in anchor and "NEVER reuse a step_line_id" in anchor)

print("\n== Q1b(i): a mark that belongs to no step is caught ==")


class C:
    def __init__(self, aw, mx, lid=""):
        self.awarded, self.max, self.met = aw, mx, aw >= mx - 1e-9
        self.description, self.evidence, self.step_line_id = "s", "", lid


class Q:
    def __init__(self, qid, score, mx, crits):
        self.qid, self.score, self.max_score = qid, score, mx
        self.remark, self.attempted, self.criteria = "", True, crits


class R:
    def __init__(self, qs):
        self.questions = qs
        self.total_score = round(sum(q.score for q in qs), 2)
        self.max_total = round(sum(q.max_score for q in qs), 2)
        self.overall_remarks = ""


def codes(report):
    return [i.code for i in check_copy(report, total_marks=report.max_total)]


# Q1.b.i exactly as it shipped: two criteria of 1.5 on a 4-mark question.
short = R([Q("Q1.b.i", 3.0, 4.0, [C(1.5, 1.5), C(1.5, 1.5)])])
check("the 1.5+1.5-on-a-4-mark-question case is blocked",
      "steps-do-not-cover-question" in codes(short), codes(short))
check("the awarded-sum check alone could NOT see it",
      "criteria-mismatch" not in codes(short),
      "1.5+1.5 reconciles perfectly with a score of 3 — that is why it slipped through")
whole = R([Q("Q1.b.i", 3.0, 4.0, [C(0.0, 1.0), C(1.5, 1.5), C(1.5, 1.5)])])
check("adding the missing provision step clears it",
      "steps-do-not-cover-question" not in codes(whole), codes(whole))
check("a question with no criteria at all is not nagged about steps",
      "steps-do-not-cover-question" not in codes(R([Q("Q2", 3.0, 4.0, [])])))
check("the issue blocks rather than warns",
      all(i.severity == "block" for i in check_copy(short, total_marks=4.0)
          if i.code == "steps-do-not-cover-question"))

print("\n== Q3b: the margin prints what the page carries, and nothing else ==")


def render(crits, mx, score, n_lines=4):
    # An evaluated page is the student's 595pt sheet widened to 770 so the marks strip
    # has somewhere to live; _content_width reads that split from _CONTENT_W. Build the
    # page the same way, or the strip is squeezed to nothing and this tests nothing.
    doc = fitz.open()
    doc.new_page(width=770, height=500)
    pr._CONTENT_W[(id(doc), 1)] = 595.0
    pr._reset_strip_state(doc)
    lines = [{"lid": f"p1_l{i}", "x0": 40.0, "x1": 300.0, "y0": 70.0 + i * 60,
              "y1": 88.0 + i * 60, "page": 1, "text": f"student line {i}"}
             for i in range(n_lines)]
    pr._render_question_marks(
        doc, QuestionMark(qid="Q3.b", score=score, max_score=mx, page=1,
                          y_fraction=0.2, remark="", attempted=True,
                          mark_style="per_step", criteria=crits),
        lines, {l["lid"]: l for l in lines}, {})
    spans = [sp["text"].strip() for b in doc[0].get_text("dict")["blocks"]
             for l in b.get("lines", []) for sp in l["spans"]
             if sp["text"].strip() and _is_red(*_rgb(sp["color"]))]
    doc.close()
    return spans


def strip_sum(spans):
    return next((s for s in spans if "+" in s and "=" in s), "")


def drawn_marks(spans):
    return [s for s in spans if s.replace(".", "").isdigit()]


# The zero step has no anchor, so it cannot be drawn. It must not be advertised either.
crits = [Criterion(description="a", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l0"),
         Criterion(description="b", awarded=0, max=1, met=False, evidence="", step_line_id=""),
         Criterion(description="c", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l2"),
         Criterion(description="d", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l3")]
spans = render(crits, 4, 3)
calc = strip_sum(spans)
check("the strip no longer prints a term the page does not carry",
      "0" not in calc.split("=")[0].split("+"), f"strip reads {calc!r}")
check("and it still totals correctly", calc.endswith("=3/4"), f"strip reads {calc!r}")
n_drawn = len(drawn_marks(spans))
terms = len([t for t in calc.split("=")[0].split("+") if t]) if calc else 0
check("the student can count the marks and reach the sum",
      terms and n_drawn >= terms,
      f"{terms} terms in the margin, {n_drawn} marks on the page")

# When the model DOES anchor the zero, it is drawn and belongs in the sum.
crits[1] = Criterion(description="b", awarded=0, max=1, met=False, evidence="",
                     step_line_id="p1_l1")
calc2 = strip_sum(render(crits, 4, 3))
check("an ANCHORED zero is shown in both places",
      "0" in calc2.split("=")[0].split("+"), f"strip reads {calc2!r}")
check("still totalling correctly", calc2.endswith("=3/4"), f"strip reads {calc2!r}")

print("\n== and the parts the reviewer passed are untouched ==")
full = [Criterion(description=d, awarded=1, max=1, met=True, evidence="",
                  step_line_id=f"p1_l{i}") for i, d in enumerate("abc")]
calc3 = strip_sum(render(full, 3, 3))
check("a fully-earned question still shows its breakdown",
      calc3.endswith("=3/3") and calc3.count("+") == 2, f"strip reads {calc3!r}")
check("the cancelled-work rule survives",
      "before you report that something is duplicated" in BL.lower())
check("the copy-slip rule survives", "A COPY SLIP IS NOT A CONTENT ERROR" in BL)
check("commerce rule 9 survives", "costs at most HALF that step" in BL)
check("commerce rule 10 survives", "read down BOTH sides item by item" in BL)


# Q1a(i) again, two rounds later. Splitting the four validity sub-parts into their own
# questions worked — each now carries its own provision and conclusion criteria — but
# the sub-part still came back 1.5/2, because the marker paid a criterion called
# "Correct provision cited (S.10 ICA)" IN FULL against lines that read only "under the
# provisions of The Indian Contract Act, 1872". The student named the Act and no
# section, and stated the rule wrongly. The grading rules already say a position given
# without the section citation loses that portion; the citation was simply invented.
print("== a citation credited where the student gave none ==")
from mathpix import LineBox, PageOCR
from preflight import _check_citation_credit

_NO_SECTION = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
    LineBox("P1L8", "Ves. it is a volid countralt befweon Mr.priya", (0., 0., 10., 10.), 1),
    LineBox("P1L13", "sefflement contract under the provisions of", (0., 0., 10., 10.), 1),
    LineBox("P1L20", "is void-ab-inito undr Section 20 of the Act", (0., 0., 10., 10.), 1),
])]


def _q(qid, desc, awarded, mx, lid):
    return QuestionMark(qid=qid, score=awarded, max_score=mx, page=1, y_fraction=0.2,
                        mark_style="per_step",
                        criteria=[Criterion(description=desc, awarded=awarded, max=mx,
                                            met=awarded >= mx, evidence="",
                                            step_line_id=lid)])


class _R:
    def __init__(self, qs):
        self.questions = qs


_flagged = _check_citation_credit(
    _R([_q("Q1.a.i", "Correct provision cited (S.10 ICA)", 0.5, 0.5, "P1L13")]),
    _NO_SECTION)
check("a fully-paid citation step with no section on the line is caught",
      len(_flagged) == 1, f"{[i.detail for i in _flagged]}")
check("and it blocks the copy", _flagged and _flagged[0].severity == "block")

check("a citation step IS accepted where the student cited the section",
      not _check_citation_credit(
          _R([_q("Q1.a.ii", "Correct provision cited (S.20)", 0.5, 0.5, "P1L20")]),
          _NO_SECTION))
# Only a step paid IN FULL is suspect: one already docked is the marker agreeing.
check("a citation step that was already docked is not flagged",
      not _check_citation_credit(
          _R([_q("Q1.a.i", "Correct provision cited (S.10 ICA)", 0.0, 0.5, "P1L13")]),
          _NO_SECTION))
check("a step that is not about citation is left alone",
      not _check_citation_credit(
          _R([_q("Q1.a.i", "Correct conclusion: valid", 0.5, 0.5, "P1L13")]),
          _NO_SECTION))
check("no OCR means no such check",
      not _check_citation_credit(
          _R([_q("Q1.a.i", "Correct provision cited (S.10 ICA)", 0.5, 0.5, "P1L13")]),
          []))


# "is checked correctly cross is not there for wrong point" (2b), and on Q3b "the
# student's wrong/inadequately stated point should be crossed/marked clearly ... the
# evaluator should not leave all five attempted points appearing equally accepted".
# Anchoring the zero step was only half of it: the renderer drew a TICK for a fully
# earned step and, for everything else, the circled mark alone — so a point worth
# nothing came out as a circled "0" and read exactly like a point marked lightly.
# Counted on the delivered copy before this: 40 ticks and not one cross anywhere.
print("== a point that earned nothing is crossed, not just circled ==")
_ticks, _crosses = [], []
_real_t, _real_x = pr._draw_tick, pr._draw_cross
pr._draw_tick = lambda *a, **k: _ticks.append(a[1:3])
pr._draw_cross = lambda *a, **k: _crosses.append(a[1:3])


def _sheet_bl(n=1):
    doc = fitz.open()
    for _ in range(n):
        pg = doc.new_page(width=595, height=842)
        for i in range(8):
            pg.draw_line(fitz.Point(70, 120 + i * 70), fitz.Point(520, 120 + i * 70),
                         color=(0.85, 0.85, 0.9), width=0.5)
    return doc.tobytes()


try:
    from mathpix import LineBox, PageOCR
    from pdf_renderer import build_evaluated_pdf as _bep_bl
    _rows = ["Right to specific performance S.58", "Damages for non-delivery S.57",
             "Anticipatory breach S.60", "Suit for interest S.61"]
    _ocr_bl = [PageOCR(page=1, image_width=595, image_height=842, lines=[
        LineBox(f"P1L{i}", t, (80.0, 100.0 + i * 70, 500.0, 150.0 + i * 70), 1)
        for i, t in enumerate(_rows)])]
    _q = QuestionMark(
        qid="Q3.b", score=3, max_score=4, page=1, y_fraction=0.1,
        anchor_line_id="P1L0", mark_style="per_step", attempted=True,
        criteria=[
            Criterion(description="right 1", awarded=1, max=1, met=True, evidence="",
                      step_line_id="P1L0"),
            Criterion(description="right 2 — not stated", awarded=0, max=1, met=False,
                      evidence="", step_line_id="P1L1"),
            Criterion(description="right 3", awarded=1, max=1, met=True, evidence="",
                      step_line_id="P1L2"),
            Criterion(description="right 4", awarded=1, max=1, met=True, evidence="",
                      step_line_id="P1L3"),
        ])
    _bep_bl(student_pdf_bytes=_sheet_bl(), student_filename="s.pdf", student_pngs=[],
            report=grader.GradeReport(total_score=3, max_total=4, overall_remarks="x",
                                      questions=[_q]),
            ocr_pages=_ocr_bl, mark_scale=1.0)
finally:
    pr._draw_tick, pr._draw_cross = _real_t, _real_x

check("the three earned points are ticked", len(_ticks) == 3, f"{len(_ticks)} ticks")
check("and the point worth nothing is CROSSED", len(_crosses) == 1,
      f"{len(_crosses)} crosses — a zero step drawn as a bare circled 0 reads as accepted")


# The three complaints that survived every attempt to fix them by instruction. They are
# one failure: the marker SEES the fault, writes it in the remark, and pays anyway. So
# the marks are made to agree with the evidence the marker itself recorded.
print("== the marks are made to agree with the marker's own evidence ==")
from grader import GradeReport as _G3, enforce_evidence_against_awards
from mathpix import LineBox, PageOCR

_NO_SEC = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
    LineBox("P1L8", "Ves it is a volid countralt befweon Mr.priya", (0., 0., 10., 10.), 1),
    LineBox("P1L10", "dettlerent for unlerewful object and unlawful", (0., 0., 10., 10.), 1),
    LineBox("P1L13", "sefflement contract under the provisions of", (0., 0., 10., 10.), 1),
    LineBox("P1L14", "The Indian Contract Act, 1872 .", (0., 0., 10., 10.), 1),
    LineBox("P2L4", "is void ab initio under Section 20 of the Act", (0., 0., 10., 10.), 2),
])]


def _crit(desc, awarded, mx, lid):
    return Criterion(description=desc, awarded=awarded, max=mx,
                     met=awarded >= mx, evidence="", step_line_id=lid)


# Q1a(i): "maximum 0.5/2 should be awarded for the correct conclusion"
_q1ai = QuestionMark(
    qid="Q1.a.i", score=1.5, max_score=2, page=1, y_fraction=0.2, attempted=True,
    remark="Marriage bureau agreement is valid.", mark_style="per_step",
    criteria=[_crit("Correct provision cited (S.10 ICA)", 0.5, 0.5, "P1L13"),
              _crit("Correct conclusion: valid", 0.5, 0.5, "P1L8"),
              _crit("Reasoning: lawful object/consideration", 0.5, 1.0, "P1L10")])
# Q3b: "Suit for specific performance (S.58) not mentioned" — and it was paid 0.5/1
_q3b = QuestionMark(
    qid="Q3.b", score=3.5, max_score=4, page=1, y_fraction=0.6, attempted=True,
    remark="Suit for specific performance (S.58) not mentioned.", mark_style="per_step",
    criteria=[_crit("Specific performance (S.58)", 0.5, 1.0, "P1L8"),
              _crit("Damages for non-delivery (S.57)", 1.0, 1.0, "P1L10"),
              _crit("Anticipatory breach (S.60)", 1.0, 1.0, "P1L13"),
              _crit("Suit for interest (S.61)", 1.0, 1.0, "P1L14")])
# A question the reviewer called correctly checked: the remark denies a DIFFERENT
# element of the same answer, and must not cost this step its half mark.
_q2ci = QuestionMark(
    qid="Q2.c.i", score=3, max_score=4, page=1, y_fraction=0.8, attempted=True,
    remark="Change of registered office: filing condition not stated.",
    mark_style="per_step",
    criteria=[_crit("Change of registered office: notice to Registrar", 0.5, 1.0, "P1L8"),
              _crit("Consequence of contravention", 1.0, 1.0, "P1L10"),
              _crit("Thirty day requirement", 1.5, 1.5, "P1L13")])
# A student who DID cite the section keeps the credit.
_q1aii = QuestionMark(
    qid="Q1.a.ii", score=1, max_score=1, page=2, y_fraction=0.2, attempted=True,
    remark="", mark_style="per_step",
    criteria=[_crit("Bilateral mistake provision cited (S.20)", 1.0, 1.0, "P2L4")])

_rep3 = _G3(total_score=9, max_total=11, overall_remarks="x",
            questions=[_q1ai, _q3b, _q2ci, _q1aii])
_notes = enforce_evidence_against_awards(_rep3, _NO_SEC)
_by3 = {q.qid: q for q in _rep3.questions}

check("Q1a(i) comes down to the conclusion alone", _by3["Q1.a.i"].score == 0.5,
      f"{_by3['Q1.a.i'].score} — the reviewer ruled maximum 0.5/2")
check("the invented citation is the step that lost it",
      _by3["Q1.a.i"].criteria[0].awarded == 0)
check("and the reasoning built on it falls with it",
      _by3["Q1.a.i"].criteria[2].awarded == 0)
check("but the correct conclusion still stands",
      _by3["Q1.a.i"].criteria[1].awarded == 0.5)
check("Q3b's unmentioned right earns nothing, so it can be crossed",
      _by3["Q3.b"].criteria[0].awarded == 0 and _by3["Q3.b"].score == 3.0)
check("a step the remark does NOT name keeps its marks",
      _by3["Q2.c.i"].score == 3.0, f"{_by3['Q2.c.i'].score}")
check("a section the student really cited is still paid",
      _by3["Q1.a.ii"].score == 1.0)
check("every change is reported", len(_notes) >= 3, f"{_notes}")
check("nothing happens without OCR to check against",
      enforce_evidence_against_awards(
          _G3(total_score=1, max_total=1, overall_remarks="x",
              questions=[QuestionMark(qid="Q9", score=1, max_score=1, page=1,
                                      y_fraction=0.5, attempted=True,
                                      criteria=[_crit("provision cited", 1, 1, "P9L1")])]),
          []) == [])


# The same complaint again, in the other rendering path. A question scored as ONE mark
# draws a single score and no per-step marks, so an element that earned nothing was
# invisible: on Q1(a)(i) the invented provision was reduced to zero and still looked
# accepted. The per-step path had already been fixed; this one had not, and the suite
# passed while it did — so this exercises it end to end, through the real renderer.
print("== a single-mark answer still crosses the element that earned nothing ==")
_t2, _x2 = [], []
_rt2, _rx2 = pr._draw_tick, pr._draw_cross
pr._draw_tick = lambda *a, **k: _t2.append(a[1:3])
pr._draw_cross = lambda *a, **k: _x2.append(a[1:3])
try:
    from mathpix import LineBox, PageOCR
    from pdf_renderer import build_evaluated_pdf as _bep2
    _lines2 = ["Yes it is a valid contract between Priya and the bureau",
               "because contract of marriage settlement is unlawful",
               "but it is not a marriage settlement contract"]
    _ocr2 = [PageOCR(page=1, image_width=595, image_height=842, lines=[
        LineBox(f"P1L{i}", t, (80.0, 100.0 + i * 70, 500.0, 150.0 + i * 70), 1)
        for i, t in enumerate(_lines2)])]
    _q2 = QuestionMark(
        qid="Q1.a.i", score=0.5, max_score=2, page=1, y_fraction=0.15,
        anchor_line_id="P1L0", mark_style="single", attempted=True,
        remark="Marriage bureau agreement is valid.",
        criteria=[
            Criterion(description="Correct conclusion: valid", awarded=0.5, max=0.5,
                      met=True, evidence="", step_line_id="P1L0"),
            Criterion(description="Correct provision cited", awarded=0, max=0.5,
                      met=False, evidence="", step_line_id="P1L2"),
            Criterion(description="Reasoning: lawful object", awarded=0, max=1,
                      met=False, evidence="", step_line_id="P1L1"),
        ])
    _bep2(student_pdf_bytes=_sheet_bl(), student_filename="s.pdf", student_pngs=[],
          report=grader.GradeReport(total_score=0.5, max_total=2, overall_remarks="x",
                                    questions=[_q2]),
          ocr_pages=_ocr2, mark_scale=1.0)
finally:
    pr._draw_tick, pr._draw_cross = _rt2, _rx2

check("both elements that earned nothing are crossed", len(_x2) == 2,
      f"{len(_x2)} crosses — a zero element left uncrossed reads as accepted")
check("a part-marked answer is not ticked", len(_t2) == 0, f"{len(_t2)} ticks")

# A fully correct single-mark answer keeps its tick and gains no crosses.
_t3, _x3 = [], []
pr._draw_tick = lambda *a, **k: _t3.append(a[1:3])
pr._draw_cross = lambda *a, **k: _x3.append(a[1:3])
try:
    _q3 = QuestionMark(
        qid="Q1.a.ii", score=2, max_score=2, page=1, y_fraction=0.15,
        anchor_line_id="P1L0", mark_style="single", attempted=True,
        criteria=[Criterion(description="all correct", awarded=2, max=2, met=True,
                            evidence="", step_line_id="P1L0")])
    _bep2(student_pdf_bytes=_sheet_bl(), student_filename="s.pdf", student_pngs=[],
          report=grader.GradeReport(total_score=2, max_total=2, overall_remarks="x",
                                    questions=[_q3]),
          ocr_pages=_ocr2, mark_scale=1.0)
finally:
    pr._draw_tick, pr._draw_cross = _rt2, _rx2

# ONE tick, on the page. The strip used to add a second beside its entry, so a
# released Core Maths page carried eight ticks down the working and eight more down
# the margin. The strip now ticks only what the sheet could not.
check("a fully correct answer is ticked exactly once, on the page",
      len(_t3) == 1, f"{len(_t3)} ticks")
check("and carries no cross", len(_x3) == 0, f"{len(_x3)} crosses")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
