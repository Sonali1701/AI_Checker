# -*- coding: utf-8 -*-
"""Write the covering note that goes to faculty with a batch of evaluated copies.

A reviewer opening a folder of PDFs needs to know, before they start: which copy is
which, what it scored, and what has already been checked mechanically so they can
spend their attention on the marking judgement instead.

    python tools/faculty_index.py --dir faculty_review_20260905
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TITLES = {
    "economics": ("Economics", "Class 11 Commerce", 25),
    "business_studies": ("Business Studies", "Class 11 Commerce", 25),
    "core_maths": ("Core Maths", "Class 11 Commerce", 25),
    "ca_corporate_laws": ("Corporate and Other Laws", "CA Intermediate", 50),
    "ca_taxation": ("Taxation (Income Tax + GST)", "CA Intermediate", 100),
    "ca_financial_management": ("Financial Management + SM", "CA Intermediate", 50),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    rows = []
    for slug in sorted(os.listdir(args.dir)):
        case = os.path.join(args.dir, slug)
        if not os.path.isdir(case):
            continue
        subject, level, total = TITLES.get(slug, (slug, "", None))
        status_path = os.path.join(case, "status.json")
        if not os.path.isfile(status_path):
            rows.append((subject, level, total, None, None, None, "not graded"))
            continue
        status = json.load(open(status_path, encoding="utf-8"))
        results = status.get("results") or []
        if not results or results[0].get("release") is not True:
            detail = "held"
            if results:
                detail = str(results[0].get("error") or "held")[:80]
            rows.append((subject, level, total, None, None, None, detail))
            continue
        r = results[0]
        pdf = next((f for f in os.listdir(case) if f.lower().endswith(".pdf")), "")
        cost = (r.get("cost") or {}).get("total_inr")
        rows.append((subject, level, total, r.get("score"), r.get("max"), cost,
                     os.path.join(slug, pdf)))

    lines = [
        "# Evaluated copies for faculty review",
        "",
        "Each PDF is the student's own answer sheet with the marking drawn on it: a tick",
        "or cross where the mark was earned, the part-mark circled beside it, and the",
        "question's score and reason in the white strip down the right-hand edge. Page 1",
        "of every copy is a cover sheet with the total, the question-wise breakdown, and",
        "where marks were lost.",
        "",
        "| Paper | Level | Score | Out of | Cost (Rs.) | File |",
        "|---|---|---|---|---|---|",
    ]
    for subject, level, total, score, mx, cost, path in rows:
        if score is None:
            lines.append(f"| {subject} | {level} | — | {total or '—'} | — | *{path}* |")
        else:
            lines.append(
                f"| {subject} | {level} | {score:g} | {mx:g} | "
                f"{cost:.2f} | `{path}` |" if cost is not None else
                f"| {subject} | {level} | {score:g} | {mx:g} | — | `{path}` |")

    lines += [
        "",
        "## Already checked mechanically",
        "",
        "These were verified on every released copy before it reached you, so they are",
        "not what needs your eye:",
        "",
        "- the copy released rather than being held, with no warning on the result",
        "- it is marked out of the paper's official total",
        "- the question-wise scores add up to the printed total",
        "- every question that was scored is named on the copy",
        "- no deduction is left without an actionable reason",
        "- every character prints (no missing currency signs or blank boxes)",
        "- the marking is drawn on the student's answer, not only in the margin",
        "",
        "## What we would like your judgement on",
        "",
        "1. Is each mark awarded or withheld the right call against the answer key?",
        "2. Is the tick in the right place — beside the working that earned it?",
        "3. Does the margin reason tell the student what was actually wrong?",
        "4. Is the cover comment something you would be happy to sign?",
    ]
    out = os.path.join(args.dir, "README_FOR_FACULTY.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
