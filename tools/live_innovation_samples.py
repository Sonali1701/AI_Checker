# -*- coding: utf-8 -*-
"""Grade the School of Innovation sample assignments on a local server, as the app does.

    python tools/live_innovation_samples.py --token-file token.txt [--port 8797]
            [--cohort FSD,DS] [--only FSD_s1,FSD_s3] [--samples build/innovation_samples]
            [--out build/innovation_run] [--no-server] [--da-total 110]

The samples are the "Assignment Sample" sheet of 13 Oct 2026: for each cohort a
question paper, a solutions PDF (the answer key) and four learner submissions on the
PW Skills CDN. They are downloaded once into --samples. The DA question paper lives
on Google Drive, so save it there by hand as DA_qp.pdf (it is also the key) and pass
--da-total, since the paper prints no total.

Each cohort goes through the product's own routes with category "innovation":
/api/detect-marks (the free "each question carries N marks" reading, or the AI
reading with its attestation), then /api/evaluate with every submission, polled to
the end. Every result is written to --out: the marked PDF and report of each copy
that was graded, and summary.csv with the state, marks, cost and reason of each
(held unread for a link-only PDF or a spreadsheet export, faculty review for a
marked copy). Costs about Rs 10 to 20 a graded copy. Never print the token.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CDN = "https://cdn.pwskills.com/"
COHORTS = {
    "FSD": {
        "subject": "HTML & CSS", "total": 160.0,
        "qp": CDN + "learn/assignment/6836b6b8a27e6845070fd124.pdf",
        "key": CDN + "learn/assignment/answerSheet/6836b6b8a27e6845070fd124.pdf",
        "sheets": [CDN + "assignment/6a548bae0e2382b0cabde903.pdf",
                   CDN + "assignment/68fcd4979706504330f287a6.pdf",
                   CDN + "assignment/695a57eb27e6a14a17321b52.pdf",
                   CDN + "assignment/69083266eaa85dfe7138ba4c.pdf"],
    },
    "DS": {
        "subject": "Python Fundamentals and Data Structures", "total": 200.0,
        "qp": CDN + "learn/assignment/699c103b5bd8a23d240aee30.pdf",
        "key": CDN + "learn/assignment/answerSheet/699c103b5bd8a23d240aee30.pdf",
        "sheets": [CDN + "assignment/699ec16a0f60bc53486253dd.pdf",
                   CDN + "assignment/6aca939be14736b8d8e4dd36.pdf",
                   CDN + "assignment/6a4bc9f36636032a8e67eeec.pdf",
                   CDN + "assignment/6a184305a576a68f99cd62c0.pdf"],
    },
    "DA": {
        "subject": "Excel Basics and Functions", "total": None,
        # https://drive.google.com/file/d/1vDoR4yQhLuGUIoRz3l9NVmYNOtE2s9rR/view
        "qp": None, "key": None,
        "sheets": [CDN + "assignment/6a72aaf92d07c03d87e07889.pdf",
                   CDN + "assignment/6a7449bba3e966634b260670.pdf",
                   CDN + "assignment/6a75a0b14f1625178b76fda6.pdf",
                   CDN + "assignment/6a76d1d1c4d3bb821f98c9bb.pdf"],
    },
}


def fetch(url: str, path: str) -> bool:
    import requests
    if os.path.exists(path):
        return True
    if not url:
        return False
    data = requests.get(url, timeout=180).content
    if not data.startswith(b"%PDF"):
        print(f"  not a PDF: {url}", flush=True)
        return False
    with open(path, "wb") as fh:
        fh.write(data)
    return True


def start_server(port: int, data_dir: str) -> subprocess.Popen:
    import requests
    env = dict(os.environ, AICHECKER_DATA_DIR=data_dir)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "client_app:app", "--host",
                             "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env)
    for _ in range(60):
        time.sleep(1)
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=5).ok:
                return proc
        except requests.RequestException:
            if proc.poll() is not None:
                break
    proc.terminate()
    sys.exit("The local server did not come up.")


def run_cohort(name: str, spec: dict, *, base: str, headers: dict, samples: str, out: str,
               only: set[str], da_total: float | None) -> list[dict]:
    import requests
    qp = os.path.join(samples, f"{name}_qp.pdf")
    key = os.path.join(samples, f"{name}_key.pdf")
    if not fetch(spec["qp"], qp):
        print(f"{name}: no question paper at {qp}; skipped", flush=True)
        return []
    if not fetch(spec["key"], key):
        key = qp                                   # the DA paper doubles as its key
    total = spec["total"] if spec["total"] is not None else da_total
    if total is None:
        print(f"{name}: pass --da-total for a paper that prints no total; skipped", flush=True)
        return []
    sheets = []
    for index, url in enumerate(spec["sheets"], start=1):
        local = os.path.join(samples, f"{name}_s{index}.pdf")
        if fetch(url, local) and (not only or f"{name}_s{index}" in only):
            sheets.append(local)
    if not sheets:
        return []
    form = {"student_class": name, "subject": spec["subject"], "total_marks": f"{total:g}",
            "category": "innovation", "provider": "claude"}
    qp_bytes = open(qp, "rb").read()
    # The free reading first (no model); the AI reading, with its attestation, only
    # when the paper prints nothing a free reader can price (the DA paper).
    marks = None
    for mode in ("auto", "ai"):
        r = requests.post(base + "/api/detect-marks", headers=headers,
                          files={"question_paper": (os.path.basename(qp), qp_bytes, "application/pdf")},
                          data=dict(form, mode=mode), timeout=600)
        if not r.ok:
            print(f"{name}: detect-marks ({mode}) {r.status_code}: {r.text[:200]}", flush=True)
            continue
        marks = r.json()
        if marks.get("items") and (marks.get("method") in ("regex", "sections")
                                   or marks.get("marks_attestation")):
            break
    if not marks or not marks.get("items"):
        print(f"{name}: no marks reading; skipped", flush=True)
        return []
    print(f"{name}: marks via {marks.get('method')}: {len(marks['items'])} questions, "
          f"total {marks.get('total')}", flush=True)
    files = [("question_paper", (os.path.basename(qp), qp_bytes, "application/pdf")),
             ("answer_key", (os.path.basename(key), open(key, "rb").read(), "application/pdf"))]
    files += [("answer_sheets", (os.path.basename(s), open(s, "rb").read(), "application/pdf"))
              for s in sheets]
    data = dict(form, key_source="upload", marks_items=json.dumps(marks["items"]),
                marks_source=str(marks.get("method") or ""),
                marks_attestation=str(marks.get("marks_attestation") or ""))
    r = requests.post(base + "/api/evaluate", headers=headers, files=files, data=data, timeout=600)
    if not r.ok:
        print(f"{name}: evaluate {r.status_code}: {r.text[:300]}", flush=True)
        return []
    job, k = r.json()["job_id"], r.json().get("download_key", "")
    print(f"{name}: job {job} with {len(sheets)} copies", flush=True)
    while True:
        time.sleep(10)
        state = requests.get(f"{base}/api/jobs/{job}", headers=headers,
                             params={"k": k}, timeout=60).json()
        print(f"  {state.get('status')} {state.get('done')}/{state.get('total')} "
              f"{state.get('current', '')[:60]}", flush=True)
        if state.get("status") in ("done", "error", "cancelled"):
            break
    rows = []
    for result in state.get("results") or []:
        index = result.get("index")
        student = result.get("student") or ""
        row = {"cohort": name, "file": student, "state": result.get("state"),
               "score": result.get("score"), "max": result.get("max"),
               "cost_inr": (result.get("cost") or {}).get("total_inr"),
               "failure_code": result.get("failure_code") or "",
               "reason": "; ".join(result.get("review_reasons") or [])
                         or result.get("error") or ""}
        rows.append(row)
        stem = os.path.join(out, f"{name}_{os.path.splitext(student)[0]}")
        if result.get("state") in ("released", "faculty_review") and result.get("score") is not None:
            for kind in ("pdf", "report"):
                got = requests.get(f"{base}/api/jobs/{job}/{kind}/{index}", headers=headers,
                                   params={"k": k}, timeout=120)
                if got.ok:
                    with open(f"{stem}.{'pdf' if kind == 'pdf' else 'json'}", "wb") as fh:
                        fh.write(got.content)
        print(f"  {student}: {row['state']} {row['score']}/{row['max']} Rs {row['cost_inr']} "
              f"{row['failure_code']} {row['reason'][:120]}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--cohort", default="FSD,DS,DA")
    ap.add_argument("--only", default="", help="comma separated: FSD_s1,DS_s2 ...")
    ap.add_argument("--samples", default=os.path.join(ROOT, "build", "innovation_samples"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "innovation_run"))
    ap.add_argument("--no-server", action="store_true", help="use the server already on --port")
    ap.add_argument("--da-total", type=float, default=None)
    args = ap.parse_args()
    token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Empty token file.")
    os.makedirs(args.samples, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    headers = {"Authorization": f"Bearer {token}"}
    only = {n.strip() for n in args.only.split(",") if n.strip()}
    proc = None if args.no_server else start_server(args.port, os.path.join(args.out, "data"))
    rows: list[dict] = []
    try:
        for name in [c.strip().upper() for c in args.cohort.split(",") if c.strip()]:
            if name not in COHORTS:
                print(f"unknown cohort {name}", flush=True)
                continue
            rows += run_cohort(name, COHORTS[name], base=base, headers=headers,
                               samples=args.samples, out=args.out, only=only,
                               da_total=args.da_total)
    finally:
        if proc is not None:
            proc.terminate()
    if rows:
        with open(os.path.join(args.out, "summary.csv"), "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n{len(rows)} copies; summary in {os.path.join(args.out, 'summary.csv')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
