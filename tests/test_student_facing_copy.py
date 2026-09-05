# -*- coding: utf-8 -*-
"""What a student actually reads: a settled choice, a whole sentence, a real glyph.

Three faults found by reading three released copies the way a faculty member would,
rather than by checking the PDF opened:

* a disputed MCQ was going to be settled by counting three readers, two of which can
  miss a strikethrough - so a cancelled answer could win the mark;
* the cover reprinted a twelve-word margin remark that its own bullet renderer then
  split at the full stop, leaving "Definition omits." standing alone;
* the cover is drawn in the base-14 fonts, so an interval written with a real minus
  and an infinity sign came out as "(-2,.)" with hollow boxes.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import cover_point
from pdf_renderer import _sanitize_for_pdf
from pipeline import settle_disputed_choice

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


print("== a cancelled answer never wins on a head count ==")
# The live copy that prompted this: the student wrote (B), struck it out, answered
# (C). The grading pass reports the cancelled B; both key-blind readers are told to
# ignore struck-out options.
check("both careful readers agreeing settles the row",
      settle_disputed_choice("B", "C", "C") == "C")
check("the cancelled option does NOT win 2-of-3",
      settle_disputed_choice("B", "C", "B") == "",
      settle_disputed_choice("B", "C", "B"))
check("careful readers disagreeing leaves it unresolved",
      settle_disputed_choice("B", "C", "D") == "")
check("an unclear confirming read leaves it unresolved",
      settle_disputed_choice("B", "C", "") == "")

print()
print("== and the rule stays closed on junk input ==")
check("an undisputed row settles nothing here",
      settle_disputed_choice("C", "C", "C") == "")
check("a non-option primary is not a dispute",
      settle_disputed_choice("", "C", "C") == "")
check("a non-option independent is not a dispute",
      settle_disputed_choice("B", "?", "?") == "")
check("case and padding do not change the verdict",
      settle_disputed_choice(" b ", "c", "C") == "C")

print()
print("== a margin remark reprinted on the cover stays one sentence ==")
check("clauses are joined, not left to be split into orphans",
      cover_point("Definition omits. Just says 'with manager'.")
      == "Definition omits - Just says 'with manager'")
check("a single-clause remark is untouched",
      cover_point("Interest omitted") == "Interest omitted")
check("an empty remark stays empty", cover_point("") == "")
check("no trailing punctuation is left dangling",
      not cover_point("No Venn diagram drawn. Elements listed only.").endswith("."))

print()
print("== the cover can render every character it is given ==")
interval = _sanitize_for_pdf("Wrote x>−2 but no interval notation (−2,∞)")
check("a real minus sign becomes a hyphen", "−" not in interval, interval)
check("infinity becomes readable", "inf" in interval, interval)
check("the whole string is drawable in the base-14 encoding",
      all(ord(ch) < 256 for ch in interval), interval)

sets = _sanitize_for_pdf("A ∩ B ⊆ C, 60°")
check("set symbols are spelled out", "intersect" in sets and "subset" in sets, sets)

accented = _sanitize_for_pdf("café naïve")
check("latin-1 accents are preserved, not stripped",
      accented == "café naïve", accented)

exotic = _sanitize_for_pdf("marks: 你好")
check("an unmappable glyph is dropped rather than drawn as a box",
      all(ord(ch) < 256 for ch in exotic), exotic)

# Dropping an unmappable glyph is right for a decoration and wrong for a currency
# sign or a capital Greek letter: those carry the meaning of the number beside them.
money = _sanitize_for_pdf("Deduct ₹500 from capital")
check("the rupee sign is spelled out, not dropped",
      "Rs.500" in money, money)
delta = _sanitize_for_pdf("Δx = 5")
check("a capital delta is spelled out, not dropped",
      delta.startswith("delta x"), delta)
check("a spelled-out symbol does not leave a double space",
      "  " not in _sanitize_for_pdf("A ∪ B"), _sanitize_for_pdf("A ∪ B"))

check("plain ascii is left exactly alone",
      _sanitize_for_pdf("Q12: 1.5 / 2") == "Q12: 1.5 / 2")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
