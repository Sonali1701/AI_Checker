# -*- coding: utf-8 -*-
"""Per-question (page-window) grading: the planner and the merge.

The model calls themselves are not exercised here — these are the two pieces that
decide what each call sees and how the answers come back together, and both are
pure functions over OCR and reports.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader
from grader import (Criterion, GradeReport, MarksItem, MarksScheme,
                    QuestionMark, _drop_unowned_blanks, _merge_chunk_reports,
                    _needs_evidence, _question_page_map,
                    _scheme_question_numbers, plan_grading_chunks)
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


def q(qid, score, mx, page_no=1, attempted=True):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=page_no,
                        y_fraction=0.3, remark="", attempted=attempted)


# --------------------------------------------------------------------------
print("\nplanning windows over a 7-page sheet")
pages = [
    page(1, ["Section-A", "1. (3)", "2. (3) RCl > RF", "Q-6:", "3-methyl but-1-ene"]),
    page(2, ["Q-7→ answer", "working"]),
    page(3, ["Section-C", "(a) 2-chlorobutane", "3-chloropentane"]),
    page(4, ["(b) 2-bromo-2-methylbutane", "Q-12. Haloarenes are"]),
    page(5, ["Q-13→ Allyl chloride", "Q-14→ (i) Alkyl halides", "Q-15→", "Q-16→"]),
    page(6, ["Section-E", "Q-17: (i) 1-bromobutane", "Q-18 → (i)"]),
    page(7, ["(iv) ethyl cyanide", "Q-20→ (i)", "Q-21→ (i) A→"]),
]
chunks = plan_grading_chunks(pages, 7, max_pages=3)
covered = [p for c in chunks for p in c["pages"]]
check("every page appears exactly once", covered == list(range(1, 8)), f"{covered}")
check("more than one window", len(chunks) > 1, f"{len(chunks)} window(s)")
check("no window exceeds twice the cap",
      all(len(c["pages"]) <= 6 for c in chunks), f"{[len(c['pages']) for c in chunks]}")
allq = [n for c in chunks for n in c["qnums"]]
check("Q17 is attributed to a window", 17 in allq, f"{allq}")
check("Q21 is attributed to a window", 21 in allq, f"{allq}")
check("no phantom question from '3-chloropentane'",
      allq.count(3) == 0 or 3 in (1, 2), f"{allq}")

# --------------------------------------------------------------------------
print("\nplanner edge cases")
check("no pages -> no windows", plan_grading_chunks(pages, 0) == [])
check("no OCR still covers every page",
      [p for c in plan_grading_chunks(None, 5, 3) for p in c["pages"]]
      == [1, 2, 3, 4, 5])
one = plan_grading_chunks([page(1, ["Q-1→ x"])], 1, 3)
check("a one-page sheet is a single window", len(one) == 1, f"{one}")

# A sheet with no labels anywhere must still be broken up, or the whole point of
# chunking is lost on exactly the copies that need it most.
blank = plan_grading_chunks([page(i, ["working with no label"]) for i in range(1, 9)],
                            8, max_pages=3)
check("an unlabelled sheet is still split", len(blank) > 1, f"{len(blank)} window(s)")
check("unlabelled split still covers every page",
      [p for c in blank for p in c["pages"]] == list(range(1, 9)))

# --------------------------------------------------------------------------
print("\nmerging the windows back together")
part1 = GradeReport(total_score=3, max_total=4, overall_remarks="ignore me",
                    questions=[q("Q1", 1, 1), q("Q2", 2, 3)])
part2 = GradeReport(total_score=5, max_total=8, overall_remarks="ignore me too",
                    questions=[q("Q10", 5, 8, page_no=5)])
merged = _merge_chunk_reports([part1, part2])
check("all questions carried over", [x.qid for x in merged.questions] == ["Q1", "Q2", "Q10"],
      f"{[x.qid for x in merged.questions]}")
check("total is the sum", merged.total_score == 8, f"{merged.total_score}")
check("max is the sum", merged.max_total == 12, f"{merged.max_total}")
check("a window's own remark is discarded", merged.overall_remarks == "",
      f"{merged.overall_remarks!r}")

# Two windows both claiming the same question is the failure that would silently
# overwrite real marks, so the first (the window that owns the page) wins.
dup = _merge_chunk_reports([
    GradeReport(total_score=2, max_total=2, overall_remarks="",
                questions=[q("Q5", 2, 2)]),
    GradeReport(total_score=0, max_total=2, overall_remarks="",
                questions=[q("Q5", 0, 2)]),
])
check("a duplicated qid is kept once", len(dup.questions) == 1, f"{len(dup.questions)}")
check("the first window's mark survives", dup.questions[0].score == 2,
      f"{dup.questions[0].score}")

check("questions come back in paper order",
      [x.qid for x in _merge_chunk_reports([part2, part1]).questions]
      == ["Q1", "Q2", "Q10"])

# --------------------------------------------------------------------------
# The paper's roster, not just the labels OCR happened to read. A question the
# reader missed used to be in no window's brief, and every window was told the
# questions not in its brief belong to another call — so nobody marked it.
print("\nwindows driven by the marks scheme")

def scheme(*pairs):
    return MarksScheme(total=float(sum(m for _, m in pairs)),
                       items=[MarksItem(qid=qid, max=float(m)) for qid, m in pairs])


sparse = [
    page(1, ["Q-1→ answer number one here", "working line"]),
    page(2, ["more working for the same answer"]),
    page(3, ["Q-3→ answer number three here", "working line"]),
    page(4, ["tail of the third answer"]),
]
# The sheet holds Q1, Q2 and Q3; OCR only read the labels for Q1 and Q3.
sch = scheme(("Q1", 2), ("Q2", 2), ("Q3", 5))
planned = plan_grading_chunks(sparse, 4, max_pages=2, marks_scheme=sch)
check("more than one window", len(planned) > 1, f"{len(planned)}")
check("the unlocated question reaches every window",
      all(2 in c["unlocated"] for c in planned),
      f"{[c['unlocated'] for c in planned]}")
check("a located question is not offered as unlocated",
      all(1 not in c["unlocated"] and 3 not in c["unlocated"] for c in planned),
      f"{[c['unlocated'] for c in planned]}")
check("no scheme means nothing is offered",
      all(c["unlocated"] == [] for c in plan_grading_chunks(sparse, 4, 2)),
      f"{[c['unlocated'] for c in plan_grading_chunks(sparse, 4, 2)]}")
check("sub-part qids collapse to their question number",
      _scheme_question_numbers(scheme(("Q16.a", 3), ("Q16.b", 2), ("Q18.iii", 1)))
      == {16, 18})
check("no scheme is an empty roster, not a crash",
      _scheme_question_numbers(None) == set())

# --------------------------------------------------------------------------
# Offering a question to every window means two of them can speak about it. The
# one that could actually see the page has to win.
print("\na marked answer beats a claim of a blank")
blank_first = _merge_chunk_reports([
    GradeReport(total_score=0, max_total=2, overall_remarks="",
                questions=[q("Q7", 0, 2, attempted=False)]),
    GradeReport(total_score=2, max_total=2, overall_remarks="",
                questions=[q("Q7", 2, 2)]),
])
check("the window that saw the answer wins", blank_first.questions[0].score == 2,
      f"{blank_first.questions[0].score}")
check("the duplicate is still collapsed to one", len(blank_first.questions) == 1)
graded_first = _merge_chunk_reports([
    GradeReport(total_score=2, max_total=2, overall_remarks="",
                questions=[q("Q7", 2, 2)]),
    GradeReport(total_score=0, max_total=2, overall_remarks="",
                questions=[q("Q7", 0, 2, attempted=False)]),
])
check("a later blank never overwrites a mark", graded_first.questions[0].score == 2,
      f"{graded_first.questions[0].score}")

# --------------------------------------------------------------------------
print("\nblank claims about pages the window never saw")
rep = GradeReport(total_score=1, max_total=6, overall_remarks="", questions=[
    q("Q4", 1, 2),                       # owned and marked
    q("Q5", 0, 2, attempted=False),      # owned and genuinely blank
    q("Q9", 0, 2, attempted=False),      # NOT owned — the window cannot know
])
n_dropped = _drop_unowned_blanks(rep, [4, 5])
check("an unowned blank claim is discarded", n_dropped == 1, f"{n_dropped}")
check("the owned blank survives", [x.qid for x in rep.questions] == ["Q4", "Q5"],
      f"{[x.qid for x in rep.questions]}")
graded_elsewhere = GradeReport(total_score=2, max_total=2, overall_remarks="",
                               questions=[q("Q9", 2, 2)])
_drop_unowned_blanks(graded_elsewhere, [4, 5])
check("a MARKED answer from an unowned question is kept",
      len(graded_elsewhere.questions) == 1)

# --------------------------------------------------------------------------
print("\nwhich pages carry a question's answer")
pmap = _question_page_map(sparse)
check("a question's region starts at its label", (pmap.get(1) or [0])[0] == 1,
      f"{pmap.get(1)}")
check("an answer running past a page break keeps both pages",
      pmap.get(1) == [1, 2], f"{pmap.get(1)}")
check("the next label opens its own region", pmap.get(3) == [3, 4], f"{pmap.get(3)}")
check("a question with no label is not invented", 2 not in pmap, f"{sorted(pmap)}")

# --------------------------------------------------------------------------
# The re-grade pass. _grade_window is stubbed: this checks WHICH questions go
# back to the model and what happens to what comes home, not the call itself.
print("\nre-grading what no window returned")

CALLS = []


def stub_window(result):
    def fake(prefix, cc, pages, student_pngs, ocr_by_page, brief, **kw):
        CALLS.append({"pages": list(pages), "brief": brief})
        return result
    return fake


def regrade(merged, ocr, sch_, result):
    CALLS.clear()
    real = grader._grade_window
    grader._grade_window = stub_window(result)
    try:
        return grader._regrade_dropped_questions(
            merged, prefix=[], cc=None, ocr_pages=ocr, student_pngs=[b""] * 4,
            ocr_by_page={p.page: p for p in ocr}, marks_scheme=sch_,
            model="m", google_token="t", task_id="j", filename="sheet.pdf",
            student_class=None, subject=None, exam_type=None, usage={})
    finally:
        grader._grade_window = real


# Q3 was answered (the OCR proves it) and came back from no window.
short = GradeReport(total_score=2, max_total=2, overall_remarks="keep me",
                    questions=[q("Q1", 2, 2)])
found = GradeReport(total_score=4, max_total=5, overall_remarks="",
                    questions=[q("Q3", 4, 5, page_no=3)])
out = regrade(short, sparse, sch, found)
check("exactly one re-grade call", len(CALLS) == 1, f"{len(CALLS)}")
check("it is sent the pages the answer is on", CALLS[0]["pages"] == [3, 4],
      f"{CALLS[0]['pages']}")
check("the brief names the dropped question", "Q3" in CALLS[0]["brief"])
check("the brief does not ask for what is already marked",
      "Q1" not in CALLS[0]["brief"], CALLS[0]["brief"])
check("the recovered mark is merged in",
      [x.qid for x in out.questions] == ["Q1", "Q3"],
      f"{[x.qid for x in out.questions]}")
check("the recovered score survives",
      [x.score for x in out.questions if x.qid == "Q3"] == [4.0])
check("the paper's own remark is not clobbered", out.overall_remarks == "keep me",
      f"{out.overall_remarks!r}")

# Q2 is on the scheme but the student wrote nothing under it: there is nothing to
# re-grade, and _reconcile_report books it as a zero. Paying for a call here would
# be paying to be told the page is blank.
complete = GradeReport(total_score=6, max_total=7, overall_remarks="", questions=[
    q("Q1", 2, 2), q("Q3", 4, 5, page_no=3)])
same = regrade(complete, sparse, sch, found)
check("nothing dropped means no call", len(CALLS) == 0, f"{len(CALLS)}")
check("the report comes back untouched", same is complete)

# A re-grade that comes home empty must leave the report exactly as it was, so the
# gate sees the gap rather than a half-merged report.
out2 = regrade(short, sparse, sch, None)
check("an empty re-grade changes nothing", out2 is short)
check("an empty re-grade still cost only one call", len(CALLS) == 1, f"{len(CALLS)}")

# A label the reader found with no words under it is not proof of a blank - it is
# the shape an under-read answer takes, and a zero there is the same catastrophe.
bare = [
    page(1, ["Q-1→ answer number one here", "working line"]),
    page(2, ["Q-2→"]),
    page(3, ["Q-3→ answer number three here"]),
]
bare_short = GradeReport(total_score=2, max_total=2, overall_remarks="",
                         questions=[q("Q1", 2, 2), q("Q3", 5, 5, page_no=3)])
found2 = GradeReport(total_score=1, max_total=2, overall_remarks="",
                     questions=[q("Q2", 1, 2, page_no=2)])
out4 = regrade(bare_short, bare, sch, found2)
check("a bare label is still re-graded", len(CALLS) == 1, f"{len(CALLS)}")
check("it is sent the page the label is on",
      CALLS and CALLS[0]["pages"] == [2], f"{CALLS[0]['pages'] if CALLS else None}")
check("the recovered answer joins the report",
      [x.qid for x in out4.questions] == ["Q1", "Q2", "Q3"],
      f"{[x.qid for x in out4.questions]}")

# A question number the paper does not have is a misread, never a question. The
# real Chemistry copy OCRs "99). Ans polar protic solvents..." as question 99.
phantom = [
    page(1, ["Q-1→ answer number one here", "working line"]),
    page(2, ["99). Ans polar protic solvents solvate both the", "more working"]),
]
regrade(GradeReport(total_score=2, max_total=2, overall_remarks="",
                    questions=[q("Q1", 2, 2)]), phantom, sch, found2)
check("a phantom question number is never chased", len(CALLS) == 0, f"{CALLS}")
check("the planner keeps the phantom out of a window's brief",
      all(99 not in c["qnums"] for c in
          plan_grading_chunks(phantom, 2, 1, sch)),
      f"{[c['qnums'] for c in plan_grading_chunks(phantom, 2, 1, sch)]}")
check("with no scheme there is no range to judge by, so it stays",
      any(99 in c["qnums"] for c in plan_grading_chunks(phantom, 2, 1)))

# A re-grade that answers a DIFFERENT question than the one asked for is discarded:
# that question already has a mark from the window that owns its page.
noise = GradeReport(total_score=1, max_total=2, overall_remarks="",
                    questions=[q("Q1", 1, 2)])
out3 = regrade(short, sparse, sch, noise)
check("an off-target re-grade is discarded", out3 is short,
      f"{[x.qid for x in out3.questions]}")
check("the original mark is untouched", short.questions[0].score == 2)

# --------------------------------------------------------------------------
# The third failure, after coverage and the denominator: a copy that is complete
# and correctly totalled and still unusable, because the marks came back bare.
print("\nwhich marks a student cannot act on")


def crit(desc, awarded, mx, anchor="", evidence=""):
    return Criterion(description=desc, awarded=awarded, max=mx,
                     met=awarded >= mx, evidence=evidence, step_line_id=anchor)


def qc(qid, score, mx, crits=(), page_no=1, attempted=True, objective=False,
       correct_option=""):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=page_no,
                        y_fraction=0.3, remark="", attempted=attempted,
                        criteria=list(crits), objective=objective,
                        correct_option=correct_option)


check("full marks need no justification",
      not _needs_evidence(qc("Q1", 2, 2)))
check("a bare score that lost marks does",
      _needs_evidence(qc("Q2", 1, 5)))
check("criteria with no anchor are not evidence",
      _needs_evidence(qc("Q3", 1, 5, [crit("step", 1, 5)])))
check("an anchored criterion is evidence",
      not _needs_evidence(qc("Q4", 1, 5, [crit("step", 1, 5, "P2L4", "student: 7.6")])))
check("an unattempted question is not asked to explain itself",
      not _needs_evidence(qc("Q5", 0, 5, attempted=False)))
check("an objective question is scored by code, not by criteria",
      not _needs_evidence(qc("Q6", 0, 1, objective=True)))
check("an old report's correct_option still counts as objective",
      not _needs_evidence(qc("Q7", 0, 1, correct_option="B")))

# --------------------------------------------------------------------------
print("\nasking again for the reasoning behind a bare mark")


def repair(merged, ocr, result):
    CALLS.clear()
    real = grader._grade_window
    grader._grade_window = stub_window(result)
    try:
        return grader._repair_unevidenced_questions(
            merged, prefix=[], cc=None, ocr_pages=ocr, student_pngs=[b""] * 4,
            ocr_by_page={p.page: p for p in ocr}, model="m", google_token="t",
            task_id="j", filename="sheet.pdf", student_class=None, subject=None,
            exam_type=None, usage={})
    finally:
        grader._grade_window = real


bare = GradeReport(total_score=3, max_total=7, overall_remarks="", questions=[
    qc("Q1", 2, 2, [crit("right", 2, 2, "P1L1")]),      # already fine
    qc("Q3", 1, 5, page_no=3),                          # bare, lost 4 marks
])
fixed = GradeReport(total_score=2, max_total=5, overall_remarks="", questions=[
    qc("Q3", 2, 5, [crit("named the reagent", 1, 2, "P3L2"),
                    crit("no mechanism shown", 1, 3, "P3L5", "student: no arrows")],
       page_no=3)])
out5 = repair(bare, sparse, fixed)
check("only the bare question is asked about", len(CALLS) == 1, f"{len(CALLS)}")
check("the brief names it", CALLS and "Q3" in CALLS[0]["brief"])
check("the fully-marked question is left alone",
      "Q1" not in (CALLS[0]["brief"] if CALLS else ""))
q3 = [x for x in out5.questions if x.qid == "Q3"][0]
check("the anchored version replaces the bare one", len(q3.criteria) == 2,
      f"{len(q3.criteria)}")
check("every replacement criterion is anchored",
      all(c.step_line_id for c in q3.criteria))
check("the order of the report is unchanged",
      [x.qid for x in out5.questions] == ["Q1", "Q3"])
check("the already-evidenced question is untouched",
      out5.questions[0].criteria[0].step_line_id == "P1L1")

# A re-mark may correct the mark - that is what marking properly means - but it may
# not change what the question is WORTH. The denominator belongs to the paper.
greedy = GradeReport(total_score=9, max_total=10, overall_remarks="", questions=[
    qc("Q3", 9, 10, [crit("step", 9, 10, "P3L2")], page_no=3)])
out6 = repair(GradeReport(total_score=1, max_total=5, overall_remarks="",
                          questions=[qc("Q3", 1, 5, page_no=3)]), sparse, greedy)
check("the max the paper set survives a greedy re-mark",
      out6.questions[0].max_score == 5.0, f"{out6.questions[0].max_score}")
check("the score is clamped to that max", out6.questions[0].score == 5.0,
      f"{out6.questions[0].score}")

# Bare in, bare out: keep the original rather than swap one unusable mark for another.
still_bare = GradeReport(total_score=2, max_total=5, overall_remarks="",
                         questions=[qc("Q3", 2, 5, [crit("step", 2, 5)], page_no=3)])
before = GradeReport(total_score=1, max_total=5, overall_remarks="",
                     questions=[qc("Q3", 1, 5, page_no=3)])
out7 = repair(before, sparse, still_bare)
check("a reply with no anchors is discarded", out7.questions[0].score == 1,
      f"{out7.questions[0].score}")
check("and it is not silently given the new criteria",
      out7.questions[0].criteria == [])

# A copy that was marked properly the first time must not pay for this pass at all.
good = GradeReport(total_score=3, max_total=5, overall_remarks="", questions=[
    qc("Q3", 3, 5, [crit("step", 3, 5, "P3L2", "student: partial")], page_no=3)])
out8 = repair(good, sparse, fixed)
check("a properly marked copy costs nothing", len(CALLS) == 0, f"{len(CALLS)}")
check("and comes back as it went in", out8 is good)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
