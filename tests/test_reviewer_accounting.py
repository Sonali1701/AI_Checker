# -*- coding: utf-8 -*-
"""The four defects a PW reviewer found on the CA Foundation Accounting copy 77cfd3cf.

Reviewer's notes, verified against the script and the question paper before being
turned into tests:

  Q2  "Depreciation is correct, only presentation issue is there, so 0.5 mark
       deduction is enough instead of 1"   -> the working note shows
       13,16,480 - 1,31,648 = 11,84,832, i.e. six months on the machine sold on
       1 Oct. The figure was right; only its placement under the 31 March block
       was wrong. The tool took a full mark.
  Q4  "Dishonour bill was not written twice it was cut by student"  -> the second
       copy of the line is struck through in the student's own ink. The tool
       reported it as taken twice and deducted a mark.
  Q6  "creditors amount of Rs 400 in balance sheet was written as purchases on cr
       side"  -> adjustment 7 is an omitted credit purchase invoice, which raises
       Purchases AND Sundry Creditors. The student filed the 400 among the
       liabilities labelled "Purchases". The tool never mentioned it.

and one more found while checking the same copy: a red strikethrough drawn across
most of the page, cancelling correct content.

The first three are prompt behaviour, so they are pinned as prompt contracts - the
wording a future edit must not quietly drop. The fourth is renderer geometry and is
tested directly.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import grader
import pdf_renderer as pr

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def prompt_for(subject="Accounting", exam="professional"):
    return grader.build_system_prompt(subject, "CA Foundation", exam_type=exam)


print("== Q4: a struck-out repeat is a correction, not a duplication ==")
p = grader.SYSTEM_PROMPT
low = p.lower()
check("the grader is told to re-examine the image before reporting a repeat",
      "before you report that something is duplicated" in low)
for word in ("duplicated", "repeated", "entered twice", "contradicts"):
    check(f"names the trigger word {word!r}", word in low)
check("says a struck copy means no deduction",
      "there is no duplication and no deduction" in low)
check("requires BOTH copies to be live before deducting",
      "both copies are live" in low)
check("warns the transcript cannot show it",
      "the transcript will never warn" in low)

print("\n== Q2: a right figure in the wrong place costs at most half a step ==")
acc = prompt_for()
check("commerce conventions are attached for Accounting",
      "COMMERCE SUBJECT CONVENTIONS" in acc)
check("presentation slips are capped at half a step",
      "costs at most HALF that step" in acc)
check("and explicitly never the whole step", "never the whole step" in acc)
check("the grader must recompute before choosing computation vs presentation",
      "Recompute before choosing" in acc)
check("the real depreciation figure is given as the worked example",
      "1,31,648" in acc)
check("the remark must confirm the figure is correct",
      "MUST say the figure is correct" in acc)
check("presentation is not double-counted across steps",
      "do not double-count it" in acc)

print("\n== Q2: the professional block no longer contradicts that ==")
check("professional mode is attached", "PROFESSIONAL EXAM MODE" in acc)
check("the blanket 'never deduct for presentation' is gone",
      "Never deduct for presentation, layout" not in acc)
check("handwriting is still never a ground", "Never deduct for handwriting" in acc)
check("the carve-out points at the commerce rule",
      "see rule 9 of the commerce conventions" in acc)
check("a correct figure never costs a whole step",
      "never costs a whole step for sitting in the wrong row" in acc)

print("\n== Q6: balance-sheet items are checked by head, not just by total ==")
check("both sides are read item by item",
      "read down BOTH sides item by item" in acc)
check("balancing is explicitly not proof of correct classification",
      "Balanced totals are NOT evidence" in acc)
check("every adjustment must be traced to both statements",
      "name where it lands on BOTH statements" in acc)
check("the omitted purchase invoice is the worked example",
      "omitted purchase invoice" in acc and "Sundry Creditors" in acc)
check("the 400-labelled-Purchases case is named",
      'labelled "Purchases" instead of raising Creditors' in acc)

print("\n== a non-commerce paper is unaffected ==")
eng = prompt_for("English", "school")
check("English does not get the commerce conventions",
      "COMMERCE SUBJECT CONVENTIONS" not in eng)
check("but every paper still gets the cancelled-work rule",
      "before you report that something is duplicated" in eng.lower())


print("\n== the strikethrough cannot cancel half the page ==")


class Ann:
    def __init__(self, **kw):
        self.type = "strikethrough"
        self.page = 1
        self.target_line_id = ""
        self.target_word = ""
        self.x_fraction = 0.2
        self.y_fraction = 0.3
        self.width_fraction = 0.3
        self.__dict__.update(kw)


def ink_width(anns, line_index=None, earned=None):
    """Widest horizontal stroke a set of annotations puts on a fresh page."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pr._draw_inline_annotations(page, anns, line_index=line_index or {},
                                earned_line_ids=earned)
    spans = [g["rect"] for g in page.get_drawings() if g["type"] == "s"]
    if not spans:
        doc.close()
        return 0.0
    r = spans[0]
    for s in spans[1:]:
        r |= s
    doc.close()
    return r.width


cw = 595.0
full = ink_width([Ann(width_fraction=1.0)])
check("a width_fraction of 1.0 no longer spans the writing area",
      full <= pr._STRIKE_MAX_FRACTION * cw + 4,
      f"drew {full:.0f}pt across a {cw:.0f}pt page")
check("the cap is well under half the page", pr._STRIKE_MAX_FRACTION <= 0.5,
      pr._STRIKE_MAX_FRACTION)
small = ink_width([Ann(width_fraction=0.10)])
check("a narrow strike is left alone", 40 < small < 90, f"{small:.0f}pt")

print("\n== a strike that cannot be placed is dropped, not spread over the line ==")
LID = "p1_l7"
line_index = {LID: (None, (100, 200, 900, 240), 1000, 1400, "i) Actual delivery")}
placed = ink_width([Ann(target_line_id=LID, target_word="delivery")], line_index)
check("a word that IS on the line still gets struck", placed > 0, f"{placed:.0f}pt")
missing = ink_width([Ann(target_line_id=LID, target_word="Constructive")], line_index)
check("a word that is NOT on the line draws nothing", missing == 0.0,
      f"drew {missing:.0f}pt through a line it could not locate")
whole = ink_width([Ann(target_line_id=LID, target_word="")], line_index)
check("targeting the whole line deliberately still works", whole > 0, f"{whole:.0f}pt")

print("\n== a step that earned its marks is never cancelled ==")
kept = ink_width([Ann(target_line_id=LID, target_word="delivery")], line_index,
                 earned={LID})
check("no strike on a fully-earned line", kept == 0.0, f"drew {kept:.0f}pt")
check("crosses are not suppressed by that rule",
      ink_width([Ann(type="cross", target_line_id=LID, target_word="delivery")],
                line_index, earned={LID}) > 0)

print("\n== an underline points at a mistake without cancelling it ==")
# The model asks for a `circle` when it wants the student to NOTICE a content error.
# That used to be drawn as a strikethrough, which reads as "deleted" — and on a real
# copy it crossed out a correct statement of law the student was distinguishing from.
LINE = {LID: (None, (100, 200, 900, 244), 1000, 1400, "Sundry creditors purchases 400")}
_doc = fitz.open()
_pg = _doc.new_page(width=595, height=842)
_x0, _y0, _x1, _y1 = pr._pdf_coords_from_line(_pg, (100, 200, 900, 244), 1000, 1400)
_doc.close()


def ink_band(kind):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pr._draw_inline_annotations(page, [Ann(type=kind, target_line_id=LID,
                                           target_word="purchases")],
                                line_index=LINE)
    rs = [g["rect"] for g in page.get_drawings() if g["type"] == "s"]
    doc.close()
    if not rs:
        return None
    r = rs[0]
    for s in rs[1:]:
        r |= s
    return (r.y0 + r.y1) / 2


mid = ink_band("underline")
check("an underline sits under the words, not through them", mid >= _y1 - 3,
      f"drawn at y={mid:.1f}, text ends at y={_y1:.1f}")
check("a strikethrough still goes through them",
      _y0 < ink_band("strikethrough") < _y1)
check("circle is drawn as an underline, not a strike",
      abs(ink_band("circle") - mid) < 1.0)
check("an underline is allowed on a step that earned its marks",
      ink_width([Ann(type="underline", target_line_id=LID, target_word="purchases")],
                LINE, earned={LID}) > 0,
      "an underline is a pointer, not a verdict, so it is not suppressed")
check("but a strike on an earned step is still suppressed",
      ink_width([Ann(type="strikethrough", target_line_id=LID, target_word="purchases")],
                LINE, earned={LID}) == 0.0)

print("\n== a word that repeats on the line is never guessed at ==")
# _word_x_in_line resolves a target with text.index() — the FIRST occurrence. On
# "Purchases 3,21,700 add omitted Purchases 400" the error is the SECOND one, and
# marking the first accuses the entry the student got right, 278pt away.
DUP = {LID: (None, (100, 200, 900, 244), 1000, 1400,
             "Purchases 3,21,700 add omitted Purchases 400")}
check("a repeated word draws nothing",
      ink_width([Ann(type="underline", target_line_id=LID, target_word="Purchases")],
                DUP) == 0.0)
check("but a phrase that pins it down is drawn",
      ink_width([Ann(type="underline", target_line_id=LID,
                     target_word="omitted Purchases 400")], DUP) > 0)
check("a word occurring once is unaffected",
      ink_width([Ann(type="underline", target_line_id=LID, target_word="omitted")],
                DUP) > 0)
check("the grader is told to make the phrase unique",
      "EXACTLY ONCE" in grader.Annotation.model_fields["target_word"].description)
check("and told what happens if it is not",
      "DROPPED rather than guessed at"
      in grader.Annotation.model_fields["target_word"].description)

print("\n== the grader knows what each mark means ==")
desc = grader.Annotation.model_fields["type"].description
check("underline is offered", "'underline'" in desc)
check("and described as the default for content errors",
      "DEFAULT for content mistakes" in desc)
check("strikethrough is described as cancelling",
      "THIS IS CANCELLED" in desc)
check("and warned off correct statements",
      "NEVER strike a statement that is correct in itself" in desc)

print("\n== the renderer passes earned lines through ==")
import inspect
src = inspect.getsource(pr._build_evaluated_pdf)
check("build collects met criteria", "earned_line_ids" in src)
check("and hands them to the annotator",
      "earned_line_ids=earned_line_ids" in src)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
