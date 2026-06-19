"""Free, dependency-light page-orientation correction.

Phones photograph answer sheets sideways (landscape) all the time. Nothing downstream
(OCR, the grading model, mark placement) handles a rotated page, so we straighten pages
to upright BEFORE anything else sees them.

Cost: $0. Uses only Pillow (already a dependency) + PyMuPDF — no Tesseract binary, no
cloud call — so it also works inside the packaged EXE.

How it works:
  1. EXIF transpose (free): fixes phone photos that carry an orientation tag.
  2. Projection analysis: text/ruled lines make a page "stripey" along one axis. We score
     all four 90-degree rotations for "uprightness" (horizontal text lines + content
     sitting top-left, as text naturally does) and pick the best.

Reliable for the common landscape/sideways case (the 90 degrees rotations). Telling
upright from a full 180-degree flip from pixels alone is inherently weak — for that, pass
an optional `probe` (a model callback) which, when supplied, gives exact 4-way accuracy.
`normalize_orientation` rebuilds ONLY the pages that need rotating, so upright pages keep
their original quality and (for digital PDFs) their text layer.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

# Rotate-and-score on a small thumbnail — cheap and orientation-invariant in cost.
_THUMB = 240


def _to_thumb(img: Image.Image) -> Image.Image:
    g = img.convert("L")
    w, h = g.size
    s = _THUMB / max(w, h)
    if s < 1:
        g = g.resize((max(1, int(w * s)), max(1, int(h * s))))
    return g


def _profiles(g: Image.Image):
    """Per-row and per-column ink counts (ink = pixels darker than ~75% of the mean)."""
    w, h = g.size
    px = g.load()
    total = 0
    s = 0
    for y in range(h):
        for x in range(w):
            s += px[x, y]
    mean = s / max(1, w * h)
    thr = mean * 0.75
    rows = [0] * h
    cols = [0] * w
    for y in range(h):
        for x in range(w):
            if px[x, y] < thr:
                rows[y] += 1
                cols[x] += 1
                total += 1
    return rows, cols, total


def _cv(a: list[int]) -> float:
    """Coefficient of variation — how 'stripey' a profile is, independent of its length
    or the amount of ink. Horizontal text lines make the ROW profile very stripey."""
    n = len(a)
    if n == 0:
        return 0.0
    m = sum(a) / n
    if m <= 0:
        return 0.0
    var = sum((v - m) ** 2 for v in a) / n
    return (var ** 0.5) / m


def _uprightness(g: Image.Image) -> float:
    """Higher = more likely this orientation is upright. Combines: (a) strong horizontal
    text banding, and (b) the natural top-heavy, left-aligned distribution of handwriting
    (pages fill from the top, lines start at the left margin)."""
    rows, cols, total = _profiles(g)
    if total <= 0:
        return 0.0
    h = len(rows)
    w = len(cols)
    banding = _cv(rows) - _cv(cols)                       # horizontal lines >> vertical
    top = sum(rows[: h // 2]) / total
    left = sum(cols[: w // 2]) / total
    top_heavy = top - 0.5                                  # >0 if more ink up top
    left_heavy = left - 0.5                                # >0 if more ink on the left
    return banding + 0.5 * top_heavy + 0.25 * left_heavy


# PIL rotate is counter-clockwise; we return the CCW angle that makes the page upright.
_ANGLES = (0, 90, 180, 270)


def detect_rotation(img: Image.Image, min_margin: float = 0.12) -> int:
    """Return the angle (0/90/180/270, CCW) to rotate `img` so text reads upright.

    SAFETY-GATED and free: a page is only rotated when some non-upright angle scores
    CLEARLY better (by `min_margin`) than leaving it as-is — so an already-upright page is
    never wrongly rotated, and genuinely ambiguous pages (e.g. text filling the whole
    sheet, where pixel cues are weak) are left untouched rather than risked. Strongest on
    the common landscape / top-left-heavy case. For guaranteed 4-way accuracy on every
    page, pass a `probe` to `normalize_orientation`."""
    img = ImageOps.exif_transpose(img)
    g0 = _to_thumb(img)
    scores = {a: _uprightness(g0 if a == 0 else g0.rotate(a, expand=True)) for a in _ANGLES}
    best = max(_ANGLES, key=lambda a: scores[a])
    if best == 0:
        return 0
    # Only act if rotating is decisively better than doing nothing.
    return best if (scores[best] - scores[0]) >= min_margin else 0


def _png_upright(page_png: bytes, angle: int) -> bytes:
    img = ImageOps.exif_transpose(Image.open(BytesIO(page_png)))
    if angle:
        img = img.rotate(angle, expand=True)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _clean_rotations(values, n: int) -> list[int]:
    out = []
    for v in (values or []):
        try:
            iv = int(v) % 360
        except (TypeError, ValueError):
            iv = 0
        out.append(iv if iv in _ANGLES else 0)
    return (out + [0] * n)[:n]


def normalize_orientation(data: bytes, filename: str, rotations=None,
                          probe=None, dpi: int = 150) -> tuple[bytes, list[int], bool]:
    """Return (bytes, per-page rotations applied, changed?) with every page upright.

    Only pages that actually need rotating are rebuilt upright; upright pages are kept
    verbatim (original quality + text layer). If nothing needs rotating (the common case)
    the ORIGINAL bytes are returned unchanged — zero overhead. The returned bytes keep the
    input KIND: a PDF stays a PDF, a single image stays a PNG, so `filename` stays valid.

    Rotation source, in priority order:
      1. `rotations` — an explicit per-page CCW-angle list (e.g. from the proxy's probe).
      2. `probe(page_pngs) -> list[int]` — a callback (a model call) consulted for ALL pages.
      3. the free `detect_rotation` heuristic."""
    import fitz

    lower = (filename or "").lower()
    is_pdf = lower.endswith(".pdf")
    if is_pdf:
        doc = fitz.open(stream=data, filetype="pdf")
        page_pngs = [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]
    else:
        page_pngs = [data]
    n = len(page_pngs)

    if rotations is not None:
        rots = _clean_rotations(rotations, n)
    elif probe is not None:
        try:
            rots = _clean_rotations(probe(page_pngs), n)
        except Exception:
            rots = [detect_rotation(Image.open(BytesIO(p))) for p in page_pngs]
    else:
        rots = [detect_rotation(Image.open(BytesIO(p))) for p in page_pngs]

    if not any(rots):
        if is_pdf:
            doc.close()
        return data, rots, False

    if not is_pdf:                       # single image → return a rotated PNG, not a PDF
        return _png_upright(data, rots[0]), rots, True

    out = fitz.open()
    for i, png in enumerate(page_pngs):
        if rots[i] == 0:
            out.insert_pdf(doc, from_page=i, to_page=i)          # keep original page as-is
        else:
            up = _png_upright(png, rots[i])
            img = fitz.open(stream=up, filetype="png")
            rect = img[0].rect
            pg = out.new_page(width=rect.width, height=rect.height)
            pg.insert_image(rect, stream=up)
            img.close()
    result = out.tobytes()
    out.close()
    doc.close()
    return result, rots, True
