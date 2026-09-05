# -*- coding: utf-8 -*-
"""One command that checks the whole tool end to end, with one token.

    python tools/verify_all.py --token-file token.txt

Everything the offline suites can prove, they prove for free first. Then the two
grading arms run on a real copy — the single call as the control, page-window
fan-out as the treatment — and every claim about the result is turned into a check
with an observed number beside it, so the answer to "is it fixed?" is a table and
not an opinion.

The claims are deliberately not about the score. A copy can come back with the right
total and be unusable: the Chemistry sheet that started all this scored 25/70 with
twenty answered sub-parts filed as never attempted, and 25 looked like an ordinary
row. So the checks are about coverage, the denominator, evidence, anchors, the gate,
and the bill.

    --runs N     grade the fan-out arm N times and report the spread (N calls' worth
                 of money; the instability it measures is the tool's largest error)
    --skip-single  fan-out arm only, when the control has already been run
    --orient     also exercise the orientation pass (the default student file is
                 already straightened, so it is skipped unless asked for)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

DEFAULT_QP = ("C:/Users/vs510/Downloads/Board Pattern Test-01_Chemistry_"
              "Parishram Infinity (2027)_16-08-2026_Questions with Solutions.pdf")
DEFAULT_STUDENT = "chem_r3/student_upright.pdf"


# --------------------------------------------------------------------------
# One check: a name, what was seen, and whether it is good enough.
# --------------------------------------------------------------------------
class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool | None, str]] = []

    def add(self, name: str, ok: bool | None, observed) -> None:
        self.rows.append((name, ok, str(observed)))
        mark = {True: "PASS", False: "FAIL", None: "n/a "}[ok]
        print(f"  [{mark}] {name}: {observed}", flush=True)

    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.rows if ok is False]

    def table(self) -> None:
        width = max(len(n) for n, _, _ in self.rows)
        print("\n" + "=" * (width + 30))
        print("VERDICT")
        print("=" * (width + 30))
        for name, ok, observed in self.rows:
            mark = {True: "PASS", False: "FAIL", None: "n/a "}[ok]
            print(f"  {mark}  {name:<{width}}  {observed}")
        bad = self.failed()
        print("=" * (width + 30))
        print(f"  {len(self.rows) - len(bad)}/{len(self.rows)} checks passed"
              + (f" — FAILED: {', '.join(bad)}" if bad else ""))


def token_minutes_left(path: str) -> float | None:
    """Minutes until the Google token expires, or None if it cannot be read."""
    try:
        raw = io.open(path, encoding="utf-8").read().strip()
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return (exp - time.time()) / 60.0
    except Exception:
        return None


def run_arm(args, out: str, fanout: bool) -> str:
    """Grade the copy into `out` through eval_one, the way the product does.

    Returns the run's captured log, because the [fanout] lines in it are the only
    record of which repair passes actually fired.
    """
    cmd = [sys.executable, "-u", os.path.join(HERE, "eval_one.py"),
           "--qp", args.qp, "--key", args.key, "--student", args.student,
           "--out", out, "--total", str(args.total),
           "--subject", args.subject, "--group", args.group,
           "--provider", args.provider, "--token-file", args.token_file]
    if args.model:
        cmd += ["--model", args.model]
    if args.human is not None:
        cmd += ["--human", str(args.human)]
    if fanout:
        cmd += ["--fanout"]
    if not args.orient:
        cmd += ["--no-orient"]
    print(f"\n$ {' '.join(cmd[2:])}\n", flush=True)

    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    lines = []
    for line in proc.stdout:
        if "Requests" in line or "warnings.warn" in line:
            continue
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    proc.wait()
    log = "".join(lines)
    io.open(os.path.join(ROOT, out + ".log"), "w", encoding="utf-8").write(log)
    return log


def load_run(out: str):
    """(report, cost, ocr_pages) from a finished run directory."""
    import mathpix
    from grader import GradeReport
    base = os.path.join(ROOT, out)
    report = GradeReport.model_validate_json(
        io.open(os.path.join(base, "report.json"), encoding="utf-8").read())
    cost = {}
    if os.path.exists(os.path.join(base, "cost.json")):
        cost = json.load(io.open(os.path.join(base, "cost.json"), encoding="utf-8"))
    ocr_pages = []
    if os.path.exists(os.path.join(base, "ocr.json")):
        ocr_pages = [mathpix.pageocr_from_dict(d) for d in
                     json.load(io.open(os.path.join(base, "ocr.json"),
                                       encoding="utf-8"))]
    return report, cost, ocr_pages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--qp", default=DEFAULT_QP)
    ap.add_argument("--key", default="")
    ap.add_argument("--student", default=DEFAULT_STUDENT)
    ap.add_argument("--total", type=float, default=70.0)
    ap.add_argument("--subject", default="Chemistry")
    ap.add_argument("--group", default="12")
    ap.add_argument("--human", type=float, default=None)
    ap.add_argument("--provider", default="claude")
    ap.add_argument("--model", default="")
    ap.add_argument("--runs", type=int, default=1,
                    help="fan-out runs; >1 measures the run-to-run spread")
    ap.add_argument("--skip-single", action="store_true")
    ap.add_argument("--orient", action="store_true")
    ap.add_argument("--out-prefix", default="verify")
    args = ap.parse_args()
    args.key = args.key or args.qp

    checks = Checks()

    # ---- 0. can this run happen at all? -----------------------------------
    print("\n=== 0. inputs ===", flush=True)
    missing = [p for p in (args.qp, args.key, os.path.join(ROOT, args.student))
               if not os.path.exists(p)]
    if missing:
        print("  cannot start — file(s) not found:")
        for path in missing:
            print(f"    {path}")
        sys.exit(2)
    left = token_minutes_left(os.path.join(ROOT, args.token_file))
    if left is None:
        print(f"  cannot read a token from {args.token_file}")
        sys.exit(2)
    if left <= 2:
        print(f"  token expired {-left:.0f} min ago — paste a fresh one into "
              f"{args.token_file} and run again")
        sys.exit(2)
    print(f"  token valid for {left:.0f} more minutes")
    print(f"  paper {os.path.basename(args.qp)}")
    print(f"  copy  {args.student}  ({args.total:g} marks)")

    # ---- 1. everything that costs nothing to prove ------------------------
    print("\n=== 1. offline regression suites ===", flush=True)
    proc = subprocess.run([sys.executable, os.path.join(HERE, "selftest.py")],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    last = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:]
    summary = last[0].strip() if last else "no output"
    checks.add("offline suites", proc.returncode == 0, summary)

    # ---- 2. the denominator, read off the paper for free ------------------
    print("\n=== 2. marks scheme (free path, no AI call) ===", flush=True)
    from pipeline import detect_marks_scheme
    qp_bytes = io.open(args.qp, "rb").read()
    scheme, method = detect_marks_scheme(qp_bytes, os.path.basename(args.qp), None,
                                         args.group, args.subject,
                                         total_marks=args.total)
    if scheme is None:
        checks.add("scheme without an AI call", False, f"none found (method {method})")
    else:
        exact = abs(scheme.total - args.total) < 0.01
        checks.add("scheme without an AI call", method in ("regex", "sections"),
                   f"method {method}")
        checks.add("scheme total matches the paper", exact,
                   f"{scheme.total:g} vs {args.total:g}, {len(scheme.items)} items")

    # ---- 3 & 4. the two grading arms --------------------------------------
    single_dir = f"{args.out_prefix}_single"
    fan_dir = f"{args.out_prefix}_fanout"
    if not args.skip_single:
        print("\n=== 3. control arm: one call for the whole paper ===", flush=True)
        run_arm(args, single_dir, fanout=False)
    print("\n=== 4. treatment arm: page-window fan-out ===", flush=True)
    fan_log = run_arm(args, fan_dir, fanout=True)

    # ---- 5. what the copy actually looks like -----------------------------
    print("\n=== 5. the marked copy ===", flush=True)
    from costs import budget_for
    from grader import find_unmarked_answers
    from preflight import blocking, check_copy

    report, cost, ocr_pages = load_run(fan_dir)
    issues = check_copy(report, total_marks=args.total, ocr_pages=ocr_pages)
    blocked = blocking(issues)
    codes = {i.code for i in issues}

    answered = [q for q in report.questions if getattr(q, "attempted", True)]
    crits = [c for q in report.questions for c in (q.criteria or [])]
    anchored = [c for c in crits if (getattr(c, "step_line_id", "") or "").strip()]
    lost = [q for q in answered if q.score < q.max_score - 1e-9]

    # Coverage: every question the paper lists has an entry, and no answer the OCR
    # can see is filed as never attempted.
    if scheme is not None:
        # A scheme item is covered by an exact match OR by its sub-parts: the paper
        # lists "Q11" worth 5 and the marker may answer it as Q11.a and Q11.b. String
        # equality would call that missing and hide the drops that are real.
        from grader import _norm_qid, _top_level_qid
        have_exact = {_norm_qid(q.qid) for q in report.questions}
        have_top = {_top_level_qid(q.qid) for q in report.questions}
        missing = [it.qid for it in scheme.items
                   if _norm_qid(it.qid) not in have_exact
                   and _top_level_qid(it.qid) not in have_top]
        checks.add("every question on the paper is in the report", not missing,
                   f"{len(scheme.items) - len(missing)}/{len(scheme.items)}"
                   + (f", missing {missing}" if missing else ""))
    unmarked = find_unmarked_answers(report, ocr_pages) if ocr_pages else []
    checks.add("no answered question filed as blank",
               not unmarked if ocr_pages else None,
               unmarked or ("none" if ocr_pages else "no OCR saved"))
    checks.add("denominator matches the paper",
               abs(report.max_total - args.total) < 0.01,
               f"{report.max_total:g}/{args.total:g}"
               + (" (rescaled)" if report.maxima_rescaled else ""))

    # Evidence: a mark the student can act on and a teacher can check.
    checks.add("marks carry rubric steps", "no-criteria-anywhere" not in codes
               and len(crits) > 0, f"{len(crits)} criteria over {len(lost)} "
               f"question(s) that lost marks")
    checks.add("steps are anchored to the page", "no-anchors-at-all" not in codes
               and len(anchored) > 0, f"{len(anchored)}/{len(crits)} anchored")
    checks.add("no marks lost without a reason",
               not any(not (q.remark or "").strip() for q in lost),
               f"{sum(1 for q in lost if not (q.remark or '').strip())}/{len(lost)}")

    # The gate is the bar that matters: a held copy is a copy nobody can send.
    checks.add("gate releases the copy", not blocked,
               "released" if not blocked else
               f"HELD — {', '.join(i.code for i in blocked)}")

    inr = float(cost.get("total_inr") or 0)
    from grader import detect_exam_type
    budget = budget_for(
        args.total,
        exam_type=detect_exam_type(args.group, args.subject),
        subject=args.subject,
    )
    checks.add("inside the budget", inr <= budget, f"Rs.{inr:.1f} of Rs.{budget:g}")

    # Which repair passes had to fire, from the run's own log.
    fired = [ln.strip() for ln in fan_log.splitlines() if "[fanout]" in ln]
    print("\n  what the run had to repair:")
    for line in fired or ["    (nothing — every window came back complete)"]:
        print(f"    {line}")

    # ---- 6. the two arms side by side -------------------------------------
    if not args.skip_single and os.path.exists(os.path.join(ROOT, single_dir,
                                                            "report.json")):
        print("\n=== 6. fan-out against the single call ===", flush=True)
        subprocess.call([sys.executable, os.path.join(HERE, "ab_compare.py"),
                         single_dir, fan_dir, "--total", str(args.total)], cwd=ROOT)
        _, single_cost, _ = load_run(single_dir)
        single_inr = float(single_cost.get("total_inr") or 0)
        checks.add("fan-out costs no more than one call", inr <= single_inr + 0.01,
                   f"Rs.{inr:.1f} vs Rs.{single_inr:.1f}")

    # ---- 7. does it say the same thing twice? -----------------------------
    if args.runs > 1:
        print(f"\n=== 7. {args.runs} identical fan-out runs ===", flush=True)
        scores = [report.total_score]
        for n in range(2, args.runs + 1):
            run_arm(args, f"{fan_dir}_{n}", fanout=True)
            again, _, _ = load_run(f"{fan_dir}_{n}")
            scores.append(again.total_score)
        spread = max(scores) - min(scores)
        checks.add("stable across identical runs", spread <= 2.0,
                   f"spread {spread:g} marks over {args.runs} runs "
                   f"({', '.join('%g' % s for s in scores)})")

    if args.human is not None:
        delta = report.total_score - args.human
        checks.add("within 2 marks of the human", abs(delta) <= 2.0,
                   f"AI {report.total_score:g} vs human {args.human:g} "
                   f"({delta:+g})")

    checks.table()
    sys.exit(1 if checks.failed() else 0)


if __name__ == "__main__":
    main()
