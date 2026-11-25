"""Evaluate the AI grader against human-marked CA copies from the review Excel.

The sample workbook ("Sample Files_ AI Tool Review.xlsx") holds real PW CA Inter /
Foundation test-series rows: the student's scanned answer booklet, the human
evaluator's marked PDF, and the human's total (usermarks/totalmarks). This script
grades N of those booklets with the tool and reports AI-vs-human deltas, saving the
AI-annotated PDFs so they can be eyeballed against the evaluator's copies.

Run it on a machine where you are signed in to the AI Checker web UI:
  1. Open the tool in the browser, sign in with your @pw.live account.
  2. DevTools (F12) -> Application/Storage -> Session Storage -> copy "pwGoogleToken".
  3. python tools/eval_ca.py --token-file token.txt          (paste the token in token.txt)
     or: set PW_GOOGLE_TOKEN=<token> && python tools/eval_ca.py

Useful flags:
  --excel PATH     workbook path (default: the Downloads sample file)
  --n N            how many booklets to grade (default 3 — each costs real money)
  --filter TEXT    only rows whose test name contains TEXT (e.g. "Taxation")
  --qp PATH        question paper PDF to grade against (optional; booklet-only otherwise)
  --key PATH       answer key / ICAI suggested-answers PDF (optional)
  --out DIR        output directory (default: eval_ca_out)

Needs: openpyxl (pip install openpyxl) plus the app's own requirements.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import os
import re
import sys
import time

# Windows consoles default to cp1252, which can't print ₹ etc. — never let a
# print() kill a run that already graded successfully.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import pw_access
from pipeline import cfg_from_request, grade_sheet

DEFAULT_EXCEL = os.path.expanduser("~/Downloads/Sample Files_ AI Tool Review.xlsx")

# Used when no official key is supplied: the model grades against its own knowledge of
# the ICAI syllabus, which is what the professional-exam prompt expects it to do well.
FALLBACK_KEY_TEXT = (
    "No official marking key is attached for this paper. Grade against your expert "
    "knowledge of the ICAI syllabus and the conventions of ICAI suggested answers for "
    "this subject: correct provisions with Act and section numbers, standard accounting "
    "treatments and formats, standard computation methods, and the usual step-wise mark "
    "allocation. Where you are genuinely unsure what the question asked or what the "
    "official answer is, grade what you can verify and flag the question's remark with "
    "[verify against key]."
)

_SUBJECT_PATTERNS = [
    (r"advanced accounting", "Advanced Accounting"),
    (r"accounting", "Accounting"),
    (r"corporate and other law|corporate law", "Corporate and Other Laws"),
    (r"business law", "Business Laws"),
    (r"taxation", "Taxation (Income Tax + GST)"),
    (r"cost and management|costing", "Cost and Management Accounting"),
    (r"financial management|fm and sm|strategic management", "Financial Management and Strategic Management"),
    (r"auditing", "Auditing and Ethics"),
    (r"economics", "Business Economics"),
]


def subject_from_test_name(name: str) -> str:
    low = (name or "").lower()
    for pattern, subject in _SUBJECT_PATTERNS:
        if re.search(pattern, low):
            return subject
    return "Commerce"


def class_from_row(batch: str, cohort: str, category: str) -> str:
    text = f"{batch or ''} {cohort or ''} {category or ''}".lower()
    if "foundation" in text or "fond" in text:
        return "CA Foundation"
    return "CA Inter"


def load_ground_truth(path: str) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    header = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(header)}

    def g(row, name):
        return row[idx[name]] if name in idx else None

    def is_url(u):
        return u and str(u).startswith("http") and len(str(u)) > 40

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not is_url(g(row, "student_test_file")) or g(row, "usermarks") is None:
            continue
        total = g(row, "totalmarks")
        rows.append({
            "test": str(g(row, "test_name") or ""),
            "student": str(g(row, "student_name") or "student"),
            "student_file": str(g(row, "student_test_file")),
            "evaluator_file": str(g(row, "evaluator_file") or ""),
            "human_marks": float(g(row, "usermarks")),
            "total_marks": float(total) if total else None,
            "human_comment": str(g(row, "new_revcomment") or ""),
            "student_class": class_from_row(
                str(g(row, "batch_name") or ""), str(g(row, "pass_cohort_name") or ""),
                str(g(row, "financeexamcategory") or "")),
            "subject": subject_from_test_name(str(g(row, "test_name") or "")),
        })
    return rows


def select_rows(rows: list[dict], n: int, *, spread: bool, skip: int) -> list[dict]:
    """First n rows, or — with spread — round-robin across subjects so a small run
    still covers Accounting, Law, Tax, Costing, FM, Audit... `skip` drops the first
    rows of each subject (already-graded samples)."""
    if not spread:
        return rows[skip: skip + n]
    by_subject: dict[str, list[dict]] = {}
    for row in rows:
        by_subject.setdefault(row["subject"], []).append(row)
    queues = [bucket[skip:] for bucket in by_subject.values()]
    picked: list[dict] = []
    while len(picked) < n and any(queues):
        for bucket in queues:
            if bucket and len(picked) < n:
                picked.append(bucket.pop(0))
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--excel", default=DEFAULT_EXCEL)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--filter", default="")
    ap.add_argument("--qp", default="", help="optional question paper PDF")
    ap.add_argument("--key", default="", help="optional answer key / suggested answers PDF")
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--out", default="eval_ca_out")
    ap.add_argument("--model", default="", help="override Claude model (default: auto by paper size)")
    ap.add_argument("--jobs", type=int, default=3,
                    help="booklets graded concurrently (model calls are I/O-bound; 3-4 fits a 1-hour token)")
    ap.add_argument("--spread", action="store_true",
                    help="sample rows round-robin across subjects instead of taking the first N")
    ap.add_argument("--skip", type=int, default=0,
                    help="skip the first K rows of each subject (to avoid re-grading earlier samples)")
    args = ap.parse_args()

    token = args.token or os.environ.get("PW_GOOGLE_TOKEN", "")
    if not token and args.token_file:
        token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("No token. Pass --token/--token-file or set PW_GOOGLE_TOKEN (see docstring).")
    if not pw_access.check_allowed(token):
        sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")

    rows = load_ground_truth(args.excel)
    if args.filter:
        rows = [r for r in rows if args.filter.lower() in r["test"].lower()]
    if not rows:
        sys.exit("No ground-truth rows matched.")
    rows = select_rows(rows, args.n, spread=args.spread, skip=args.skip)

    qp_pngs: list[bytes] = []
    if args.qp:
        from grader import pdf_or_image_to_pngs
        qp_pngs = pdf_or_image_to_pngs(open(args.qp, "rb").read(), os.path.basename(args.qp))
    ak_pngs = None
    ak_text = None
    if args.key:
        from grader import pdf_or_image_to_pngs
        ak_pngs = pdf_or_image_to_pngs(open(args.key, "rb").read(), os.path.basename(args.key))
    else:
        ak_text = FALLBACK_KEY_TEXT

    os.makedirs(args.out, exist_ok=True)

    def grade_one(i: int, row: dict) -> dict:
        label = f"[{i + 1}/{len(rows)}] {row['student']} — {row['test']}"
        print(label, flush=True)
        try:
            sa_bytes = requests.get(row["student_file"], timeout=120).content
            cfg = cfg_from_request(
                "claude", args.model, row["subject"],
                google_token=token,
                task_id=pw_access.new_task_id("eval-ca"),
                student_class=row["student_class"],
                exam_type="professional",
            )
            result = None
            for attempt in range(3):  # long calls hit transient connection resets
                try:
                    result = grade_sheet(
                        qp_pngs=qp_pngs,
                        sa_bytes=sa_bytes,
                        filename=f"{row['student']}.pdf",
                        ak_pngs=ak_pngs,
                        ak_text=ak_text,
                        marks_scheme=None,
                        cfg=cfg,
                        student_class=row["student_class"],
                        subject=row["subject"],
                        use_mathpix=True,
                        total_marks=row["total_marks"],
                    )
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                    if attempt == 2:
                        raise
                    print(f"    transient network error ({exc}); retrying in 20s...")
                    time.sleep(20)
            report = result["report"]
            stem = f"{i}_{re.sub(r'[^A-Za-z0-9]+', '_', row['student'])}"
            pdf_path = os.path.join(args.out, f"{stem}_ai.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(result["pdf_bytes"])
            # The human evaluator's copy, side by side, for the per-question comparison.
            if row["evaluator_file"].startswith("http"):
                try:
                    with open(os.path.join(args.out, f"{stem}_human.pdf"), "wb") as fh:
                        fh.write(requests.get(row["evaluator_file"], timeout=120).content)
                except requests.RequestException as exc:
                    print(f"    (evaluator PDF download failed: {exc})")
            # Full structured report — needed to diagnose tick/remark placement.
            with open(os.path.join(args.out, f"{stem}_report.json"), "w", encoding="utf-8") as fh:
                fh.write(report.model_dump_json(indent=1))
            delta = report.total_score - row["human_marks"]
            cost = result["cost"]
            print(f"    {label}: AI {report.total_score:g}/{report.max_total:g} · human "
                  f"{row['human_marks']:g}/{row['total_marks'] or 0:g} · delta {delta:+g} · "
                  f"cost Rs.{cost.total_inr:g} (in={cost.input_tokens} out={cost.output_tokens})",
                  flush=True)
            return {
                "student": row["student"], "test": row["test"], "subject": row["subject"],
                "class": row["student_class"], "ai_score": report.total_score,
                "ai_max": report.max_total, "human_score": row["human_marks"],
                "paper_total": row["total_marks"], "delta": round(delta, 2),
                "model": cfg.model, "cost_inr": cost.total_inr,
                "input_tokens": cost.input_tokens, "cached_input_tokens": cost.cached_input_tokens,
                "output_tokens": cost.output_tokens, "llm_cost_usd": cost.llm_cost_usd,
                "mathpix_cost_usd": cost.mathpix_cost_usd,
                "ai_pdf": pdf_path, "evaluator_pdf_url": row["evaluator_file"],
                "human_comment": row["human_comment"], "error": "",
            }
        except Exception as exc:
            print(f"    {label}: FAILED: {type(exc).__name__}: {exc}", flush=True)
            return {"student": row["student"], "test": row["test"], "subject": row["subject"],
                    "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda ir: grade_one(*ir), enumerate(rows)))

    csv_path = os.path.join(args.out, "results.csv")
    fields = ["student", "test", "subject", "class", "ai_score", "ai_max", "human_score",
              "paper_total", "delta", "model", "cost_inr", "input_tokens",
              "cached_input_tokens", "output_tokens", "llm_cost_usd", "mathpix_cost_usd",
              "ai_pdf", "evaluator_pdf_url", "human_comment", "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    graded = [r for r in results if not r.get("error")]
    if graded:
        mae = sum(abs(r["delta"]) for r in graded) / len(graded)
        bias = sum(r["delta"] for r in graded) / len(graded)
        print(f"\nGraded {len(graded)}/{len(results)} · mean |delta| {mae:.1f} marks · "
              f"mean bias {bias:+.1f} (positive = AI over-awards vs human)")
        by_subject: dict[str, list[dict]] = {}
        for r in graded:
            by_subject.setdefault(r["subject"], []).append(r)
        for subject, rs in sorted(by_subject.items()):
            sb = sum(r["delta"] for r in rs) / len(rs)
            print(f"  {subject:<28} n={len(rs):<2} bias {sb:+.1f}  "
                  f"deltas {[r['delta'] for r in rs]}")
    print(f"Details: {csv_path} — compare each *_ai.pdf against its evaluator_pdf_url.")


if __name__ == "__main__":
    main()
