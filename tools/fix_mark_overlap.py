# -*- coding: utf-8 -*-
"""Repair an already-rendered evaluated PDF whose step marks sit on the student's
own writing, and whose margin remark text needs correcting.

The renderer bug is fixed (pdf_renderer._clear_x_for_mark slides a mark clear of
anything else written on its row), but copies produced before that still carry
the overlap. This measures ink UNDER each tick+circle cluster directly from the
rendered page — no OCR needed — and relocates only the clusters that actually
cover the student's ink. Vector art is redacted with images left untouched, so
the scanned sheet itself is never modified.
"""
import argparse
import sys

import fitz

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pdf_renderer import _draw_number_in_circle, _draw_tick  # noqa: E402

RED = (0.8, 0.078, 0.098)
DPI = 150


def is_red(colour) -> bool:
    return bool(colour) and colour[0] > 0.55 and colour[1] < 0.4 and colour[2] < 0.4


def circles_on(page: fitz.Page) -> list[dict]:
    """Each circled part-mark, paired with the tick drawn just left of it.

    The mark's own digits are read back out of the circle here. Without them the
    repair could measure and report an overlap but not redraw it — `main` asks for
    `c["text"]` — so every run that actually had a mark to move died with a KeyError
    and only --dry-run worked.
    """
    reds = []
    for dr in page.get_drawings():
        if is_red(dr.get("color")) or is_red(dr.get("fill")):
            reds.append(dr)
    spans = [sp for b in page.get_text("dict")["blocks"] if b["type"] == 0
             for ln in b.get("lines", []) for sp in ln.get("spans", [])]
    out = []
    for dr in reds:
        items = dr["items"]
        if len(items) != 4 or not all(it[0] == "c" for it in items):
            continue
        r = dr["rect"]
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        cluster = fitz.Rect(r)
        for other in reds:                       # the tick sits just to its left
            o = other["rect"]
            ocx, ocy = (o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2
            if abs(ocy - cy) < 16 and cx - 58 < ocx < cx - 6:
                cluster |= o
        inside = [sp["text"].strip() for sp in spans
                  if r.contains(fitz.Point((sp["bbox"][0] + sp["bbox"][2]) / 2,
                                           (sp["bbox"][1] + sp["bbox"][3]) / 2))
                  and sp["text"].strip()]
        out.append({"circle": r, "cluster": cluster, "cx": cx, "cy": cy,
                    "radius": r.width / 2, "text": "".join(inside)})
    return out


def ink_map(page: fitz.Page):
    """Greyscale-ish view of the page with RED annotation pixels ignored, so only
    the student's own (dark, bluish) writing counts as ink."""
    pm = page.get_pixmap(dpi=DPI)
    scale = DPI / 72.0
    w, h, n = pm.width, pm.height, pm.n
    buf = pm.samples

    def inked(x_pt: float, y_pt: float) -> bool:
        px, py = int(x_pt * scale), int(y_pt * scale)
        if not (0 <= px < w and 0 <= py < h):
            return False
        i = (py * w + px) * n
        r, g, b = buf[i], buf[i + 1], buf[i + 2]
        if r > 120 and g < 90 and b < 90:        # a red annotation, not student ink
            return False
        return (r + g + b) / 3 < 150             # dark enough to be pen

    return inked, scale


def ink_fraction(inked, cx: float, cy: float, radius: float) -> float:
    hits = total = 0
    step = max(1.0, radius / 6)
    y = cy - radius
    while y <= cy + radius:
        x = cx - radius
        while x <= cx + radius:
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                total += 1
                hits += 1 if inked(x, y) else 0
            x += step
        y += step
    return hits / max(total, 1)


# A mark only means anything beside the step it marks, so it may shift a little
# along its own row and no further. Rightward is preferred (past the amount column,
# the same direction the renderer fix now takes); a short hop left is the fallback.
MAX_RIGHT = 95.0
MAX_LEFT = 38.0


def find_clear_x(inked, cx: float, cy: float, radius: float,
                 left_limit: float, right_limit: float) -> float | None:
    """Nearest x ON THIS ROW where the circle covers no student ink.

    Deliberately short-range: an earlier version searched 220pt in both directions
    and cheerfully relocated marks 177pt into the left margin, which reads far worse
    than the overlap it was fixing.
    """
    candidates = [cx + o for o in range(8, int(MAX_RIGHT), 3)]
    candidates += [cx - o for o in range(8, int(MAX_LEFT), 3)]
    for cand in candidates:
        if cand - radius < left_limit or cand + radius > right_limit:
            continue
        if ink_fraction(inked, cand, cy, radius) <= 0.02:
            return cand
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="")
    ap.add_argument("--threshold", type=float, default=0.15,
                    help="ink fraction under a mark that counts as an overlap")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    moves = []
    for pno in range(len(doc)):
        page = doc[pno]
        inked, _ = ink_map(page)
        content_right = page.rect.width
        for b in page.get_text("dict")["blocks"]:
            if b["type"] == 1:                                  # the scanned sheet
                content_right = min(content_right, b["bbox"][2])
        for c in circles_on(page):
            frac = ink_fraction(inked, c["cx"], c["cy"], c["radius"])
            if frac <= args.threshold:
                continue
            new_x = find_clear_x(inked, c["cx"], c["cy"], c["radius"],
                                 left_limit=4.0, right_limit=content_right - 4)
            if new_x is None:
                print(f"  page {pno+1}: mark at ({c['cx']:.0f},{c['cy']:.0f}) covers "
                      f"{frac:.0%} ink — NO clear slot found, left as is")
                continue
            moves.append((pno, c, new_x))
            print(f"  page {pno+1}: mark at ({c['cx']:.0f},{c['cy']:.0f}) covers "
                  f"{frac:.0%} ink -> moved to x={new_x:.0f} "
                  f"({new_x - c['cx']:+.0f}pt)")

    if not moves:
        print("No overlapping marks found.")
        return
    if args.dry_run:
        print(f"\n{len(moves)} mark(s) would be moved.")
        return

    for pno, c, _new_x in moves:
        doc[pno].add_redact_annot(c["cluster"] + (-2, -2, 2, 2))
    for pno in {p for p, _c, _x in moves}:
        doc[pno].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=fitz.PDF_REDACT_TEXT_NONE)

    for pno, c, new_x in moves:
        page = doc[pno]
        tick_size = c["radius"] * 1.5
        _draw_tick(page, new_x - c["radius"] - tick_size * 1.1, c["cy"], size=tick_size)
        _draw_number_in_circle(page, new_x, c["cy"], c["text"],
                               size=c["radius"] / 1.9)

    out = args.out or args.pdf.replace(".pdf", "_marks_fixed.pdf")
    doc.save(out, garbage=3, deflate=True)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
