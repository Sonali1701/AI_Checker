# -*- coding: utf-8 -*-
"""Design marking against faculty marks.

    python tools/design_calibrate.py --before
        the AI marks of builds 61 to 64 (stored in the calibration file) vs faculty
    python tools/design_calibrate.py --jobstore
        the latest design jobs in the local job store vs faculty
    python tools/design_calibrate.py --run --paper "NID MOCK-1_1750496699699.pdf" \\
            --token-file token.txt [--port 8799] [--only Arijeet,Vihar]
        grade every calibration sheet on a running local server (uvicorn client_app:app)
        and compare. Costs about Rs 20 to 30 a sheet. Never print the token.

The calibration set is tools/design_calibration/nid_mock1_faculty.json: 11 NID Mock 1
sheets with the faculty's own marks and comments (one more, incomplete, is kept but not
scored). A good build has a small mean error, little bias, and a spread close to the
faculty's: the faculty use the whole range, so an AI that keeps every sheet near the
middle can have a modest error and still rank students wrongly.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIBRATION = os.path.join(ROOT, "tools", "design_calibration", "nid_mock1_faculty.json")
JOBSTORE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AI Copy Checker", "jobs.sqlite3")


def load(path: str = CALIBRATION) -> dict:
    return json.load(open(path, encoding="utf-8"))


def scored_sheets(data: dict) -> list[dict]:
    return [s for s in data["sheets"] if s.get("faculty") and not s.get("incomplete")]


def _spearman(a: list[float], b: list[float]) -> float | None:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return round(cov / (va * vb), 2) if va and vb else None


def _sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def compare(ai: dict[str, dict[str, float]], data: dict | None = None) -> dict:
    """ai: {student: {qid: marks}}. Returns per question and per sheet errors.

    Per question percentages make the 15 and 20 mark questions comparable."""
    data = data or load()
    maxima = {q: float(v["max"]) for q, v in data["questions"].items()}
    rows, totals_ai, totals_fac = [], [], []
    per_q: dict[str, list[tuple[float, float]]] = {q: [] for q in maxima}
    for sheet in scored_sheets(data):
        marks = ai.get(sheet["student"])
        if not marks:
            continue
        fac = sheet["faculty"]
        t_ai = sum(float(marks.get(q, 0) or 0) for q in maxima)
        t_fac = sum(float(fac.get(q, 0) or 0) for q in maxima)
        totals_ai.append(t_ai)
        totals_fac.append(t_fac)
        rows.append({"student": sheet["student"], "ai": round(t_ai, 2), "faculty": round(t_fac, 2),
                     "delta": round(t_ai - t_fac, 2)})
        for q in maxima:
            if q in fac:
                per_q[q].append((100 * float(marks.get(q, 0) or 0) / maxima[q],
                                 100 * float(fac[q]) / maxima[q]))
    questions = {}
    for q, pairs in per_q.items():
        if not pairs:
            continue
        questions[q] = {
            "n": len(pairs),
            "mae_pct": round(sum(abs(a - f) for a, f in pairs) / len(pairs), 1),
            "bias_pct": round(sum(a - f for a, f in pairs) / len(pairs), 1),
            "ai_sd_pct": round(_sd([a for a, _ in pairs]), 1),
            "faculty_sd_pct": round(_sd([f for _, f in pairs]), 1),
        }
    n = len(rows)
    return {
        "sheets": rows,
        "n": n,
        "total_mae": round(sum(abs(r["delta"]) for r in rows) / n, 2) if n else None,
        "total_bias": round(sum(r["delta"] for r in rows) / n, 2) if n else None,
        "total_ai_sd": round(_sd(totals_ai), 1),
        "total_faculty_sd": round(_sd(totals_fac), 1),
        "rank_correlation": _spearman(totals_ai, totals_fac),
        "questions": questions,
    }


def report(result: dict) -> str:
    lines = [f"{'student':10s} {'AI':>6s} {'faculty':>8s} {'delta':>6s}"]
    for r in sorted(result["sheets"], key=lambda r: r["faculty"]):
        lines.append(f"{r['student']:10s} {r['ai']:6g} {r['faculty']:8g} {r['delta']:+6g}")
    lines.append(f"sheets {result['n']}: mean error {result['total_mae']} marks of 70, bias "
                 f"{result['total_bias']:+}, spread AI {result['total_ai_sd']} vs faculty "
                 f"{result['total_faculty_sd']}, rank correlation {result['rank_correlation']}")
    for q, s in result["questions"].items():
        lines.append(f"  {q}: mean error {s['mae_pct']} percent, bias {s['bias_pct']:+} percent, "
                     f"spread AI {s['ai_sd_pct']} vs faculty {s['faculty_sd_pct']} (n {s['n']})")
    return "\n".join(lines)


def ai_before(data: dict) -> dict[str, dict[str, float]]:
    return {s["student"]: s["ai_before"]["marks"] for s in data["sheets"] if s.get("ai_before")}


def ai_from_jobstore(data: dict, path: str = JOBSTORE, job_ids: set[str] | None = None) -> dict:
    """The latest design job for each calibration sheet in the local job store."""
    by_file = {s["file"]: s["student"] for s in data["sheets"]}
    found: dict[str, tuple[float, dict]] = {}
    db = sqlite3.connect(path)
    for job_id, snap in db.execute("select job_id, snapshot_json from jobs"):
        if job_ids is not None and job_id not in job_ids:
            continue
        job = json.loads(snap)
        if job.get("mode") != "design":
            continue
        reports = job.get("reports") or {}
        items = reports.items() if isinstance(reports, dict) else enumerate(reports)
        names = {str(r.get("index")): r.get("student") for r in job.get("results") or []}
        for key, rep in items:
            name = names.get(str(key)) or (job.get("results") or [{}])[0].get("student")
            student = by_file.get(os.path.basename(str(name or "")))
            report_ = rep.get("report", rep) if isinstance(rep, dict) else None
            if not student or not isinstance(report_, dict):
                continue
            marks = {str(a.get("qid")): float(a.get("score", a.get("awarded", 0)) or 0)
                     for a in report_.get("answers") or []}
            created = float(job.get("created") or 0)
            if student not in found or created > found[student][0]:
                found[student] = (created, marks)
    return {s: m for s, (_, m) in found.items()}


def run(args, data: dict) -> set[str]:
    import requests
    cache = os.path.join(ROOT, "build", "design_calibration")
    os.makedirs(cache, exist_ok=True)
    token = open(args.token_file, encoding="utf-8").read().strip()
    headers = {"Authorization": f"Bearer {token}"}
    base = f"http://127.0.0.1:{args.port}"
    only = {n.strip() for n in (args.only or "").split(",") if n.strip()}
    paper = open(args.paper, "rb").read()
    marks = ", ".join(f"{v['max']:g}" for v in data["questions"].values())
    jobs = set()
    for sheet in scored_sheets(data):
        if only and sheet["student"] not in only:
            continue
        local = os.path.join(cache, sheet["file"])
        if not os.path.exists(local):
            open(local, "wb").write(requests.get(sheet["student_url"], timeout=120).content)
        files = [("answer_sheets", (sheet["file"], open(local, "rb").read(), "application/pdf")),
                 ("question_paper", (os.path.basename(args.paper), paper, "application/pdf"))]
        r = requests.post(base + "/api/evaluate-design", headers=headers, files=files,
                          data={"question_marks": marks}, timeout=600)
        r.raise_for_status()
        job = r.json()["job_id"]
        jobs.add(job)
        print("started", sheet["student"], job, flush=True)
        while True:
            time.sleep(10)
            state = requests.get(f"{base}/api/jobs/{job}", headers=headers, timeout=60).json()
            if state.get("status") in ("done", "error", "cancelled"):
                print("  ", state.get("status"), [(x.get("score"), x.get("max"))
                                                   for x in state.get("results") or []], flush=True)
                break
    return jobs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", action="store_true")
    ap.add_argument("--jobstore", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--paper", default="")
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--only", default="")
    ap.add_argument("--db", default=JOBSTORE)
    args = ap.parse_args()
    data = load()
    if args.run:
        jobs = run(args, data)
        print(report(compare(ai_from_jobstore(data, args.db, jobs), data)))
    elif args.jobstore:
        print(report(compare(ai_from_jobstore(data, args.db), data)))
    else:
        print(report(compare(ai_before(data), data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
