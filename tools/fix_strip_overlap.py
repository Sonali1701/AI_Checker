# -*- coding: utf-8 -*-
"""Repair an already-rendered evaluated PDF whose margin remark sits on top of a
wrapped score calculation.

The renderer bug is fixed (pdf_renderer._wrap_to_width is now shared by the code
that draws a strip entry and the code that reserves its height), but copies that
were already produced still carry the overlap. This nudges the remark block down
below the calculation it collided with. Text only — the scanned page image and
every tick on it are left untouched.
"""
import argparse
import re
import sys

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QID_RE = re.compile(r"^Q\d")
REMARK_SIZE_MAX = 14.8      # strip head/calc render at 15.0, remarks at 14.5
LINE_FACTOR = 1.35          # matches _draw_handwritten_remark's line_gap ratio


def entries_on(page: fitz.Page) -> list[dict]:
    """Group the marks-strip text into per-question entries."""
    strip_x = page.rect.width * 0.72
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for sp in line["spans"]:
                if sp["bbox"][0] >= strip_x and "Kalam" in sp["font"]:
                    spans.append({
                        "text": sp["text"], "x": sp["origin"][0], "y": sp["origin"][1],
                        "size": sp["size"], "bbox": fitz.Rect(sp["bbox"]),
                        "color": fitz.sRGB_to_pdf(sp["color"]),
                    })
    spans.sort(key=lambda s: s["y"])
    entries, cur = [], None
    for s in spans:
        if QID_RE.match(s["text"].strip()):
            cur = {"head": s, "calc": [], "remark": []}
            entries.append(cur)
        elif cur is not None:
            (cur["calc"] if s["size"] > REMARK_SIZE_MAX else cur["remark"]).append(s)
    return entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    fixes = []
    for pno in range(len(doc)):
        page = doc[pno]
        for e in entries_on(page):
            if not e["calc"] or not e["remark"]:
                continue
            last_calc = max(c["y"] for c in e["calc"])
            first_rem = min(r["y"] for r in e["remark"])
            gap = e["remark"][0]["size"] * LINE_FACTOR
            need = last_calc + gap
            if first_rem >= need - 0.5:
                continue
            shift = need - first_rem
            bottom = max(r["y"] for r in e["remark"]) + shift
            if bottom > page.rect.height - 6:
                print(f"  page {pno+1} {e['head']['text'].strip()}: SKIPPED — "
                      f"shifting {shift:.1f}pt would run off the page")
                continue
            fixes.append((pno, e, shift))
            print(f"  page {pno+1} {e['head']['text'].strip():<6} "
                  f"remark moved down {shift:.1f}pt "
                  f"({len(e['remark'])} lines, below calc at y={last_calc:.1f})")

    if not fixes:
        print("No overlaps found.")
        return
    if args.dry_run:
        print(f"\n{len(fixes)} entr{'y' if len(fixes)==1 else 'ies'} would be repaired.")
        return

    # Erase the whole colliding group — the CALCULATION as well as the remark. The
    # two overlap by definition, so a box big enough to clear the remark's cursive
    # also clips the calculation's glyphs; erasing only the remark left the strip
    # missing its "0.5+0+0.5+0.5+0+ / 0.5=2/3" line entirely. Both are redrawn below.
    # images=NONE so the scanned sheet and its ticks are never touched.
    for pno, e, _shift in fixes:
        page = doc[pno]
        for sp in e["calc"] + e["remark"]:
            box = fitz.Rect(sp["bbox"])
            box.x1 = page.rect.width - 2      # cursive overhangs its reported bbox
            box.y0 -= 3
            box.y1 += 4
            page.add_redact_annot(box)
    for pno in {p for p, _e, _s in fixes}:
        doc[pno].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    # Redraw: the calculation exactly where it was, the remark shifted clear of it.
    for pno, e, shift in fixes:
        page = doc[pno]
        page.insert_font(fontname="kalam", fontfile="Kalam-Regular.ttf")
        for c in e["calc"]:
            page.insert_text((c["x"], c["y"]), c["text"],
                             fontsize=c["size"], color=c["color"], fontname="kalam")
        for r in e["remark"]:
            page.insert_text((r["x"], r["y"] + shift), r["text"],
                             fontsize=r["size"], color=r["color"], fontname="kalam")

    out = args.out or args.pdf.replace(".pdf", "_fixed.pdf")
    doc.save(out, garbage=3, deflate=True)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
