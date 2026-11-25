"""Run marks-scheme detection on a question paper straight from the command line.

Same code path the app uses, but prints the full traceback on failure — so a
detection problem can be diagnosed without clicking through the UI.

  python tools/detect_marks_cli.py "paper.pdf" --token-file token.txt \
      --class "CA Foundation" --subject "Business Laws" --total 50
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pw_access
from pipeline import cfg_from_request, detect_marks_scheme


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paper")
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--class", dest="student_class", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--exam-type", default="")
    ap.add_argument("--total", type=float, default=None)
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    token = args.token or os.environ.get("PW_GOOGLE_TOKEN", "")
    if not token and args.token_file:
        token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("No token. Pass --token/--token-file or set PW_GOOGLE_TOKEN.")
    if not pw_access.check_allowed(token):
        sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")

    data = open(args.paper, "rb").read()
    cfg = cfg_from_request(
        "claude", args.model, args.subject,
        google_token=token,
        task_id=pw_access.new_task_id("detect-marks-cli"),
        student_class=args.student_class,
        exam_type=args.exam_type,
    )
    print(f"exam_type={cfg.exam_type} model={cfg.model} total={args.total}")
    try:
        scheme, method = detect_marks_scheme(
            data, os.path.basename(args.paper), cfg,
            args.student_class or None, args.subject or None,
            total_marks=args.total,
        )
    except Exception:
        traceback.print_exc()
        sys.exit("DETECTION FAILED (traceback above)")

    if scheme is None:
        sys.exit("No scheme detected.")
    print(f"\nmethod: {method}")
    for item in scheme.items:
        print(f"  {item.qid:<12} {item.max:>5g}   {item.description}")
    print(f"  {'PARTS SUM':<12} {sum(i.max for i in scheme.items):>5g}")
    if args.total:
        print(f"  {'PAPER TOTAL':<12} {args.total:>5g}")


if __name__ == "__main__":
    main()
