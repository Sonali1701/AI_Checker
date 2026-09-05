# -*- coding: utf-8 -*-
"""Compare two eval_one runs on the same copy — the A/B for per-question grading.

    python tools/ab_compare.py ab_single ab_fanout --total 70

Deliberately does NOT lead with the score delta. The whole reason this comparison
exists is that the total is the one number a badly broken copy can get right by
accident: the Chemistry sheet scored 25/70 with twenty answered sub-parts filed as
unattempted, and 25 looked like a perfectly ordinary row.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from grader import GradeReport
from preflight import blocking, check_copy


def load(directory: str):
    with io.open(os.path.join(directory, "report.json"), encoding="utf-8") as fh:
        report = GradeReport(**json.load(fh))
    cost = {}
    path = os.path.join(directory, "cost.json")
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as fh:
            cost = json.load(fh)
    return report, cost


def card(report, cost, total_marks):
    qs = list(report.questions or [])
    answered = [q for q in qs if getattr(q, "attempted", True)]
    unattempted = [q for q in qs if not getattr(q, "attempted", True)]
    lost = [q for q in answered if q.score < q.max_score - 1e-9]
    crits = [c for q in qs for c in (q.criteria or [])]
    with_ev = [c for c in crits if (getattr(c, "evidence", "") or "").strip()]
    anchored = [q for q in answered
                if (getattr(q, "anchor_line_id", "") or "").strip()]
    step_anchored = [c for c in crits
                     if (getattr(c, "step_line_id", "") or "").strip()]
    issues = check_copy(report, total_marks=total_marks)
    return {
        "score": f"{report.total_score:g}/{report.max_total:g}",
        "questions": len(qs),
        "called unattempted": len(unattempted),
        "marks zeroed as blank": round(sum(q.max_score for q in unattempted), 1),
        "criteria": len(crits),
        "criteria with evidence": f"{len(with_ev)}/{len(crits)}" if crits else "0/0",
        "criteria anchored": f"{len(step_anchored)}/{len(crits)}" if crits else "0/0",
        "questions anchored": f"{len(anchored)}/{len(answered)}" if answered else "0/0",
        "lost marks, no reason": f"{sum(1 for q in lost if not (q.remark or '').strip())}"
                                 f"/{len(lost)}",
        "blocking issues": len(blocking(issues)),
        "verdict": "released" if not blocking(issues) else "HELD",
        "cost Rs.": round(float(cost.get("total_inr") or 0), 1),
        "output tokens": int(cost.get("output_tokens") or 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", help="control run directory (single call)")
    ap.add_argument("b", help="treatment run directory (fan-out)")
    ap.add_argument("--total", type=float, default=None)
    args = ap.parse_args()

    ra, ca = load(args.a)
    rb, cb = load(args.b)
    ka, kb = card(ra, ca, args.total), card(rb, cb, args.total)

    width = max(len(k) for k in ka)
    print(f"\n{'':<{width}}  {'single call':>16}  {'fan-out':>16}")
    print("-" * (width + 38))
    for key in ka:
        va, vb = str(ka[key]), str(kb[key])
        flag = "  <--" if va != vb else ""
        print(f"{key:<{width}}  {va:>16}  {vb:>16}{flag}")

    print("\nquestions only one arm graded:")
    qa = {q.qid for q in ra.questions}
    qb = {q.qid for q in rb.questions}
    print(f"  single-call only: {sorted(qa - qb) or 'none'}")
    print(f"  fan-out only    : {sorted(qb - qa) or 'none'}")


if __name__ == "__main__":
    main()
