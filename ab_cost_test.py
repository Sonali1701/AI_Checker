"""A/B cost-vs-quality harness: is Flash + more thinking as good as Pro, for less money?

Grades the SAME student sheets under several (model, thinking_budget) variants and reports,
per sheet and in aggregate: total-score divergence from the baseline, the worst per-question
divergence, the token cost, and the % cost vs baseline. Use it to decide whether the
science/maths subjects can move off Pro without losing grading quality.

USAGE
  1. Set your real key (the one on Render):  set GOOGLE_API_KEY=...    (PowerShell: $env:GOOGLE_API_KEY="...")
  2. Copy ab_config.json -> ab_config.json and fill in your paths.
  3. python ab_cost_test.py ab_config.json

Notes
  - This calls the paid API once per (sheet x variant). Keep the sheet list small (2-4) at first.
  - Baseline is the FIRST variant in the config (put Pro-4096 first).
  - It does NOT touch the production grading path; it's a standalone measurement tool.
"""
from __future__ import annotations

import json
import os
import sys
import time

try:                                  # load GOOGLE_API_KEY etc. from a local .env, like app.py
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                     # noqa: BLE001 — dotenv optional; env vars still work
    pass

from costs import compute_cost
from grader import marks_scheme_from_pdf, pdf_or_image_to_pngs
from grader_gemini import grade_answer_sheet

DEFAULT_VARIANTS = [
    {"label": "Pro-4096",    "model": "gemini-2.5-pro",   "thinking_budget": 4096},
    {"label": "Flash-8192",  "model": "gemini-2.5-flash", "thinking_budget": 8192},
    {"label": "Flash-12288", "model": "gemini-2.5-flash", "thinking_budget": 12288},
]


def _load_pngs(path):
    if not path:
        return None
    data = open(path, "rb").read()
    return pdf_or_image_to_pngs(data, os.path.basename(path))


def _q_scores(report) -> dict[str, float]:
    return {str(q.qid): float(q.score) for q in report.questions}


def _q_remarks(report) -> dict[str, str]:
    return {str(q.qid): (getattr(q, "remark", "") or "") for q in report.questions}


def _grade_one(cfg, qp, ak_pngs, ak_text, scheme, variant, key, use_vertex):
    usage: dict = {}
    t0 = time.perf_counter()
    report = grade_answer_sheet(
        question_paper_pngs=qp,
        student_pngs=variant["_sa_pngs"],
        answer_key_pngs=ak_pngs, answer_key_text=ak_text or None,
        marks_scheme=scheme,
        model=variant["model"], api_key=key, use_vertex=use_vertex,
        student_class=cfg.get("student_class"), subject=cfg.get("subject"),
        usage_out=usage, thinking_budget=variant.get("thinking_budget"),
    )
    dt = time.perf_counter() - t0
    cost = compute_cost(usage)
    return report, cost, dt


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("ERROR: set GOOGLE_API_KEY first."); sys.exit(1)
    use_vertex = key.startswith("AQ") or str(os.environ.get("GEMINI_USE_VERTEX", "")).lower() in ("1", "true", "yes")

    qp = _load_pngs(cfg["question_paper"])
    ak_pngs = _load_pngs(cfg.get("answer_key"))
    ak_text = cfg.get("answer_key_text") or ""
    scheme = None
    ms_from = cfg.get("marks_scheme_from") or ""
    if ms_from == "qp":
        scheme = marks_scheme_from_pdf(open(cfg["question_paper"], "rb").read(), os.path.basename(cfg["question_paper"]))
    elif ms_from:
        scheme = marks_scheme_from_pdf(open(ms_from, "rb").read(), os.path.basename(ms_from))
    variants = cfg.get("variants") or DEFAULT_VARIANTS
    sheets = cfg["student_sheets"]
    print(f"subject={cfg.get('subject')} class={cfg.get('student_class')} "
          f"sheets={len(sheets)} variants={[v['label'] for v in variants]} vertex={use_vertex}\n")

    results = {}   # sheet -> variant_label -> {total, max, qs, cost_inr, sec}
    for sheet in sheets:
        sa_pngs = _load_pngs(sheet)
        name = os.path.basename(sheet)
        results[name] = {}
        for v in variants:
            v = {**v, "_sa_pngs": sa_pngs}
            try:
                report, cost, dt = _grade_one(cfg, qp, ak_pngs, ak_text, scheme, v, key, use_vertex)
                results[name][v["label"]] = {
                    "total": float(report.total_score), "max": float(report.max_total),
                    "qs": _q_scores(report), "qr": _q_remarks(report), "cost_inr": cost.total_inr,
                    "in_tok": cost.input_tokens, "out_tok": cost.output_tokens, "sec": round(dt, 1),
                }
                print(f"  {name:28s} {v['label']:14s} "
                      f"score={report.total_score:5.1f}/{report.max_total:<5.1f} "
                      f"cost=Rs{cost.total_inr:6.3f} out_tok={cost.output_tokens:6d} {dt:5.1f}s")
            except Exception as e:  # noqa: BLE001
                results[name][v["label"]] = {"error": str(e)}
                print(f"  {name:28s} {v['label']:14s} ERROR: {e}")

    # ---- Comparison vs baseline (first variant) ----
    base = variants[0]["label"]
    print(f"\n=== DIVERGENCE vs baseline ({base}) — lower is better ===")
    agg = {v["label"]: {"abs_total": [], "worst_q": [], "cost": []} for v in variants}
    for name, byv in results.items():
        b = byv.get(base, {})
        if "qs" not in b:
            continue
        for v in variants:
            r = byv.get(v["label"], {})
            if "qs" not in r:
                continue
            total_diff = r["total"] - b["total"]
            common = set(b["qs"]) & set(r["qs"])
            worst_q = max((abs(r["qs"][q] - b["qs"][q]) for q in common), default=0.0)
            agg[v["label"]]["abs_total"].append(abs(total_diff))
            agg[v["label"]]["worst_q"].append(worst_q)
            agg[v["label"]]["cost"].append(r["cost_inr"])
            if v["label"] != base:
                print(f"  {name:28s} {v['label']:14s} total {total_diff:+.1f}  worst-question {worst_q:.1f}")
                # Show the single most-divergent question with both models' score + remark,
                # so we can tell "graded differently" from "dropped the question entirely".
                if common:
                    qid = max(common, key=lambda q: abs(r["qs"][q] - b["qs"][q]))
                    br = (b.get("qr", {}) or {}).get(qid, "")
                    vr = (r.get("qr", {}) or {}).get(qid, "")
                    print(f"      worst={qid}: {base} {b['qs'][qid]:.1f} «{br[:70]}»")
                    print(f"      worst={qid}: {v['label']} {r['qs'][qid]:.1f} «{vr[:70]}»")

    print(f"\n=== AGGREGATE (avg over {len(results)} sheets) ===")
    base_cost = (sum(agg[base]["cost"]) / len(agg[base]["cost"])) if agg[base]["cost"] else 0.0
    for v in variants:
        a = agg[v["label"]]
        if not a["cost"]:
            print(f"  {v['label']:14s} (no successful runs)"); continue
        avg_cost = sum(a["cost"]) / len(a["cost"])
        avg_abs = sum(a["abs_total"]) / len(a["abs_total"]) if a["abs_total"] else 0.0
        avg_worst = sum(a["worst_q"]) / len(a["worst_q"]) if a["worst_q"] else 0.0
        pct = (avg_cost / base_cost * 100) if base_cost else 100.0
        print(f"  {v['label']:14s} avg_cost=Rs{avg_cost:6.3f} ({pct:5.1f}% of baseline)  "
              f"avg|total diff|={avg_abs:.2f}  avg worst-question={avg_worst:.2f}")

    out = sys.argv[1].replace(".json", "_results.json")
    json.dump(results, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\nFull results -> {out}")
    print("Read it as: if a Flash variant's avg|total diff| and worst-question are ~0, it matches Pro — switch and save the % shown.")


if __name__ == "__main__":
    main()
