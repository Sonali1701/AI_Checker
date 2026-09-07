# -*- coding: utf-8 -*-
"""Remarks must read like a teacher wrote them, on the cover AND in the margin.

Taken from a real evaluated copy that read like a generated report:
  cover : "You have demonstrated a solid understanding of MCQ-based concepts.
           Your main losses came from incorrect answers in Q7 and Q8. Focus on
           carefully applying the profit-available-for-appropriation logic..."
  margin: "sales entry amount wrong; fire/insurance entry incorrect"
  MCQ   : "Incorrect option (chose C, correct B)"  <- on EVERY wrong MCQ, from code
For comparison, the human evaluator on the CA booklet wrote:
  "Deduction u/s 10AA is not available, since he commenced operation in P.Y. 2021-22"
— a real sentence naming the actual rule.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (
    GradeReport,
    QuestionMark,
    _reconcile_report,
    build_system_prompt,
    humanise_remark,
    robotic_phrases,
)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("== report-speak openers are stripped, meaning kept ==")
CASES = [
    ("Overall, the balance sheet does not tally.", "The balance sheet does not tally"),
    ("In summary, your working notes are missing.", "Your working notes are missing"),
    ("It is evident that the closing stock was undervalued.",
     "The closing stock was undervalued"),
    ("Lost 1 mark due to incorrect antonym.", "Incorrect antonym"),
    ("Deducted 0.5 marks for missing working note", "Missing working note"),
    ("Criterion 2 not met - working note missing", "Working note missing"),
]
for raw, want in CASES:
    got = humanise_remark(raw)
    check(f"{raw[:38]!r} cleaned", got == want, f"got {got!r}, want {want!r}")

print("== a remark a teacher would actually write is left ALONE ==")
KEEP = [
    "Should be Rs 92,200",
    "Working note missing",
    "Interest on capital is allowed only if profit permits",
    "Format not followed",
    "Ans: B",
]
for text in KEEP:
    check(f"{text[:40]!r} untouched", humanise_remark(text) == text,
          f"became {humanise_remark(text)!r}")
check("a sentence with a dash becomes two sentences (dashes are banned)",
      humanise_remark("Deduction u/s 10AA not available - operations began in P.Y. 2021-22")
      == "Deduction u/s 10AA not available. Operations began in P.Y. 2021-22.",
      humanise_remark("Deduction u/s 10AA not available - operations began in P.Y. 2021-22"))
check("empty stays empty", humanise_remark("") == "")
check("None-safe", humanise_remark(None) == "")

print("== dashes and semicolons become sentences, hyphenated words survive ==")
check("a semicolon list becomes sentences",
      humanise_remark("bank total is off; drawings entry missed")
      == "Bank total is off. Drawings entry missed.",
      humanise_remark("bank total is off; drawings entry missed"))
check("a free-standing dash becomes a sentence",
      humanise_remark("Should be Rs 2,05,460 - the drawings entry is missing")
      == "Should be Rs 2,05,460. The drawings entry is missing.",
      humanise_remark("Should be Rs 2,05,460 - the drawings entry is missing"))
for word in ("depreciation add-back subtracted", "second-hand machinery",
             "Deduction u/s 10AA not available", "T-account not balanced"):
    check(f"hyphen inside {word.split()[0]!r} survives",
          humanise_remark(word).lower().replace(".", "") .startswith(word.split()[0].lower()),
          humanise_remark(word))
check("no dash survives in the output",
      " - " not in humanise_remark("a - b - c"), humanise_remark("a - b - c"))
check("no semicolon survives in the output",
      ";" not in humanise_remark("a; b; c"), humanise_remark("a; b; c"))

print("== the tool never leaks its own uncertainty to the student ==")
leaky = "interest shown as Rs 480 but OCR shows Rs 400 - verify"
out = humanise_remark(leaky)
check("the word OCR never reaches the student", "ocr" not in out.lower(), out)
check("'verify' hedging is dropped", "verify" not in out.lower(), out)
check("the real content survives", "480" in out, out)
check("'needs verification' is dropped",
      "verification" not in humanise_remark("M1 opening balance needs verification").lower())

print("== regex sanity: word boundaries really are word boundaries ==")
# A  written into a NON-raw string becomes  (backspace) and silently matches
# nothing. That shipped once; this catches it.
from grader import _HEDGE_RE, _TOOL_LEAK_RE
for name, rx in (("_TOOL_LEAK_RE", _TOOL_LEAK_RE), ("_HEDGE_RE", _HEDGE_RE)):
    check(f"{name} has no stray control character", "" not in rx.pattern,
          repr(rx.pattern))

print("== a margin note keeps ONE fault and stays short ==")
LONG = ("Bank closing balance shown as Rs 2,20,460 instead of Rs 2,05,460; the Jan 31 "
        "deposit entry appears to use Rs 22,700 correctly but the bank total is off")
short = humanise_remark(LONG, margin=True)
check("an over-long margin note is cut to its first fault",
      len(short.split()) <= 12, f"{len(short.split())} words: {short}")
check("the fault that survives is the FIRST one", short.startswith("Bank closing balance"), short)
check("the cover comment is NOT truncated",
      len(humanise_remark(LONG, margin=False).split()) > 12)
check("a short note is untouched by the margin rule",
      humanise_remark("Working note missing", margin=True) == "Working note missing")
check("a single long sentence is left alone rather than cut mid-clause",
      humanise_remark("Depreciation on Machine One should be charged only upto the "
                      "actual date of sale", margin=True).endswith("sale"))

print("== the judged teacher-voice lines all pass our own rules ==")
JUDGED = [
    "Depreciation on Machine 1 only upto 1 Oct, not 31 March.",
    "Drawings Rs 15,000 missed in bank column. Should be 2,05,460.",
    "Dishonoured bill Rs 26,200 taken twice.",
    "Bills for Collection entry missing on 5 June for first bill.",
    "Net profit Rs 34,503 not added to capital. Should be 80,593.",
]
for line in JUDGED:
    check(f"{line[:34]!r} is <= 12 words", len(line.split()) <= 12, f"{len(line.split())}")
    check(f"{line[:34]!r} survives cleaning unchanged",
          humanise_remark(line, margin=True).rstrip(".") == line.rstrip("."),
          humanise_remark(line, margin=True))
    check(f"{line[:34]!r} is clean of report-speak", robotic_phrases(line) == [],
          f"{robotic_phrases(line)}")

print("== the prompt bans reciting the rule instead of marking the error ==")
_p = build_system_prompt("Accounting", "CA Foundation", "professional")
check("prompt carries the rule-vs-error tell", "MARK THE ERROR, DO NOT STATE THE RULE" in _p)
check("prompt caps margin notes at 12 words", "12 WORDS MAXIMUM" in _p)
check("prompt demands ONE fault", "Name ONE fault" in _p)
check("prompt forbids inventing unstated facts", "is an invention" in _p)

print("== the linter catches the register that shipped ==")
BAD_COVER = ("You have demonstrated a solid understanding of MCQ-based concepts. "
             "Your main losses came from incorrect answers in Q7 and Q8. "
             "Focus on carefully applying the profit-available-for-appropriation logic.")
hits = robotic_phrases(BAD_COVER)
check("the real cover comment is flagged", len(hits) >= 2, f"{hits}")
GOOD_COVER = ("Section A is solid - your definitions are clear. Most of your marks went "
              "in the goodwill sum, where the depreciation adjustment has the wrong "
              "sign. Revise adjusted-profit questions before the next test.")
check("a teacher-voiced cover comment is clean", robotic_phrases(GOOD_COVER) == [],
      f"{robotic_phrases(GOOD_COVER)}")
check("'the student' is flagged", "the student" in robotic_phrases("the student failed to show working"))
check("semicolon-stacked margin remarks are flagged",
      "semicolon-stacked fragments" in
      robotic_phrases("sales entry amount wrong; fire/insurance entry incorrect"))
check("a normal sentence with no semicolon is clean",
      robotic_phrases("Should be Rs 92,200") == [])

print("== cleaning runs on BOTH the cover comment and every margin remark ==")
rep = _reconcile_report(GradeReport(
    total_score=1, max_total=2,
    overall_remarks="Overall, you have done well.",
    questions=[QuestionMark(qid="Q1", score=1, max_score=2, page=1, y_fraction=0.5,
                            remark="Lost 1 mark due to wrong total")]), None, None)
check("cover comment cleaned", rep.overall_remarks == "You have done well",
      repr(rep.overall_remarks))
check("margin remark cleaned", rep.questions[0].remark == "Wrong total",
      repr(rep.questions[0].remark))

print("== the prompt actually carries the guidance ==")
prompt = build_system_prompt("Accountancy", "Class 11", "school")
check("HOW TO WRITE REMARKS section present", "HOW TO WRITE REMARKS" in prompt)
for banned in ("Your main losses came from", "demonstrated a solid understanding"):
    check(f"prompt names {banned!r} as banned", banned in prompt)
check("prompt forbids semicolon-stacked fragments", "semicolons" in prompt)
check("prompt forbids mentioning marks in a remark",
      "never mention marks" in prompt.lower() or "NEVER mention marks" in prompt)
check("prompt no longer teaches the 'Lost 1 mark due to' example",
      'e.g. "Lost 1 mark due to incorrect antonym' not in prompt)
check("prompt shows the human evaluator's real sentence as the model",
      "commenced operation in P.Y. 2021-22" in prompt)

print()
print("== a 1-mark part is not split into method and answer ==")
# MEASURED on a Class 10 Physics copy. Q6(i) is "[1 M] Find the position of the
# candle flame (u)". The student wrote u = -30 cm, which is the paper's own answer,
# and scored 0.5 with the remark "Stated u=-30 cm without showing magnification
# calculation step". The numerical rule said a correct value carries the question
# "only if working is shown", and on a 1-mark part that invents a 0.5 method / 0.5
# answer split the paper never allocated: the mark is for producing the value, and
# an examiner marking one mark for "find u" gives it for u = -30 cm. The same paper
# graded a second copy that DID show the magnification step at 1/1, so the tool was
# also scoring the same correct answer two different ways.
_np = build_system_prompt("Physics", "Class 10", "school")

# ...but ONLY after the part is established as attempted at all. The same copy left
# Q6(i) blank and wrote u = -30 inside part (ii)'s working, where it is an INPUT to
# (ii), not an answer to (i). The tool credited it 0.5; relaxing the working rule
# alone would have pushed that to a full mark for a blank part, so the blank test has
# to run first.
check("a labelled part left blank scores 0", "A labelled part left BLANK is unattempted" in _np)
check("and that is settled before any partial credit",
      _np.find("A labelled part left BLANK") < _np.find("no room for a separate method mark"))
check("a value inside another part's working is not this part's answer",
      "there it is an INPUT to that part" in _np)
check("but parts answered together still count as attempted",
      "a combined answer is attempted for every part it covers" in _np)

check("the 1-mark exception is stated",
      "no room for a separate method mark" in _np)
check("it names the full mark, not a fraction",
      "takes the FULL mark and unshown working costs NOTHING" in _np)
check("it forbids the invented half", 'invents a split the paper never allocated' in _np)
check("but a part that ASKS for the working keeps it",
      '"show that"' in _np and "there the working is the answer" in _np)
check("the general rule for bigger parts still stands",
      "A correct final value carries the question only if working is shown" in _np)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
