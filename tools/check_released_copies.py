# -*- coding: utf-8 -*-
"""Check released copies the way a faculty reviewer would open them.

Run after a live batch to answer one question per copy: is there anything here a
reviewer would send back? Every check is a fault actually found on a real copy at
some point, so a clean table means the batch is worth their time.

    python tools/check_released_copies.py --dir faculty_review_20260905
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from remark_quality import fragment_problem, remark_quality_problem  # noqa: E402

STRIP_MIN_WIDTH = 175.0
STRIP_FRACTION = 0.24


def content_width(page_width: float) -> float:
    """The student's own writing area, before the marks strip was added."""
    # strip = max(0.24 * original, 175), so the original inverts by which arm won.
    wide = page_width / (1.0 + STRIP_FRACTION)
    return wide if wide >= STRIP_MIN_WIDTH / STRIP_FRACTION else page_width - STRIP_MIN_WIDTH


def is_red(color) -> bool:
    return bool(color) and color[0] > 0.6 and color[1] < 0.4 and color[2] < 0.4


def check_copy(slug: str, case_dir: str, official_total: float | None) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def add(name, ok, detail=""):
        out.append((name, bool(ok), str(detail)))

    status_path = os.path.join(case_dir, "status.json")
    if not os.path.isfile(status_path):
        add("the copy was graded at all", False, "no status.json")
        return out
    status = json.load(open(status_path, encoding="utf-8"))
    results = status.get("results") or []
    if not results:
        add("the copy was released", False, f"state={status.get('state')!r}")
        return out
    r = results[0]

    add("released, not held", r.get("release") is True,
        f"held_reasons={r.get('held_reasons')} error={r.get('error')}")
    add("no warning on the result", not (r.get("warning") or "").strip(),
        (r.get("warning") or "")[:200])

    score, max_score = r.get("score"), r.get("max")
    if official_total is not None:
        add("marked out of the paper's official total",
            max_score is not None and abs(float(max_score) - official_total) < 0.01,
            f"{max_score} vs official {official_total}")
    add("score is within the denominator",
        score is not None and max_score is not None and 0 <= float(score) <= float(max_score) + 1e-6,
        f"{score}/{max_score}")

    questions = r.get("questions") or []
    add("every question carries a score", all("score" in q for q in questions), len(questions))
    part_sum = sum(float(q.get("score") or 0) for q in questions)
    add("question scores add up to the total",
        score is not None and abs(part_sum - float(score)) < 0.05,
        f"parts {part_sum:g} vs total {score}")

    # Feedback a student would actually read.
    bad_quality = [f"{q.get('q')}: {remark_quality_problem(q.get('remark'))}"
                   for q in questions
                   if float(q.get("score") or 0) < float(q.get("max") or 0) - 1e-9
                   and remark_quality_problem(q.get("remark"))]
    add("no unusable deduction remark", not bad_quality, "; ".join(bad_quality[:4]))
    fragments = [f"{q.get('q')}: {q.get('remark')!r}" for q in questions
                 if fragment_problem(q.get("remark"))]
    add("no remark is a fragment naming nothing", not fragments, "; ".join(fragments[:3]))

    pdfs = [f for f in os.listdir(case_dir) if f.lower().endswith(".pdf")]
    if not pdfs:
        add("an evaluated PDF was produced", False, "no pdf in the case folder")
        return out
    doc = fitz.open(os.path.join(case_dir, pdfs[0]))
    add("an evaluated PDF was produced", doc.page_count > 0, f"{pdfs[0]} ({doc.page_count} pages)")

    text = "".join(p.get_text() for p in doc)
    exotic = sorted({c for c in text if ord(c) > 255})
    add("every character on the copy is drawable", not exotic, "".join(exotic)[:40])

    qids = [str(q.get("q") or "") for q in questions]
    missing = [q for q in qids if q and q not in text]
    add("every scored question is named on the copy", not missing, ", ".join(missing[:8]))

    # Marks on the student's own answer, not only stacked in the margin.
    on_answer = in_strip = 0
    for i in range(doc.page_count):
        page = doc[i]
        edge = content_width(page.rect.width)
        for dr in page.get_drawings():
            if not is_red(dr.get("color") or dr.get("fill")):
                continue
            rect = dr["rect"]
            if rect.width > page.rect.width * 0.5 or rect.height > page.rect.height * 0.5:
                continue  # the strip's own border rule, not a mark
            if rect.x1 <= edge + 2:
                on_answer += 1
            elif rect.x0 >= edge - 2:
                in_strip += 1
    add("marks are drawn on the student's answer", on_answer > 0,
        f"on-answer {on_answer}, strip {in_strip}")


    # WHAT A READER SEES, not just what the numbers add up to. Both of the faults a
    # reviewer caught by eye - a cover listing "Q1, Q1b, Q1a, Q1c" and a strip reading
    # "Q8, Q6, Q4, Q2" - passed every check above, because none of them looked at the
    # ORDER of anything or at whether two different questions can be told apart.
    def qid_key(qid):
        import re as _re
        m = _re.match(r"\s*[Qq]?\s*(\d+)", qid or "")
        suffix = "".join(c for c in (qid or "") if c.isalpha()).lower().lstrip("q")
        return (int(m.group(1)) if m else 9999, suffix)

    cover = doc[0].get_text()
    # ONLY the question-wise totals column. "Where marks were lost" repeats the same
    # qids with reasons after them, and matching those too made the order check fail
    # on a cover that was in fact correctly ordered.
    listed = re.findall(r"^(Q[\w.]+):\s*[\d.]+\s*/", cover, re.MULTILINE)
    add("the cover lists questions in paper order",
        listed == sorted(listed, key=qid_key)
        or _sections_then_order(listed, qid_key),
        " ".join(listed[:14]))

    # A paper that numbers two parts from 1 (CA: Part I MCQ Q1-Q8, Part II Q1(a)-Q4(b))
    # must say which is which, or "Q1: 2/2" reads as a part of "Q1a: 1/5".
    bare = {q for q in listed if q[1:].isdigit()}
    subbed = {q for q in listed if not q[1:].isdigit()}
    collide = {q for q in subbed
               if any(b for b in bare if qid_key(b)[0] == qid_key(q)[0])}
    add("sections are named when two parts share a number",
        not collide or ("Objective" in cover and "Descriptive" in cover),
        f"{sorted(collide)[:6]} share a number with an MCQ but the cover names no section")

    # Strip verdicts, page by page, must read down in question order.
    for i in range(doc.page_count):
        page = doc[i]
        edge = content_width(page.rect.width)
        rows = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp in l["spans"]:
                    t = sp["text"].strip()
                    if sp["bbox"][0] >= edge - 2 and re.match(r"^Q[\w.]+\s", t + " "):
                        rows.append((round(sp["bbox"][1]), t.split()[0]))
        # Objective verdicts only. A descriptive entry sits beside its own answer, and
        # a student who writes 1(b) before 1(a) SHOULD get a strip that reads Q1b then
        # Q1a - it is following their sheet, which is the point. Checking those for
        # question order reported a correct copy as broken.
        order = [q for _y, q in sorted(rows) if q[1:].isdigit()]
        if len(order) > 1:
            add(f"page {i + 1} objective verdicts read in question order",
                order == sorted(order, key=qid_key), " ".join(order[:12]))
    # ...and the whole objective block belongs on one page, next to the answers.
    obj_pages = {}
    for i in range(doc.page_count):
        page = doc[i]
        edge = content_width(page.rect.width)
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp in l["spans"]:
                    t = sp["text"].strip()
                    if sp["bbox"][0] >= edge - 2 and re.match(r"^Q\d+\s", t + " "):
                        obj_pages.setdefault(i + 1, []).append(t.split()[0])
    strays = [p for p, qs in obj_pages.items() if len(qs) == 1] if len(obj_pages) > 1 else []
    add("objective verdicts are not scattered one per page", not strays,
        f"single verdict stranded on page(s) {strays}")
    doc.close()
    return out


def _sections_then_order(listed, qid_key) -> bool:
    """True when the list is ordered within each part, MCQs first then sub-parts."""
    bare = [q for q in listed if q[1:].isdigit()]
    subbed = [q for q in listed if not q[1:].isdigit()]
    return (listed == bare + subbed
            and bare == sorted(bare, key=qid_key)
            and subbed == sorted(subbed, key=qid_key))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--totals", default="",
                    help="slug=total,slug=total — the paper's official marks")
    args = ap.parse_args()

    totals = {}
    for pair in args.totals.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            totals[k.strip()] = float(v)

    slugs = sorted(d for d in os.listdir(args.dir)
                   if os.path.isdir(os.path.join(args.dir, d)))
    failures = 0
    for slug in slugs:
        print("=" * 78)
        print(slug)
        for name, ok, detail in check_copy(slug, os.path.join(args.dir, slug),
                                           totals.get(slug)):
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
                  + (f" — {detail}" if detail and not ok else
                     (f"  ({detail})" if detail and "on-answer" in detail else "")))
            failures += 0 if ok else 1
    print("=" * 78)
    print(f"{len(slugs)} copies checked, {failures} problem(s)")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
