# -*- coding: utf-8 -*-
"""Grade the PW Skills feedback samples on a running app and set the AI marks beside the SME's.

    python tools/live_innovation_feedback.py --cases cases.json --token-file token.txt
            [--port 8765] [--out build/innovation_feedback] [--jobs 4] [--only FSD_CSS,DA_DAX]

The token is the app session token of the app on --port (it is tied to that process).
cases.json is a list of papers, each with the copies to grade on it:

    [{"id": "FSD_CSS", "subject": "CSS Basics", "total": 200,
      "qp": "path/to/paper.pdf", "key": "path/to/solutions.pdf" (or null, with
      "generate_key": true for the tool's own key; null alone puts the paper in its place),
      "sheets": [{"id": "AF2D", "path": "path/to/copy.pdf", "sme": "193/200",
                  "note": "what the SME said"},
                 {"id": "AF13", "link": "https://drive.google.com/drive/folders/..."}]}]

A sheet given as a link is uploaded as a .url file, the way a learner's link-only
submission reaches the checker. Every paper runs as one job with category
"innovation", --jobs papers at a time. For each copy the marked PDF and the report go
to --out, and summary.csv holds state, AI marks, SME marks, cost, time and the
question-wise marks and remarks. Costs about Rs 10 to 20 a graded copy. Never print
the token.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PRINT = threading.Lock()


def say(text: str) -> None:
    with _PRINT:
        print(text, flush=True)


def sheet_upload(sheet: dict) -> tuple[str, bytes, str]:
    if sheet.get("link"):
        body = f"[InternetShortcut]\nURL={sheet['link']}\n".encode("utf-8")
        return f"{sheet['id']}.url", body, "application/octet-stream"
    path = sheet["path"]
    name = f"{sheet['id']}_{os.path.basename(path)}"
    mime = "application/pdf" if path.lower().endswith(".pdf") else "application/octet-stream"
    return name, open(path, "rb").read(), mime


def marks_reading(base: str, headers: dict, case: dict, form: dict, qp_bytes: bytes) -> dict | None:
    import requests
    for mode in ("auto", "ai"):
        r = requests.post(base + "/api/detect-marks", headers=headers,
                          files={"question_paper": (os.path.basename(case["qp"]), qp_bytes,
                                                    "application/pdf")},
                          data=dict(form, mode=mode), timeout=900)
        if not r.ok:
            say(f"{case['id']}: detect-marks ({mode}) {r.status_code}: {r.text[:200]}")
            continue
        marks = r.json()
        if marks.get("items") and (marks.get("method") in ("regex", "sections")
                                   or marks.get("marks_attestation")):
            return marks
    return None


def question_lines(report: dict) -> str:
    out = []
    for q in report.get("questions") or []:
        score, top = q.get("score"), q.get("max_score")
        remark = re.sub(r"\s+", " ", str(q.get("remark") or "")).strip()
        out.append(f"{q.get('qid')} {score:g}/{top:g}" + (f": {remark}" if remark else "")
                   if isinstance(score, (int, float)) and isinstance(top, (int, float))
                   else f"{q.get('qid')}: {remark}")
    return " | ".join(out)


def run_case(case: dict, *, base: str, headers: dict, out: str) -> list[dict]:
    import requests
    form = {"student_class": case.get("cohort", case["id"].split("_")[0]),
            "subject": case["subject"], "total_marks": f"{case['total']:g}",
            "category": "innovation", "provider": "claude"}
    qp_bytes = open(case["qp"], "rb").read()
    # No solutions file: the tool writes the key ("generate_key"), or the paper stands in.
    generate = not case.get("key") and bool(case.get("generate_key"))
    key_path = case.get("key") or case["qp"]
    marks = marks_reading(base, headers, case, form, qp_bytes)
    if not marks:
        say(f"{case['id']}: no marks reading; skipped")
        return []
    say(f"{case['id']}: marks via {marks.get('method')}: {len(marks['items'])} questions, "
        f"total {marks.get('total')}")
    uploads = [sheet_upload(s) for s in case["sheets"]]
    files = [("question_paper", (os.path.basename(case["qp"]), qp_bytes, "application/pdf"))]
    if not generate:
        files.append(("answer_key", (os.path.basename(key_path), open(key_path, "rb").read(),
                                     "application/pdf")))
    files += [("answer_sheets", u) for u in uploads]
    data = dict(form, key_source="generate" if generate else "upload",
                marks_items=json.dumps(marks["items"]),
                marks_source=str(marks.get("method") or ""),
                marks_attestation=str(marks.get("marks_attestation") or ""))
    started = time.time()
    r = requests.post(base + "/api/evaluate", headers=headers, files=files, data=data,
                      timeout=900)
    if not r.ok:
        say(f"{case['id']}: evaluate {r.status_code}: {r.text[:300]}")
        return []
    job, k = r.json()["job_id"], r.json().get("download_key", "")
    say(f"{case['id']}: job {job} with {len(uploads)} copies")
    seen = 0
    while True:
        time.sleep(15)
        try:
            got = requests.get(f"{base}/api/jobs/{job}", headers=headers,
                               params={"k": k}, timeout=60)
        except requests.RequestException:
            continue
        if got.status_code in (401, 403, 404):
            # The app restarted (its sign-in and its jobs are per process): say so.
            say(f"{case['id']}: job {job} lost ({got.status_code}: {got.text[:160]})")
            return []
        if not got.ok:
            continue
        state = got.json()
        if (state.get("done") or 0) != seen:
            seen = state.get("done") or 0
            say(f"  {case['id']}: {seen}/{state.get('total')} after "
                f"{(time.time() - started) / 60:.1f} min")
        if state.get("status") in ("done", "error", "cancelled"):
            break
    minutes = (time.time() - started) / 60
    by_name = {u[0]: s for u, s in zip(uploads, case["sheets"])}
    rows = []
    for result in state.get("results") or []:
        index, student = result.get("index"), result.get("student") or ""
        sheet = by_name.get(student, {})
        stem = os.path.join(out, f"{case['id']}_{sheet.get('id', index)}")
        report = {}
        if result.get("score") is not None:
            for kind in ("pdf", "report"):
                got = requests.get(f"{base}/api/jobs/{job}/{kind}/{index}", headers=headers,
                                   params={"k": k}, timeout=120)
                if got.ok:
                    with open(f"{stem}.{'pdf' if kind == 'pdf' else 'json'}", "wb") as fh:
                        fh.write(got.content)
                    if kind == "report":
                        try:
                            saved = got.json()
                        except ValueError:
                            saved = {}
                        report = saved.get("report") or saved
        row = {"case": case["id"], "copy": sheet.get("id", student), "state": result.get("state"),
               "ai": (f"{result.get('score'):g}/{result.get('max'):g}"
                      if result.get("score") is not None else "NA"),
               "sme": sheet.get("sme", ""), "cost_inr": (result.get("cost") or {}).get("total_inr"),
               "job_minutes": round(minutes, 1),
               "reason": " ; ".join(result.get("review_reasons") or []) or result.get("error") or "",
               "cover_remark": re.sub(r"\s+", " ", str(report.get("overall_remarks") or "")),
               "questions": question_lines(report), "sme_note": sheet.get("note", "")}
        rows.append(row)
        say(f"  {case['id']} {row['copy']}: {row['state']} AI {row['ai']} SME {row['sme']} "
            f"Rs {row['cost_inr']}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default=os.path.join("build", "innovation_feedback"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Empty token file.")
    cases = json.load(open(args.cases, encoding="utf-8"))
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    cases = [c for c in cases if not only or c["id"] in only]
    os.makedirs(args.out, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    headers = {"Authorization": f"Bearer {token}"}
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for found in pool.map(lambda c: run_case(c, base=base, headers=headers, out=args.out),
                              cases):
            rows += found
    if rows:
        path = os.path.join(args.out, "summary.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        say(f"\n{len(rows)} copies; summary in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
