# -*- coding: utf-8 -*-
"""Grade ONE explicit (question paper, solution, student sheet) set.

The tab harness (eval_tabs.py) drives the sample Excel; this drives a set of
loose files, which is how complete 100-mark CA sets actually arrive.
"""
import argparse
import dataclasses
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mathpix
import pw_access
from costs import budget_for
from pipeline import cfg_from_request, detect_marks_scheme, grade_sheet
from preflight import blocking
from server import _make_orienter
from grader import pdf_or_image_to_pngs, pdf_text_layer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qp", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--student", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--human", type=float, default=None)
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--provider", default="claude")
    ap.add_argument("--model", default="")
    ap.add_argument("--no-mathpix", action="store_true")
    ap.add_argument("--fanout", action="store_true",
                    help="grade a page-window at a time (the A/B arm)")
    ap.add_argument("--no-orient", action="store_true",
                    help="skip the orientation pass the app applies")
    ap.add_argument("--no-audit", action="store_true",
                    help="skip the moderation/second-examiner pass entirely")
    args = ap.parse_args()

    token = open(args.token_file, encoding="utf-8").read().strip()
    if not token or not pw_access.check_allowed(token):
        sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")
    os.makedirs(args.out, exist_ok=True)

    qp_bytes = open(args.qp, "rb").read()
    key_bytes = open(args.key, "rb").read()
    sa_bytes = open(args.student, "rb").read()

    cfg = cfg_from_request(args.provider, args.model, args.subject, google_token=token,
                           task_id=pw_access.new_task_id("eval-one"),
                           student_class=args.group, total_marks=args.total)
    # A/B arm: page-window grading instead of one call for the whole booklet.
    cfg = dataclasses.replace(cfg, fanout=bool(args.fanout))
    if args.no_audit:
        import pipeline as _pipeline
        _pipeline.moderation_applies = lambda *a, **k: False
    print(f"routed -> provider={cfg.provider} model={cfg.model} exam_type={cfg.exam_type}",
          flush=True)

    # Straighten sideways photos exactly the way the app does. This is server.py's
    # OWN orienter, not a copy: measuring a path the product does not run is how the
    # hard-coded-model bug hid for so long. Students photograph pages in landscape
    # constantly, and a rotated page returns NO OCR lines at all, so every mark on it
    # loses its anchor.
    if not args.no_orient:
        orient = _make_orienter(cfg)
        qp_bytes = orient(qp_bytes, "qp.pdf")
        key_bytes = orient(key_bytes, "key.pdf")
        before = sa_bytes
        sa_bytes = orient(sa_bytes, "student.pdf")
        print(f"orientation: student sheet {'straightened' if sa_bytes is not before else 'already upright'}",
              flush=True)
        with open(os.path.join(args.out, "student_upright.pdf"), "wb") as fh:
            fh.write(sa_bytes)

    qp_pngs = pdf_or_image_to_pngs(qp_bytes, "qp.pdf")
    qp_text = pdf_text_layer(qp_bytes, "qp.pdf")
    ak_pngs = pdf_or_image_to_pngs(key_bytes, "key.pdf")
    ak_text_layer = pdf_text_layer(key_bytes, "key.pdf")
    print(f"qp {len(qp_pngs)}p ({len(qp_text)} chars text) | "
          f"key {len(ak_pngs)}p ({len(ak_text_layer)} chars text)", flush=True)

    scheme = None
    try:
        found, method = detect_marks_scheme(qp_bytes, "qp.pdf", cfg, args.group,
                                            args.subject, total_marks=args.total)
        if found is not None and method in ("regex", "sections", "ai"):
            scheme = found
            print(f"marks scheme via {method}: total {found.total:g}, "
                  f"{len(found.items)} items", flush=True)
    except Exception as exc:
        print(f"scheme extraction failed ({type(exc).__name__}: {exc})", flush=True)

    t0 = time.time()
    result = grade_sheet(
        qp_pngs=qp_pngs, sa_bytes=sa_bytes, filename="student.pdf",
        ak_pngs=ak_pngs, ak_text=None, marks_scheme=scheme, cfg=cfg,
        student_class=args.group, subject=args.subject,
        use_mathpix=not args.no_mathpix, total_marks=args.total,
        qp_text=qp_text, ak_text_layer=ak_text_layer,
    )
    rep, cost = result["report"], result["cost"]
    with open(os.path.join(args.out, "ai.pdf"), "wb") as fh:
        fh.write(result["pdf_bytes"])
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
        fh.write(rep.model_dump_json(indent=1))
    # The OCR is what the page actually said. Saving it makes the run re-checkable
    # for free afterwards — coverage, anchors and the "was it really blank?" question
    # all need the page, and re-fetching it costs money the run already spent.
    ocr_pages = result.get("ocr_pages") or []
    if ocr_pages:
        with open(os.path.join(args.out, "ocr.json"), "w", encoding="utf-8") as fh:
            json.dump([mathpix.pageocr_to_dict(p) for p in ocr_pages], fh)
    with open(os.path.join(args.out, "cost.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": cost.model, "input_tokens": cost.input_tokens,
                   "cached_input_tokens": cost.cached_input_tokens,
                   "cache_write_tokens": cost.cache_write_tokens,
                   "output_tokens": cost.output_tokens,
                   "mathpix_pages": cost.mathpix_pages,
                   "llm_cost_usd": cost.llm_cost_usd,
                   "mathpix_cost_usd": cost.mathpix_cost_usd,
                   "total_inr": cost.total_inr}, fh, indent=1)

    # costs.budget_for is the single source of truth for both department and size:
    # a large school science paper stays at Rs.15, while a matching CA/commerce paper
    # receives the Rs.20 tier.
    budget = budget_for(
        args.total, exam_type=cfg.exam_type, subject=args.subject)
    line = (f"AI {rep.total_score:g}/{rep.max_total:g}")
    if args.human is not None:
        line += f" vs human {args.human:g}/{args.total:g} | delta {rep.total_score-args.human:+g}"
    print(f"\n{line}\n  Rs.{cost.total_inr:.1f} (budget {budget:g}) "
          f"{'OVER' if cost.total_inr > budget else 'ok'} | "
          f"in {cost.input_tokens} cache_r {cost.cached_input_tokens} "
          f"cache_w {cost.cache_write_tokens} out {cost.output_tokens} | "
          f"mathpix {cost.mathpix_pages}p | {time.time()-t0:.0f}s", flush=True)
    # A run that prints only a score is how a broken copy looks ordinary. These are
    # the numbers that separate "marked" from "marked and usable".
    qs = list(rep.questions or [])
    answered = [q for q in qs if getattr(q, "attempted", True)]
    crits = [c for q in qs for c in (q.criteria or [])]
    anchored = [c for c in crits if (getattr(c, "step_line_id", "") or "").strip()]
    lost = [q for q in answered if q.score < q.max_score - 1e-9]
    issues = result.get("issues") or []
    blocked = blocking(issues)
    print(f"  questions {len(qs)} ({len(answered)} answered, "
          f"{len(qs) - len(answered)} blank)  criteria {len(crits)}  "
          f"anchored {len(anchored)}/{len(crits)}  "
          f"lost marks with no reason "
          f"{sum(1 for q in lost if not (q.remark or '').strip())}/{len(lost)}")
    print(f"  gate: {'RELEASED' if not blocked else 'HELD'} "
          f"({len(blocked)} blocking, {len(issues) - len(blocked)} warning)")
    for issue in blocked:
        print(f"    {issue}")


if __name__ == "__main__":
    main()
