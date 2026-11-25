# -*- coding: utf-8 -*-
"""Offline regression: the official total must be enforced, not just warned about.

Reproduces the EXACT failing Accounting report (58.5/64.5 vs official 50) and
verifies every enforcement layer without any network call:
  L1  _reconcile_report with an authoritative scheme forces maxima outright
  L2  apply_repair_corrections applies a repair pass's corrected maxima
  L3  force_official_total guarantees the denominator deterministically
Plus parse_repair drift tolerance and the PDF sentence splitter ("64.5" stays whole).
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (
    Criterion,
    GradeReport,
    MarksItem,
    MarksScheme,
    QuestionMark,
    RepairItem,
    _reconcile_report,
    apply_repair_corrections,
    build_repair_prompt,
    force_official_total,
    parse_repair,
)

FAILS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def crit(desc, a, m):
    return Criterion(description=desc, awarded=a, max=m, met=a >= m)


def q(qid, score, max_score, criteria=None, remark=""):
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=1,
                        y_fraction=0.5, remark=remark, criteria=criteria or [])


def failing_report():
    """The report from the user's evaluated PDF: maxima sum 64.5, official total 50."""
    return GradeReport(
        total_score=58.5,
        max_total=64.5,
        overall_remarks="You demonstrate a strong understanding of accounting principles. "
                        "However, there are notable calculation errors in Q2 and Q3.",
        questions=[
            q("1(a)", 2, 2, [crit("Statement", 1, 1), crit("Reason", 1, 1)]),
            q("1(b)", 2, 2, [crit("Statement", 1, 1), crit("Reason", 1, 1)]),
            q("1(c)", 2, 2, [crit("Statement", 1, 1), crit("Reason", 1, 1)]),
            q("1(d)", 2, 2, [crit("Statement", 1, 1), crit("Reason", 1, 1)]),
            q("1(e)", 1, 2, [crit("Statement", 1, 1), crit("Reason", 0, 1)],
              remark="Reasoning unclear"),
            q("2", 4.5, 7.5, [crit("Opening WDV", 0.5, 1.5), crit("Dep M1", 1, 2),
                              crit("Dep M2", 1, 2), crit("Working note", 2, 2)],
              remark="Errors in opening WDV"),
            q("3", 6.5, 7.5, [crit("Format", 2, 2), crit("Entries", 3, 3.5),
                              crit("Deposit calc", 1.5, 2)], remark="Deposit error"),
            q("4", 8.5, 9.5, [crit("Format", 2, 2), crit("Items", 5, 5.5),
                              crit("Cheque treatment", 1.5, 2)], remark="Cheque error"),
            q("5", 10, 10),
            q("6", 20, 20, [crit("Trading a/c", 8, 8), crit("P&L a/c", 6, 6),
                            crit("Balance sheet", 6, 6)]),
        ],
    )


TRUE_SCHEME = MarksScheme(total=50, items=[
    MarksItem(qid="Q1.a", max=2), MarksItem(qid="Q1.b", max=2), MarksItem(qid="Q1.c", max=2),
    MarksItem(qid="Q1.d", max=2), MarksItem(qid="Q1.e", max=2),
    MarksItem(qid="Q2", max=5), MarksItem(qid="Q3", max=5), MarksItem(qid="Q4", max=5),
    MarksItem(qid="Q5", max=10), MarksItem(qid="Q6", max=15),
])


print("== L1: authoritative scheme forces maxima in _reconcile_report ==")
r = _reconcile_report(failing_report(), TRUE_SCHEME, 50)
check("max_total == 50", abs(r.max_total - 50) < 1e-9, f"got {r.max_total}")
check("no mismatch warning", "TOTAL MISMATCH" not in r.overall_remarks)
by = {qq.qid: qq for qq in r.questions}
check("Q6 forced to /15", any(abs(qq.max_score - 15) < 1e-9 for qq in r.questions
                              if qq.qid in ("6", "Q6")), str([(qq.qid, qq.max_score) for qq in r.questions]))
check("score <= max everywhere", all(qq.score <= qq.max_score + 1e-9 for qq in r.questions))
check("total_score <= 50", r.total_score <= 50 + 1e-9, f"got {r.total_score}")

print("== L2: apply_repair_corrections ==")
corrections = [
    RepairItem(qid="1(a)", max_score=2, score=2), RepairItem(qid="1(b)", max_score=2, score=2),
    RepairItem(qid="1(c)", max_score=2, score=2), RepairItem(qid="1(d)", max_score=2, score=2),
    RepairItem(qid="1(e)", max_score=2, score=1),
    RepairItem(qid="2", max_score=5, score=3), RepairItem(qid="3", max_score=5, score=4.5),
    RepairItem(qid="4", max_score=5, score=4.5), RepairItem(qid="5", max_score=10, score=10),
    RepairItem(qid="6", max_score=15, score=15),
]
r2 = apply_repair_corrections(failing_report(), corrections, 50)
check("max_total == 50", abs(r2.max_total - 50) < 1e-9, f"got {r2.max_total}")
check("no mismatch warning", "TOTAL MISMATCH" not in r2.overall_remarks)
by2 = {qq.qid: qq for qq in r2.questions}
check("Q2 -> /5", abs(by2["2"].max_score - 5) < 1e-9, f"got {by2['2'].max_score}")
check("Q2 score == 3", abs(by2["2"].score - 3) < 1e-9, f"got {by2['2'].score}")
check("Q2 criteria sum == 5", abs(sum(c.max for c in by2["2"].criteria) - 5) < 1e-9,
      str([(c.description, c.awarded, c.max) for c in by2["2"].criteria]))
check("Q2 criteria awarded sum == 3", abs(sum(c.awarded for c in by2["2"].criteria) - 3) < 1e-9,
      str([(c.description, c.awarded, c.max) for c in by2["2"].criteria]))
check("Q6 -> 15/15", abs(by2["6"].max_score - 15) < 1e-9 and abs(by2["6"].score - 15) < 1e-9,
      f"got {by2['6'].score}/{by2['6'].max_score}")
check("Q6 criteria sum == 15", abs(sum(c.max for c in by2["6"].criteria) - 15) < 1e-9)
check("half-mark steps only", all(abs(qq.score * 2 - round(qq.score * 2)) < 1e-9
                                  and abs(qq.max_score * 2 - round(qq.max_score * 2)) < 1e-9
                                  for qq in r2.questions))
# 9 + 3 + 4.5 + 4.5 + 10 + 15 = 46 (the human evaluator gave 45/50 — within 1 mark)
check("expected final 46/50", abs(r2.total_score - 46.0) < 1e-9, f"got {r2.total_score}")
sec = {s["qid"]: s for s in r2.section_totals}
check("section Q1 = 9/10", abs(sec["Q1"]["score"] - 9) < 1e-9 and abs(sec["Q1"]["max_score"] - 10) < 1e-9,
      str(sec.get("Q1")))

print("== L2b: bad repair (wrong sum) raises ==")
bad = [RepairItem(qid="1(a)", max_score=47, score=40)]
try:
    apply_repair_corrections(failing_report(), bad, 50)
    check("raises ValueError", False)
except ValueError:
    check("raises ValueError", True)

print("== L2c: repair with a PHANTOM question (max 0) drops it ==")
rep = failing_report()
rep.questions.append(q("7", 3, 6, remark="phantom — paper has no Q7"))
rep.max_total = 70.5
corrections_ph = corrections + [RepairItem(qid="7", max_score=0, score=0)]
r2c = apply_repair_corrections(rep, corrections_ph, 50)
check("max_total == 50", abs(r2c.max_total - 50) < 1e-9, f"got {r2c.max_total}")
check("phantom Q7 gone or 0-max", all(qq.max_score > 0 or qq.qid != "7" for qq in r2c.questions)
      and abs(sum(qq.max_score for qq in r2c.questions) - 50) < 1e-9)

print("== L3: force_official_total (deterministic, no model) ==")
r3 = force_official_total(failing_report(), 50)
check("max_total == 50 exactly", abs(r3.max_total - 50) < 1e-9, f"got {r3.max_total}")
check("rescale notice present", "per-question split" in r3.overall_remarks
      and "proportional" in r3.overall_remarks, r3.overall_remarks[:120])
check("no mismatch warning", "TOTAL MISMATCH" not in r3.overall_remarks)
check("does not ask for a paper the user already supplied",
      "upload the question paper" not in r3.overall_remarks)
check("the notice is not repeated when rescaling twice",
      force_official_total(r3, 50).overall_remarks.count("proportional") == 1,
      force_official_total(r3, 50).overall_remarks[:160])
check("score <= max everywhere", all(qq.score <= qq.max_score + 1e-9 for qq in r3.questions))
check("half-mark steps only", all(abs(qq.max_score * 2 - round(qq.max_score * 2)) < 1e-9
                                  for qq in r3.questions))
print("   forced split:", [(qq.qid, qq.score, qq.max_score) for qq in r3.questions])
print("   forced total:", r3.total_score, "/", r3.max_total)

print("== L3b: force on a report with zero maxima still lands on target ==")
weird = GradeReport(total_score=0, max_total=0, overall_remarks="",
                    questions=[q("1", 0, 0), q("2", 0, 0)])
r3b = force_official_total(weird, 20)
check("max_total == 20", abs(r3b.max_total - 20) < 1e-9, f"got {r3b.max_total}")

print("== the denominator invariant holds at the EXIT of grade_prepared ==")
# A booklet once shipped 25/27 on a 25-mark paper, so the guard is re-asserted after
# every step. This drives the real pipeline with the model call stubbed out.
import pipeline as _pipeline

def _stub_grade(**kw):
    """A grader that returns the wrong denominator no matter what."""
    return GradeReport(total_score=25, max_total=27, overall_remarks="x", questions=[
        q("Q1", 25, 27, [crit("a", 25, 27)])])

_saved = (_pipeline.claude_grade, _pipeline.claude_repair, _pipeline.moderation_applies)
_pipeline.claude_grade = _stub_grade
def _boom(*a, **k):
    raise RuntimeError("repair unavailable")
_pipeline.claude_repair = _boom
_pipeline.moderation_applies = lambda cfg, subject: False
try:
    out = _pipeline.grade_prepared(
        qp_pngs=[], sa_pngs=[b"x"], ak_pngs=None, ak_text="key", marks_scheme=None,
        cfg=_pipeline.ProviderConfig(exam_type="school"),
        filename="t.pdf", subject="Economics", use_mathpix=False, total_marks=25.0)
    rep = out["report"]
    check("wrong denominator is forced to the official total",
          abs(rep.max_total - 25) < 1e-9, f"got {rep.max_total}")
    check("score stays within the forced total", rep.total_score <= 25 + 1e-9)
    check("the rescale is disclosed", "proportional" in rep.overall_remarks,
          rep.overall_remarks[:120])
finally:
    (_pipeline.claude_grade, _pipeline.claude_repair, _pipeline.moderation_applies) = _saved

print("== parse_repair drift tolerance ==")
cases = {
    "clean": '{"questions": [{"qid": "1(a)", "max_score": 2, "score": 2}]}',
    "fenced": '```json\n{"questions": [{"qid": "1(a)", "max_score": 2, "score": 2}]}\n```',
    "bare list": '[{"qid": "1(a)", "max_score": 2, "score": 2}]',
    "aliases": '{"questions": [{"question_id": "1(a)", "max": "2 Marks", "awarded": "2"}]}',
    "wrapper": '{"repair": {"questions": [{"qid": "1(a)", "max_score": 2, "score": 2}]}}',
    "prose+json": 'Here are the corrections:\n{"questions": [{"qid": "1(a)", "max_score": 2, "score": 2}]}',
}
for name, text in cases.items():
    try:
        items = parse_repair(text)
        ok = len(items) == 1 and items[0].qid == "1(a)" and items[0].max_score == 2 and items[0].score == 2
        check(f"parse {name}", ok, repr(items))
    except Exception as exc:
        check(f"parse {name}", False, f"{type(exc).__name__}: {exc}")

print("== repair prompt sanity ==")
p = build_repair_prompt(failing_report(), 50, TRUE_SCHEME,
                        "MASTER TEST 1\nMarks : 50\n1(a) ... (2 Marks)")
check("prompt cites official 50", "50" in p and "MUST sum EXACTLY to 50" in p)
check("prompt lists current rows", "6: 20/20" in p)
check("prompt carries paper text", "MASTER TEST" in p)

print("== PDF sentence splitter keeps decimals whole ==")
from pdf_renderer import _split_remark_sentences
warn = ("[TOTAL MISMATCH: per-question maxima sum to 64.5 but the official paper total "
        "is 50 — review the maxima before trusting this score] You demonstrate a strong "
        "understanding. However, Q2 has errors worth Rs. 3 in total.")
parts = _split_remark_sentences(warn)
check("64.5 stays in one bullet", any("64.5" in s for s in parts)
      and not any(s.strip().startswith("5 ") for s in parts), repr(parts))
check("bracketed notice is its own bullet", parts[0].startswith("[TOTAL") and parts[0].endswith("]"),
      repr(parts[0]))
check("'Rs. 3' not split", any("Rs. 3" in s for s in parts), repr(parts))
check("normal sentences do split", len(parts) >= 3, repr(parts))


# A part the student left blank inside a question they DID answer belongs to them, not
# to the questions they answered. On a choice paper the scheme cannot be authoritative
# (its tags cover every alternative), so _reconcile_report's zero-entry pass never runs
# and the booklet comes back short. REAL COPY: CA Inter Advanced Accounting, Master
# Test-01, official 50. The model returned Q1 10, Q2 10, Q3 10, Q4 10, Q5.b 5 = 45; the
# missing 5 was Q5.a, simply not written. The old last resort scaled every question up
# by 50/45, printing "Q1 6.5/11.5", "Q2 10.5/11", "Q5.b 5/5.5" — denominators the paper
# never had, on a copy going to a CA faculty reviewer.
print("== an unanswered part is booked as 0, not smeared over the rest ==")
from grader import MarksItem, MarksScheme, fill_unanswered_parts

_CA_SCHEME = MarksScheme(total=60.0, items=[
    MarksItem(qid=q, max=m) for q, m in
    [("Q1", 10.0), ("Q2", 10.0), ("Q3", 10.0), ("Q4.a", 5.0), ("Q4.b", 5.0),
     ("Q5.a", 5.0), ("Q5.b", 5.0), ("Q6.a", 5.0), ("Q6.b", 5.0)]])


def _ca_report():
    rows = [("Q1.i", 2, 2), ("Q1.ii", 0, 2), ("Q1.iii", 0, 2), ("Q1.iv", 2, 2),
            ("Q1.v", 2, 2), ("Q2", 10, 10), ("Q3", 9, 10), ("Q4.a", 5, 5),
            ("Q4.b", 5, 5), ("Q5.b", 5, 5)]
    return GradeReport(
        total_score=40, max_total=45, overall_remarks="",
        questions=[QuestionMark(qid=q, score=s, max_score=m, page=1, y_fraction=0.5,
                                attempted=True) for q, s, m in rows])


_ca = _ca_report()
check("the fill reports success", fill_unanswered_parts(_ca, _CA_SCHEME, 50.0))
check("the denominator is the official 50", abs(_ca.max_total - 50.0) < 0.01,
      f"max_total={_ca.max_total}")
check("the score is unchanged by it", abs(_ca.total_score - 40.0) < 0.01,
      f"total={_ca.total_score}")
_tops = {}
for _q in _ca.questions:
    _t = _q.qid.split(".")[0]
    _a = _tops.setdefault(_t, [0.0, 0.0])
    _a[0] += float(_q.score); _a[1] += float(_q.max_score)
check("every printed maximum survives intact",
      all(abs(v[1] - 10.0) < 0.01 for v in _tops.values()),
      f"{ {k: '%g/%g' % (v[0], v[1]) for k, v in _tops.items()} }")
check("the blank part shows as 0 out of its own marks",
      any(q.qid == "Q5.a" and q.score == 0 and q.max_score == 5 and not q.attempted
          for q in _ca.questions))
# "Answer any four of five": a question the student was ENTITLED to skip is not theirs
# to lose, and must not appear on the copy at all.
check("a question the student was entitled to skip is left out",
      not any(q.qid.startswith("Q6") for q in _ca.questions),
      f"{[q.qid for q in _ca.questions]}")

_no_scheme = _ca_report()
check("with no scheme it declines rather than guessing",
      not fill_unanswered_parts(_no_scheme, None, 50.0))
_wrong = _ca_report()
check("it declines when the parts would not reach the official total",
      not fill_unanswered_parts(_wrong, _CA_SCHEME, 70.0))

# The grader's decomposition is not stable run to run: three runs of THIS copy returned
# per-question maxima summing to 45, 44 and 45 again. A rule that only fired on an exact
# landing worked once and then quietly handed back to the global rescale, so the copy
# went out with "/11.5" anyway. Choice decides WHICH questions count, never what one is
# worth, so each answered question is pinned to its printed total instead.
_drift = GradeReport(
    total_score=39, max_total=44, overall_remarks="",
    questions=[QuestionMark(qid=q, score=sc, max_score=m, page=1, y_fraction=0.5,
                            attempted=True) for q, sc, m in
               [("Q1.i", 2, 2), ("Q1.ii", 0, 2), ("Q1.iii", 0, 2), ("Q1.iv", 2, 2),
                ("Q1.v", 2, 2), ("Q2", 10, 10), ("Q3", 9, 9), ("Q4.a", 4, 5),
                ("Q4.b", 5, 5), ("Q5.b", 5, 5)]])
check("a drifted decomposition still reaches the official total",
      fill_unanswered_parts(_drift, _CA_SCHEME, 50.0))
_dtops = {}
for _q in _drift.questions:
    _t = _q.qid.split(".")[0]
    _a = _dtops.setdefault(_t, [0.0, 0.0])
    _a[0] += float(_q.score); _a[1] += float(_q.max_score)
check("and every question still shows its printed maximum",
      all(abs(v[1] - 10.0) < 0.01 for v in _dtops.values()),
      f"{ {k: '%g/%g' % (v[0], v[1]) for k, v in _dtops.items()} }")
check("the drift is absorbed inside the question it came from",
      abs(_dtops["Q3"][1] - 10.0) < 0.01 and abs(_dtops["Q2"][1] - 10.0) < 0.01)

# The grading pass stamps "[TOTAL MISMATCH: ... review the maxima before trusting this
# score]" on the remarks when its own maxima miss the paper. force_official_total
# strips it; booking the unanswered parts did not, so a finished CA copy went out with
# that notice at the top of the cover page — still telling the reader not to trust a
# total that the very next column printed, correctly, as 50.
_stamped = _ca_report()
_stamped.overall_remarks = ("[TOTAL MISMATCH: per-question maxima sum to 45 but the "
                            "official paper total is 50 - review the maxima before "
                            "trusting this score] Strong work across the paper.")
fill_unanswered_parts(_stamped, _CA_SCHEME, 50.0)
check("the stale mismatch notice is stripped once the total is right",
      "TOTAL MISMATCH" not in _stamped.overall_remarks, repr(_stamped.overall_remarks)[:90])
check("the teacher's own words survive",
      "Strong work across the paper" in _stamped.overall_remarks,
      repr(_stamped.overall_remarks))

# ...and the gate that should have caught it only ever read the PER-QUESTION remarks.
from preflight import check_copy as _cc
_bad = _ca_report()
_bad.overall_remarks = "[TOTAL MISMATCH: something internal] Well done."
check("the release gate now reads the cover-page remarks too",
      any(i.code == "overall-remark-wording" and i.severity == "block"
          for i in _cc(_bad, total_marks=45.0)))
_ok = _ca_report()
_ok.overall_remarks = "Strong work across the paper. Revise AS-20 before the next test."
check("and passes ordinary teacher wording",
      not any(i.code == "overall-remark-wording" for i in _cc(_ok, total_marks=45.0)))


# A repair that lands half a mark out is still a per-question split read off the paper.
# Rejecting it leaves only the deterministic rescale, which touches EVERY question:
# on a 70-mark Class 12 Chemistry paper the repair returned 70.5, was thrown away over
# 0.5, and the fallback then smeared 76.5 across the whole copy.
print("== a repair that is half a mark out is nudged, not thrown away ==")
from grader import RepairItem, apply_repair_corrections as _arc


def _rep5():
    return GradeReport(total_score=30, max_total=76.5, overall_remarks="x",
                       questions=[QuestionMark(qid=f"Q{i}", score=1, max_score=1.5,
                                               page=1, y_fraction=0.5)
                                  for i in range(1, 6)])


_items = [RepairItem(qid=f"Q{i}", max_score=m, score=1)
          for i, m in zip(range(1, 6), [20, 20, 15, 10, 5.5])]
_fixed = _arc(_rep5(), _items, 70.0)
check("the denominator lands on the official total", abs(_fixed.max_total - 70.0) < 0.01,
      _fixed.max_total)
_by = {q.qid: q.max_score for q in _fixed.questions}
check("the difference comes off the largest question", abs(_by["Q1"] - 19.5) < 0.01, _by)
check("every other printed maximum is untouched",
      all(abs(_by[q] - m) < 0.01 for q, m in
          (("Q2", 20.0), ("Q3", 15.0), ("Q4", 10.0), ("Q5", 5.5))), _by)

_far = False
try:
    _arc(_rep5(), [RepairItem(qid="Q1", max_score=40, score=1)], 70.0)
except ValueError:
    _far = True
check("a repair that is genuinely wrong is still refused", _far)

# The other failure this pass has: a reply that stops marking and starts filling in.
print("== a reply that repeats one remark across the paper is caught ==")
from grader import report_is_stub

_stub = GradeReport(
    total_score=9, max_total=70, overall_remarks="Good understanding of mechanisms.",
    questions=[QuestionMark(qid=f"Q{i}", score=0, max_score=2, page=1, y_fraction=0.5,
                            attempted=True, remark="Incorrect answer")
               for i in range(1, 20)])
check("the stub reply is detected", bool(report_is_stub(_stub)), report_is_stub(_stub))

_real = GradeReport(
    total_score=30, max_total=70, overall_remarks="x",
    questions=[QuestionMark(qid=f"Q{i}", score=1, max_score=2, page=1, y_fraction=0.5,
                            attempted=True, remark=f"missed step {i}")
               for i in range(1, 20)])
check("a properly marked paper is not", not report_is_stub(_real))
# A weak copy where the examiner DID write a reason each time must pass, however low.
_weak = GradeReport(
    total_score=2, max_total=70, overall_remarks="x",
    questions=[QuestionMark(qid=f"Q{i}", score=0, max_score=2, page=1, y_fraction=0.5,
                            attempted=True, remark=f"no working shown for part {i}")
               for i in range(1, 20)])
check("nor is a genuinely weak copy with real reasons", not report_is_stub(_weak))
check("a short paper is never judged on this", not report_is_stub(GradeReport(
    total_score=0, max_total=4, overall_remarks="x",
    questions=[QuestionMark(qid=f"Q{i}", score=0, max_score=2, page=1, y_fraction=0.5,
                            attempted=True, remark="Incorrect answer") for i in (1, 2)])))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
