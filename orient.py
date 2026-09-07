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


# Rebuilt pages carry a phone PHOTO. PNG cannot compress a photo, and PyMuPDF stores
# the inserted stream without re-compressing it, so a 4 MB booklet with four sideways
# pages came back as 159 MB (4167x3125 px x 3 bytes, uncompressed, per rebuilt page) —
# big enough to break the grading payload downstream. JPEG at a sane resolution keeps
# the same page perfectly legible at ~1 MB.
_REBUILD_MAX_EDGE = 2600
_REBUILD_QUALITY = 90


def _jpeg_upright(page_png: bytes, angle: int) -> bytes:
    """Upright the page as a JPEG, capped so we never upsample a phone photo."""
    img = ImageOps.exif_transpose(Image.open(BytesIO(page_png)))
    if angle:
        img = img.rotate(angle, expand=True)
    img = img.convert("RGB")
    longest = max(img.size)
    if longest > _REBUILD_MAX_EDGE:
        scale = _REBUILD_MAX_EDGE / longest
        img = img.resize((max(1, round(img.width * scale)),
                          max(1, round(img.height * scale))), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=_REBUILD_QUALITY, optimize=True)
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
            up = _jpeg_upright(png, rots[i])
            img = fitz.open(stream=up, filetype="jpeg")
            rect = img[0].rect
            pg = out.new_page(width=rect.width, height=rect.height)
            pg.insert_image(rect, stream=up)
            img.close()
    result = out.tobytes(deflate=True, garbage=3)
    out.close()
    doc.close()
    return result, rots, True


# ---------- Two-page spreads -----------------------------------------------------
#
# A school student photographs the OPEN notebook, so one image holds two facing
# pages. Nothing upstream notices: the writing is upright, so the rotation pass
# passes it through, and OCR then orders lines by their y on the page — which
# interleaves the left page with the right one and hands the grader a scrambled
# answer. MEASURED on a Class 12 Chemistry sheet: two of its five images were
# spreads, and 30 of 46 questions came back "not attempted" against a copy where
# the student had plainly answered Q19 through Q21.
#
# A spread is told from a single page by shape. A notebook page is taller than it
# is wide; two of them side by side are wider than tall, by a wide margin.
_SPREAD_MIN_ASPECT = 1.20    # width/height above this is two pages, not one
_SPREAD_SEARCH = 0.16        # look this far either side of the middle for the gutter


def _gutter_x(img: "Image.Image") -> int:
    """Where the two pages meet — the emptiest column band near the middle.

    Not the DARKEST column: the binding does throw a shadow, but it is a thin line
    inside a wide band of blank margin, and on a photo whose top edge shows the desk
    a darkness sum picks the background instead — it split one spread at 64% of its
    width, straight through the right-hand page. What reliably marks the fold is the
    absence of WRITING: both pages keep a margin, so the emptiest window in the
    middle band is the gutter. Falls back to the centre when nothing stands out.
    """
    import numpy as np

    g = img.convert("L")
    w, h = g.size
    lo, hi = int(w * (0.5 - _SPREAD_SEARCH)), int(w * (0.5 + _SPREAD_SEARCH))
    if hi - lo < 8:
        return w // 2
    arr = np.asarray(g, dtype=np.uint8)
    # Ignore the top and bottom eighths: that is where a photo shows the desk, the
    # page edge and the shadow, none of which say anything about the fold.
    band = arr[int(h * 0.12):int(h * 0.88), lo:hi]
    ink = (band < 140).sum(axis=0).astype(float)
    win = max(3, int(w * 0.01))                  # a fold is ~1% of the spread wide
    if win < ink.size:
        smooth = np.convolve(ink, np.ones(win) / win, mode="same")
    else:
        smooth = ink
    # The MIDDLE of the emptiest region, not its first column: where the two pages
    # keep a generous margin the empty run is wide, and argmin alone cuts at the edge
    # of that run — one spread split 35/65 and carried half the left page onto the
    # right one. Every column within a hair of the minimum is part of the same gutter.
    floor = smooth.min()
    flat = np.flatnonzero(smooth <= floor + max(1.0, floor * 0.15))
    return lo + int(np.median(flat))


def split_two_page_spreads(data: bytes, filename: str,
                           dpi: int = 150) -> tuple[bytes, bool]:
    """Split every photographed spread into its two facing pages, left then right.

    Returns (bytes, changed?). When no page is a spread the ORIGINAL bytes come
    back untouched, so a normal upload pays nothing. Any failure returns the input
    unchanged — a scrambled read is bad, losing the copy is worse.
    """
    import fitz

    try:
        if not (filename or "").lower().endswith(".pdf"):
            return data, False
        doc = fitz.open(stream=data, filetype="pdf")
        if not any(p.rect.width / max(p.rect.height, 1) >= _SPREAD_MIN_ASPECT
                   for p in doc):
            doc.close()
            return data, False
        out = fitz.open()
        for page in doc:
            if page.rect.width / max(page.rect.height, 1) < _SPREAD_MIN_ASPECT:
                out.insert_pdf(doc, from_page=page.number, to_page=page.number)
                continue
            img = Image.open(BytesIO(page.get_pixmap(dpi=dpi).tobytes("png")))
            cut = _gutter_x(img)
            for box in ((0, 0, cut, img.height), (cut, 0, img.width, img.height)):
                half = img.crop(box)
                if half.width < 40 or half.height < 40:
                    continue
                jpeg = _jpeg_upright(_png_bytes(half), 0)
                w, h = Image.open(BytesIO(jpeg)).size
                pg = out.new_page(width=w, height=h)
                pg.insert_image(fitz.Rect(0, 0, w, h), stream=jpeg)
        result = out.tobytes(garbage=3, deflate=True)
        out.close()
        doc.close()
        return result, True
    except Exception:
        return data, False


def _png_bytes(img: "Image.Image") -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
