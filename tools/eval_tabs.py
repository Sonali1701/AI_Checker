# -*- coding: utf-8 -*-
"""Grade the CA / Commerce sample tabs and compare against the human evaluator.

    # offline: build the ground-truth table, no token needed
    python tools/eval_tabs.py --tab CA --prepare

    # then grade
    python tools/eval_tabs.py --tab CA --token-file token.txt --jobs 3

The CA and Commerce worksheets of "Sample Files_ AI Tool Review.xlsx" carry the
question paper and solution links as well as the student and evaluated PDFs, so each
row can be graded the way the app does it: scheme extracted from the paper, official
total enforced, moderation against the solution.

The human's marks are not a column — they are written on the evaluated PDF, so they
are read out of its text layer ("40/50").
"""
from __future__ import annotations

import argparse
import dataclasses
import csv
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import fitz
import openpyxl
import requests

import pw_access
from grader import pdf_or_image_to_pngs, pdf_text_layer
from costs import budget_for
from pipeline import cfg_from_request, detect_marks_scheme, grade_sheet
from server import _make_orienter

DEFAULT_EXCEL = os.path.expanduser("~/Downloads/Sample Files_ AI Tool Review.xlsx")
DOWNLOADS = os.path.expanduser("~/Downloads")

# Column aliases — the two tabs spell the same things differently
# ("Test Q Link" vs "Question Paper Link", "Student't File" vs "Student File").
COLS = {
    "group": ("Category", "Class"),
    "series": ("test_series_name", "Test Series Name"),
    "test": ("test_name with subject", "Test Name with Subject"),
    "qp": ("Test Q Link", "Question Paper Link"),
    "key": ("Test Answer Link", "Solution Link"),
    "student": ("Student File", "Student't File", "Student File "),
    "evaluator": ("Evaluator  file", "Evaluated File", "Evaluator file"),
}

_DRIVE_RE = re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)")
_FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
# "29/100, Need to work more on learning the concepts" — an overall quality rating,
# not the paper total. The comma + words after it is what distinguishes it from a mark.
_RATING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*100\s*,\s*[A-Za-z]")


def direct_url(link: str) -> str:
    """Turn a share link into something requests can fetch. '' if not a URL."""
    link = (link or "").strip()
    if not link.startswith("http"):
        return ""
    m = _DRIVE_RE.search(link)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return link


def local_copy(name: str) -> str:
    """Some rows name a file instead of linking it; look for it in Downloads."""
    name = (name or "").strip()
    if not name or name.startswith("http"):
        return ""
    direct = os.path.join(DOWNLOADS, name)
    if os.path.exists(direct):
        return direct
    stem = os.path.splitext(name)[0][:40].lower()
    for entry in os.listdir(DOWNLOADS):
        if entry.lower().startswith(stem) and entry.lower().endswith(".pdf"):
            return os.path.join(DOWNLOADS, entry)
    return ""


def fetch(link: str, cache_dir: str, tag: str) -> bytes:
    """Download (with a small on-disk cache) or read a local file. b'' if unavailable."""
    path = os.path.join(cache_dir, tag + ".pdf")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return open(path, "rb").read()
    url = direct_url(link)
    data = b""
    if url:
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                data = r.content
        except requests.RequestException:
            data = b""
    if not data:
        local = local_copy(link)
        if local:
            data = open(local, "rb").read()
    if data:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return data


def human_marks(pdf_bytes: bytes) -> tuple[float | None, float | None, list[str]]:
    """Read the evaluator's grand total off their PDF.

    PW evaluated copies write per-question marks and one grand total as "40/50". The
    grand total is the fraction with the LARGEST denominator (the paper's full marks);
    per-question fractions all have smaller ones.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None, None, []
    seen: list[tuple[float, float]] = []
    pages: list[str] = []
    for page in doc:
        body = page.get_text() or ""
        pages.append(body)
        for got, out_of in _FRACTION_RE.findall(body):
            g, o = float(got), float(out_of)
            if 0 < o <= 200 and g <= o:
                seen.append((g, o))
    doc.close()
    text = "\n".join(pages)
    if not seen:
        return None, None, []
    # Some evaluators also write an overall QUALITY RATING out of 100 followed by a
    # comment — "29/100, Need to work more on learning the concepts" — on a paper that
    # is really out of 30. Taking the largest denominator picks the rating and silently
    # grades the whole booklet against the wrong total, so drop those.
    ratings = {float(v) for v in _RATING_RE.findall(text)}
    if ratings:
        other = [(g, o) for g, o in seen if not (o == 100 and g in ratings)]
        if other and max(o for _, o in other) != 100:
            seen = other
    best_total = max(o for _, o in seen)
    for g, o in seen:
        if o == best_total:
            return g, o, [f"{a:g}/{b:g}" for a, b in seen[:14]]
    return None, None, []


def load_tab(path: str, tab: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    if tab not in wb.sheetnames:
        sys.exit(f"No worksheet {tab!r}. Available: {wb.sheetnames}")
    rows = list(wb[tab].iter_rows(values_only=True))
    header = [str(c or "").strip() for c in rows[0]]

    def pick(row, key):
        for name in COLS[key]:
            if name in header:
                value = row[header.index(name)]
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    out = []
    for row in rows[1:]:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        record = {k: pick(row, k) for k in COLS}
        if not record["student"].startswith("http"):
            continue
        out.append(record)
    return out


# CA Final papers name the subject only in the series ("... Nov 2026 - IDT").
_PAPER_ABBREV = {
    "dt": "Direct Tax", "idt": "Indirect Tax", "fr": "Financial Reporting",
    "afm": "Advanced Financial Management", "aaa": "Advanced Auditing",
    "scmpe": "Strategic Cost Management and Performance Evaluation",
    "sfm": "Strategic Financial Management", "law": "Corporate Law",
    "audit": "Auditing", "gst": "GST", "fm": "Financial Management",
    "sm": "Strategic Management", "eis": "Enterprise Information Systems",
}
_TEST_LABEL_RE = re.compile(
    r"(?i)\b(?:master|unit)\s+test(?:\s+series)?\s*[-–—:]?\s*\d*|\btest\s*\d+\b")
_BRACKET_TAIL_RE = re.compile(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*$")


def subject_of(test_name: str, series: str = "") -> str:
    """Best-effort subject for a row.

    'Master Test Series 01 : Accounting'            -> 'Accounting'
    'Master Test Series 01 (Advanced Accounting)'   -> 'Advanced Accounting'
    'Cost and Management Accounting Master Test 05' -> 'Cost and Management Accounting'
    'Master Test 01' + series '... Nov 2026 - IDT'  -> 'Indirect Tax'
    """
    text = (test_name or "").strip()
    bracket = _BRACKET_TAIL_RE.search(text)
    if bracket:
        text = bracket.group(1)
    elif ":" in text:
        text = text.split(":", 1)[1]
    text = _TEST_LABEL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:()")
    if text and not re.fullmatch(r"[\d\s.]+", text):
        return text
    # Nothing usable in the test name — the series carries the paper code.
    tail = re.split(r"[-–—]", series or "")[-1].strip()
    key = re.sub(r"[^a-z]", "", tail.lower())
    if key in _PAPER_ABBREV:
        return _PAPER_ABBREV[key]
    if tail and not re.fullmatch(r"[\d\s.]+", tail):
        return tail
    return (test_name or "Unknown").strip()


def load_previous_run(directory: str) -> list[dict]:
    """Rebuild the graded rows from an earlier run's results.csv + its files/ cache.

    Lets a second model be A/B'd against the same booklets and the same human marks
    without the Excel or any downloads.
    """
    csv_path = os.path.join(directory, "results.csv")
    files = os.path.join(directory, "files")
    if not os.path.exists(csv_path):
        sys.exit(f"No results.csv in {directory}")
    out = []
    for row in csv.DictReader(open(csv_path, encoding="utf-8")):
        if not row.get("human") or not row.get("paper_total"):
            continue
        index = int(row["row"])

        def cached(kind: str) -> bytes:
            path = os.path.join(files, f"{index:02d}_{kind}.pdf")
            return open(path, "rb").read() if os.path.exists(path) else b""

        student = cached("student")
        if not student:
            continue
        out.append({
            "index": index, "group": row["group"], "subject": row["subject"],
            "test": row.get("test", ""), "human": float(row["human"]),
            "paper_total": float(row["paper_total"]), "student_bytes": student,
            "qp_bytes": cached("qp"), "key_bytes": cached("key"),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--excel", default=DEFAULT_EXCEL)
    ap.add_argument("--tab", default="CA", help="CA or Commerce")
    ap.add_argument("--n", type=int, default=0, help="limit rows (0 = all)")
    ap.add_argument("--rows", default="", help="grade only these row numbers, e.g. 3 or 0,3,5")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--fanout", action="store_true",
                    help="grade a page-window at a time instead of one call per "
                         "paper (the A/B arm for per-question grading)")
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--model", default="", help="override the grading model")
    ap.add_argument("--provider", default="claude", choices=("claude",),
                    help="grading provider (claude bills only visible output — no hidden thinking)")
    ap.add_argument("--prepare", action="store_true",
                    help="download the files and read the human marks; grade nothing")
    ap.add_argument("--no-orient", action="store_true",
                    help="skip the orientation pass the app applies")
    ap.add_argument("--reuse", default="",
                    help="skip the Excel: take the rows and human marks from a previous "
                         "run's results.csv and its cached files/ (for A/B-ing models)")
    args = ap.parse_args()

    out_dir = args.out or f"eval_{args.tab.lower()}_out"
    cache = os.path.join(out_dir, "files")
    os.makedirs(cache, exist_ok=True)

    if args.reuse:
        prepared = load_previous_run(args.reuse)
        if args.rows:
            wanted = {int(x) for x in args.rows.replace(" ", "").split(",") if x}
            prepared = [p for p in prepared if p["index"] in wanted]
        if args.n:
            prepared = prepared[: args.n]
        print(f"reusing {len(prepared)} rows + cached files from {args.reuse}\n")
        for p in prepared:
            print(f"[{p['index']:02d}] {p['group']:<14} {p['subject'][:30]:<30} "
                  f"qp={'ok' if p['qp_bytes'] else '-':<3} key={'ok' if p['key_bytes'] else '-':<3} "
                  f"human={p['human']:g}/{p['paper_total']:g}")
        return grade_all(prepared, args, out_dir)

    rows = load_tab(args.excel, args.tab)
    if args.n:
        rows = rows[: args.n]
    print(f"{args.tab}: {len(rows)} rows with a student file\n")

    # ---- Phase 1: assemble the material and the ground truth (no token needed) ----
    prepared = []
    for i, row in enumerate(rows):
        tag = f"{i:02d}"
        student = fetch(row["student"], cache, tag + "_student")
        evaluator = fetch(row["evaluator"], cache, tag + "_human")
        qp = fetch(row["qp"], cache, tag + "_qp")
        key = fetch(row["key"], cache, tag + "_key")
        got, total, samples = human_marks(evaluator) if evaluator else (None, None, [])
        prepared.append({**row, "index": i, "student_bytes": student, "qp_bytes": qp,
                         "key_bytes": key, "human": got, "paper_total": total,
                         "subject": subject_of(row["test"], row["series"])})
        print(f"[{tag}] {row['group']:<14} {subject_of(row["test"], row["series"])[:30]:<30} "
              f"student={'ok' if student else 'MISSING':<7} qp={'ok' if qp else '-':<3} "
              f"key={'ok' if key else '-':<3} "
              f"human={f'{got:g}/{total:g}' if got is not None else 'NOT FOUND'}")
        if got is None and evaluator:
            print(f"       fractions seen on the evaluated copy: {samples}")

    usable = [p for p in prepared if p["student_bytes"] and p["human"] is not None]
    print(f"\ngradeable rows (student file + human marks): {len(usable)}/{len(prepared)}")
    if args.prepare:
        print("--prepare: stopping before grading.")
        return

    return grade_all(usable, args, out_dir)


def scorecard(rep, issues, cost, budget) -> dict:
    """What a marked copy has to get right, beyond the grand total.

    The delta against the human total was the only thing this harness measured, and
    a badly broken copy can satisfy it by coincidence: a Class 12 Chemistry sheet
    scored 25/70 with twenty answered sub-parts filed as unattempted, and 25 was
    close enough to the human's mark to look like an ordinary row. Every column
    below would have failed on that copy.
    """
    qs = list(rep.questions or [])
    answered = [q for q in qs if getattr(q, "attempted", True)]
    lost = [q for q in answered if q.score < q.max_score - 1e-9]
    anchored = [q for q in answered if (getattr(q, "anchor_line_id", "") or "").strip()]
    crits = [c for q in qs for c in (q.criteria or [])]
    with_ev = [c for c in crits if (getattr(c, "evidence", "") or "").strip()]
    blocking = [i for i in (issues or []) if getattr(i, "severity", "") == "block"]
    false_blanks = list(getattr(rep, "false_blank_qids", None) or [])
    return {
        # answers the marker never read — 0 is the only acceptable value
        "false_blanks": len(false_blanks),
        "false_blank_marks": round(sum(q.max_score for q in qs
                                       if q.qid in set(false_blanks)), 2),
        # per-step evidence: without it a student is told a number and nothing else
        "criteria": len(crits),
        "questions_lost_marks": len(lost),
        "lost_with_no_reason": sum(1 for q in lost if not (q.remark or "").strip()),
        "evidence_share": round(len(with_ev) / len(crits), 2) if crits else 0.0,
        # placement: an unanchored mark is positioned by an estimated page fraction
        "anchored_share": round(len(anchored) / len(answered), 2) if answered else 0.0,
        # the free release gate
        "blocking": len(blocking),
        "released": not blocking,
        "over_budget": cost.total_inr > budget,
    }


def grade_all(usable: list[dict], args, out_dir: str) -> None:
    """Grade the prepared rows; report AI-vs-human and cost against the budget."""
    token = args.token or os.environ.get("PW_GOOGLE_TOKEN", "")
    if not token and args.token_file:
        token = open(args.token_file, encoding="utf-8").read().strip()
    if not token:
        sys.exit("No token. Pass --token/--token-file, or use --prepare for the offline phase.")
    if not pw_access.check_allowed(token):
        sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")

    # ---- Phase 2: grade ----
    def run(p: dict) -> dict:
        label = f"[{p['index']:02d}] {p['subject'][:28]}"
        base = {"row": p["index"], "group": p["group"], "subject": p["subject"],
                "test": p["test"], "human": p["human"], "paper_total": p["paper_total"]}
        try:
            cfg = cfg_from_request(
                args.provider, args.model, p["subject"], google_token=token,
                task_id=pw_access.new_task_id("eval-tab"),
                student_class=p["group"], total_marks=p["paper_total"],
            )
            # A/B: --fanout grades a page-window at a time instead of the whole
            # paper in one call. Run the sweep twice into different --out dirs and
            # diff the scorecards; the flag is the only thing that changes.
            cfg = dataclasses.replace(cfg, fanout=bool(args.fanout),
                                      cache_ttl="1h" if len(usable) > 2 else "5m")
            # Straighten sideways photos exactly the way the app does, using server.py's
            # OWN orienter. This sweep did NOT do this for a long time, which silently
            # invalidated every number it produced: a rotated page returns no OCR lines
            # at all, so the model grades a booklet it can only half read. On the CA
            # Taxation booklet that difference was worth ~20 marks out of 100.
            sa_bytes = p["student_bytes"]
            qp_bytes, key_bytes = p["qp_bytes"], p["key_bytes"]
            if not args.no_orient:
                orient = _make_orienter(cfg)
                sa_bytes = orient(sa_bytes, f"{p['index']:02d}.pdf")
                if qp_bytes:
                    qp_bytes = orient(qp_bytes, "qp.pdf")
                if key_bytes:
                    key_bytes = orient(key_bytes, "key.pdf")

            qp_pngs = pdf_or_image_to_pngs(qp_bytes, "qp.pdf") if qp_bytes else []
            qp_text = pdf_text_layer(qp_bytes, "qp.pdf") if qp_bytes else ""
            ak_pngs = pdf_or_image_to_pngs(key_bytes, "key.pdf") if key_bytes else None
            ak_text_layer = pdf_text_layer(key_bytes, "key.pdf") if key_bytes else ""
            ak_text = None if ak_pngs else (
                "No official solution was supplied. Mark against standard expected answers "
                "for this subject, awarding step-wise credit for correct working.")
            scheme = None
            if qp_bytes:
                try:
                    found, method = detect_marks_scheme(
                        qp_bytes, "qp.pdf", cfg, p["group"], p["subject"],
                        total_marks=p["paper_total"])
                    if found is not None and method in ("regex", "sections", "ai"):
                        scheme = found
                except Exception as exc:
                    print(f"{label}: scheme extraction failed ({exc})", flush=True)
            t0 = time.time()
            result = grade_sheet(
                qp_pngs=qp_pngs, sa_bytes=sa_bytes, filename=f"{p['index']:02d}.pdf",
                ak_pngs=ak_pngs, ak_text=ak_text, marks_scheme=scheme, cfg=cfg,
                student_class=p["group"], subject=p["subject"], use_mathpix=True,
                total_marks=p["paper_total"], qp_text=qp_text, ak_text_layer=ak_text_layer,
            )
            rep, cost = result["report"], result["cost"]
            with open(os.path.join(out_dir, f"{p['index']:02d}_ai.pdf"), "wb") as fh:
                fh.write(result["pdf_bytes"])
            with open(os.path.join(out_dir, f"{p['index']:02d}_report.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(rep.model_dump_json(indent=1))
            delta = rep.total_score - p["human"]
            budget = budget_for(
                p["paper_total"], exam_type=cfg.exam_type, subject=p["subject"])
            card = scorecard(rep, result.get("issues"), cost, budget)
            print(f"{label}: AI {rep.total_score:g}/{rep.max_total:g} vs human "
                  f"{p['human']:g}/{p['paper_total']:g} | delta {delta:+g} | "
                  f"Rs.{cost.total_inr:.1f} (budget {budget:g}) "
                  f"{'OVER' if cost.total_inr > budget else 'ok'} | {time.time()-t0:.0f}s",
                  flush=True)
            print(f"{label}:   {'RELEASED' if card['released'] else 'HELD'} | "
                  f"missed answers {card['false_blanks']} ({card['false_blank_marks']:g} "
                  f"marks) | criteria {card['criteria']} | anchored "
                  f"{card['anchored_share']:.0%} | evidence {card['evidence_share']:.0%} "
                  f"| no reason given {card['lost_with_no_reason']}/"
                  f"{card['questions_lost_marks']}", flush=True)
            return {**base, **card, "ai": rep.total_score, "ai_max": rep.max_total,
                    "delta": round(delta, 2), "cost_inr": round(cost.total_inr, 2),
                    "budget": budget, "model": cost.model,
                    "input_tokens": cost.input_tokens, "output_tokens": cost.output_tokens,
                    "had_qp": bool(p["qp_bytes"]), "had_key": bool(p["key_bytes"]), "error": ""}
        except Exception as exc:
            print(f"{label}: FAILED {type(exc).__name__}: {exc}", flush=True)
            return {**base, "error": f"{type(exc).__name__}: {exc}"}

    print(f"\ngrading {len(usable)} rows, {args.jobs} at a time...\n")
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(run, usable))

    fields = ["row", "group", "subject", "test", "human", "paper_total", "ai", "ai_max",
              "delta", "released", "blocking", "false_blanks", "false_blank_marks",
              "criteria", "questions_lost_marks", "lost_with_no_reason",
              "evidence_share", "anchored_share",
              "cost_inr", "budget", "over_budget", "model",
              "input_tokens", "output_tokens", "had_qp", "had_key", "error"]
    csv_path = os.path.join(out_dir, "results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in fields} for r in results])

    ok = [r for r in results if not r.get("error")]
    if ok:
        mae = sum(abs(r["delta"]) for r in ok) / len(ok)
        bias = sum(r["delta"] for r in ok) / len(ok)
        cost = sum(r["cost_inr"] for r in ok) / len(ok)
        over = [r for r in ok if r["cost_inr"] > r["budget"]]
        print(f"\n{'='*70}\nGraded {len(ok)}/{len(results)}")
        print(f"  mean |delta| {mae:.1f} marks   mean bias {bias:+.1f} "
              f"(+ = AI over-awards)")
        print(f"  mean cost Rs.{cost:.1f}   over budget: {len(over)}/{len(ok)}")
        by_subject: dict[str, list[dict]] = {}
        for r in ok:
            by_subject.setdefault(r["subject"], []).append(r)
        for subject, rs in sorted(by_subject.items()):
            print(f"    {subject[:30]:<32} n={len(rs)} "
                  f"bias {sum(x['delta'] for x in rs)/len(rs):+.1f} "
                  f"Rs.{sum(x['cost_inr'] for x in rs)/len(rs):.1f} "
                  f"deltas {[x['delta'] for x in rs]}")
    print(f"\nDetails: {csv_path}")


if __name__ == "__main__":
    main()
