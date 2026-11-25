# -*- coding: utf-8 -*-
"""Grade the teaching category sample copies on a local server, as the app does.

    python tools/live_teaching_samples.py --token-file token.txt [--port 8796]
            [--only KVS_PRT,EMRS_TGT_MATHS,PGT_PHYSICS] [--samples build/teaching_samples]
            [--out build/teaching_run] [--no-server]

The samples are the "AI Copy cheking feedback" sheet (1-w3FULV_VA9sA6ZnrV2Q6W_090nVXjkZ0nm1TmysXko),
15 Oct 2026. Two papers carry the SMEs' own marks (KVS PRT Mock 01: 28.5 of 40,
EMRS TGT Maths Mock 01: 36 of 60) and measure whether the depth rules of 15 Oct
(categories/teaching.py rules 7 to 12) bring the tool down to them. Their papers and
copies are on Drive behind a sign-in, so save them in --samples by hand under the
names in PAPERS (KVS_PRT_qp.pdf, KVS_PRT_s1.pdf, ...). The other thirteen exams have
a public paper and a copy on static.pw.live and are downloaded once.

Each paper goes through the product's own routes with category "teaching" and no
answer key, so the job writes its model answer key from the paper:
/api/detect-marks (the free reading first, the AI reading only when that fails),
then /api/evaluate, polled to the end. Every result is written to --out: the marked
PDF, the report and the job zip (it carries AI_MODEL_ANSWER_KEY.pdf), and
summary.csv with the state, marks, the evaluators' marks where known, cost and
reason. Costs about Rs 15 to 40 a copy plus the key. Never print the token.
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

DRIVE = "https://drive.google.com/uc?export=download&id="
STATIC = "https://static.pw.live/"
CDN = STATIC + "5b09189f7285894d9130ccd0/"
# name: level, subject, total, paper URL, copy URL, evaluators' marks (None = not marked yet)
PAPERS = {
    "KVS_PRT": ("PRT", "English, Hindi, Maths, EVS, SST, Physical and Art Education", 40,
                "", "", 28.5),
    "EMRS_TGT_MATHS": ("TGT", "Mathematics", 60, "", "", 36.0),
    "PGT_PHYSICS": ("PGT", "Physics", 40, DRIVE + "1arkGk4Wjr8Sm5p4DRZlMsEtxharViw1w",
                    CDN + "bf63f74a-44fc-4a3e-9e1b-0605e782d940.pdf", None),
    "PRT_MUSIC": ("PRT", "Music", 40, DRIVE + "1tllZSO16ODvhv3rd3N7RAZ2Wiw0mlP2K",
                  STATIC + "7e425bda-cd8b-46b4-95cd-58f3aa3ec438.pdf", None),
    "PGT_ECONOMICS": ("PGT", "Economics", 40, DRIVE + "1d3k3F6XzmW_KFD9Iwh6QVwGrnfVjbLsL",
                      CDN + "bc63d1bc-cd4d-422b-8a6f-710f08085d99.pdf", None),
    "PGT_CS": ("PGT", "Computer Science", 40, DRIVE + "1L8R-0zePmFpJuxDiMiDwKC5f5S7pNJAN",
               CDN + "b52dea9a-f527-40fa-b63b-43ad750c2be9.pdf", None),
    "EMRS_TGT_SCIENCE": ("TGT", "Science", 60, DRIVE + "14mpTxxVVxAAJ3sH3mz8xvMRC43GW5vBk",
                         CDN + "8b6ef055-34e0-47a2-a078-58af8284dca3.pdf", None),
    "TGT_SST": ("TGT", "Social Science", 40, DRIVE + "1f338tIl_xy2PxOsSHbA2kEEsJTvB3_xo",
                CDN + "6c5aca82-b944-445e-a42d-03092d11d084.pdf", None),
    "EMRS_TGT_ENGLISH": ("TGT", "English", 60, DRIVE + "1PBor7LFvtWPzT8m-h1dfeRCyoj72FTYn",
                         CDN + "7918bda7-5851-4949-8921-a22e9acb5f5b.pdf", None),
    "EMRS_PGT_HINDI": ("PGT", "Hindi", 60, DRIVE + "1_x8UmsaeIhQurrvKadGLpUvojjXaBarh",
                       CDN + "bd59afc0-42c9-47cc-b54c-83ff6de6b0ac.pdf", None),
    "EMRS_PGT_GEOGRAPHY": ("PGT", "Geography", 60, DRIVE + "1InngCRxpxAipfZay_1UHdz6MMhWM8Qef",
                           CDN + "ab9b3501-8e83-45c0-8053-692152e1880c.pdf", None),
    "EMRS_PGT_CHEMISTRY": ("PGT", "Chemistry", 60, DRIVE + "1vSXx5mDmgblkbeUGaqcd_hFFMEhwYvzu",
                           STATIC + "141e17d7-5c7b-444f-a4f1-a3d55a0c3e05.pdf", None),
    "PGT_BIOLOGY": ("PGT", "Biology", 40, DRIVE + "1AvfPZog5aBld_y_ramopuDy4BlwvvoKL",
                    CDN + "0459e38f-1cc1-440b-9288-ccfeebaac4bf.pdf", None),
    "EMRS_PGT_HISTORY": ("PGT", "History", 60, DRIVE + "1h6fYLOANAyrTmSy98OD_vTfeuKVyITa1",
                         CDN + "1d1926f6-f6ff-49f8-b61c-a3e9f8a5f4ad.pdf", None),
    "UPLT_HOME_SCIENCE": ("LT Grade", "Home Science", 200, DRIVE + "1itMnvorPuAoEPgua4K8E3ApCHphuGsva",
                          CDN + "169b7b54-f21a-4d58-9654-8039f19f633c.pdf", None),
}


def fetch(url: str, path: str) -> bool:
    import requests
    if os.path.exists(path):
        return True
    if not url:
        return False
    data = requests.get(url, timeout=300).content
    if not data.startswith(b"%PDF"):
        print(f"  not a PDF (a sign-in page?): {url}", flush=True)
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


def source_descriptions(marks: dict, qp_bytes: bytes, name: str) -> dict:
    """The rows as THIS source's free reader labels them, for a server built before it.

    The server checks only each row's qid and maximum against the paper, so an older
    build grades with the question text it is sent. Used to measure a reader fix on the
    installed EXE (21 Oct 2026: the instruction list of every teaching mock was sent as
    the text of Q1 to Q4) without waiting for a build. AI readings are left alone."""
    if marks.get("method") not in ("regex", "sections"):
        return marks
    import grader
    scheme = grader.marks_scheme_from_pdf(qp_bytes, f"{name}.pdf")
    text = {item.qid: item.description for item in (scheme.items if scheme else [])}
    items = [dict(item, part=text.get(item["qid"], item.get("part", ""))) for item in marks["items"]]
    return dict(marks, items=items)


def submit(name: str, spec: tuple, *, base: str, headers: dict, samples: str,
           fixed_descriptions: bool = False) -> dict | None:
    import requests
    level, subject, total, qp_url, sheet_url, _human = spec
    qp = os.path.join(samples, f"{name}_qp.pdf")
    sheet = os.path.join(samples, f"{name}_s1.pdf")
    if not fetch(qp_url, qp) or not fetch(sheet_url, sheet):
        print(f"{name}: paper or copy missing in {samples}; skipped", flush=True)
        return None
    form = {"student_class": level, "subject": subject, "total_marks": f"{total:g}",
            "category": "teaching", "provider": "claude"}
    qp_bytes = open(qp, "rb").read()
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
        return None
    if fixed_descriptions:
        marks = source_descriptions(marks, qp_bytes, name)
    print(f"{name}: marks via {marks.get('method')}: {len(marks['items'])} questions, "
          f"total {marks.get('total')}", flush=True)
    files = [("question_paper", (os.path.basename(qp), qp_bytes, "application/pdf")),
             ("answer_sheets", (os.path.basename(sheet), open(sheet, "rb").read(), "application/pdf"))]
    # No answer key: the teaching category writes its model key from the paper.
    data = dict(form, key_source="rubric", marks_items=json.dumps(marks["items"]),
                marks_source=str(marks.get("method") or ""),
                marks_attestation=str(marks.get("marks_attestation") or ""))
    r = requests.post(base + "/api/evaluate", headers=headers, files=files, data=data, timeout=600)
    if not r.ok:
        print(f"{name}: evaluate {r.status_code}: {r.text[:300]}", flush=True)
        return None
    job = {"name": name, "id": r.json()["job_id"], "k": r.json().get("download_key", "")}
    print(f"{name}: job {job['id']}", flush=True)
    return job


def collect(job: dict, state: dict, *, base: str, headers: dict, out: str) -> list[dict]:
    import requests
    name = job["name"]
    human = PAPERS[name][5]
    rows = []
    for result in state.get("results") or []:
        index = result.get("index")
        row = {"paper": name, "state": result.get("state"), "score": result.get("score"),
               "max": result.get("max"), "evaluators": "" if human is None else human,
               "cost_inr": (result.get("cost") or {}).get("total_inr"),
               "failure_code": result.get("failure_code") or "",
               "reason": "; ".join(result.get("review_reasons") or []) or result.get("error") or ""}
        rows.append(row)
        if result.get("score") is not None:
            for kind, ext in (("pdf", "pdf"), ("report", "json")):
                got = requests.get(f"{base}/api/jobs/{job['id']}/{kind}/{index}", headers=headers,
                                   params={"k": job["k"]}, timeout=120)
                if got.ok:
                    with open(os.path.join(out, f"{name}.{ext}"), "wb") as fh:
                        fh.write(got.content)
        print(f"  {name}: {row['state']} {row['score']}/{row['max']} (evaluators {row['evaluators'] or '-'}) "
              f"Rs {row['cost_inr']} {row['failure_code']} {row['reason'][:140]}", flush=True)
    got = requests.get(f"{base}/api/jobs/{job['id']}/zip", headers=headers,
                       params={"k": job["k"]}, timeout=300)
    if got.ok:
        with open(os.path.join(out, f"{name}.zip"), "wb") as fh:
            fh.write(got.content)
    return rows


def main() -> int:
    import requests
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--only", default="", help="comma separated paper names from PAPERS")
    ap.add_argument("--samples", default=os.path.join(ROOT, "build", "teaching_samples"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "teaching_run"))
    ap.add_argument("--no-server", action="store_true", help="use the server already on --port")
    ap.add_argument("--fixed-descriptions", action="store_true",
                    help="send this source's question text with a free marks reading "
                         "(to test a reader fix on an older EXE)")
    args = ap.parse_args()
    token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Empty token file.")
    os.makedirs(args.samples, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    headers = {"Authorization": f"Bearer {token}"}
    only = [n.strip().upper() for n in args.only.split(",") if n.strip()] or list(PAPERS)
    proc = None if args.no_server else start_server(args.port, os.path.join(args.out, "data"))
    rows: list[dict] = []
    try:
        # Every paper is its own job (its own model key), all queued at once so the
        # server runs them side by side.
        jobs = [j for j in (submit(n, PAPERS[n], base=base, headers=headers, samples=args.samples,
                                   fixed_descriptions=args.fixed_descriptions)
                            for n in only if n in PAPERS) if j]
        pending = list(jobs)
        while pending:
            time.sleep(15)
            for job in list(pending):
                state = requests.get(f"{base}/api/jobs/{job['id']}", headers=headers,
                                     params={"k": job["k"]}, timeout=60).json()
                if state.get("status") in ("done", "error", "cancelled"):
                    pending.remove(job)
                    rows += collect(job, state, base=base, headers=headers, out=args.out)
            if pending:
                print(f"  waiting on {', '.join(j['name'] for j in pending)}", flush=True)
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
