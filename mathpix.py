"""Mathpix OCR client — returns per-page text with line bounding boxes for precise mark placement."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import io
import re
from dataclasses import dataclass, field

import pw_access
from PIL import Image, ImageDraw, ImageFont


_HEADER_PREFIXES = ("q.", "q ", "q)")
_SECTION_PREFIX = "section"
_LONE_LABELS = {f"({r})" for r in (
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "a", "b", "c", "d", "e", "f",
)}


def readable_ocr_text(text: str) -> str:
    """Remove OCR's presentation wrappers without removing mathematical operators."""
    value = str(text or "").strip()
    for wrapper in (r"\(", r"\)", r"\[", r"\]"):
        value = value.replace(wrapper, "")
    # Repeat for nested presentation wrappers, but never unwrap a formula command.
    for _ in range(3):
        value = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\(?:left|right)\b", "", value)
    return " ".join(value.split()).strip("$").strip()


# A scanner app's footer printed under the photo: "Scanned with OKEN Scanner",
# "Scanned with CamScanner". MEASURED 2026-09-06 (Maths, Rishita): it was the last
# OCR line of Q15's region, and Q15's mark was drawn on it, below the photo.
_SCANNER_MARK_RE = re.compile(
    r"^\W*(?:scanned\s+(?:with|by|using)\b(?:\W+\w+){0,3}|camscanner)\W*$", re.IGNORECASE)


def is_scanner_watermark(text: str) -> bool:
    """A scanner app's printed footer, never the student's writing."""
    return bool(_SCANNER_MARK_RE.match(readable_ocr_text(text)))


def is_header_line(text: str) -> bool:
    """True if the line is a question/section header, not actual student working."""
    s = readable_ocr_text(text).lower()
    if not s:
        return True
    if is_scanner_watermark(s):
        return True
    # Q2 alone is a heading; Q2 (b) Vx < Vy contains the student's answer.
    # The old prefix check labelled the entire latter line as non-evidence.
    if re.fullmatch(r"(?:q(?:uestion)?|ans(?:wer)?|sol(?:ution)?)\s*(?:no\.?)?"
                    r"\s*[-.:)#]*\s*\d{1,2}(?:\s*\([a-z0-9]+\)|"
                    r"\s*\.\s*(?:[ivxlcdm]+|[a-z]|\d+)|"
                    r"\s+(?:[ivxlcdm]+|[a-z]|\d+)[.)])*\s*[.):\-]*", s):
        return True
    if s.startswith(_SECTION_PREFIX) and len(s) <= 14:
        return True
    if s in _LONE_LABELS:
        return True
    if len(s) <= 5 and s.startswith("(") and s.endswith(")"):
        return True
    return False


@dataclass
class WordBox:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None


@dataclass
class LineBox:
    line_id: str           # e.g. "P1L3" — stable ID we generate
    text: str              # OCR'd line text (may contain inline LaTeX)
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in IMAGE PIXEL coordinates
    page: int              # 1-indexed page on student answer sheet
    # Mathpix has NO strikethrough field — its line_data carries type, subtype,
    # is_printed, is_handwritten, confidence and error_id, and none of them mark
    # crossed-out content (checked against the /v3/text reference). So we detect it
    # ourselves from the page image we already hold: see detect_struck_lines().
    cancelled: bool = False
    confidence: float | None = None
    kind: str = "text"
    # Mathpix's own reading of whether this line is PRINTED (typed) rather than
    # handwritten. A booklet made of printed lines is a document, not an answer sheet.
    printed: bool = False


@dataclass
class PageOCR:
    page: int                          # 1-indexed
    image_width: int                   # pixels of the PNG we sent to Mathpix
    image_height: int
    lines: list[LineBox] = field(default_factory=list)
    words: list[WordBox] = field(default_factory=list)
    # Set by pipeline.blank_scanned_page: a white scanned page with no handwriting
    # (ruling and show-through only). An empty page that is blank is not "unread".
    blank: bool = False

    def transcript(self) -> str:
        """Format as a labelled transcript Claude can reference by [line_id]."""
        return "\n".join(f"[{l.line_id}] {l.text}" for l in self.lines)


def pageocr_to_dict(p: "PageOCR") -> dict:
    """JSON-safe dict for sending OCR results over the wire (proxy -> client), so the
    client can place mark anchors when it builds the evaluated PDF locally."""
    return {
        "page": p.page,
        "image_width": p.image_width,
        "image_height": p.image_height,
        "lines": [
            {"line_id": l.line_id, "text": l.text, "bbox": list(l.bbox),
             "page": l.page, "cancelled": bool(l.cancelled),
             "confidence": l.confidence, "kind": l.kind,
             "printed": bool(getattr(l, "printed", False))}
            for l in p.lines
        ],
        "words": [{"text": w.text, "bbox": list(w.bbox),
                   "confidence": w.confidence} for w in p.words],
        "blank": bool(getattr(p, "blank", False)),
    }


def pageocr_from_dict(d: dict) -> "PageOCR":
    """Inverse of pageocr_to_dict — rebuild a PageOCR on the client side."""
    return PageOCR(
        page=int(d["page"]),
        image_width=int(d["image_width"]),
        image_height=int(d["image_height"]),
        lines=[
            LineBox(line_id=l["line_id"], text=l["text"],
                    bbox=tuple(l["bbox"]), page=int(l["page"]),
                    cancelled=bool(l.get("cancelled", False)),
                    confidence=_confidence(l.get("confidence")),
                    kind=str(l.get("kind", "text")),
                    printed=bool(l.get("printed", False)))
            for l in d.get("lines", [])
        ],
        words=[WordBox(str(w["text"]), tuple(w["bbox"]),
                       _confidence(w.get("confidence")))
               for w in d.get("words", [])],
        blank=bool(d.get("blank", False)),
    )


def _confidence(value) -> float | None:
    try:
        value = float(value)
        return value if 0 <= value <= 1 else None
    except (TypeError, ValueError):
        return None


def word_target_bbox(page: PageOCR, line: LineBox, target: str):
    """Resolve a unique phrase using measured word contours, never character widths."""
    def normalize(text):
        return "".join(str(text).split()).casefold()

    wanted = normalize(target)
    if not wanted:
        return None
    x0, y0, x1, y1 = line.bbox
    words = sorted((w for w in page.words
                    if x0 - 2 <= (w.bbox[0] + w.bbox[2]) / 2 <= x1 + 2
                    and y0 - 2 <= (w.bbox[1] + w.bbox[3]) / 2 <= y1 + 2),
                   key=lambda w: w.bbox[0])
    matches = []
    for start in range(len(words)):
        combined = ""
        for end in range(start, len(words)):
            combined += normalize(words[end].text)
            if combined == wanted:
                group = words[start:end + 1]
                # Never concatenate words from different table rows merely
                # because their x coordinates happen to be adjacent.
                same_row = max(w.bbox[1] for w in group) < min(w.bbox[3] for w in group)
                if same_row and all(w.confidence is None or w.confidence >= 0.8 for w in group):
                    matches.append((min(w.bbox[0] for w in group),
                                    min(w.bbox[1] for w in group),
                                    max(w.bbox[2] for w in group),
                                    max(w.bbox[3] for w in group)))
                break
            if not wanted.startswith(combined):
                break
    return matches[0] if len(matches) == 1 else None


def _bbox_from_cnt(cnt: list[list[float]]) -> tuple[float, float, float, float]:
    """Convert a Mathpix `cnt` polygon (list of [x, y] points) to (x0, y0, x1, y1)."""
    xs = [p[0] for p in cnt]
    ys = [p[1] for p in cnt]
    return (min(xs), min(ys), max(xs), max(ys))


# Mathpix application errors that mean "this page has no usable content" rather
# than "your setup is broken". These should degrade to an empty page, not abort
# the whole evaluation — a cover/form page or a faint scan legitimately OCRs to
# nothing, and grading + heuristic mark placement still work without it.
_SOFT_MATHPIX_ERROR_IDS = {
    "content_not_found", "image_no_content", "no_content",
    "image_decode_error", "image_download_error", "math_confidence",
}


def _is_auth_error(data: dict) -> bool:
    """True if a Mathpix error body indicates a credential/config problem we must surface."""
    info = data.get("error_info") or {}
    blob = f"{data.get('error', '')} {info.get('id', '')} {info.get('message', '')}".lower()
    return any(k in blob for k in ("unauthorized", "invalid", "credential", "app_key", "app_id", "forbidden"))


# The proxy is a Vercel function with a hard ~4 MB request limit and no blob-upload
# path for Mathpix — so each page image must be sent comfortably under that. High-res
# phone scans (CA answer booklets especially) blow straight past it as raw PNG.
_OCR_MAX_EDGE = 2200          # ~185 dpi on A4 — plenty for handwriting OCR
_OCR_TARGET_BYTES = 1_500_000  # binary size; base64 adds ~33%


def _shrink_for_ocr(png_bytes: bytes) -> tuple[bytes, str, float]:
    """Return (image_bytes, mime, scale) small enough for the proxy.

    `scale` maps ORIGINAL pixel coords -> sent-image coords (sent = original * scale),
    so callers can divide Mathpix bboxes by it to get original-image coordinates.
    """
    if len(png_bytes) <= _OCR_TARGET_BYTES:
        return png_bytes, "image/png", 1.0
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        scale = min(1.0, _OCR_MAX_EDGE / float(max(w, h)))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        data = png_bytes
        for quality in (85, 70, 55):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= _OCR_TARGET_BYTES:
                break
        return data, "image/jpeg", scale
    except Exception:
        return png_bytes, "image/png", 1.0


def paper_crop(png_bytes: bytes):
    """(enhanced png, (x0, y0), scale): the photo cropped to the paper, contrast
    stretched and enlarged, for a second OCR read; None when nothing would change.

    A page photographed small on a dark desk, or faint in poor light, came back
    from Mathpix with 0 to 2 lines (Radiant Class 9 Maths, 2026-09-30: Akriti pages
    7 and 8, Isha page 9, Shaarav page 5), and the answers on it were graded "not
    attempted". Boxes read from the returned image map back to the original with
    x = x0 + x' / scale.
    """
    import numpy as np
    from PIL import ImageOps
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    arr = np.asarray(img, dtype=np.uint8)
    h, w = arr.shape
    # Paper is the bright part of the photo; the desk and shadows are darker.
    bright = arr > (np.percentile(arr, 10) + np.percentile(arr, 90)) / 2
    rows = np.flatnonzero(bright.mean(axis=1) > 0.35)
    cols = np.flatnonzero(bright.mean(axis=0) > 0.35)
    x0, y0, x1, y1 = 0, 0, w, h
    if rows.size and cols.size:
        y0, y1 = int(rows[0]), int(rows[-1]) + 1
        x0, x1 = int(cols[0]), int(cols[-1]) + 1
    if (x1 - x0) < 0.2 * w or (y1 - y0) < 0.2 * h:
        x0, y0, x1, y1 = 0, 0, w, h
    crop = ImageOps.autocontrast(img.crop((x0, y0, x1, y1)), cutoff=2)
    scale = min(3.0, 2400.0 / max(crop.size))
    if scale > 1.05:
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
    else:
        scale = 1.0
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), (x0, y0), scale


def ocr_page_enhanced(png_bytes: bytes, page_num: int, *, google_token: str,
                      task_id: str, filename: str = "") -> "PageOCR":
    """ocr_page on the paper_crop of this photo, boxes in the ORIGINAL frame."""
    enhanced, (x0, y0), scale = paper_crop(png_bytes)
    page = ocr_page(enhanced, page_num, google_token=google_token, task_id=task_id,
                    filename=filename)
    width, height = Image.open(io.BytesIO(png_bytes)).size

    def back(b):
        return (x0 + b[0] / scale, y0 + b[1] / scale, x0 + b[2] / scale, y0 + b[3] / scale)

    for line in page.lines:
        line.bbox = back(line.bbox)
    for word in page.words:
        word.bbox = back(word.bbox)
    page.image_width, page.image_height = width, height
    return page


SPREAD_ASPECT = 1.20          # a photo this wide may hold two facing pages
_FOLD_SEARCH = (0.30, 0.70)   # where the fold between them can be


def spread_fold(lines, width: float) -> float | None:
    """x of the fold on a photo of two facing pages, or None when there is none.

    Found from the text itself: the x that the fewest lines run across, with lines
    on both sides of it. A page whose writing runs across the middle has no fold.
    """
    if len(lines) < 4 or width <= 0:
        return None
    # Search within the writing, not the page: a photo of an open notebook is often
    # placed on a portrait page with blank paper around it (Sonakshi's Biology copy,
    # 2026-09-30), and the fold then sits far from the page's middle.
    left_edge = min(ln.bbox[0] for ln in lines)
    span = max(ln.bbox[2] for ln in lines) - left_edge
    if span <= 0:
        return None
    lo, hi = _FOLD_SEARCH
    best, best_cross, best_step = None, None, 50
    for step in range(int(lo * 100), int(hi * 100) + 1):
        x = left_edge + span * step / 100
        cross = sum(1 for ln in lines if ln.bbox[0] < x < ln.bbox[2])
        left = sum(1 for ln in lines if ln.bbox[2] <= x)
        right = sum(1 for ln in lines if ln.bbox[0] >= x)
        if left < 2 or right < 2:
            continue
        if best_cross is None or cross < best_cross or (
                cross == best_cross and abs(step - 50) < abs(best_step - 50)):
            best, best_cross, best_step = x, cross, step
    if best is None or best_cross > max(1, 0.1 * len(lines)):
        return None
    return best


def looks_like_spread(lines, width: float, height: float) -> bool:
    """A wide page, or wide writing on any page (a landscape photo of an open
    notebook placed on a portrait page)."""
    if width / max(height, 1) >= SPREAD_ASPECT:
        return True
    if len(lines) < 8:
        return False
    x0 = min(ln.bbox[0] for ln in lines)
    x1 = max(ln.bbox[2] for ln in lines)
    y0 = min(ln.bbox[1] for ln in lines)
    y1 = max(ln.bbox[3] for ln in lines)
    return (x1 - x0) / max(y1 - y0, 1) >= SPREAD_ASPECT


def order_spread_lines(lines, width: float, page_num: int):
    """Left page's lines first, then the right page's, renumbered in that order.

    OCR orders lines by height on the photo, which interleaves two facing pages and
    hands the grader a scrambled answer (30 of 46 questions "not attempted" on a
    Class 12 Chemistry copy). Lines are never cut: one running across the fold keeps
    its place on the side where it starts.
    """
    fold = spread_fold(lines, width)
    if fold is None:
        return lines
    centre = lambda ln: (ln.bbox[0] + ln.bbox[2]) / 2
    ordered = ([ln for ln in lines if centre(ln) < fold]
               + [ln for ln in lines if centre(ln) >= fold])
    if [ln.line_id for ln in ordered] == [ln.line_id for ln in lines]:
        return lines
    counters = {"L": 0, "D": 0}
    for ln in ordered:
        kind = "D" if ln.line_id.startswith(f"P{page_num}D") else "L"
        counters[kind] += 1
        ln.line_id = f"P{page_num}{kind}{counters[kind]}"
    print(f"[ocr] page {page_num}: two facing pages, read left page first "
          f"(fold at {fold / width:.0%})", flush=True)
    return ordered


def ocr_page(
    png_bytes: bytes,
    page_num: int,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
) -> PageOCR:
    """OCR a single page image. Returns text lines with bounding boxes in pixel coords.

    If Mathpix reports a content-level error (e.g. "Content not found" on a
    sparse cover/form page), this returns an *empty* PageOCR for that page so the
    rest of the submission still gets processed. Auth/credential errors are
    re-raised so a misconfigured key isn't silently swallowed.
    """
    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    send_bytes, mime, scale = _shrink_for_ocr(png_bytes)
    payload = {
        "src": f"data:{mime};base64," + base64.standard_b64encode(send_bytes).decode("utf-8"),
        "formats": ["text"],
        # Mathpix rejects line data and word data in the same request. Line
        # geometry is required for question ownership and step marks; optional
        # word-level highlights must not break OCR or add an unbudgeted call.
        "include_line_data": True,
        # Orientation is normalized before OCR; contours must remain in that frame.
        "auto_rotate_confidence_threshold": 1,
        # leave math_inline_delimiters / math_display_delimiters at defaults
    }
    if not google_token or not task_id:
        raise PermissionError("A signed-in PW user and task_id are required.")
    data = pw_access.mathpix_ocr(
        google_token,
        request=payload,
        filename=filename,
        count=1,
        task_id=task_id,
    )
    if "error" in data:
        info = data.get("error_info") or {}
        err_id = (info.get("id") or "").lower()
        if _is_auth_error(data):
            raise RuntimeError(f"Mathpix error on page {page_num}: {data['error']}")
        if err_id in _SOFT_MATHPIX_ERROR_IDS:
            # No usable content on this page — degrade gracefully.
            return PageOCR(page=page_num, image_width=width, image_height=height, lines=[])
        raise RuntimeError(f"Mathpix error on page {page_num}: {data['error']}")
    if "line_data" not in data and "text" not in data:
        # Neither a result nor an error — an unrecognized shape (e.g. a proxy
        # envelope) would otherwise read as "0 lines" and silently degrade every
        # mark placement on the sheet. Fail loudly instead.
        keys = sorted(data)[:8] if isinstance(data, dict) else type(data).__name__
        raise RuntimeError(
            f"Mathpix returned an unrecognized response shape on page {page_num} (keys: {keys})."
        )

    lines: list[LineBox] = []
    for idx, ld in enumerate(data.get("line_data", []), start=1):
        kind = ld.get("type", "text")
        visual = kind in ("diagram", "chart", "table")
        if kind not in ("text", "math", "figure_label") and not visual:
            continue
        text = (ld.get("text") or ld.get("latex") or
                (f"[detected {kind}]" if visual else "")).strip()
        if not text:
            continue
        cnt = ld.get("cnt")
        if not cnt:
            continue
        bbox = _bbox_from_cnt(cnt)
        if scale != 1.0:
            # Bboxes are in sent-(downscaled)-image coords; map back to the original.
            bbox = tuple(v / scale for v in bbox)
        lines.append(LineBox(
            line_id=f"P{page_num}{'D' if visual else 'L'}{idx}",
            text=text,
            bbox=bbox,
            page=page_num,
            confidence=_confidence(ld.get("confidence")),
            kind=kind,
            printed=bool(ld.get("is_printed")) and not bool(ld.get("is_handwritten")),
        ))

    if width and height and looks_like_spread(lines, width, height):
        lines = order_spread_lines(lines, width, page_num)
    words = [WordBox(
        text=str(w.get("text") or w.get("latex") or ""),
        bbox=tuple(v / scale for v in _bbox_from_cnt(w["cnt"])),
        confidence=_confidence(w.get("confidence")),
    ) for w in data.get("word_data", []) if w.get("cnt")
             and (w.get("text") or w.get("latex"))]
    return PageOCR(page=page_num, image_width=width, image_height=height,
                   lines=lines, words=words)


# Pages are independent and each call is I/O-bound on the proxy (~2-4s), so this is
# wall-clock straight off an 18-page booklet: 4 workers is 5 waves, 6 is 3. Raised only
# once 429 joined pw_access._RETRY_STATUSES — before that, throttling us for our own
# concurrency killed a page outright and cost every mark anchor on it.
_OCR_CONCURRENCY = 6


# --- Struck-out ("cut") working -------------------------------------------------
#
# A reviewer found the tool marking working the student had crossed out. Mathpix
# cannot help: its line_data has no strikethrough/cancelled field, and struck text is
# transcribed exactly like live text. But we already hold the page image and every
# line's bbox, so the strike itself can be measured.
#
# A strike is a long, near-horizontal run of ink through the MIDDLE of a line.
#
# Band alone is NOT enough to tell one from a notebook's ruling. The band was chosen
# on the assumption that ruling and underlines "lie at or below the baseline", but a
# Mathpix bbox on a photographed ruled page is generous enough that the next printed
# rule falls squarely inside 0.30-0.72 of the line height. Measured on an 8-page CBSE
# Chemistry sheet (150 dpi, phone photo of a ruled notebook): the rules read 29-101
# grey — well under the 128 ink threshold — and coverage alone flagged 64 of 125 lines
# as cancelled. Every one was live handwriting, including the answer to Q20.ii.c, which
# the grader then reported as "Answer cancelled. No surviving attempt."
#
# So coverage is only the first gate. A rule or an underline is separated from a strike
# by three further measurements, all on the same row:
#   * continuity - a pen strike is ONE stroke, so its longest unbroken run covers most
#     of the line. The densest row of cursive reaches the coverage bar too, but as many
#     short letter-strokes: median longest-run there was 0.53 of the width.
#   * extent - a printed rule runs the full width of the sheet and keeps going past the
#     words; a strike stops with them. Checked with vertical slack, because a hand-held
#     photo is never square to the page and a rule drifts across rows.
#   * ink below - a strike has the rest of the writing under it; an underline and a
#     baseline rule have almost nothing.
# Together these took the same sheet from 64 false positives to 0.
_STRIKE_BAND = (0.30, 0.72)   # fraction of line height searched, top-down
_STRIKE_COVERAGE = 0.62       # a strike spans most of the line's width
# On ruled paper the printed rule occupies band rows of its own, so a genuine strike
# and the rule together run to ~0.27 of the band and 0.22 threw the strike away with
# the rule. Measured on the ruled-page fixture: ruling alone 0.09, ruling + a real
# strike 0.27, a solid block of ink 1.00 — which is all this guard has to exclude.
_STRIKE_MAX_ROWS = 0.40       # more ink rows than this means a solid block, not a rule
_STRIKE_MIN_RUN = 0.80        # longest UNBROKEN run, as a fraction of the line width
_STRIKE_RUN_BRIDGE = 4        # px of pale gap tolerated inside that run (pen lift)
_STRIKE_MARGIN = 0.10         # fraction of page width probed either side of the line
_STRIKE_MARGIN_INK = 0.55     # this much ink out there means a page rule, not a strike
_STRIKE_MARGIN_SKEW = 6       # rows of vertical slack, for a photo that is not square
_STRIKE_MIN_INK_BELOW = 0.12  # a strike has writing under it; an underline does not


def _row_ink_coverage(gray, x0: int, y: int, x1: int, threshold: int) -> float:
    """Fraction of this pixel row, across the line's width, that is ink."""
    width = x1 - x0
    if width <= 0:
        return 0.0
    # One count over the row: pixel by pixel in Python this took a quarter of a live run's
    # working time on 1 Nov 2026, as it runs for every row of every line of every page.
    return int((gray[y, x0:x1] < threshold).sum()) / width


def _row_ink_run(gray, x0: int, y: int, x1: int, threshold: int) -> float:
    """Longest UNBROKEN ink run in this row, as a fraction of the line's width.

    Small pale gaps are bridged: a pen lifts, and thresholding eats the anti-aliased
    edge of a thin stroke. Written as one pass so a full-width row costs one scan.
    """
    width = x1 - x0
    if width <= 0:
        return 0.0
    best = current = gap = 0
    for dark in (gray[y, x0:x1] < threshold).tolist():
        if dark:
            current += gap + 1
            gap = 0
            if current > best:
                best = current
        else:
            gap += 1
            if gap > _STRIKE_RUN_BRIDGE:
                current = gap = 0
    return best / width


def _margin_ink(gray, y: int, x0: int, x1: int, height: int, threshold: int) -> float:
    """Most ink found in this row just outside the line, allowing for photo skew.

    Returns 0.0 when there is no margin worth measuring, which keeps a line that runs
    to the edge of the sheet from being judged on a couple of pixels.
    """
    if x1 - x0 < 20:
        return 0.0
    lo = max(0, y - _STRIKE_MARGIN_SKEW)
    hi = min(height, y + _STRIKE_MARGIN_SKEW + 1)
    return max(_row_ink_coverage(gray, x0, yy, x1, threshold) for yy in range(lo, hi))


def detect_struck_lines(page_png: bytes, lines: list[LineBox],
                        scale: float = 1.0) -> set[str]:
    """line_ids whose writing has been struck through.

    Deliberately conservative: it is far worse to discard a student's live answer as
    "cancelled" than to mark a cancelled one, so a line is only reported when a single
    near-continuous rule crosses most of its width through the middle band AND the
    line is not simply dense with ink.
    """
    if not lines:
        return set()
    try:
        img = Image.open(io.BytesIO(page_png)).convert("L")
    except Exception:
        return set()
    import numpy as np
    gray = np.asarray(img)
    w, h = img.size
    struck: set[str] = set()
    for ln in lines:
        try:
            x0, y0, x1, y1 = (int(v * scale) for v in ln.bbox)
        except Exception:
            continue
        x0, x1 = max(0, x0), min(w, x1)
        y0, y1 = max(0, y0), min(h, y1)
        if x1 - x0 < 25 or y1 - y0 < 8:
            continue
        band0 = y0 + int((y1 - y0) * _STRIKE_BAND[0])
        band1 = y0 + int((y1 - y0) * _STRIKE_BAND[1])
        if band1 <= band0:
            continue
        rows = [_row_ink_coverage(gray, x0, y, x1, 128) for y in range(band0, band1)]
        if not rows:
            continue
        hits = [r for r in rows if r >= _STRIKE_COVERAGE]
        # One or two rules, not a solid block: a heavily inked line (a table cell full
        # of digits, a dense signature) would otherwise read as struck.
        if not hits or len(hits) / len(rows) > _STRIKE_MAX_ROWS:
            continue
        margin = int(w * _STRIKE_MARGIN)
        # Below the candidate row, stopping short of the baseline so the line the
        # writing SITS on is never counted as the writing itself.
        below_end = min(h, y0 + int((y1 - y0) * 0.92))
        for y in range(band0, band1):
            if _row_ink_coverage(gray, x0, y, x1, 128) < _STRIKE_COVERAGE:
                continue
            if _row_ink_run(gray, x0, y, x1, 128) < _STRIKE_MIN_RUN:
                continue    # many short letter-strokes, not one stroke
            if max(_margin_ink(gray, y, max(0, x0 - margin), x0, h, 128),
                   _margin_ink(gray, y, x1, min(w, x1 + margin), h, 128)) \
                    >= _STRIKE_MARGIN_INK:
                continue    # carries on past the words: the page's own ruling
            below = [_row_ink_coverage(gray, x0, yy, x1, 128)
                     for yy in range(min(h - 1, y + 4), below_end)]
            if not below or sum(below) / len(below) < _STRIKE_MIN_INK_BELOW:
                continue    # nothing underneath: an underline, not a strike
            struck.add(ln.line_id)
            break
    return struck


# A student's label at the start of a line: "Q1 A) Partners", "(3) Interest",
# "Ans 4.", "5) B", "10". The number must end there (a delimiter, a space or the
# line's end), so working such as "96000 - 60000" is never read as question 96.
_LABEL_RE = re.compile(
    r"^\s*(?:q(?:ues(?:tion)?)?|ans(?:wer)?)?\.?\s*[\(\[]?\s*(\d{1,2})(?=\s*[\)\]\.:\-]|\s|$)",
    re.IGNORECASE)


def booklet_is_typed(ocr_pages) -> tuple[bool, int, int]:
    """Whether the booklet is a printed or typed document: (typed?, printed, handwritten).

    Mathpix marks every line it reads as printed or handwritten. An answer sheet
    is handwritten through; a printed worksheet with the student's answers written
    in still carries many handwritten lines. So a booklet is a document when it
    has ten or more printed lines of real length and at most a tenth of its lines
    (never more than three) are handwritten.
    """
    printed = handwritten = 0
    for page in list(ocr_pages or []):
        for line in list(getattr(page, "lines", None) or []):
            if str(getattr(line, "kind", "text") or "text") != "text":
                continue
            if len(str(getattr(line, "text", "") or "").split()) < 3:
                continue
            if getattr(line, "printed", False):
                printed += 1
            else:
                handwritten += 1
    total = printed + handwritten
    typed = printed >= 10 and handwritten <= max(3, int(0.1 * total))
    return typed, printed, handwritten


def question_labels_found(ocr_pages, numbers) -> int:
    """How many of the paper's question numbers the student labelled an answer with."""
    wanted = {int(n) for n in numbers}
    found: set[int] = set()
    for page in list(ocr_pages or []):
        for line in list(getattr(page, "lines", None) or []):
            match = _LABEL_RE.match(readable_ocr_text(str(getattr(line, "text", "") or "")))
            if match and int(match.group(1)) in wanted:
                found.add(int(match.group(1)))
    return len(found)


def lines_run_vertically(page: "PageOCR") -> tuple[bool, int, int]:
    """Whether this page's writing was read sideways: (sideways?, tall lines, lines).

    On an upright page a line of handwriting is a wide, low box. When the page is
    fed to OCR sideways the same lines come back as tall, narrow boxes with
    unreadable text: on the Accountancy copy c0f259bb (2026-09-17) the orientation
    probe turned two upright pages by 90 degrees and every one of their OCR lines
    was over four times taller than wide. Three or more such lines making up at
    least 60% of the page's text lines is the signal; a lone tall box (a bracket,
    a column of figures) is not.
    """
    tall = total = 0
    for line in list(getattr(page, "lines", None) or []):
        if str(getattr(line, "kind", "text") or "text") != "text":
            continue
        text = str(getattr(line, "text", "") or "").strip()
        if len(text) < 3:
            continue
        x0, y0, x1, y1 = line.bbox
        w, h = max(1.0, float(x1 - x0)), max(1.0, float(y1 - y0))
        total += 1
        if h > 1.6 * w:
            tall += 1
    return (tall >= 3 and tall >= 0.6 * total), tall, total


# An answer list is many short tokens. "(47) A" is six characters and its box is
# roughly square whichever way up the page is, so the tall-and-narrow test above
# cannot judge such a page at all - it is not evidence of uprightness, it is an
# absence of evidence.
_SHORT_TOKEN_CHARS = 8
_SHORT_TOKEN_SHARE = 0.7
_SHORT_TOKEN_MIN_LINES = 8


def short_token_page(page: "PageOCR") -> bool:
    """Whether this page is mostly short tokens, so line shape cannot judge its turn.

    MEASURED 18 Nov 2026, Bihar Board Class 11 Economics row 164: page 1 is a
    photograph of a ruled notebook page holding fifty MCQ answers written as "(47) A".
    Taken sideways, the printed rules run across the image and look exactly like lines
    of text, so the free orientation heuristic scored it 0.805 upright against -0.619
    for a quarter turn - confidently backwards. lines_run_vertically did not catch it
    either: these boxes are not tall and narrow. The page was graded sideways, its
    letters misread, and the copy came out 21 marks above the evaluator, crediting a
    question whose answer was A against a key of B and one the student left blank.
    """
    short = total = 0
    for line in list(getattr(page, "lines", None) or []):
        if str(getattr(line, "kind", "text") or "text") != "text":
            continue
        text = str(getattr(line, "text", "") or "").strip()
        if not text:
            continue
        total += 1
        if len(text) <= _SHORT_TOKEN_CHARS:
            short += 1
    return (total >= _SHORT_TOKEN_MIN_LINES
            and short >= _SHORT_TOKEN_SHARE * total)


def mark_struck_lines(page: "PageOCR", page_png: bytes, scale: float = 1.0) -> int:
    """Flag cancelled lines on `page` in place; returns how many were found."""
    struck = detect_struck_lines(page_png, page.lines, scale=scale)
    for ln in page.lines:
        ln.cancelled = ln.line_id in struck
    return len(struck)


def ocr_all_pages(
    png_pages: list[bytes],
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    max_workers: int = _OCR_CONCURRENCY,
) -> list[PageOCR]:
    """OCR every page, a few in flight at a time.

    Pages are independent and each call is I/O-bound on the proxy (~2-4s), so a
    20-page booklet took 40-80s serially before the grading call even started.
    Order is preserved, and an auth/hard error still propagates from pool.map.
    """
    if not png_pages:
        return []
    if len(png_pages) == 1:
        page = ocr_page(png_pages[0], 1, google_token=google_token,
                        task_id=task_id, filename=filename)
        mark_struck_lines(page, png_pages[0])
        return [page]

    def run(item: tuple[int, bytes]) -> PageOCR:
        index, png = item
        page = ocr_page(png, index, google_token=google_token,
                        task_id=task_id, filename=filename)
        # Free and local: the strike is measured off the same PNG we just sent.
        mark_struck_lines(page, png)
        return page

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(png_pages)))) as pool:
        pages = list(pool.map(run, enumerate(png_pages, start=1)))
    total = sum(1 for p in pages for ln in p.lines if ln.cancelled)
    if total:
        print(f"[ocr] {total} line(s) look struck through and will be treated as "
              f"cancelled working", flush=True)
    return pages


def build_transcript(pages: list[PageOCR]) -> str:
    """Build a combined OCR transcript for the full student submission, with page headers.

    Lines that are question/section headers are tagged `(HEADER)` so the model
    knows not to anchor step ticks there. Diagram regions are tagged
    `(DIAGRAM REGION)`.
    """
    parts: list[str] = []
    for p in pages:
        parts.append(f"=== Page {p.page} ===")
        for ln in p.lines:
            tag = ""
            if getattr(ln, "cancelled", False):
                # The transcript reads identically for struck and live text, which is
                # how cancelled working came to be marked. Say it explicitly.
                tag = " (CANCELLED — the student crossed this out; do not grade it or anchor to it)"
            elif "D" in ln.line_id and "L" not in ln.line_id:
                tag = " (DIAGRAM REGION — use this anchor for steps whose evidence is a drawing/figure/table)"
            elif is_header_line(ln.text):
                tag = " (HEADER — never use as step_line_id)"
            parts.append(f"[{ln.line_id}] {ln.text}{tag}")
    return "\n".join(parts)


def synthesize_diagram_regions(page: PageOCR, min_gap_px: int = 180) -> PageOCR:
    """Add synthetic diagram-region anchors for big vertical gaps between OCR
    lines. OCR can't see hand-drawn diagrams, but a wide blank band between
    text lines is almost always a figure — give it an anchor like 'P7D1' so
    the grader can target it.

    The page keeps the reading order the OCR (and order_spread_lines) gave it; each
    new region goes in just before the line under its gap. Re-sorting the whole page
    by height undid that order (Parishram Chemistry, 2026-09-20): the two pages of a
    notebook photo were interleaved row by row, and a reaction scheme drawn a few
    pixels above its "(9)" label was handed to the answer before it.
    """
    text_lines = [l for l in page.lines if not l.line_id.startswith(f"P{page.page}D")]
    if not text_lines:
        return page
    sorted_lines = sorted(text_lines, key=lambda l: l.bbox[1])
    W = page.image_width
    prev_bottom = sorted_lines[0].bbox[3]
    diag_idx = 1
    margin_x = max(40, W // 30)
    new_lines: list[LineBox] = []
    before: dict[int, list[LineBox]] = {}
    for line in sorted_lines[1:]:
        y0 = line.bbox[1]
        detected_in_gap = any(l.kind in {"diagram", "chart", "table"}
                              and l.bbox[1] < y0 and l.bbox[3] > prev_bottom
                              for l in page.lines)
        if y0 - prev_bottom >= min_gap_px and not detected_in_gap:
            while any(l.line_id == f"P{page.page}D{diag_idx}" for l in page.lines):
                diag_idx += 1
            new_lines.append(LineBox(
                line_id=f"P{page.page}D{diag_idx}",
                text="[diagram region — likely a figure/diagram/table the OCR could not transcribe]",
                bbox=(margin_x, prev_bottom + 8, W - margin_x, y0 - 8),
                page=page.page,
                kind="synthetic_region",
            ))
            before.setdefault(id(line), []).append(new_lines[-1])
            diag_idx += 1
        prev_bottom = max(prev_bottom, line.bbox[3])
    if new_lines:
        ordered: list[LineBox] = []
        for line in page.lines:
            ordered.extend(before.get(id(line), ()))
            ordered.append(line)
        page.lines[:] = ordered
    return page


def overlay_anchors_on_png(png_bytes: bytes, page_ocr: PageOCR) -> bytes:
    """Draw each line_id label directly on the page image, so the model can
    map visual position to line_id at a glance instead of having to infer it
    from the textual transcript alone. This is the biggest accuracy win for
    step-mark placement.

    - Text lines: small blue label `[PnLk]` in the right margin of each line.
    - Diagram regions: light-orange outlined box + label so the model can pick
      a diagram anchor for figure/table-based steps.
    - Headers: red `[PnLk]` label so the model never anchors a step there.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(16, W // 90)
    font = None
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    def label_size(t):
        b = draw.textbbox((0, 0), t, font=font)
        return b[2] - b[0], b[3] - b[1]

    for ln in page_ocr.lines:
        x0, y0, x1, y1 = ln.bbox
        is_diagram = "D" in ln.line_id and "L" not in ln.line_id
        is_header = (not is_diagram) and is_header_line(ln.text)

        text = ln.line_id
        tw, th = label_size(text)
        # Default placement: just past the right end of the line
        lx = x1 + 10
        ly = (y0 + y1) / 2 - th / 2
        # Wrap to left margin if it would fall off the page
        if lx + tw + 6 > W - 2:
            lx = max(4, x0 - tw - 12)

        if is_diagram:
            bg = (255, 235, 200, 230)
            fg = (200, 90, 0)
            # outline the diagram region itself
            draw.rectangle([x0, y0, x1, y1], outline=fg + (200,), width=2)
            # Place the label inside the top-left of the region
            lx = x0 + 6
            ly = y0 + 6
        elif is_header:
            bg = (255, 220, 220, 220)
            fg = (180, 30, 30)
        else:
            bg = (220, 235, 255, 215)
            fg = (10, 50, 160)

        draw.rectangle(
            [lx - 3, ly - 2, lx + tw + 4, ly + th + 3],
            fill=bg,
            outline=fg + (220,),
            width=1,
        )
        draw.text((lx, ly), text, fill=fg, font=font)

    combined = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    combined.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
