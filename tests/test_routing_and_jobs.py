# -*- coding: utf-8 -*-
"""Model routing, tolerant JSON parsing, and job-result access control.

The routing checks exist because the frontend once hard-coded
model="gemini-2.5-flash", which silently defeated the professional/commerce
upgrade to gemini-2.5-pro for every sheet graded through the app.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json

import server
from grader import parse_audit, parse_grade_report, parse_marks_scheme, parse_repair
from pipeline import cfg_from_request

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def cfg(model="", subject="", student_class="", exam_type=""):
    return cfg_from_request("gemini", model, subject, google_token="t", task_id="x",
                            student_class=student_class, exam_type=exam_type)


print("== the frontend must not pin a model (that killed pro routing) ==")
app_js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "static", "app.js"), encoding="utf-8").read()
m = re.search(r"const\s+PROVIDER\s*=\s*\"(\w+)\"\s*,\s*MODEL\s*=\s*\"([^\"]*)\"", app_js)
check("app.js declares PROVIDER/MODEL", m is not None)
if m:
    check("app.js sends an EMPTY model so the server routes", m.group(2) == "",
          f"app.js pins model={m.group(2)!r}")

print("== server-side routing ==")
# Reasoning-heavy papers grade on the BUDGET model; the pro model is spent on the
# moderation pass instead (see the cost policy in pipeline.py). gemini-2.5-pro cannot
# meet the per-copy budget for bulk grading: this proxy strips thinkingConfig and pro
# spends ~9k uncapped thinking tokens per 2 pages, billed as output.
from pipeline import AUDIT_MODEL, _BUDGET_GRADING_MODEL, audit_model_for
check("CA Foundation -> budget grader", cfg(student_class="CA Foundation",
      subject="Accounting").model == _BUDGET_GRADING_MODEL)
check("explicit exam_type=professional -> budget grader",
      cfg(exam_type="professional").model == _BUDGET_GRADING_MODEL)
check("school Accountancy -> budget grader", cfg(student_class="Class 12",
      subject="Accountancy").model == _BUDGET_GRADING_MODEL)
check("school Physics -> budget grader", cfg(student_class="Class 11",
      subject="Physics").model == _BUDGET_GRADING_MODEL)
# The audit/repair second-opinion model stays on the SAME PROVIDER that graded (no
# cross-provider round trip) but must be a genuinely stronger model than the grader —
# see pipeline.audit_model_for's docstring for the measurement that ruled out
# self-audit (a cheap model re-checking its own awards made an over-award worse).
from pipeline import _CLAUDE_SECOND_OPINION_MODEL
professional = cfg(exam_type="professional")
check("claude-graded sheets are audited by claude's STRONGER model, not themselves",
      audit_model_for(professional) == _CLAUDE_SECOND_OPINION_MODEL
      if professional.provider == "claude" else
      audit_model_for(professional) == AUDIT_MODEL)
check("the claude auditor is never the same as the grader",
      audit_model_for(professional) != professional.model
      if professional.provider == "claude" else True)
check("gemini-graded sheets are audited by the pro model",
      audit_model_for(cfg(model="gemini-2.5-flash", subject="English")) == AUDIT_MODEL)
# EVERY paper gets the measured grader now, not just "reasoning" subjects: the old
# split quietly sent Business Studies (a commerce subject) to a different model than
# the Accountancy paper beside it.
for _subject in ("English", "Business Studies", "Hindi", "Social Science", "Physics", ""):
    check(f"{_subject or '(blank subject)'} -> budget grader",
          cfg(student_class="Class 11", subject=_subject).model == _BUDGET_GRADING_MODEL,
          cfg(student_class="Class 11", subject=_subject).model)
check("claude budget model flips the provider too",
      cfg(subject="Accountancy").provider == ("claude" if _BUDGET_GRADING_MODEL.startswith("claude")
                                              else "gemini"))
check("an explicit claude request with no model gets the budget grader, not opus",
      cfg_from_request("claude", "", "Accounting", google_token="t",
                       task_id="x").model == _BUDGET_GRADING_MODEL)
check("explicit model still wins", cfg(model="gemini-2.5-flash-lite",
                                       student_class="CA Inter").model == "gemini-2.5-flash-lite")
check("professional detected from class text", cfg(student_class="CA Inter Group 1",
                                                   subject="Advanced Accounting").exam_type
      == "professional")

print("== tolerant JSON parsing (same rules for every model reply) ==")
Q = '{"qid":"Q1","score":1,"max_score":2,"page":1,"y_fraction":0.5}'
prose = 'Sure! Here is the result:\n{"%s}\nLet me know if you need changes.'
check("grade report: fenced", parse_grade_report(
    '```json\n{"total_score":1,"max_total":2,"overall_remarks":"x","questions":['
    + Q + ']}\n```').max_total == 2)
check("grade report: prose-wrapped", parse_grade_report(
    prose % ('total_score":1,"max_total":2,"overall_remarks":"x","questions":[' + Q + ']')).max_total == 2)
check("marks scheme: prose-wrapped", parse_marks_scheme(
    prose % 'total":10,"items":[{"qid":"Q1","max":10}]').items[0].max == 10)
check("repair: prose-wrapped", parse_repair(
    prose % 'questions":[{"qid":"Q1","max_score":5,"score":4}]')[0].max_score == 5)
check("audit: prose-wrapped", parse_audit(
    prose % 'items":[{"row":0,"correct":2,"reason":"r"}]')[0].correct == 2)

print("== structured prose fields are flattened, never rejected ==")
# Live gemini-2.5-pro returned criterion `evidence` as an OBJECT, which failed
# pydantic validation and killed the ENTIRE sheet. Prose fields must be coerced.
drifted = parse_grade_report(
    '{"total_score":1,"max_total":2,"overall_remarks":["good","but"],'
    '"questions":[{"qid":"Q1","score":1,"max_score":2,"page":1,"y_fraction":0.5,'
    '"remark":{"why":"wrong total"},"criteria":[{"description":"Notes","awarded":1,'
    '"max":2,"met":false,"evidence":{"student_value":"35,85,800","expected":"25,85,800"}}]}]}')
crit = drifted.questions[0].criteria[0]
check("dict evidence flattened", "35,85,800" in crit.evidence and "25,85,800" in crit.evidence,
      repr(crit.evidence))
check("dict remark flattened", "wrong total" in drifted.questions[0].remark)
check("list overall_remarks flattened", drifted.overall_remarks == "good; but",
      repr(drifted.overall_remarks))
check("plain string evidence untouched", parse_grade_report(
    '{"total_score":0,"max_total":1,"overall_remarks":"x","questions":[{"qid":"Q1","score":0,'
    '"max_score":1,"page":1,"y_fraction":0.5,"criteria":[{"description":"d","awarded":0,'
    '"max":1,"met":false,"evidence":"plain text"}]}]}').questions[0].criteria[0].evidence
      == "plain text")

print("== truncated / renamed model replies are salvaged, not lost ==")
# Both shapes seen live during the CA/Commerce sweep on the budget grader.
from grader import parse_audit
trunc = ('[{"row":0,"correct":2,"reason":"ok"},{"row":1,"correct":1,"reason":"ok"},'
         '{"row":2,"correct":0,"reason":"unterm')
check("truncated audit array keeps complete rows",
      [(i.row, i.correct) for i in parse_audit(trunc)] == [(0, 2.0), (1, 1.0)])
check("truncated audit object keeps complete rows",
      [i.row for i in parse_audit('{"items":[{"row":0,"correct":2},{"row":1,"correct":3},{"row":2,"c')]
      == [0, 1])
trunc_report = ('{"total_score":5,"max_total":10,"overall_remarks":"x","questions":['
                '{"qid":"Q1","score":5,"max_score":10,"page":1,"y_fraction":0.5},{"qid":"Q2","sco')
check("truncated grade report keeps complete questions",
      [q.qid for q in parse_grade_report(trunc_report).questions] == ["Q1"])
# A live flash reply carried no `questions` key at all. That must FAIL loudly:
# an empty report would ship as a silent 0/0 (nothing for the denominator guards
# to rescale), which is far worse than a recorded failure.
try:
    parse_grade_report('{"student_name":"X","total_score":18,"max_total":25,'
                       '"overall_remarks":"ok","section_totals":[]}')
    check("empty report raises", False, "no error raised")
except ValueError:
    check("empty report raises", True)
check("questions under a renamed key are found",
      [q.qid for q in parse_grade_report(
          '{"total_score":3,"max_total":4,"overall_remarks":"x","question_marks":'
          '[{"qid":"Q1","score":3,"max_score":4,"page":1,"y_fraction":0.5}]}').questions] == ["Q1"])

print("== job results are not readable without the job's key ==")
# The endpoint functions are called directly: this environment's httpx/starlette
# combination cannot construct a TestClient, and the access rule lives in the
# handlers, not in the HTTP layer.
from fastapi import HTTPException

server.JOBS.clear()
server.JOBS["job1"] = {
    "status": "done", "total": 1, "done": 1, "current": "", "error": None,
    "results": [{"index": 0, "student": "a.pdf", "ok": True, "score": 1, "max": 2,
                 "percent": 50, "remarks": "", "questions": [], "cost": {}, "model": "m",
                 "warning": "", "release": True}],
    "pdfs": {0: b"%PDF-1.4 fake"}, "key": "secretkey", "created": server.time.time(),
}


def status_of(call) -> int:
    """HTTP status a handler produces: the raised code, or 200 when it returns."""
    try:
        call()
        return 200
    except HTTPException as exc:
        return exc.status_code


check("status without key -> 403", status_of(lambda: server.job_status("job1")) == 403)
check("pdf without key -> 403", status_of(lambda: server.job_pdf("job1", 0)) == 403)
check("zip without key -> 403", status_of(lambda: server.job_zip("job1")) == 403)
check("wrong key -> 403", status_of(lambda: server.job_status("job1", "nope")) == 403)
check("empty key rejected", status_of(lambda: server.job_status("job1", "")) == 403)
check("status with key -> 200", status_of(lambda: server.job_status("job1", "secretkey")) == 200)
pdf = server.job_pdf("job1", 0, "secretkey")
check("pdf with key returns the pdf", pdf.body.startswith(b"%PDF"))
check("zip with key -> 200", status_of(lambda: server.job_zip("job1", "secretkey")) == 200)
server.JOBS["job1"]["results"][0]["release"] = False
server.JOBS["job1"]["results"][0]["warning"] = "Exact automatic blocker"
held_status = json.loads(server.job_status("job1", "secretkey").body)
held_public = held_status["results"][0]
check("held status redacts the provisional score",
      held_public.get("held") is True
      and not any(field in held_public for field in
                  ("score", "max", "percent", "remarks", "questions")),
      held_public)
check("held status keeps the actionable blocker while redacting the mark",
      held_public.get("error") == "Exact automatic blocker", held_public)
check("held pdf is denied even with the key",
      status_of(lambda: server.job_pdf("job1", 0, "secretkey")) == 409)
check("zip with no released copies is denied",
      status_of(lambda: server.job_zip("job1", "secretkey")) == 409)
server.JOBS["job1"]["results"][0]["release"] = True
check("unknown job -> 404", status_of(lambda: server.job_status("nope", "x")) == 404)
check("missing sheet index -> 404", status_of(lambda: server.job_pdf("job1", 9, "secretkey")) == 404)

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "app.js"), encoding="utf-8") as handle:
    browser_js = handle.read()
check("single-sheet held result displays the server's exact reason",
      "Reason: ${reason}" in browser_js and "const reason = r.error" in browser_js)

print("== finished jobs are evicted (memory) ==")
server.JOBS.clear()
old = server.time.time() - server._JOB_TTL_SECONDS - 10
server.JOBS["stale"] = {"status": "done", "created": old, "pdfs": {}, "results": []}
server.JOBS["fresh"] = {"status": "done", "created": server.time.time(), "pdfs": {}, "results": []}
server.JOBS["running"] = {"status": "running", "created": old, "pdfs": {}, "results": []}
server._evict_jobs()
check("stale finished job dropped", "stale" not in server.JOBS)
check("fresh job kept", "fresh" in server.JOBS)
check("running job kept even when old", "running" in server.JOBS)

server.JOBS.clear()
for i in range(server._MAX_JOBS + 5):
    server.JOBS[f"j{i}"] = {"status": "done", "created": server.time.time() + i,
                            "pdfs": {}, "results": []}
server._evict_jobs()
check(f"registry capped at {server._MAX_JOBS}", len(server.JOBS) <= server._MAX_JOBS,
      f"got {len(server.JOBS)}")
server.JOBS.clear()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
