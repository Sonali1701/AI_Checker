# -*- coding: utf-8 -*-
"""Grade ONE copy N times with identical inputs, and report what moved.

The tool's largest measured error is not a wrong judgement, it is an unstable
marking scheme. Nine archived runs of one CA Inter Taxation copy — the same
question paper and answer key attached to every one of them, verified byte-identical
— scored it 10.0, 37.5, 39.0, 48.5, 51.0, 52.5, 59.0, 65.0 and 67.5 against a human's
42. Seven of the nine gave objective questions 1.5 marks where the paper prints
"(2 Marks)", and every one still summed to exactly 100 because the failed repair pass
hands over to a proportional rescale.

A single run cannot show any of that. This runs the same copy repeatedly and prints
the spread, so the question "did the change work?" has a number attached.

    python tools/repeat_run.py --qp ... --key ... --student ... --total 100 \
        --human 42 --runs 3 --out-prefix tax_check

Each run costs real money — roughly Rs 15-20 on a 100-mark paper — so --runs 3 is
the sensible default rather than a large sample.
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qp", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--student", required=True)
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--human", type=float, default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-prefix", default="repeat")
    ap.add_argument("--subject", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--provider", default="claude")
    ap.add_argument("--model", default="")
    ap.add_argument("--token-file", default="token.txt")
    args, passthrough = ap.parse_known_args()

    outs = []
    for n in range(1, args.runs + 1):
        out = f"{args.out_prefix}_{n}"
        print(f"\n{'=' * 70}\nRUN {n} of {args.runs} -> {out}\n{'=' * 70}", flush=True)
        cmd = [sys.executable, os.path.join(HERE, "eval_one.py"),
               "--qp", args.qp, "--key", args.key, "--student", args.student,
               "--out", out, "--total", str(args.total),
               "--subject", args.subject, "--group", args.group,
               "--provider", args.provider, "--token-file", args.token_file]
        if args.model:
            cmd += ["--model", args.model]
        if args.human is not None:
            cmd += ["--human", str(args.human)]
        cmd += passthrough
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc != 0:
            print(f"run {n} exited {rc}", flush=True)
        outs.append(out)

    from grader import GradeReport, pdf_text_layer, scheme_contradicts_paper
    paper_text = pdf_text_layer(open(args.qp, "rb").read(), os.path.basename(args.qp))

    print(f"\n{'=' * 70}\nSPREAD OVER {args.runs} IDENTICAL RUNS\n{'=' * 70}")
    scores, rows = [], []
    for out in outs:
        path = os.path.join(ROOT, out, "report.json")
        if not os.path.exists(path):
            rows.append((out, None, None, "no report — run failed"))
            continue
        rep = GradeReport.model_validate(json.load(open(path, encoding="utf-8")))
        cost_path = os.path.join(ROOT, out, "cost.json")
        inr = json.load(open(cost_path, encoding="utf-8"))["total_inr"] if \
            os.path.exists(cost_path) else None
        conflict = scheme_contradicts_paper(rep, paper_text, args.total)
        scores.append(rep.total_score)
        rows.append((out, rep.total_score, len(rep.questions),
                     conflict or ("rescaled" if rep.maxima_rescaled else "faithful"),
                     inr))

    for row in rows:
        out, score, nq, note = row[0], row[1], row[2], row[3]
        inr = row[4] if len(row) > 4 else None
        head = f"  {out:16s} " + (f"{score:6g}/{args.total:g}" if score is not None else "   —   ")
        head += f"  {nq or '?':>3} questions"
        if inr is not None:
            head += f"  Rs{inr:5.1f}"
        print(head + f"  {note}")

    if len(scores) > 1:
        spread = max(scores) - min(scores)
        print(f"\n  spread {spread:g} marks ({min(scores):g} to {max(scores):g})")
        if args.human is not None:
            deltas = [s - args.human for s in scores]
            print(f"  vs human {args.human:g}: " +
                  ", ".join(f"{d:+g}" for d in deltas) +
                  f" | mean abs {sum(abs(d) for d in deltas) / len(deltas):.1f}")
        print("\n  The nine archived runs of the CA Inter Taxation copy spread 57.5 marks.")
        print("  Anything close to that means the scheme is still moving between runs.")


if __name__ == "__main__":
    main()
