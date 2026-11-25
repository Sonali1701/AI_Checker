# -*- coding: utf-8 -*-
"""Offline regression for the moderation (second-examiner) pass.

The moderator states what each mark row SHOULD be. It corrects the grader in both
directions — restoring marks taken for something the student did, and removing marks
given for something the working does not support — because the budget grader is a
cheap model whose failure mode is over-awarding.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (
    AuditItem, Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark,
    _audit_rows, _reconcile_report, apply_audit, build_audit_prompt, has_audit_rows,
    has_deductions,
    parse_audit,
)
from pipeline import ProviderConfig, audit_model_for, audits_awarded_marks, moderation_applies

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def crit(desc, a, m, ev=""):
    return Criterion(description=desc, awarded=a, max=m, met=a >= m, evidence=ev)


def q(qid, score, max_score, criteria=None, remark="", **kw):
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=1,
                        y_fraction=0.5, remark=remark, criteria=criteria or [], **kw)


def adv_report():
    """The Advanced Accounting case: Q4.a wrongly 0/5, Q2 9/10, Q1 MCQs objective."""
    return GradeReport(
        total_score=34.5, max_total=50, overall_remarks="Good work.",
        questions=[
            q("Q1.i", 2, 2, objective=True, chosen_option="A", correct_option="A"),
            q("Q1.ii", 0, 2, objective=True, chosen_option="B", correct_option="A",
              remark="Incorrect option (chose B, correct A)"),
            q("Q2", 9, 10, [crit("Format", 2, 2), crit("Notes", 6, 7, "share capital note incomplete"),
                            crit("Totals", 1, 1)], remark="Minor omissions"),
            q("Q4.a", 0, 5, [crit("Weighted avg shares", 0, 2, "student: 4,57,500; key: 4,57,500"),
                             crit("EPS", 0, 2, "student: 4.8; key: 4.80"),
                             crit("AS 20 explanation", 0, 1, "not explained")],
              remark="Incorrect calculation of net profit and weighted average shares."),
            q("Q4.b", 4.5, 5, [crit("Treatment", 2, 2), crit("Reason", 2.5, 3, "conclusion thin")]),
            q("Q5.a", 0, 5, remark="Unattempted."),
        ],
    )


# Deduction-only rows (include_awards=False):
#   #0 Q2 Notes 6/7 | #1 Q4.a shares 0/2 | #2 Q4.a EPS 0/2 | #3 Q4.a AS20 0/1
#   #4 Q4.b Reason 2.5/3 | #5 Q5.a whole 0/5
print("== row enumeration ==")
rows = _audit_rows(adv_report())
check("6 deduction rows, MCQs excluded", len(rows) == 6, str([(x[0].qid, x[1]) for x in rows]))
rows_aw = _audit_rows(adv_report(), include_awards=True)
check("include_awards adds the awarded rows",
      len(rows_aw) == 9, str([(x[0].qid, x[1]) for x in rows_aw]))
check("objective questions never moderated",
      all(not x[0].qid.startswith("Q1.") for x in rows_aw))

print("== prompt ==")
r = adv_report()
check("deductions detected", has_deductions(r))
p = build_audit_prompt(r, "KEY: EPS = 21,96,000 / 4,57,500 = 4.80", "transcript text")
check("rows and criterion indices are numbered",
      "#1 [Q4.a; criterion 0] Weighted avg shares: now 0 of 2" in p, p[:600])
check("examiner's reason shown", "student: 4,57,500; key: 4,57,500" in p)
check("whole-question row present", "#5 [Q4.a]" not in p and "whole question" in p)
check("both directions instructed", "RAISE a row" in p and "LOWER a row" in p)
check("unverifiable => unchanged", "LEAVE UNCHANGED" in p)
check("key + transcript carried", "KEY: EPS" in p and "transcript text" in p)
p_aw = build_audit_prompt(r, "k", "t", include_awards=True)
check("awards appear only with include_awards",
      "Format: now 2 of 2" in p_aw and "Format: now 2 of 2" not in p)

print("== apply_audit: raises, lowers, clamps ==")
verdicts = [
    AuditItem(row=1, correct=2, reason="matches key"),            # restore 2
    AuditItem(row=2, correct=9, reason="over-ask -> clamp to max 2"),
    AuditItem(row=3, correct=0, reason="AS 20 not explained"),    # unchanged
    AuditItem(row=0, correct=6.5, reason="note partly present"),  # +0.5
    AuditItem(row=5, correct=-3, reason="negative -> clamp to 0"),
    AuditItem(row=99, correct=5, reason="out of range — ignored"),
    AuditItem(row=1, correct=0, reason="duplicate row — ignored"),
]
r2, moved = apply_audit(adv_report(), verdicts)
by = {x.qid: x for x in r2.questions}
check("net moved == 4.5", abs(moved - 4.5) < 1e-9, f"got {moved}")
check("Q4.a now 4/5", abs(by["Q4.a"].score - 4) < 1e-9, f"got {by['Q4.a'].score}")
check("Q4.a max untouched", abs(by["Q4.a"].max_score - 5) < 1e-9)
check("EPS clamped to its max 2", abs(by["Q4.a"].criteria[1].awarded - 2) < 1e-9)
check("negative clamped to 0", abs(by["Q5.a"].score) < 1e-9)
check("duplicate row ignored", abs(by["Q4.a"].criteria[0].awarded - 2) < 1e-9)
check("Q2 now 9.5/10", abs(by["Q2"].score - 9.5) < 1e-9, f"got {by['Q2'].score}")
check("objective untouched", abs(by["Q1.ii"].score) < 1e-9)
check("total == 20", abs(r2.total_score - 20) < 1e-9, f"got {r2.total_score}")
check("max_total unchanged 50", abs(r2.max_total - 50) < 1e-9)
sec = {s["qid"]: s for s in r2.section_totals}
check("section Q4 = 8.5/10", abs(sec["Q4"]["score"] - 8.5) < 1e-9, str(sec.get("Q4")))

print("== lowering unsupported awards (the cheap-grader failure mode) ==")
# With include_awards, row #0 is Q2 'Format' (2/2 awarded).
rows_aw = _audit_rows(adv_report(), include_awards=True)
check("row 0 is an awarded row", rows_aw[0][0].qid == "Q2" and rows_aw[0][1] == 0,
      str((rows_aw[0][0].qid, rows_aw[0][1])))
r4, moved4 = apply_audit(adv_report(),
                         [AuditItem(row=0, correct=0.5, reason="Schedule III format not followed")],
                         include_awards=True)
by4 = {x.qid: x for x in r4.questions}
check("award reduced 2 -> 0.5", abs(by4["Q2"].criteria[0].awarded - 0.5) < 1e-9,
      f"got {by4['Q2'].criteria[0].awarded}")
check("net moved is negative", abs(moved4 + 1.5) < 1e-9, f"got {moved4}")
check("Q2 total fell to 7.5", abs(by4["Q2"].score - 7.5) < 1e-9, f"got {by4['Q2'].score}")

print("== full restore clears the stored reason ==")
r3, _ = apply_audit(adv_report(), [AuditItem(row=4, correct=3, reason="ok")])
q4b = {x.qid: x for x in r3.questions}["Q4.b"]
check("Q4.b 5/5, remark and evidence cleared",
      abs(q4b.score - 5) < 1e-9 and q4b.remark == "" and q4b.criteria[1].evidence == "")

print("== forced maxima survive moderation ==")
rep = GradeReport(total_score=3, max_total=5, overall_remarks="", questions=[
    q("Q3", 3, 4, [crit("a", 1, 2, "x"), crit("b", 2, 2)]),
])
rep = _reconcile_report(rep, MarksScheme(total=7, items=[MarksItem(qid="Q3", max=7)]), 7)
check("scheme forced Q3 max to 7", abs(rep.questions[0].max_score - 7) < 1e-9)
rep, got = apply_audit(rep, [AuditItem(row=0, correct=2, reason="ok")])
check("ruling applied", abs(got - 1) < 1e-9, f"got {got}")
check("Q3 max STILL 7", abs(rep.questions[0].max_score - 7) < 1e-9)
check("Q3 score 4/7", abs(rep.questions[0].score - 4) < 1e-9 and abs(rep.max_total - 7) < 1e-9)

print("== parse_audit tolerance ==")
cases = {
    "clean": '{"items":[{"row":0,"correct":2,"reason":"r"}]}',
    "fenced": '```json\n{"items":[{"row":0,"correct":2}]}\n```',
    # The shape gemini-2.5-pro actually returned live: a bare array keyed by "id".
    "bare list keyed by id (LIVE)": '[{"id":0,"correct":2}]',
    "positional, no row key": '[{"correct":"2 marks"}]',
    "alias correct_marks": '{"verdicts":[{"row":0,"correct_marks":2}]}',
    "prose+json": 'Rulings:\n{"items":[{"row":0,"correct":2,"reason":"r"}]}',
    "structured reason": '{"items":[{"row":0,"correct":2,"reason":{"why":"matches key"}}]}',
}
for name, text in cases.items():
    try:
        items = parse_audit(text)
        ok = len(items) == 1 and items[0].row == 0 and items[0].correct == 2
        check(f"parse {name}", ok, repr(items))
    except Exception as exc:
        check(f"parse {name}", False, f"{type(exc).__name__}: {exc}")
check("entry with no ruling is dropped", parse_audit('[{"row":0,"reason":"unsure"}]') == [])

print("== the audit is NOT skipped when a cheap grader awards everything ==")
# A budget grader's worst failure is full marks everywhere. Gating the audit on
# deductions alone would skip it in exactly that case.
perfect = GradeReport(total_score=10, max_total=10, overall_remarks="", questions=[
    q("Q1", 10, 10, [crit("Working", 6, 6), crit("Answer", 4, 4)]),
])
check("no deductions to find", not has_deductions(perfect))
check("deduction-only gate would SKIP it", not has_audit_rows(perfect, False))
check("award-aware gate still RUNS it", has_audit_rows(perfect, True))
lowered, moved = apply_audit(
    perfect, [AuditItem(row=0, correct=2, reason="working not shown in transcript")],
    include_awards=True)
check("unsupported award can be cut", abs(lowered.questions[0].criteria[0].awarded - 2) < 1e-9)
check("question total falls to 6/10", abs(lowered.questions[0].score - 6) < 1e-9,
      f"got {lowered.questions[0].score}")
check("net movement negative", abs(moved + 4) < 1e-9, f"got {moved}")

print("== cost policy ==")
pro = ProviderConfig(provider="claude", model="claude-opus-4-8", exam_type="professional")
budget = ProviderConfig(provider="claude", model="claude-haiku-4-5", exam_type="professional")
check("audit always runs on the strongest Claude model", audit_model_for(budget) == "claude-opus-4-8")
check("a Gemini config is coerced to Claude", ProviderConfig(
      model="gemini-2.5-flash", exam_type="professional").is_claude)
check("cheap grader => audit checks awards too", audits_awarded_marks(budget))
check("strongest grader => audit checks deductions only", not audits_awarded_marks(pro))
check("professional applies", moderation_applies(budget, "Physics"))
check("commerce subject applies", moderation_applies(ProviderConfig(exam_type="school"), "Accountancy"))
check("school physics does not", not moderation_applies(ProviderConfig(exam_type="school"), "Physics"))

print("== who audits whom, and whether awards are re-checked ==")
# Pinned because it is load-bearing and easy to flip on a wrong hunch. Self-audit
# (grader == auditor) was MEASURED WORSE on the CA Taxation booklet (human 42):
# self-audit, bidirectional-anyway 67.5 (+25.5) vs self-audit, restore-only 59.0
# (+17.0) — a cheap model re-checking its own awards just confirms its own
# generosity. The fix is a genuinely STRONGER auditor, never the grader itself:
# claude-opus-4-8 for Claude routes (matching the opus default already used for
# marks-scheme extraction on the same route), gemini-2.5-pro for Gemini routes
# (unchanged — this was already right). See pipeline.audit_model_for's docstring
# for the full measurement this is pinned against.
from pipeline import (
    _CLAUDE_SECOND_OPINION_MODEL,
    audit_model_for,
    audits_awarded_marks,
    cfg_from_request,
)


def _cfg(provider="gemini", model="", student_class="CA Inter Group 1"):
    return cfg_from_request(provider, model, "Taxation", google_token="t",
                            task_id="x", student_class=student_class)


route = _cfg()
check("the app's default route grades on Claude", route.is_claude, route.model)
check("a Claude sheet is audited by claude's STRONGER model, never itself",
      audit_model_for(route) == _CLAUDE_SECOND_OPINION_MODEL
      and audit_model_for(route) != route.model,
      audit_model_for(route))
check("that pairing DOES re-check awarded marks (real second opinion, not self-audit)",
      audits_awarded_marks(route))
check("haiku is also audited by opus, never by itself",
      audit_model_for(_cfg("claude", "claude-haiku-4-5")) == _CLAUDE_SECOND_OPINION_MODEL)
check("a GEMINI request is graded on Claude and audited by the stronger Claude model",
      audit_model_for(_cfg("gemini", "gemini-2.5-flash", "Class 12"))
      == _CLAUDE_SECOND_OPINION_MODEL)
check("and that pairing DOES re-check awarded marks",
      audits_awarded_marks(_cfg("gemini", "gemini-2.5-flash", "Class 12")))
check("the auditor never second-guesses its own awards (claude side)",
      not audits_awarded_marks(_cfg("claude", _CLAUDE_SECOND_OPINION_MODEL)))


# Moderation is text-only and skips objective items by design, so nothing downstream
# re-checks an MCQ. REAL COPY: CA Inter Advanced Accounting Master Test-01, Q1(iii).
# The student circled (C), the key says (B), and the grading pass reported chosen='B'
# and awarded a full 2 of 2 — marks for a plainly wrong answer, which is the first
# thing a faculty reviewer tests. Mathpix had already read the line correctly.
print("== a misread MCQ letter is caught against the page's own transcript ==")
from grader import verify_objective_choices
from mathpix import LineBox, PageOCR

_MCQ_LINES = {
    24: "(1) i) cash flow from operabing Acrivities (4)",   # a circled (A), read as 4
    25: "i) Cash frow from Investing astivides (B)",
    26: "iii) cass flow from financing acrivisies (c)",     # the student's real answer
    27: "iv) Cash flow from Operaring Acrivies (7)",        # a circled (A), read as 7
    28: "v) Yes, Diclosme is regurised to be made",         # letter is on the NEXT line
}
_MCQ_OCR = [PageOCR(page=7, image_width=1000, image_height=1400,
                    lines=[LineBox(f"P7L{n}", t, (0.0, 0.0, 10.0, 10.0), 7)
                           for n, t in _MCQ_LINES.items()])]


def _mcq_report():
    rows = [("Q1.i", "P7L24", "A", "A", 2.0), ("Q1.ii", "P7L25", "B", "A", 0.0),
            ("Q1.iii", "P7L26", "B", "B", 2.0), ("Q1.iv", "P7L27", "A", "A", 2.0),
            ("Q1.v", "P7L28", "B", "B", 2.0)]
    return GradeReport(
        total_score=8, max_total=10, overall_remarks="",
        questions=[QuestionMark(qid=q, score=sc, max_score=2.0, page=7,
                                y_fraction=0.5, anchor_line_id=a, objective=True,
                                chosen_option=ch, correct_option=co, attempted=True)
                   for q, a, ch, co, sc in rows])


_mr = _mcq_report()
_notes = verify_objective_choices(_mr, _MCQ_OCR)
_by = {q.qid: q for q in _mr.questions}
check("the misread item is corrected", len(_notes) == 1, f"{_notes}")
check("it now reads the letter the student wrote", _by["Q1.iii"].chosen_option == "C")
check("and scores zero against a key of B", _by["Q1.iii"].score == 0.0)
check("the student is told the right answer", _by["Q1.iii"].remark == "Ans: B")
check("the total drops by exactly that item", _mr.total_score == 6.0, _mr.total_score)
# Mathpix renders a circled (A) as "(4)"/"(7)" often enough that anything which is
# not a clean letter must be left alone rather than treated as evidence.
check("a digit misread is NOT treated as a letter", _by["Q1.i"].chosen_option == "A")
check("nor on the second one", _by["Q1.iv"].chosen_option == "A")
check("an item whose letter is on a continuation line is left alone",
      _by["Q1.v"].chosen_option == "B" and _by["Q1.v"].score == 2.0)
check("an item the transcript agrees with is untouched",
      _by["Q1.ii"].chosen_option == "B" and _by["Q1.ii"].score == 0.0)
check("no OCR at all changes nothing", verify_objective_choices(_mcq_report(), []) == [])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
