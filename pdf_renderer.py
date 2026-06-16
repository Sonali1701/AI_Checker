"""Render the evaluated PDF: cover page + annotated student pages + summary page."""
from __future__ import annotations

import hashlib
import math
import os
import re
from io import BytesIO

import fitz  # PyMuPDF

from grader import GradeReport
from mathpix import PageOCR

RED = (0.80, 0.08, 0.10)
BLACK = (0, 0, 0)
# Backwards compat — anything that still imports GREEN gets red instead.
GREEN = RED


# ---------- Kalam font for handwritten remarks & marks ----------

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Use Kalam font from project folder
_CURSIVE_FONT_CANDIDATES = [
    os.path.join(_PROJECT_DIR, "Kalam-Regular.ttf"),  # Kalam font in project folder
    os.path.join(_PROJECT_DIR, "kalam_regular.ttf"),  # Alternative lowercase filename
    os.path.join(_PROJECT_DIR, "fonts", "cursive.ttf"),
    "C:\\Windows\\Fonts\\segoesc.ttf",   # Segoe Script (Windows)
    "C:\\Windows\\Fonts\\LHANDW.TTF",    # Lucida Handwriting (Windows)
    "C:\\Windows\\Fonts\\FRSCRIPT.TTF",  # French Script (Windows)
    "/usr/share/fonts/truetype/caveat/Caveat-Regular.ttf",  # Linux (if user installs Caveat)
    "/Library/Fonts/Bradley Hand Bold.ttf",  # macOS
]
_CURSIVE_FONT_FILE: str | None = None
for _p in _CURSIVE_FONT_CANDIDATES:
    if os.path.exists(_p):
        _CURSIVE_FONT_FILE = _p
        break


def _sanitize_for_pdf(text: str) -> str:
    """Sanitize text for PDF output, handling special Unicode characters.

    Converts Greek letters and special math symbols to their readable ASCII equivalents
    to avoid encoding issues in PDF rendering.
    """
    if not text:
        return text

    # Map of special characters that might cause encoding issues
    replacements = {
        'α': 'a',      # Greek alpha
        'β': 'b',      # Greek beta
        'γ': 'g',      # Greek gamma
        'δ': 'd',      # Greek delta
        'ε': 'e',      # Greek epsilon
        'λ': 'l',      # Greek lambda
        'π': 'pi',     # Greek pi
        'σ': 's',      # Greek sigma
        'θ': 'th',     # Greek theta
        'ω': 'w',      # Greek omega
        '±': '+/-',    # Plus-minus
        '≈': '~',      # Approximately equal
        '≠': '!=',     # Not equal
        '≤': '<=',     # Less than or equal
        '≥': '>=',     # Greater than or equal
        '√': 'sqrt',   # Square root
        '∞': 'inf',    # Infinity
        '∑': 'sum',    # Summation
        '∫': 'integral',  # Integral
        '→': '->',     # Arrow
        '←': '<-',     # Left arrow
    }

    result = text
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)

    return result


def _register_cursive_font(page: fitz.Page) -> tuple[str, str | None]:
    """Register the cursive font on this page if available.

    Returns (fontname, fontfile). If no TTF is found, returns ('tiit', None) —
    PyMuPDF's built-in Times Italic, which is the closest base-14 fallback.
    """
    if _CURSIVE_FONT_FILE is None:
        return ("tiit", None)
    fontname = "cursive"
    try:
        page.insert_font(fontname=fontname, fontfile=_CURSIVE_FONT_FILE)
        return (fontname, _CURSIVE_FONT_FILE)
    except Exception:
        return ("tiit", None)


def _draw_handwritten_remark(page: fitz.Page, x: float, y: float,
                             text: str, max_width: float = 280.0,
                             size: float = 13.0,
                             fontname: str | None = None,
                             draw_bg: bool = False) -> float:
    """Write a short red 'teacher remark' near (x, y).

    `y` is the BASELINE of the first line. Returns the y of the last baseline
    so callers can chain. Wraps to `max_width`.

    - fontname=None  → cursive/handwriting font (Segoe Script / Caveat / bundled
      cursive.ttf, else Times Italic). Looks hand-written but is thin on busy pages.
    - fontname="helv" (or any base-14 name) → use it directly. Crisp and legible.
    - draw_bg=True → paint a near-opaque white box behind each line first, so the
      red text stands out even over faint ruling. Only safe in blank bands.
    """
    if not text or not text.strip():
        return y
    # Register the font on this page ONCE. After that, only the fontname is
    # needed — passing fontfile to insert_text on every call makes PyMuPDF
    # silently drop back to Helvetica. When the caller picks an explicit base-14
    # font (e.g. "helv") there is no fontfile to register or measure with.
    if fontname is None:
        fontname, fontfile = _register_cursive_font(page)
    else:
        fontfile = None
    text = text.strip()
    # Sanitize text to avoid encoding issues with special characters
    text = _sanitize_for_pdf(text)

    # Width measurement: for a registered TTF, get_text_length needs the
    # fontfile path to look up metrics. For base14 'tiit' it doesn't.
    def _measure(s: str) -> float:
        try:
            if fontfile:
                return fitz.get_text_length(s, fontname=fontname,
                                            fontsize=size, fontfile=fontfile)
            return fitz.get_text_length(s, fontname=fontname, fontsize=size)
        except Exception:
            return len(s) * size * 0.55

    # Word-wrap manually so we can choose where each line breaks.
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        trial = (current + " " + w).strip()
        if _measure(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)

    line_gap = size * 1.35
    cy = y
    for ln in lines:
        if draw_bg:
            # White backing so the note reads cleanly against ruling / faint ink.
            # The band finder already guarantees this slot is clear of student
            # writing, so we are only painting over blank paper / ruled lines.
            w = _measure(ln)
            # Pad generously on the right/bottom — cursive slants forward and
            # has descenders (g, y, p) that drop below the baseline.
            bg = fitz.Rect(x - 3.0, cy - size, x + w + size * 0.6, cy + size * 0.45)
            try:
                page.draw_rect(bg, fill=(1, 1, 1), color=None,
                               fill_opacity=0.88, width=0)
            except Exception:
                pass
        # The font is already registered on this page — DO NOT pass fontfile
        # here, or PyMuPDF will reset to Helvetica.
        # NOTE: do NOT use render_mode=2 / border_width to fake bold — PyMuPDF's
        # border_width is relative to fontsize, so even a small value over-strokes
        # the glyphs into solid red bars that bury the text.
        try:
            page.insert_text((x, cy), ln, fontsize=size, color=RED,
                             fontname=fontname)
        except Exception:
            # Fallback: try with helvetica-bold which has better Unicode support
            try:
                page.insert_text((x, cy), ln, fontsize=size, color=RED, fontname="hebo")
            except Exception:
                # Final fallback: use helvetica
                page.insert_text((x, cy), ln, fontsize=size, color=RED, fontname="helv")
        cy += line_gap
    return cy - line_gap


def _wobble(seed: str, i: int, amp: float = 1.0) -> float:
    """Deterministic small jitter in [-amp, +amp] derived from seed+i (hand-drawn feel)."""
    h = hashlib.md5(f"{seed}:{i}".encode()).digest()
    return ((h[0] / 255.0) - 0.5) * 2.0 * amp


def _tapered_stroke(page: fitz.Page, pts: list[tuple[float, float]],
                    w_start: float, w_end: float, seed: str = "") -> None:
    """Draw a polyline from pts with width tapering from w_start to w_end.

    Each segment is sub-divided so the width changes smoothly. Small positional
    jitter is added so the stroke doesn't look mechanically straight.
    """
    if len(pts) < 2:
        return
    # Build a cumulative length to map width along the whole stroke.
    seg_lens = []
    total = 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        d = math.hypot(bx - ax, by - ay)
        seg_lens.append(d)
        total += d
    if total <= 0:
        return

    SUBDIV = 6  # sub-segments per polyline segment
    travelled = 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        seg = seg_lens[i]
        for s in range(SUBDIV):
            t0 = s / SUBDIV
            t1 = (s + 1) / SUBDIV
            x0 = ax + (bx - ax) * t0
            y0 = ay + (by - ay) * t0
            x1 = ax + (bx - ax) * t1
            y1 = ay + (by - ay) * t1
            # global progress for width
            g = (travelled + seg * (t0 + t1) / 2) / total
            w = w_start + (w_end - w_start) * g
            # jitter perpendicular-ish so it stays close to the line
            jx = _wobble(seed or "s", i * SUBDIV + s, amp=0.25)
            jy = _wobble(seed or "s", i * SUBDIV + s + 99, amp=0.25)
            page.draw_line(
                fitz.Point(x0 + jx, y0 + jy),
                fitz.Point(x1 + jx, y1 + jy),
                color=RED, width=max(0.3, w),
            )
        travelled += seg


def _draw_tick(page: fitz.Page, x: float, y: float, size: float = 26,
               seed: str = "") -> None:
    """Hand-drawn red tick: fat at the start, thin at the tail.

    Anchored so the elbow sits near (x + size*0.30, y). The tail extends
    long and upward — like a teacher's flick.
    """
    p1 = (x, y - size * 0.25)
    p2 = (x + size * 0.30, y + size * 0.20)
    p3 = (x + size * 1.50, y - size * 1.40)
    w_fat = max(1.6, size * 0.20)
    w_thin = max(0.4, size * 0.05)
    _tapered_stroke(page, [p1, p2, p3], w_fat, w_thin, seed=seed or f"t{x:.1f}{y:.1f}")


def _draw_cross(page: fitz.Page, x: float, y: float, size: float = 10,
                seed: str = "") -> None:
    """Hand-drawn red cross centred at (x, y) with tapered strokes."""
    h = size / 2
    w_fat = max(0.9, size * 0.15)  # Reduced from 0.22
    w_thin = max(0.3, size * 0.06)  # Reduced from 0.09
    _tapered_stroke(page, [(x - h, y - h), (x + h, y + h)], w_fat, w_thin,
                    seed=seed or f"x1{x:.1f}{y:.1f}")
    _tapered_stroke(page, [(x + h, y - h), (x - h, y + h)], w_fat, w_thin,
                    seed=seed or f"x2{x:.1f}{y:.1f}")


# --- Hand-drawn digit shapes (unit box: x ∈ [0, W], y ∈ [0, 1], top-left origin) ---
# Each entry: (relative_width, [polyline, polyline, ...]) where polylines are
# the pen strokes used to draw the digit.
_DIGIT_PATHS: dict[str, tuple[float, list[list[tuple[float, float]]]]] = {
    # rel_w must >= rightmost x in the path, or the next glyph starts inside
    # this one (which made "0.5" render as "O5" — the dot was hidden inside
    # the 0). 0 reaches 0.93; 1's crossbar reaches 0.70.
    "0": (0.98, [[
        (0.50, 0.02), (0.78, 0.10), (0.93, 0.32), (0.93, 0.60), (0.80, 0.88),
        (0.52, 0.98), (0.22, 0.90), (0.05, 0.65), (0.05, 0.35), (0.22, 0.10),
        (0.45, 0.02),
    ]]),
    "1": (0.75, [[(0.05, 0.28), (0.42, 0.05), (0.42, 0.95)],
                 [(0.15, 0.95), (0.70, 0.95)]]),
    "2": (0.78, [[
        (0.05, 0.25), (0.18, 0.07), (0.42, 0.03), (0.62, 0.15), (0.65, 0.38),
        (0.42, 0.60), (0.18, 0.78), (0.05, 0.95), (0.72, 0.95),
    ]]),
    "3": (0.74, [[
        (0.05, 0.18), (0.20, 0.05), (0.45, 0.05), (0.62, 0.20), (0.55, 0.40),
        (0.32, 0.48), (0.58, 0.55), (0.68, 0.75), (0.52, 0.93), (0.22, 0.95),
        (0.05, 0.82),
    ]]),
    "4": (0.78, [[(0.52, 0.05), (0.05, 0.65), (0.72, 0.65)],
                 [(0.50, 0.32), (0.52, 0.95)]]),
    "5": (0.74, [[
        (0.60, 0.08), (0.18, 0.08), (0.10, 0.45), (0.32, 0.40), (0.55, 0.48),
        (0.68, 0.68), (0.55, 0.90), (0.28, 0.95), (0.08, 0.85),
    ]]),
    "6": (0.72, [[
        (0.62, 0.12), (0.40, 0.05), (0.18, 0.22), (0.05, 0.52), (0.05, 0.78),
        (0.22, 0.95), (0.48, 0.95), (0.65, 0.78), (0.55, 0.58), (0.30, 0.55),
        (0.10, 0.65),
    ]]),
    "7": (0.74, [[(0.05, 0.08), (0.68, 0.08), (0.30, 0.95)]]),
    "8": (0.74, [[
        (0.38, 0.48), (0.20, 0.38), (0.12, 0.22), (0.28, 0.05), (0.48, 0.08),
        (0.62, 0.22), (0.55, 0.40), (0.38, 0.48), (0.20, 0.55), (0.05, 0.72),
        (0.22, 0.92), (0.50, 0.95), (0.68, 0.78), (0.55, 0.58), (0.38, 0.48),
    ]]),
    "9": (0.72, [[
        (0.62, 0.45), (0.42, 0.52), (0.18, 0.45), (0.08, 0.25), (0.22, 0.08),
        (0.48, 0.05), (0.65, 0.20), (0.62, 0.45), (0.58, 0.70), (0.42, 0.92),
        (0.15, 0.95),
    ]]),
    # A visible round dot at baseline — small horizontal footprint so it
    # hugs the preceding digit, but drawn as a tight closed loop with enough
    # passes that the pen actually leaves an inked spot (not an invisible line).
    ".": (0.22, [
        [(0.05, 0.86), (0.11, 0.82), (0.17, 0.86), (0.17, 0.92),
         (0.11, 0.96), (0.05, 0.92), (0.05, 0.86)],
        [(0.07, 0.88), (0.13, 0.86), (0.15, 0.90), (0.13, 0.94),
         (0.07, 0.92), (0.07, 0.88)],
    ]),
}


def _draw_digit(page: fitz.Page, x: float, y_top: float, ch: str,
                size: float, seed: str = "") -> float:
    """Draw one hand-drawn digit at (x, y_top). Returns x of right edge."""
    spec = _DIGIT_PATHS.get(ch)
    if spec is None:
        return x  # silently skip unknown chars
    # Special case the decimal point: a polyline that small never inks visibly,
    # so we drop a filled disk at the baseline instead.
    if ch == ".":
        cx = x + 0.10 * size + _wobble(seed, 0, amp=0.4)
        cy = y_top + 0.90 * size + _wobble(seed, 1, amp=0.4)
        r = max(1.4, size * 0.085)
        page.draw_circle(fitz.Point(cx, cy), r, color=RED, fill=RED, width=0.5)
        return x + 0.22 * size
    rel_w, strokes = spec
    w_fat = max(1.1, size * 0.13)
    w_thin = max(0.5, size * 0.07)
    for si, stroke in enumerate(strokes):
        pts = [(x + px * size + _wobble(seed, si * 50 + i, amp=0.35),
                y_top + py * size + _wobble(seed, si * 50 + i + 17, amp=0.35))
               for i, (px, py) in enumerate(stroke)]
        _tapered_stroke(page, pts, w_fat, w_thin, seed=f"{seed}|d{si}")
    return x + rel_w * size


def _draw_number(page: fitz.Page, x: float, y_center: float, text: str,
                 size: float = 15, seed: str = "") -> float:
    """Draw a hand-drawn number string. y_center is vertical mid-line of digits.

    Returns x of the right edge for downstream layout.
    """
    y_top = y_center - size / 2
    cur_x = x
    for i, ch in enumerate(text):
        cur_x = _draw_digit(page, cur_x, y_top, ch, size, seed=f"{seed}|{i}{ch}")
        # Tighter kerning around a dot so "0.5" doesn't visually split into "0 5".
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "." or nxt == ".":
            cur_x += size * 0.02
        else:
            cur_x += size * 0.08
    return cur_x


def _number_width(text: str, size: float = 15) -> float:
    """Approximate rendered width of a hand-drawn number, for layout."""
    w = 0.0
    for i, ch in enumerate(text):
        spec = _DIGIT_PATHS.get(ch)
        if spec:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            kern = size * (0.02 if ch == "." or nxt == "." else 0.08)
            w += spec[0] * size + kern
    return w


def _draw_strikethrough(page: fitz.Page, x: float, y: float, width: float) -> None:
    """Hand-drawn red strikethrough with slight wobble and width taper."""
    seed = f"st{x:.1f}{y:.1f}{width:.1f}"
    steps = max(8, int(width / 6))
    pts = []
    for i in range(steps + 1):
        t = i / steps
        px = x + width * t
        py = y + _wobble(seed, i, amp=0.6)
        pts.append((px, py))
    _tapered_stroke(page, pts, w_start=1.6, w_end=0.8, seed=seed)


def _draw_hand_circle(page: fitz.Page, cx: float, cy: float,
                      rx: float, ry: float, seed: str = "") -> None:
    """Draw a wobbly hand-drawn red circle around (cx, cy)."""
    seed = seed or f"c{cx:.1f}{cy:.1f}"
    # Slightly tilt the ellipse for human feel
    tilt = _wobble(seed, 0, amp=0.18)
    cos_t, sin_t = math.cos(tilt), math.sin(tilt)
    # Start a little past 0 and overshoot — like a real circle pen-stroke
    start = _wobble(seed, 1, amp=0.4)
    end = 2 * math.pi + 0.35 + _wobble(seed, 2, amp=0.3)
    N = 56
    pts = []
    for i in range(N + 1):
        a = start + (end - start) * (i / N)
        # radius wobble
        r_jx = 1.0 + _wobble(seed, 10 + i, amp=0.06)
        r_jy = 1.0 + _wobble(seed, 200 + i, amp=0.06)
        lx = rx * r_jx * math.cos(a)
        ly = ry * r_jy * math.sin(a)
        # rotate
        x = cx + lx * cos_t - ly * sin_t
        y = cy + lx * sin_t + ly * cos_t
        pts.append((x, y))
    # variable width: a touch fatter on the downstroke, thinner at the tail
    _tapered_stroke(page, pts, w_start=1.4, w_end=0.7, seed=seed)


def _draw_number_in_circle(page: fitz.Page, x: float, y: float, text: str,
                           size: float = 11, seed: str = "") -> None:
    """Draw a number inside a perfect circle with bolder, centered numbers."""
    # Significantly larger circles with proportional number size
    is_decimal = "." in text
    if is_decimal:
        radius = size * 1.9 + 1.5  # Much larger circle for decimals
        font_size = size * 1.9  # Much larger font for decimals
    else:
        radius = size * 1.8 + 1.5  # Much larger circle for whole numbers
        font_size = size * 1.8  # Much larger font size

    # Draw circle with thicker border for better visibility
    page.draw_circle(fitz.Point(x, y), radius, color=RED, width=1.8)

    # Draw the number TRULY centered in the circle.
    # insert_text positions text by its BASELINE, so we must find the glyph's
    # actual visual bounding box and shift the baseline so the glyph's center
    # lands exactly on the circle center (x, y).
    try:
        font = fitz.Font("hebo")  # Helvetica-Bold
        text_width = font.text_length(text, fontsize=font_size)

        # Glyph vertical extent relative to the baseline (y=0).
        # glyph_bbox returns values already in EM units (fraction of font size),
        # with +y UP, so y1 is the top of the glyph and y0 the bottom (~0 for digits).
        y0s, y1s = [], []
        for ch in text:
            bb = font.glyph_bbox(ord(ch))
            y0s.append(bb.y0)
            y1s.append(bb.y1)
        # Scale em-unit extents to the actual font size.
        glyph_top = max(y1s) * font_size     # distance above baseline
        glyph_bottom = min(y0s) * font_size  # distance above baseline (<=0)

        # Visual center of the glyph measured UPWARD from the baseline.
        glyph_center_above_baseline = (glyph_top + glyph_bottom) / 2.0

        # In PDF page coords +y is DOWN. We want the glyph's visual center at y.
        # baseline_y (page) - glyph_center_above_baseline == y  =>
        baseline_y = y + glyph_center_above_baseline

        page.insert_text((x - text_width / 2, baseline_y), text, fontsize=font_size,
                        color=RED, fontname="hebo")
    except Exception:
        # Fallback to hand-drawn numbers if bold font fails
        _draw_number(page, x - _number_width(text, font_size) / 2, y + font_size / 2.5, text,
                     size=font_size, seed=seed + "n")


# ----- OCR coordinate translation -----

_HEADER_PREFIXES = ("q.", "q ", "q)")
_SECTION_PREFIX = "section"

# A bare question label like "Q2", "Q2)", "Q.27)", "Q 1." — i.e. the whole short
# line is just a question number, with no answer content. These get a tick
# dropped on them if not recognised as headers (the "1 on the next page" bug).
_QHEADER_RE = re.compile(r"^q\s*\.?\s*\d+\s*[).:\-]*$")

# A line that STARTS a (sub-)question block: a "Qn" label FOLLOWED BY a separator
# — "Q6)", "Q.6", "Q6:", or "Q6) Evaluate ...". Used to trim a neighbouring
# question's text out of a band (a band runs up to the next question's anchor,
# so the next question's first line can bleed in). The trailing separator is
# REQUIRED so genuine equation lines that merely start with "Qn" — e.g. the
# physics variables "Q1 = 2Q2", "Q1 = mc(T2-T1)", "q1=q2+q3" — are NOT mistaken
# for a question label and dropped. Bare labels ("Q6") are caught by _QHEADER_RE.
# The comma is included because these answer sheets label questions "Q1,", "Q2,,".
_QLABEL_LEAD_RE = re.compile(r"^q\s*\.?\s*\d+\s*[).:,\-]")

# Hindi / Devanagari equivalents so Devanagari sheets get the same header-skipping
# and band-trimming as English. "प्रश्न" = "question", "प्र." its abbreviation,
# "खंड"/"खण्ड"/"भाग" = section/part. Devanagari digits ०-९ and the danda "।" act
# like Western digits and separators. Conservative on purpose: a bare "प्र" is a
# prefix of ordinary words (प्रकाश "light", प्रकार "type"), so we only treat it as
# a label when it carries the abbreviation dot/sign or a number/separator —
# never bare — to avoid trimming or skipping real answer lines.
_HI_SECTION_PREFIXES = ("खंड", "खण्ड", "भाग")
# Bare Hindi label occupying a short line: "प्रश्न", "प्रश्न १", "प्र. 2".
_QHEADER_HI_RE = re.compile(r"^(?:प्रश्न|प्र[.॰])\s*[\d०-९]*\s*[).:।,\-]*$")
# A line that STARTS a Hindi question block (label + content) — used for trimming.
_QLABEL_LEAD_HI_RE = re.compile(r"^(?:प्र[.॰]|प्रश्न\s*(?:[\d०-९]|[).:।,\-]))")

# Bare sub-part labels in every common form: "(i)", "i)", "ii.", "a)", "(b)" ...
# Built from an explicit roman/letter list so we never mis-flag real words that
# happen to be made of roman-numeral letters (e.g. "mix", "lid").
_LABEL_TOKENS: set[str] = set()
for _t in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
           "a", "b", "c", "d", "e", "f", "g", "h"):
    for _body in (_t, f"({_t})", f"({_t}", f"{_t})"):
        for _suf in ("", ".", ")", ":"):
            _LABEL_TOKENS.add(_body + _suf)
_LONE_ROMAN = _LABEL_TOKENS  # kept for backwards-compat references

# Form/cover-page labels that OCR catches but are NOT student writing.
# A tick anchored here ends up floating at the top of the page next to the
# candidate's name field, which is exactly the wrong place.
_FORM_LABEL_SNIPPETS = (
    "name of the student", "candidate signature", "invigilator", "vidyapeeth",
    "selection hoga", "erp no", "batch code", "subject-", "subject :", "subject:",
    "date -", "date:", "date :",
)


def _is_header(text: str) -> bool:
    """A line is a 'header' if it's just a question/section label, not student working.

    Key nuance: a line like 'Q1) i) new snow or rain.' starts with 'q1)' but has
    a real answer on the same line — that's NOT a header. Only short bare labels
    (or any line matching a form-field snippet) are headers.
    """
    s = text.strip()
    sl = s.lower()
    if not sl:
        return True
    # Form/cover-page labels are headers even when long (e.g. 'Name of the Student (In CAPITALS): SAKSHAM TORA').
    for snip in _FORM_LABEL_SNIPPETS:
        if snip in sl:
            return True
    # Long lines past 22 chars are treated as answer lines — they almost
    # always contain real content past the label.
    if len(sl) > 22:
        return False
    if sl.startswith(_HEADER_PREFIXES):
        return True
    if _QHEADER_RE.match(sl):          # bare "Q2", "Q2)", "Q.27)", "Q 1."
        return True
    if _QHEADER_HI_RE.match(sl):       # bare "प्रश्न", "प्रश्न १", "प्र. 2"
        return True
    if sl.startswith(_SECTION_PREFIX) and len(sl) <= 14:
        return True
    if sl.startswith(_HI_SECTION_PREFIXES) and len(sl) <= 14:
        return True
    if sl in _LABEL_TOKENS:            # "(i)", "i)", "ii.", "a)", "(b)" ...
        return True
    if len(sl) <= 5 and sl.startswith("(") and sl.endswith(")"):
        return True
    return False


def _estimate_remark_height(text: str, max_w: float, size: float) -> float:
    """Rough vertical extent the cursive remark will occupy once word-wrapped.

    We only need a conservative estimate to reserve a blank slot — the real
    wrapping happens in _draw_handwritten_remark."""
    chars_per_line = max(8, int(max_w / (size * 0.5)))
    n_lines = max(1, math.ceil(len(text.strip()) / chars_per_line))
    return n_lines * size * 1.3 + 6


# Per-document, per-page record of vertical bands already consumed by a teacher
# remark. Without this, every sub-question's note independently picks the SAME
# nearest blank gap and they overprint into garble. Keyed by id(doc) so it is
# reset per build (see _reset_remark_bands in build_evaluated_pdf).
_REMARK_BANDS: dict[int, dict[int, list[tuple[float, float]]]] = {}


def _reset_remark_bands(doc: fitz.Document) -> None:
    _REMARK_BANDS[id(doc)] = {}


def _remark_bands(doc: fitz.Document, page_num: int) -> list[tuple[float, float]]:
    return _REMARK_BANDS.setdefault(id(doc), {}).setdefault(page_num, [])


# ---------- Right-hand marks/remarks strip ----------
#
# Each student page is widened with a blank white strip on the right. All
# per-question ticks, scores, and remarks are written there — never on top of
# the student's writing, and never clipped at the page edge. `_CONTENT_W` records
# the ORIGINAL (left) content width per page so OCR pixel→PDF mapping still maps
# onto the writing area, not the widened page. `_STRIP_CURSOR` packs entries
# top-to-bottom within each page's strip so they never overlap each other.
STRIP_FRACTION = 0.24        # strip width as a fraction of the original page width
STRIP_MIN_WIDTH = 175.0      # but never narrower than this (points)
_STRIP_TOP = 40.0            # first entry starts below the strip header
_CONTENT_W: dict[tuple[int, int], float] = {}
_STRIP_CURSOR: dict[int, dict[int, float]] = {}


def _content_width(page: fitz.Page) -> float:
    """Width of the student-writing area (original page width), excluding the
    right-hand marks strip. Falls back to full page width for un-widened pages."""
    return _CONTENT_W.get((id(page.parent), page.number + 1), page.rect.width)


def _reset_strip_state(doc: fitz.Document) -> None:
    _STRIP_CURSOR[id(doc)] = {}


def _reserve_strip_slot(doc: fitz.Document, page_num: int, y_pref: float,
                        height: float, page_h: float) -> float:
    """Reserve a vertical slot of `height` in the strip near y_pref, never above
    the running cursor (so entries stack without overlap). Returns the top y."""
    reg = _STRIP_CURSOR.setdefault(id(doc), {})
    cursor = reg.get(page_num, _STRIP_TOP)
    y = max(y_pref, cursor)
    # If it would run off the bottom, pull it up as far as the cursor allows.
    if y + height > page_h - 8:
        y = max(cursor, page_h - 8 - height)
    reg[page_num] = y + height + 6.0
    return y


def _strip_wrap_lines(text: str, max_w: float, size: float,
                      fontname: str = "helv") -> int:
    """Count how many wrapped lines `text` needs at width max_w — for height
    reservation. Measured slightly wide so we over-reserve rather than overlap."""
    words = text.split()
    lines, cur = 1, ""
    for w in words:
        trial = (cur + " " + w).strip()
        if cur and fitz.get_text_length(trial, fontname=fontname, fontsize=size + 1) > max_w:
            lines += 1
            cur = w
        else:
            cur = trial
    return lines


def _draw_strip_entry(page: fitz.Page, qid: str, awarded: float,
                      max_score: float, remark: str, y_pref: float,
                      steps: list[float] | None = None) -> None:
    """Write one question's verdict in the right-hand strip: the score calculation
    and the remark. Tick/cross is now drawn directly on the sheet."""
    doc = page.parent
    rect = page.rect
    cw = _content_width(page)
    x0 = cw + 12.0
    x1 = rect.width - 8.0
    max_w = max(110.0, x1 - x0)

    full = max_score > 0 and awarded >= max_score - 1e-6
    zero = awarded <= 1e-6
    remark = (remark or "").strip()
    # Sanitize remark to avoid PDF encoding issues with special characters
    remark = _sanitize_for_pdf(remark)
    show_reason = bool(remark) and not full

    def _fmt(v: float) -> str:
        return str(int(v)) if float(v).is_integer() else f"{v:g}"

    # Show step-by-step calculation if steps are provided
    if steps and len(steps) > 1:
        # Show calculation with denominator: 0.5+1=1.5/2
        calc_txt = "+".join(_fmt(s) for s in steps) + f"={_fmt(awarded)}/{_fmt(max_score)}" if max_score else _fmt(awarded)
        head = f"{qid}"
        score_txt = calc_txt
    else:
        score_txt = f"{_fmt(awarded)}/{_fmt(max_score)}" if max_score else _fmt(awarded)
        head = f"{qid}  {score_txt}".strip()

    head_size = 12.0  # Increased from 11.0
    head_line_h = head_size * 1.4
    reason_size = 14.5  # Increased from 11.5 for better visibility

    # If we have steps, show calculation on its own line
    if steps and len(steps) > 1:
        reason_lines = _strip_wrap_lines(remark, max_w, reason_size) if show_reason else 0
        block_h = head_line_h * 2 + (reason_lines * reason_size * 1.32 + 8 if show_reason else 0) + 8
    else:
        reason_lines = _strip_wrap_lines(remark, max_w, reason_size) if show_reason else 0
        block_h = head_line_h + (reason_lines * reason_size * 1.32 + 8 if show_reason else 0) + 8

    y = _reserve_strip_slot(doc, page.number + 1, y_pref - head_size, block_h, rect.height)

    # Show question ID and calculation
    page.insert_text((x0, y + head_size), head, fontsize=head_size,
                     color=RED, fontname="hebo")

    if steps and len(steps) > 1:
        page.insert_text((x0, y + head_line_h * 2), score_txt, fontsize=head_size,
                         color=RED, fontname="hebo")
        reason_y = y + head_line_h * 2 + reason_size
    else:
        reason_y = y + head_line_h + reason_size

    if show_reason:
        _draw_handwritten_remark(page, x0, reason_y,
                                 remark, max_width=max_w, size=reason_size)


# Per-document, per-page bands of actual INK on the page image (handwriting,
# scribbles, crossed-out tables). The OCR only returns clean text lines, so it
# misses messy regions; scanning the image keeps remarks off real ink that the
# OCR never reported. Keyed by id(doc); populated in build_evaluated_pdf.
_INK_BANDS: dict[int, dict[int, list[tuple[float, float]]]] = {}


def _ink_intervals_from_png(png_bytes: bytes, pdf_h: float) -> list[tuple[float, float]]:
    """Scan a page image and return vertical bands (in PDF coords) that contain
    substantial ink — handwriting, scribbles, crossed-out tables.

    Thin full-width strokes (the notebook's printed ruling) are filtered out so
    that blank ruled lines still read as blank: a real line of writing occupies
    a band ~15-40px tall, whereas a printed rule is only 1-3px. Returns [] on any
    failure so the caller transparently falls back to OCR-only placement."""
    try:
        import io

        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = im.size
        if w == 0 or h == 0:
            return []
        # Downscale tall scans for speed; ~1000 rows keeps line-level resolution.
        target_h = min(h, 1000)
        if target_h < h:
            im = im.resize((max(1, w * target_h // h), target_h))
            w, h = im.size
        arr = np.asarray(im)                 # h×w, 0=black .. 255=white
        dark_frac = (arr < 150).mean(axis=1)  # fraction of dark pixels per row
        occupied = dark_frac > 0.012          # row has some ink

        sy = pdf_h / h
        # Group consecutive occupied rows into raw intervals (PDF coords).
        raw: list[tuple[float, float]] = []
        start = None
        for i, occ in enumerate(occupied):
            if occ and start is None:
                start = i
            elif not occ and start is not None:
                raw.append((start * sy, i * sy))
                start = None
        if start is not None:
            raw.append((start * sy, h * sy))

        # Bridge tiny vertical gaps (writing has internal whitespace between
        # words/letters) so one line of text stays a single band.
        merged: list[tuple[float, float]] = []
        for a, b in raw:
            if merged and a - merged[-1][1] <= 6.0:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))

        # Drop bands too thin to be writing — those are the printed ruling.
        return [(a, b) for a, b in merged if (b - a) >= 8.0]
    except Exception:
        return []


def _ink_bands(doc: fitz.Document, page_num: int) -> list[tuple[float, float]]:
    return _INK_BANDS.setdefault(id(doc), {}).get(page_num, [])


def _find_blank_band(page_lines: list[dict], y_from: float, needed_h: float,
                     page_h: float, search_limit: float = 9999.0,
                     extra_occupied: list[tuple[float, float]] | None = None
                     ) -> float | None:
    """Find the top y of the nearest vertical gap >= needed_h at or below y_from
    that is clear of every OCR line box AND every already-placed remark band.

    Returns None if no clear slot exists within search_limit of y_from (the
    caller then shrinks the font or falls back). Conservative: it treats ANY
    interval whose y-range overlaps the candidate band as a clash, so the note
    never lands on student ink or on a sibling note."""
    bottom = page_h - 10
    if y_from + needed_h > bottom:
        return None
    occ = [(pl["y0"], pl["y1"]) for pl in page_lines]
    if extra_occupied:
        occ.extend(extra_occupied)
    occ.sort(key=lambda t: t[0])
    cursor = y_from
    while cursor + needed_h <= bottom and cursor <= y_from + search_limit:
        clash = None
        for a, b in occ:
            if b <= cursor:
                continue              # interval entirely above the candidate band
            if a >= cursor + needed_h:
                break                 # sorted: nothing below clashes either
            clash = (a, b)
            break
        if clash is None:
            return cursor
        cursor = clash[1] + 3          # jump just below the clashing line
    return None


def _draw_remark_on_band(student_doc: fitz.Document, m, my_lines: list[dict],
                         by_lid: dict[str, dict] | None = None) -> None:
    """Scribble m.remark in cursive red in BLANK space near the last answer line.

    Only fires when (a) the model gave a remark and (b) the student lost marks.
    Anchors to the last NON-HEADER line of the band, then searches downward for
    a vertical gap clear of all student writing so the note never overlaps ink.
    """
    remark = (getattr(m, "remark", "") or "").strip()
    if not remark:
        return
    # Sanitize remark to avoid PDF encoding issues
    remark = _sanitize_for_pdf(remark)
    try:
        score = float(getattr(m, "score", 0) or 0)
        max_score = float(getattr(m, "max_score", 0) or 0)
    except (TypeError, ValueError):
        return
    if max_score > 0 and score >= max_score:
        return  # full marks — no margin note needed

    # Prefix the note with its sub-question id so it stays unambiguous even when
    # the nearest blank slot sits a little below the answer it refers to.
    qid = str(getattr(m, "qid", "") or "").strip()
    remark = f"{qid}: {remark}" if qid else remark
    real_lines = [pl for pl in my_lines if not _is_header(pl["text"])] or my_lines
    if not real_lines:
        return
    last = real_lines[-1]
    page_idx = last["page"] - 1
    if page_idx < 0 or page_idx >= len(student_doc):
        return
    page = student_doc[page_idx]
    rect = page.rect
    seed = f"{getattr(m, 'qid', '')}|rk"

    # Every OCR line box on this page — used to keep the note off student ink.
    page_lines: list[dict] = []
    if by_lid:
        page_lines = [pl for pl in by_lid.values() if pl.get("page") == last["page"]]

    # Start a little right of where the answer begins, with a wide column so the
    # note rarely needs more than two wrapped lines.
    rx = max(24.0, min(last["x0"] + 8 + _wobble(seed, 0, amp=3), rect.width - 230))
    max_w = max(190.0, rect.width - rx - 24)

    # Bands already taken by earlier notes on THIS page — so two sub-questions
    # never stack their reasons in the same gap and overprint into garble.
    taken = _remark_bands(student_doc, last["page"])
    # Plus every band of real ink on the page image — catches scribbles and
    # crossed-out tables the OCR never returned as text lines.
    occupied_extra = taken + _ink_bands(student_doc, last["page"])

    # Place the note in the nearest blank vertical slot below the answer that is
    # clear of student ink AND earlier notes. Shrink the font once if the
    # full-size note won't fit; only as a last resort drop it just below the line.
    size = 15.5
    needed_h = _estimate_remark_height(remark, max_w, size)
    slot_top = _find_blank_band(page_lines, last["y1"] + 6, needed_h, rect.height,
                                extra_occupied=occupied_extra)
    if slot_top is None:
        size = 12.5
        needed_h = _estimate_remark_height(remark, max_w, size)
        slot_top = _find_blank_band(page_lines, last["y1"] + 6, needed_h, rect.height,
                                    extra_occupied=occupied_extra)
    if slot_top is None:
        slot_top = min(last["y1"] + 6, rect.height - needed_h - 8)

    # Reserve this band so the next note on the page avoids it.
    taken.append((slot_top, slot_top + needed_h))

    ry = slot_top + size + _wobble(seed, 1, amp=1.0)  # baseline of first line
    # Cursive handwriting, no backing box — a larger size + the blank-band
    # placement (off student ink) keep it readable without looking fake.
    _draw_handwritten_remark(page, rx, ry, remark, max_width=max_w, size=size)


def _build_line_index(ocr_pages: list[PageOCR] | None) -> dict[str, tuple]:
    """Map line_id -> (page_num, bbox_px, image_w, image_h, line_text)."""
    idx: dict[str, tuple] = {}
    if not ocr_pages:
        return idx
    for p in ocr_pages:
        for line in p.lines:
            idx[line.line_id] = (line.page, line.bbox, p.image_width, p.image_height, line.text)
    return idx


def _pdf_coords_from_line(
    page: fitz.Page, bbox_px: tuple[float, float, float, float],
    img_w: int, img_h: int,
) -> tuple[float, float, float, float]:
    """Convert pixel bbox on the OCR'd image to PDF coords on the rendered page.

    Scales x by the CONTENT width (the original writing area), not the widened
    page width, so coordinates land on the student's writing — not in the strip."""
    rect = page.rect
    sx = _content_width(page) / img_w
    sy = rect.height / img_h
    x0, y0, x1, y1 = bbox_px
    return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)


def _word_x_in_line(line_text: str, target: str, line_x0: float, line_x1: float) -> float:
    """Approximate the x position of `target` within a line by character offset."""
    if not target or target not in line_text:
        return (line_x0 + line_x1) / 2
    idx = line_text.index(target)
    frac = (idx + len(target) / 2) / max(1, len(line_text))
    return line_x0 + frac * (line_x1 - line_x0)


def _draw_text(page: fitz.Page, x: float, y: float, text: str, *, size: float = 10,
               color=RED, fontname: str = "helv", max_width: float | None = None) -> float:
    """Draw text and return the y position after the last line."""
    if max_width is None:
        page.insert_text((x, y), text, fontsize=size, color=color, fontname=fontname)
        return y + size * 1.2

    # naive word wrap
    words = text.split()
    line = ""
    line_height = size * 1.25
    for w in words:
        trial = (line + " " + w).strip()
        # estimate width
        if fitz.get_text_length(trial, fontname=fontname, fontsize=size) > max_width:
            page.insert_text((x, y), line, fontsize=size, color=color, fontname=fontname)
            y += line_height
            line = w
        else:
            line = trial
    if line:
        page.insert_text((x, y), line, fontsize=size, color=color, fontname=fontname)
        y += line_height
    return y


def _draw_bulleted_remarks(page: fitz.Page, x: float, y: float, text: str, *,
                          size: float = 11, color=BLACK, fontname: str = "helv",
                          max_width: float = 515) -> float:
    """Draw remarks as bullet points for the student. Splits by periods and formats nicely."""
    if not text or not text.strip():
        return y

    # Replace "the student" with "you" to make it personal for the student
    text = text.replace("The student", "You").replace("the student", "you")

    # Split text by periods to create bullet points
    sentences = [s.strip() for s in text.split('.') if s.strip()]

    line_height = size * 1.35
    indent = 15
    bullet_x = x + 5
    bullet_indent = x + indent

    for sentence in sentences:
        # Add back period if it was removed by split
        if not sentence.endswith(('?', '!')):
            sentence = sentence + '.'

        # Word wrap each sentence
        words = sentence.split()
        lines_for_sentence = []
        current_line = ""

        for w in words:
            trial = (current_line + " " + w).strip()
            # estimate width (accounting for bullet space)
            if fitz.get_text_length(trial, fontname=fontname, fontsize=size) > max_width - indent - 5:
                if current_line:
                    lines_for_sentence.append(current_line)
                current_line = w
            else:
                current_line = trial

        if current_line:
            lines_for_sentence.append(current_line)

        # Draw all lines for this sentence, with bullet only on first line
        for line_idx, line in enumerate(lines_for_sentence):
            if line_idx == 0:
                # First line of sentence gets the bullet
                page.insert_text((bullet_x, y), "•", fontsize=size, color=color, fontname=fontname)
                page.insert_text((bullet_indent, y), line, fontsize=size, color=color, fontname=fontname)
            else:
                # Continuation lines are indented to match first line
                page.insert_text((bullet_indent, y), line, fontsize=size, color=color, fontname=fontname)
            y += line_height

    return y


def _cover_page(doc: fitz.Document, report: GradeReport) -> None:
    page = doc.new_page(width=595, height=842)  # A4
    # EVALUATED banner
    page.draw_rect(fitz.Rect(40, 40, 555, 100), color=RED, width=2)
    page.insert_text((180, 80), "EVALUATED", fontsize=36, color=RED, fontname="hebo")

    # Total
    page.insert_text(
        (40, 160),
        f"Total Score: {report.total_score} / {report.max_total}",
        fontsize=24, color=RED, fontname="hebo",
    )

    # Remarks (formatted as bullet points for the student)
    y = 220
    page.insert_text((40, y), "Remarks:", fontsize=14, color=BLACK, fontname="hebo")
    y = _draw_bulleted_remarks(page, 40, y + 22, report.overall_remarks, size=11, color=BLACK, max_width=515)

    # Section breakdown
    y += 30
    page.insert_text((40, y), "Question-wise Totals:", fontsize=14, color=BLACK, fontname="hebo")
    y += 25
    for sec in report.section_totals:
        line = f"{sec.get('qid', '?')}: {sec.get('score', 0)} / {sec.get('max_score', 0)}"
        page.insert_text((60, y), line, fontsize=12, color=RED, fontname="hebo")
        y += 20
        if y > 780:
            break


def _add_diagram_anchors(lines: list[dict], gap_threshold: float = 70.0) -> list[dict]:
    """Synthesize virtual 'diagram' anchor lines in big vertical gaps between OCR lines.

    OCR doesn't catch hand-drawn diagrams, so for steps the model couldn't
    anchor to a text line we still need somewhere to drop the tick. A wide
    blank band between two consecutive text lines is almost certainly a
    diagram — give it a synthetic line in the middle.
    """
    if not lines:
        return lines
    augmented = list(lines)
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if a["page"] != b["page"]:
            continue
        gap = b["y0"] - a["y1"]
        if gap >= gap_threshold:
            mid_y = (a["y1"] + b["y0"]) / 2
            augmented.append({
                "lid": f"_diag@P{a['page']}@{int(mid_y)}",
                "x0": min(a["x0"], b["x0"]),
                "y0": mid_y - 14,
                "x1": max(a["x1"], b["x1"]),
                "y1": mid_y + 14,
                "text": "[diagram]",
                "page": a["page"],
            })
    # Also add an anchor below the final line if there's a big blank tail
    # (the last criterion of a diagram-heavy question often falls here).
    augmented.sort(key=lambda r: (r["page"], r["y0"]))
    return augmented


def _is_synthetic_anchor(pl: dict) -> bool:
    """True for the virtual blank-band anchors added by _add_diagram_anchors —
    they carry text '[diagram]' and a lid like '_diag@P2@1234'. A real OCR text
    line is never synthetic."""
    return (str(pl.get("lid", "")).startswith("_diag@")
            or (pl.get("text") or "").strip().lower() == "[diagram]")


def _render_question_marks(student_doc: fitz.Document, m, my_lines: list[dict],
                            by_lid: dict[str, dict],
                            placed_by_page: dict[int, list[float]] | None = None) -> None:
    """Write this question's verdict: step marks on the sheet for numerical problems,
    and calculation/remark in the right-hand marks strip.

    `placed_by_page` is a SHARED page->[y] registry of marks already drawn by EARLIER
    questions, so marks from different questions on the same page (e.g. a dense MCQ
    block) stagger vertically instead of piling on top of each other."""
    if placed_by_page is None:
        placed_by_page = {}
    aw = float(getattr(m, "score", 0) or 0)
    mx = float(getattr(m, "max_score", 0) or 0)
    remark = (getattr(m, "remark", "") or "").strip()
    qid = str(getattr(m, "qid", "") or "").strip()

    # Collect criteria for step-by-step marking
    criteria = list(getattr(m, "criteria", []) or [])
    steps = [float(getattr(c, "awarded", 0) or 0) for c in criteria] if criteria else []

    # A band runs up to the NEXT question's anchor, so the next question's first
    # line can bleed in (e.g. "Q6) Evaluate ... = 8..."). Keep THIS question's
    # own first line, but truncate at the next question's label so neither the
    # numerical test nor step placement is contaminated by a neighbour.
    own_lines: list[dict] = []
    for i, pl in enumerate(my_lines):
        t = (pl.get("text") or "").strip().lower()
        if i > 0 and (_QHEADER_RE.match(t) or _QLABEL_LEAD_RE.match(t)
                      or _QLABEL_LEAD_HI_RE.match(t)):
            break
        own_lines.append(pl)
    my_lines = own_lines

    # Decide whether this question earns per-step marks on the sheet.
    #
    # A teacher ticks each step of a NUMERICAL / multi-step solution AND each
    # point of an ENUMERATED / labelled-diagram answer ("state 4 causes",
    # "label the parts"), but gives a SINGLE tick + total on flowing prose (an
    # essay, a history explanation). The grader can declare this per question
    # via `mark_style`; when it leaves it "auto" we infer from the content.
    band_text = " ".join((pl.get("text") or "") for pl in my_lines)
    digits = sum(ch.isdigit() for ch in band_text)
    letters = sum(ch.isalpha() for ch in band_text)
    # Math symbols are the reliable signal. The digit fallback requires the
    # digits to be DENSE (a real calculation), not just a few years in prose —
    # otherwise a history answer citing 1857, 1947, 1950… reads as "numerical"
    # and gets over-marked.
    looks_numerical = (
        any(sym in band_text for sym in
            ("=", "→", "⇒", "×", "÷", "√", "^", "∴", "Ω", "Rs", "%"))
        or (digits >= 6 and digits >= 0.18 * (digits + letters))
    )
    real = [pl for pl in my_lines if not _is_header(pl.get("text") or "")]
    anchor_lines = real or my_lines

    mark_style = str(getattr(m, "mark_style", "auto") or "auto").strip().lower()
    if mark_style == "per_step":
        base_step = len(criteria) > 1            # numerical OR enumerated points
    elif mark_style in ("single", "whole"):
        base_step = False                        # flowing prose — one tick + total
    else:                                        # "auto": infer from the content
        base_step = len(criteria) > 1 and looks_numerical
    # Need somewhere to place the per-step marks; with no anchor lines fall back
    # to the single-mark branch (which handles the empty band via y_fraction).
    use_step_marks = base_step and bool(anchor_lines)

    # When the whole question scored 0 AND the model already wrote a margin
    # remark, skip the per-step crosses — the cursive remark conveys it and a
    # column of crosses just looks like litter (a teacher crosses once).
    suppress_zero_crosses = aw <= 1e-6 and remark != ""

    if not anchor_lines:
        page_num = max(1, min(int(getattr(m, "page", 1) or 1), len(student_doc)))
        y_last = float(getattr(m, "y_fraction", 0.1) or 0.1) * student_doc[page_num - 1].rect.height
        x_end = 200.0
        page = student_doc[page_num - 1]
    else:
        page_num = anchor_lines[0]["page"]
        page = student_doc[page_num - 1]

    rect = page.rect
    cw = _content_width(page)
    seed = f"{qid}|mark"

    # For step-marked problems (numerical work OR enumerated points / labelled
    # diagrams): tick EACH step/point on the sheet where the student earned it —
    # with the marks for that step circled right after — exactly like a teacher
    # going down a solution. A question's band can span several pages, so every
    # step is resolved and drawn page-by-page.
    if use_step_marks:
        # Candidate lines for step ticks: the band's real (non-header) lines,
        # ordered top-to-bottom across pages. Fall back to all band lines if the
        # band is somehow header-only.
        real_band = sorted(
            (pl for pl in my_lines if not _is_header(pl.get("text") or "")),
            key=lambda r: (r["page"], r["y0"], r["x0"]),
        )
        candidates = real_band or sorted(
            my_lines, key=lambda r: (r["page"], r["y0"], r["x0"]))

        # Stage 1: honour an explicit step_line_id when it points at a real line
        # inside this band — on ANY page of the band, not just the first.
        band_lids = {pl["lid"] for pl in my_lines}
        assigned: dict[int, dict] = {}
        unassigned: list[int] = []
        for ci, c in enumerate(criteria):
            lid = (getattr(c, "step_line_id", "") or "").strip()
            pl = by_lid.get(lid)
            if pl is not None and lid in band_lids and not _is_header(pl.get("text") or ""):
                assigned[ci] = pl
            else:
                unassigned.append(ci)

        # Stage 2: spread the unanchored steps evenly down the band so each step
        # lands on its own line instead of all piling onto the last one.
        if unassigned and candidates:
            n = len(unassigned)
            for k, ci in enumerate(unassigned):
                idx = min(int((k + 0.5) * len(candidates) / n), len(candidates) - 1)
                assigned[ci] = candidates[idx]

        # Stage 3: if two steps resolved to the same line, push the later one to
        # the next free real line so ticks never stack on top of each other.
        used_lids: set[str] = set()
        for ci in sorted(assigned, key=lambda c: (assigned[c]["page"], assigned[c]["y0"])):
            pl = assigned[ci]
            if pl["lid"] in used_lids:
                later = [p for p in candidates
                         if (p["page"], p["y0"]) >= (pl["page"], pl["y0"])
                         and p["lid"] not in used_lids]
                if later:
                    pl = later[0]
                    assigned[ci] = pl
            used_lids.add(pl["lid"])

        # Group the assigned criteria BY their resolved line. Several steps that
        # land on the same line (e.g. a 5-mark answer written on one line, or two
        # steps the OCR merged) become ONE tick + the COMBINED marks for that
        # line — a teacher ticks a line once, not five times on top of itself.
        groups: dict[str, dict] = {}
        for ci in assigned:
            pl = assigned[ci]
            g = groups.get(pl["lid"])
            if g is None:
                g = {"pl": pl, "awarded": 0.0, "max": 0.0}
                groups[pl["lid"]] = g
            g["awarded"] += float(getattr(criteria[ci], "awarded", 0) or 0)
            g["max"] += float(getattr(criteria[ci], "max", 0) or 0)

        # Stage 4: draw one mark per line, on its OWN page, nudging down on the
        # rare occasion two lines' marks would still overlap vertically. Share the
        # cross-question registry so step marks don't collide with other questions'.
        drawn_by_page = placed_by_page
        ordered_groups = sorted(
            groups.values(), key=lambda g: (g["pl"]["page"], g["pl"]["y0"]))
        for gi, g in enumerate(ordered_groups):
            pl = g["pl"]
            awarded = g["awarded"]
            max_step = g["max"]
            if awarded <= 1e-6 and (max_step <= 0 or suppress_zero_crosses):
                continue  # nothing earned and the remark already explains it

            pg = student_doc[pl["page"] - 1]
            cwp = _content_width(pg)
            sstep = f"{seed}|g{gi}"

            # Find a vertical slot >= 30pt from every mark already on this page,
            # preferring the step's own line. Search downward first; if the page
            # tail is full, search back upward — never clamp several marks onto
            # the same y (overprint) or off the page (draws nothing).
            ceiling = pg.rect.height - 20
            floor_y = 24.0
            placed = drawn_by_page.setdefault(pl["page"], [])
            y_mark = max(floor_y, min((pl["y0"] + pl["y1"]) / 2, ceiling))

            def _collides(yy: float) -> bool:
                return any(abs(yy - py) < 30 for py in placed)

            if _collides(y_mark):
                down = y_mark
                while down <= ceiling and _collides(down):
                    down += 36
                if down <= ceiling:
                    y_mark = down
                else:
                    up = y_mark
                    while up >= floor_y and _collides(up):
                        up -= 36
                    y_mark = max(floor_y, up)
            placed.append(y_mark)

            # Size the circle to the EXACT text first — _draw_number_in_circle
            # uses a larger radius for decimals (half marks like "0.5") — then
            # reserve the tick+circle cluster from that true width.
            mark_text = (str(int(awarded)) if float(awarded).is_integer()
                         else f"{awarded:g}")
            is_dec = "." in mark_text
            tick_size = 20
            circle_size = 8
            circle_r = circle_size * (1.9 if is_dec else 1.8) + 1.5
            cluster_w = tick_size * 1.5 + 6 + circle_r * 2

            # Place the tick just past where the writing ends, but never let the
            # cluster cross the right edge onto the marks strip: the right cap
            # wins over the left floor when the line itself starts far right.
            right_cap = cwp - 6 - cluster_w
            tick_x = min(pl["x1"] + 12 + _wobble(sstep, 0, amp=5), right_cap)
            floor = pl["x0"] + 2
            tick_x = max(floor, tick_x) if floor <= right_cap else right_cap
            tick_x = max(2.0, tick_x)

            if awarded > 0:
                _draw_tick(pg, tick_x, y_mark, size=tick_size, seed=sstep + "t")
                mark_x = tick_x + tick_size * 1.5 + circle_r + 6
                _draw_number_in_circle(pg, mark_x, y_mark, mark_text,
                                       size=circle_size, seed=sstep)
            else:
                # Line attempted but earned nothing — cross it like a teacher.
                _draw_cross(pg, tick_x, y_mark, size=tick_size, seed=sstep + "x")
    else:
        # Single mark question: draw one tick (or cross) where the answer ends.
        # The band can cross a page break, so derive the page from the line we
        # actually mark — NOT the band's first page (used for the strip below).
        if anchor_lines:
            last = anchor_lines[-1]
            mark_page = student_doc[last["page"] - 1]
            mcw = _content_width(mark_page)
            y_mark = (last["y0"] + last["y1"]) / 2
            x_end = last["x1"]
            mark_page_num = last["page"]
        else:
            mark_page = page
            mcw = cw
            y_mark = float(getattr(m, "y_fraction", 0.1) or 0.1) * rect.height
            x_end = 200.0
            mark_page_num = page_num

        # Stagger vertically against every mark already placed on this page (across
        # ALL questions) so dense pages — e.g. the MCQ block — never overlap.
        placed = placed_by_page.setdefault(mark_page_num, [])
        ceiling = mark_page.rect.height - 20
        floor_y = 24.0
        y_mark = max(floor_y, min(y_mark, ceiling))

        def _coll(yy: float) -> bool:
            return any(abs(yy - py) < 26 for py in placed)

        if _coll(y_mark):
            down = y_mark
            while down <= ceiling and _coll(down):
                down += 30
            if down <= ceiling:
                y_mark = down
            else:
                up = y_mark
                while up >= floor_y and _coll(up):
                    up -= 30
                y_mark = max(floor_y, up)
        placed.append(y_mark)

        # OBJECTIVE questions (MCQ / assertion-reason / match): a single tick
        # (correct) or cross (wrong) — NO circled number. The score is shown in the
        # clean right-hand MARKS column, so dense MCQ pages don't pile up numbers.
        is_obj = bool(str(getattr(m, "correct_option", "") or "").strip())
        if is_obj:
            tick_size = 26
            tick_x = max(2.0, min(x_end + 16, mcw - 6 - tick_size * 1.6))
            attempted = bool(str(getattr(m, "chosen_option", "") or "").strip())
            if aw >= mx and mx > 0:
                _draw_tick(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
            elif attempted:
                _draw_cross(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
            # unattempted → nothing inline (the margin column says "Unattempted")
        else:
            tick_size = 32
            # Reserve the tick+circle cluster so it never spills onto the strip.
            circle13_r = 13 * (1.8 if float(aw).is_integer() else 1.9) + 1.5
            cluster_w = tick_size * 1.7 + circle13_r
            tick_x = max(2.0, min(x_end + 20, mcw - 6 - cluster_w))
            if aw <= 1e-6:  # Zero marks - only draw cross, no circle
                _draw_cross(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
            else:  # Positive marks - draw tick and circled mark
                _draw_tick(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
                circle_x = tick_x + tick_size * 1.7
                _draw_number_in_circle(mark_page, circle_x, y_mark,
                                      str(int(aw)) if float(aw).is_integer() else f"{aw:g}",
                                      size=13, seed=seed + "c")  # Increased size to 13

    # Show calculation/remark in the strip. Only show the per-step breakdown
    # ("1+1=2/2") when this question was actually marked step-by-step on the
    # sheet — otherwise (descriptive / empty band) show the plain score so the
    # strip never advertises steps the sheet doesn't show.
    y_strip = (anchor_lines[0]["y0"] + anchor_lines[0]["y1"]) / 2 if anchor_lines else rect.height * 0.5
    _draw_strip_entry(page, qid, aw, mx, remark, y_strip,
                      steps=steps if (use_step_marks and len(steps) > 1) else None)
    return


def _render_question_marks_LEGACY(student_doc: fitz.Document, m, my_lines: list[dict],
                            by_lid: dict[str, dict]) -> None:
    """Deprecated inline placement (kept for reference; no longer called)."""
    criteria = list(getattr(m, "criteria", []) or [])

    # Inject synthetic diagram anchors so criteria without a real OCR line
    # have somewhere to land that isn't a text line.
    my_lines_aug = _add_diagram_anchors(my_lines)

    # Stage 1: honour explicit step_line_id when it's a real line we know about
    # AND it's inside this question's band.
    band_lids = {pl["lid"] for pl in my_lines_aug}
    assigned: dict[int, dict] = {}
    unassigned_idx: list[int] = []
    for ci, c in enumerate(criteria):
        lid = (getattr(c, "step_line_id", "") or "").strip()
        if lid in band_lids:
            # find the pl in my_lines_aug
            pl = next(p for p in my_lines_aug if p["lid"] == lid)
            assigned[ci] = pl
        else:
            unassigned_idx.append(ci)

    # Stage 2: distribute unassigned criteria across band candidates (prefer
    # real, non-header text lines). Synthetic "[diagram]" anchors sit in blank
    # bands — only use them when the model EXPLICITLY anchored a step there
    # (Stage 1). Distributing a normal sub-question's tick onto one drops a
    # floating mark on empty paper (the stray "✓ 1" in the page's blank tail).
    if unassigned_idx and my_lines_aug:
        candidates = [pl for pl in my_lines_aug
                      if not _is_header(pl["text"]) and not _is_synthetic_anchor(pl)]
        if not candidates:
            # Genuinely no real text line in the band — fall back to diagram
            # anchors (diagram-only question) rather than nothing.
            candidates = [pl for pl in my_lines_aug if not _is_header(pl["text"])] \
                or my_lines_aug[:]
        n = len(unassigned_idx)
        for k, ci in enumerate(unassigned_idx):
            idx = min(int((k + 0.5) * len(candidates) / max(1, n)), len(candidates) - 1)
            assigned[ci] = candidates[idx]

    # Stage 3: collision/header resolution — push duplicates or header-anchored
    # criteria down to the next free non-header line in the band (across pages).
    used_lids: set[str] = set()
    order_for_resolve = sorted(
        assigned.keys(),
        key=lambda ci: (assigned[ci]["page"], assigned[ci]["y0"], assigned[ci]["x0"]),
    )
    for ci in order_for_resolve:
        pl = assigned[ci]
        # An explicit diagram anchor (Stage 1) is fine to keep; only MOVE when
        # the line is a duplicate or a header. When we do move, never relocate
        # onto a synthetic blank-band anchor.
        needs_move = pl["lid"] in used_lids or _is_header(pl["text"])
        if needs_move:
            later = [
                p for p in my_lines_aug
                if (p["page"], p["y0"]) >= (pl["page"], pl["y0"])
                and p["lid"] not in used_lids
                and not _is_header(p["text"])
                and not _is_synthetic_anchor(p)
            ]
            if later:
                pl = later[0]
                assigned[ci] = pl
        used_lids.add(pl["lid"])

    # Stage 4: render. Group already-drawn glyph rects per page so collisions
    # are tracked separately on each page.
    used_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    font_size = 15
    MIN_GAP_X = 14.0
    MIN_GAP_Y = 6.0

    ordered = sorted(
        [ci for ci in range(len(criteria)) if ci in assigned],
        key=lambda ci: (assigned[ci]["page"], assigned[ci]["y0"], assigned[ci]["x0"]),
    )
    # Reserve a right-edge safety zone — the tick AND its trailing number must
    # both fit inside the page, never on top of the red margin line.
    SAFE_RIGHT_PAD = 12.0  # min distance the rightmost glyph stays from rect.width

    # --- Decide if this question gets step-by-step ticks or one tick + total. ---
    # A human teacher only puts per-step marks on numerical work or questions
    # with explicit sub-parts. Pure descriptive answers get one tick and the
    # total at the end.
    qid = str(getattr(m, "qid", "") or "")
    has_subparts = "(" in qid
    band_text = " ".join((pl.get("text") or "") for pl in my_lines_aug)
    looks_numerical = any(sym in band_text for sym in ("=", "→", "Ω", "Ω", "Rs", "R_")) \
        or sum(ch.isdigit() for ch in band_text) >= 6
    use_step_marks = (has_subparts or looks_numerical) and len(ordered) >= 2

    if not use_step_marks and ordered:
        # Theoretical / single-flow answer: just one tick + total at the last
        # real line of the band. Skip individual step ticks.
        # Anchor to the last NON-HEADER line so the tick never lands on a form
        # label / section header (e.g. the signature box at the top of a page).
        non_header_ordered = [c for c in ordered
                              if not _is_header(assigned[c].get("text") or "")]
        if not non_header_ordered:
            # Nothing real to anchor to — keep the written reason, drop the tick.
            _draw_remark_on_band(student_doc, m, my_lines_aug, by_lid)
            return
        last_ci = non_header_ordered[-1]
        pl = assigned[last_ci]
        page = student_doc[pl["page"] - 1]
        rect = page.rect
        seed = f"{m.qid}|total"
        total_awarded = sum(float(getattr(criteria[c], "awarded", 0) or 0) for c in ordered)
        max_total = sum(float(getattr(criteria[c], "max", 0) or 0) for c in ordered)
        score_txt = (str(int(total_awarded)) if float(total_awarded).is_integer()
                     else f"{total_awarded:g}")
        text_w = _number_width(score_txt, size=font_size)
        full_credit = total_awarded > 0 and total_awarded >= max_total * 0.999
        partial_credit = 0 < total_awarded < max_total
        tick_size = 24
        glyph_w = tick_size * 1.5 if (full_credit or partial_credit) else 0
        cluster_w = glyph_w + (4 if glyph_w else 0) + text_w
        max_right = rect.width - SAFE_RIGHT_PAD
        cx = min(pl["x1"] + 14 + _wobble(seed, 0, amp=6), max_right - cluster_w)
        cx = max(pl["x0"], cx)
        cy = (pl["y0"] + pl["y1"]) / 2 + _wobble(seed, 1, amp=2)
        if full_credit or partial_credit:
            _draw_tick(page, cx, cy, size=tick_size, seed=seed + "t")
            num_x = cx + glyph_w + 4
        else:
            _draw_cross(page, cx + 8, cy, size=16, seed=seed + "x")
            num_x = cx + 20
        _draw_number(page, num_x, cy, score_txt, size=font_size, seed=seed + "n")
        _draw_remark_on_band(student_doc, m, my_lines_aug, by_lid)
        return

    # If the question scored zero AND the model gave a remark, skip drawing
    # individual zero-credit crosses for each criterion. The cursive remark
    # ("Unattempted." / "Incorrect answer.") already conveys the same info,
    # and a floating "✗" in the right margin next to a blank line just looks
    # like noise.
    suppress_zero_crosses = (
        float(getattr(m, "score", 0) or 0) <= 0
        and (getattr(m, "remark", "") or "").strip() != ""
    )

    for ci in ordered:
        c = criteria[ci]
        pl = assigned[ci]
        # Never draw any mark on a header / form-label line (signature box,
        # "Section-B", bare "Q2", "(i)"). Synthetic diagram anchors ("[diagram]")
        # are not headers, so diagram ticks still land correctly.
        if _is_header(pl.get("text") or ""):
            continue
        page = student_doc[pl["page"] - 1]
        rect = page.rect
        seed = f"{m.qid}|c{ci}|{pl['lid']}"
        awarded = float(getattr(c, "awarded", 0) or 0)
        is_tick = awarded > 0
        if not is_tick and suppress_zero_crosses:
            continue
        # Also skip a zero-credit cross if the assigned line is a header / has
        # no real student text past the label — there's nothing meaningful to
        # mark wrong, so the floating margin "✗" would mislead.
        if not is_tick:
            line_text = (pl.get("text") or "").strip()
            if _is_header(line_text) or len(line_text) <= 2:
                continue

        used = used_by_page.setdefault(pl["page"], [])

        if is_tick:
            size = 24 + _wobble(seed, 2, amp=5.0)
            glyph_w = size * 1.5
            glyph_h = size * 1.65
        else:
            size = 16 + _wobble(seed, 3, amp=3.0)
            glyph_w = size
            glyph_h = size

        # Total width of the tick+number cluster (so we never split the unit
        # past the page edge with the number stranded against the margin).
        # Per-step: show marks awarded for THIS step only (not the running
        # total). Cumulative numbers confused readers — a human teacher writes
        # "1" next to each correctly-done step, not 1, 2, 3...
        num_txt = ""
        num_w = 0.0
        if awarded > 0:
            num_txt = str(int(awarded)) if float(awarded).is_integer() else f"{awarded:g}"
            num_w = _number_width(num_txt, size=font_size)
        cluster_w = glyph_w + (4 + num_w if num_w else 0)

        offset = 8 + abs(_wobble(seed, 0, amp=14))
        sx = pl["x1"] + offset
        sy = (pl["y0"] + pl["y1"]) / 2 + _wobble(seed, 1, amp=3)

        # If the whole cluster won't fit to the right of where the writing ends,
        # drop it to a fresh line below — same column as the line start so it
        # stays inside the writing area and never overlaps the red margin.
        max_right = rect.width - SAFE_RIGHT_PAD
        if sx + cluster_w > max_right:
            # Place below the line; pull the cluster left if needed.
            sy = pl["y1"] + glyph_h * 0.55
            sx = min(pl["x1"] + offset, max_right - cluster_w)
            sx = max(pl["x0"], sx)

        # Collision resolution against earlier glyphs on the same page.
        for _ in range(8):
            bumped = False
            for ux0, uy0, ux1, uy1 in used:
                if (sx < ux1 + MIN_GAP_X and sx + cluster_w > ux0 - MIN_GAP_X
                        and sy - glyph_h / 2 < uy1 + MIN_GAP_Y
                        and sy + glyph_h / 2 > uy0 - MIN_GAP_Y):
                    sx = ux1 + MIN_GAP_X
                    bumped = True
            if not bumped:
                break
            # If pushing right has shoved us past the safe edge, wrap below.
            if sx + cluster_w > max_right:
                sy += glyph_h + 4
                sx = max(pl["x0"], max_right - cluster_w)
                break

        # Final hard clamp — never let any part of the cluster cross the edge.
        if sx + cluster_w > max_right:
            sx = max(4.0, max_right - cluster_w)

        if is_tick:
            _draw_tick(page, sx, sy, size=size, seed=seed)
            tick_end_x = sx + size * 1.5
        else:
            _draw_cross(page, sx + size * 0.4, sy, size=size, seed=seed)
            tick_end_x = sx + size * 1.0

        used.append((sx, sy - glyph_h / 2, sx + glyph_w, sy + glyph_h / 2))

        if num_txt:
            nx = tick_end_x + 4 + _wobble(seed, 4, amp=1.0)
            # Keep the number anchored to its tick — never clamp it independently
            # to the right edge (that's what produced "orphan tick + lonely number"
            # when the tick fell off-page).
            if nx + num_w > max_right:
                nx = max_right - num_w
            ny_center = sy + _wobble(seed, 5, amp=1.0)
            _draw_number(page, nx, ny_center, num_txt,
                         size=font_size, seed=seed + "n")
            used.append((nx, ny_center - font_size / 2,
                         nx + num_w, ny_center + font_size / 2))

    # Fallback: if no criteria rendered (1-mark questions), drop the score next
    # to the last text line of the band.
    if not ordered:
        score = m.score
        score_txt = str(int(score)) if float(score).is_integer() else f"{score:g}"
        seed = f"{m.qid}|score"
        text_w = _number_width(score_txt, size=font_size)
        real_lines = [pl for pl in my_lines if not _is_header(pl["text"])] or my_lines
        has_tick = float(m.score) >= float(m.max_score) and m.max_score > 0
        # Cluster = tick (≈36pt wide) + 4 gap + number, so reserve that much.
        cluster_w = (36 + 4 if has_tick else 0) + text_w
        if real_lines:
            target = real_lines[-1]
            page = student_doc[target["page"] - 1]
            rect = page.rect
            max_right = rect.width - 12
            cluster_x = min(target["x1"] + 18 + _wobble(seed, 0, amp=6),
                            max_right - cluster_w)
            cluster_x = max(target["x0"], cluster_x)
            cy = (target["y0"] + target["y1"]) / 2 + _wobble(seed, 1, amp=2)
        else:
            page = student_doc[m.page - 1]
            rect = page.rect
            max_right = rect.width - 12
            cy = m.y_fraction * rect.height
            cluster_x = max(20.0, max_right - cluster_w)
        if has_tick:
            _draw_tick(page, cluster_x, cy, size=24, seed=seed + "t")
            cx = cluster_x + 36 + 4
        else:
            cx = cluster_x
        _draw_number(page, cx, cy, score_txt,
                     size=font_size, seed=seed + "n")

    # Teacher's handwritten reason — drawn for both the step-tick path AND the
    # fallback path. Skipped automatically if remark is empty or full marks.
    _draw_remark_on_band(student_doc, m, my_lines_aug, by_lid)


def _annotate_questions(student_doc: fitz.Document, questions: list,
                        line_index: dict) -> None:
    """Per-question annotator. Each question's band spans from its anchor to
    the next question's anchor (possibly crossing page breaks), and its
    criteria can be placed on any page in that band."""
    if not questions:
        return

    # Convert OCR lines to PDF coords per page, once.
    lines_by_page: dict[int, list[dict]] = {}
    for i in range(len(student_doc)):
        page_num = i + 1
        page = student_doc[i]
        plines: list[dict] = []
        for lid, (p, bbox_px, img_w, img_h, text) in line_index.items():
            if p != page_num:
                continue
            x0, y0, x1, y1 = _pdf_coords_from_line(page, bbox_px, img_w, img_h)
            plines.append({
                "lid": lid, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "text": text, "page": page_num,
            })
        plines.sort(key=lambda r: r["y0"])
        lines_by_page[page_num] = plines

    by_lid: dict[str, dict] = {pl["lid"]: pl for plist in lines_by_page.values() for pl in plist}

    def _first_real_line_at_or_below(page_num: int, y_min: float) -> dict | None:
        """Find the first non-header OCR line at/below y_min on page_num, walking
        onto subsequent pages if needed. Returns None if nothing real exists."""
        for pn in range(page_num, len(student_doc) + 1):
            cands = [
                p for p in lines_by_page.get(pn, [])
                if not _is_header(p["text"])
                and (pn > page_num or p["y0"] >= y_min - 4)
            ]
            if cands:
                cands.sort(key=lambda r: r["y0"])
                return cands[0]
        return None

    def q_anchor(q) -> tuple[int, float]:
        pl = by_lid.get(getattr(q, "anchor_line_id", "") or "")
        if pl and not _is_header(pl["text"]):
            return (pl["page"], (pl["y0"] + pl["y1"]) / 2)
        if pl and _is_header(pl["text"]):
            # Model anchored to a form / section / bare-label line — snap to the
            # first real answer line at or below it so the band starts on the
            # student's actual writing. Use y_fraction as a floor so sub-questions
            # that both had bad anchors don't collapse onto the same line.
            page_h = student_doc[pl["page"] - 1].rect.height
            y_floor = max(pl["y0"], q.y_fraction * page_h)
            real = _first_real_line_at_or_below(pl["page"], y_floor)
            if real:
                return (real["page"], (real["y0"] + real["y1"]) / 2)
            return (pl["page"], (pl["y0"] + pl["y1"]) / 2)
        page_num = q.page
        if 1 <= page_num <= len(student_doc):
            fallback_y = q.y_fraction * student_doc[page_num - 1].rect.height
            real = _first_real_line_at_or_below(page_num, fallback_y)
            if real:
                return (real["page"], (real["y0"] + real["y1"]) / 2)
            return (page_num, fallback_y)
        return (page_num, q.y_fraction * 842)

    qs_sorted = sorted(questions, key=q_anchor)

    # Shared page->[y] registry so marks from DIFFERENT questions on the same page
    # (the MCQ block especially) stagger vertically instead of overlapping.
    placed_by_page: dict[int, list[float]] = {}

    for qi, q in enumerate(qs_sorted):
        start_page, start_y = q_anchor(q)
        if qi + 1 < len(qs_sorted):
            end_page, end_y = q_anchor(qs_sorted[qi + 1])
        else:
            end_page = len(student_doc)
            end_y = student_doc[end_page - 1].rect.height if end_page >= 1 else 842
            # include the bottom: pretend end_y is just past the page bottom
            end_y = end_y + 1

        my_lines: list[dict] = []
        for pn in range(start_page, end_page + 1):
            for pl in lines_by_page.get(pn, []):
                ymid = (pl["y0"] + pl["y1"]) / 2
                if pn == start_page and ymid < start_y - 4:
                    continue
                if pn == end_page and qi + 1 < len(qs_sorted) and ymid >= end_y - 4:
                    continue
                my_lines.append(pl)

        _render_question_marks(student_doc, q, my_lines, by_lid, placed_by_page)


def _annotate_student_page(page: fitz.Page, marks_on_page: list,
                           line_index: dict | None = None) -> None:
    """Annotate the page like a human teacher would:

    - Step ticks/crosses sit just past where the writing ends on each line
      (NOT in a fixed right-edge column) — short lines get marked mid-page,
      long lines get marked near the right edge, exactly where the pen lands.
    - The overall score for a question is written next to the END of its
      last answer line and circled. No column, no fixed x.
    - If criteria don't carry `step_line_id`, ticks are distributed across
      the question's answer lines so step marking still appears.
    """
    rect = page.rect
    line_index = line_index or {}
    page_num = page.number + 1  # fitz pages are 0-indexed

    # All OCR lines on THIS page, with PDF-coord bboxes, sorted top-to-bottom.
    page_lines: list[dict] = []
    for lid, (p, bbox_px, img_w, img_h, text) in line_index.items():
        if p != page_num:
            continue
        x0, y0, x1, y1 = _pdf_coords_from_line(page, bbox_px, img_w, img_h)
        page_lines.append({
            "lid": lid, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text,
        })
    page_lines.sort(key=lambda r: r["y0"])
    by_lid = {pl["lid"]: pl for pl in page_lines}

    def anchor_y(m) -> float:
        pl = by_lid.get(m.anchor_line_id or "")
        if pl:
            return (pl["y0"] + pl["y1"]) / 2
        return m.y_fraction * rect.height

    marks_sorted = sorted(marks_on_page, key=anchor_y)

    # Figure out the vertical band each question owns: from its own anchor to
    # the next question's anchor (or page bottom). This lets us collect the
    # answer lines for that question — used both for step-tick distribution
    # and for placing the score circle next to the LAST line of the answer.
    bands: list[tuple[float, float]] = []
    for i, m in enumerate(marks_sorted):
        y_top = anchor_y(m) - 6
        y_bot = anchor_y(marks_sorted[i + 1]) - 6 if i + 1 < len(marks_sorted) else rect.height
        bands.append((y_top, y_bot))

    for m, (y_top, y_bot) in zip(marks_sorted, bands):
        my_lines = [pl for pl in page_lines if y_top <= (pl["y0"] + pl["y1"]) / 2 < y_bot]

        # ---------------- step marks ----------------
        criteria = list(getattr(m, "criteria", []) or [])

        # 1) Honour explicit step_line_id from Claude where given.
        # 2) For the rest, spread them across `my_lines` so we still get
        #    visible step ticks even when the model didn't anchor them.
        assigned: dict[int, dict] = {}
        unassigned_idx: list[int] = []
        for ci, c in enumerate(criteria):
            lid = getattr(c, "step_line_id", "") or ""
            if lid in by_lid:
                assigned[ci] = by_lid[lid]
            else:
                unassigned_idx.append(ci)

        if unassigned_idx and my_lines:
            # Skip the very first line if it likely starts with "Q.." — that's
            # the question header, not a working step.
            candidates = my_lines[:]
            if candidates and candidates[0]["text"].strip().lower().startswith(("q.", "q ", "q)")):
                candidates = candidates[1:] or my_lines
            n = len(unassigned_idx)
            for k, ci in enumerate(unassigned_idx):
                idx = min(int((k + 0.5) * len(candidates) / max(1, n)), len(candidates) - 1)
                assigned[ci] = candidates[idx]

        # Resolve placement problems Gemini commonly makes:
        # (1) Multiple criteria share the same step_line_id — push later ones
        #     down to subsequent answer lines so step ticks land at each step.
        # (2) A criterion is anchored to a question-header line ("Q.27)", "(a)")
        #     — push it to the first real working line.
        def _is_header(text: str) -> bool:
            s = text.strip().lower()
            return (s.startswith(("q.", "q ", "q)"))
                    or (len(s) <= 5 and s.startswith("(") and s.endswith(")")))

        used_lids: set[str] = set()
        for ci in sorted(assigned.keys(), key=lambda c: (assigned[c]["y0"], assigned[c]["x0"])):
            pl = assigned[ci]
            needs_move = pl["lid"] in used_lids or _is_header(pl["text"])
            if needs_move:
                later = [p for p in my_lines
                         if p["y0"] >= pl["y0"]
                         and p["lid"] not in used_lids
                         and not _is_header(p["text"])]
                if later:
                    pl = later[0]
                    assigned[ci] = pl
            used_lids.add(pl["lid"])

        used: list[tuple[float, float, float, float]] = []  # (x0, y0, x1, y1) of each glyph cluster
        # Render criteria in the order they appear down the page so cumulative
        # numbers (1, 2, 3...) flow naturally with the answer.
        ordered = sorted(
            [ci for ci in range(len(criteria)) if ci in assigned],
            key=lambda ci: (assigned[ci]["y0"], assigned[ci]["x0"]),
        )
        running = 0.0
        font_size = 15
        MIN_GAP_X = 14.0  # minimum horizontal padding between adjacent numbers
        MIN_GAP_Y = 6.0
        for ci in ordered:
            c = criteria[ci]
            pl = assigned[ci]
            seed = f"{m.qid}|c{ci}|{pl['lid']}"
            awarded = float(getattr(c, "awarded", 0) or 0)
            max_c = float(getattr(c, "max", awarded) or awarded)
            running += awarded
            # Treat any positive award as a tick. A bare cross is only used
            # when zero credit was given for that step — otherwise the cross
            # contradicts the running tally next to it.
            is_tick = awarded > 0

            # Mark lands just past where the writing actually ends on that line.
            offset = 8 + abs(_wobble(seed, 0, amp=14))
            sx = min(pl["x1"] + offset, rect.width - 18)
            sy = (pl["y0"] + pl["y1"]) / 2 + _wobble(seed, 1, amp=3)

            if is_tick:
                size = 24 + _wobble(seed, 2, amp=5.0)
                glyph_w = size * 1.5
                glyph_h = size * 1.65
            else:
                size = 16 + _wobble(seed, 3, amp=3.0)
                glyph_w = size
                glyph_h = size

            # Resolve collisions against everything already drawn on this page
            # (ticks, crosses, *and* their cumulative numbers). Push right as
            # far as possible; if we hit the page edge, drop to next line.
            for _ in range(8):
                bumped = False
                for ux0, uy0, ux1, uy1 in used:
                    if (sx < ux1 + MIN_GAP_X and sx + glyph_w > ux0 - MIN_GAP_X
                            and sy - glyph_h / 2 < uy1 + MIN_GAP_Y
                            and sy + glyph_h / 2 > uy0 - MIN_GAP_Y):
                        sx = ux1 + MIN_GAP_X
                        bumped = True
                if not bumped:
                    break
            if sx + glyph_w > rect.width - 4:
                # No room on this line — drop just below it.
                sx = pl["x1"] + 8
                sy = pl["y1"] + glyph_h * 0.6

            if is_tick:
                _draw_tick(page, sx, sy, size=size, seed=seed)
                tick_end_x = sx + size * 1.5
            else:
                _draw_cross(page, sx + size * 0.4, sy, size=size, seed=seed)
                tick_end_x = sx + size * 1.0

            used.append((sx, sy - glyph_h / 2, sx + glyph_w, sy + glyph_h / 2))

            # Running cumulative marks next to the tick — like a teacher
            # tallying step by step ("1", "2", "3"...). Skip when nothing
            # was awarded for this step (the cross alone tells the story).
            if awarded > 0:
                num_txt = str(int(running)) if float(running).is_integer() else f"{running:g}"
                num_w = _number_width(num_txt, size=font_size)
                nx = tick_end_x + 4 + _wobble(seed, 4, amp=1.5)
                ny_center = sy + _wobble(seed, 5, amp=1.0)
                # Don't let the number butt up against an earlier glyph.
                for ux0, uy0, ux1, uy1 in used:
                    if (nx < ux1 + MIN_GAP_X and nx + num_w > ux0 - MIN_GAP_X
                            and ny_center - font_size / 2 < uy1 + MIN_GAP_Y
                            and ny_center + font_size / 2 > uy0 - MIN_GAP_Y):
                        nx = ux1 + MIN_GAP_X
                if nx + num_w > rect.width - 4:
                    nx = rect.width - num_w - 4
                _draw_number(page, nx, ny_center, num_txt,
                             size=font_size, seed=seed + "n")
                used.append((nx, ny_center - font_size / 2,
                             nx + num_w, ny_center + font_size / 2))

        # ---------------- fallback total ----------------
        # If no criteria were rendered (1-mark questions, missing OCR), still
        # drop the score next to the end of the last answer line — no circle.
        if not ordered:
            score = m.score
            score_txt = str(int(score)) if float(score).is_integer() else f"{score:g}"
            seed = f"{m.qid}|score"
            text_w = _number_width(score_txt, size=font_size)
            if my_lines:
                target = my_lines[-1]
                cx = min(target["x1"] + 18 + _wobble(seed, 0, amp=6),
                         rect.width - text_w - 6)
                cy = (target["y0"] + target["y1"]) / 2 + _wobble(seed, 1, amp=2)
            else:
                cy = anchor_y(m)
                cx = rect.width * 0.82 + _wobble(seed, 0, amp=10)
            # tick before the number for 1-mark fully-correct answers
            if float(m.score) >= float(m.max_score) and m.max_score > 0:
                _draw_tick(page, cx - 30, cy, size=24, seed=seed + "t")
            _draw_number(page, cx, cy, score_txt,
                         size=font_size, seed=seed + "n")

        # ---------------- teacher remark (legible) ----------------
        # When the student lost marks AND the model gave a remark, write a short
        # reason below the last answer line — clean red on a white backing, with
        # the sub-question id so it stays unambiguous.
        if (m.remark and m.remark.strip()
                and m.score < m.max_score):
            qid = str(getattr(m, "qid", "") or "").strip()
            note = f"{qid}: {m.remark.strip()}" if qid else m.remark.strip()
            if my_lines:
                last = my_lines[-1]
                rx = last["x0"] + 12
                ry = last["y1"] + 16 + _wobble(f"{m.qid}|rk", 0, amp=2)
            else:
                rx = 40
                ry = anchor_y(m) + 18
            # Clamp to page bounds; if overflowing the bottom, push above the line.
            if ry > rect.height - 24:
                ry = (my_lines[-1]["y0"] - 8) if my_lines else (anchor_y(m) - 8)
            max_w = max(180.0, rect.width - rx - 70)
            _draw_handwritten_remark(page, rx, ry, note, max_width=max_w,
                                     size=15.5)


def _draw_inline_annotations(page: fitz.Page, annotations: list,
                             line_index: dict | None = None) -> None:
    """Mark wrong words ON the student's writing: ✗ for objective slips, and a
    strikethrough through the offending word/phrase. `circle` annotations are
    rendered as strikethroughs too (a circle over busy handwriting reads as
    clutter; a line through the wrong word is clearer)."""
    rect = page.rect
    cw = _content_width(page)  # writing-area width (exclude the marks strip)
    line_index = line_index or {}
    for a in annotations:
        # Treat circle like strikethrough — strike the wrong word instead of
        # ringing it.
        is_strike = a.type in ("strikethrough", "circle")
        x = y = None
        width = None
        if a.target_line_id and a.target_line_id in line_index:
            _, bbox_px, img_w, img_h, line_text = line_index[a.target_line_id]
            x0, y0, x1, y1 = _pdf_coords_from_line(page, bbox_px, img_w, img_h)
            y = (y0 + y1) / 2
            if a.target_word and a.target_word in line_text:
                x = _word_x_in_line(line_text, a.target_word, x0, x1)
                # strikethrough width sized to the target word
                if is_strike:
                    word_frac = len(a.target_word) / max(1, len(line_text))
                    width = max(15.0, word_frac * (x1 - x0))
                    # re-center start at left edge of word
                    x = x - width / 2
            else:
                x = (x0 + x1) / 2
                if is_strike:
                    width = x1 - x0
                    x = x0
        else:
            x = a.x_fraction * cw
            y = a.y_fraction * rect.height
            width = max(20.0, a.width_fraction * cw) if is_strike else None

        seed = f"a{a.page}{a.target_word or ''}{x:.1f}{y:.1f}"
        if a.type == "cross":
            size = 11 + _wobble(seed, 0, amp=2.5)
            _draw_cross(page, x, y, size=size, seed=seed)
        else:  # strikethrough or circle
            _draw_strikethrough(page, x, y, width or 40.0)


def _summary_page(doc: fitz.Document, report: GradeReport) -> None:
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 60), "Question-wise Summary:", fontsize=18, color=RED, fontname="hebo")

    y = 100
    lost = [q for q in report.questions if q.score < q.max_score and q.remark]
    if not lost:
        page.insert_text((40, y), "Full marks awarded across the board.", fontsize=12, color=BLACK)
        return
    for q in lost:
        header = f"{q.qid}:"
        page.insert_text((40, y), header, fontsize=12, color=RED, fontname="hebo")
        y = _draw_text(page, 90, y, q.remark, size=12, color=RED, max_width=460)
        y += 8
        if y > 800:
            page = doc.new_page(width=595, height=842)
            y = 60


def build_evaluated_pdf(student_pdf_bytes: bytes, student_filename: str,
                        student_pngs: list[bytes], report: GradeReport,
                        ocr_pages: list[PageOCR] | None = None) -> bytes:
    """Build the evaluated PDF and return its bytes.

    If the student submission was a PDF, we annotate the original pages directly.
    Otherwise we build PDF pages from the student PNGs.
    """
    # Recompute total AND max from per-question scores so the cover page never
    # disagrees with the question-wise breakdown. Also rebuild section_totals
    # from the same source of truth. (The model sometimes drops not-attempted
    # questions from max_total while still listing them at 0/max — recomputing
    # the denominator here keeps the percentage honest.)
    report.total_score = round(sum(q.score for q in report.questions), 2)
    report.max_total = round(sum(q.max_score for q in report.questions), 2)
    # Rebuild section_totals keyed by the top-level question (Q1, Q2, ...)
    sec_acc: dict[str, dict] = {}
    for q in report.questions:
        top = q.qid.split(".")[0].split("(")[0].strip() or q.qid
        bucket = sec_acc.setdefault(top, {"qid": top, "score": 0.0, "max_score": 0.0})
        bucket["score"] += q.score
        bucket["max_score"] += q.max_score
    if sec_acc:
        # preserve numeric ordering when qids look like "Q12"
        def sec_key(d):
            qid = d["qid"]
            digits = "".join(ch for ch in qid if ch.isdigit())
            return int(digits) if digits else 9999
        report.section_totals = [
            {"qid": d["qid"],
             "score": round(d["score"], 2),
             "max_score": round(d["max_score"], 2)}
            for d in sorted(sec_acc.values(), key=sec_key)
        ]

    out = fitz.open()
    _cover_page(out, report)

    # Build or open the student doc
    is_pdf = student_filename.lower().endswith(".pdf")
    if is_pdf:
        student_doc = fitz.open(stream=student_pdf_bytes, filetype="pdf")
    else:
        student_doc = fitz.open()
        for png in student_pngs:
            img = fitz.open(stream=png, filetype="png")
            rect = img[0].rect
            pdf_page = student_doc.new_page(width=rect.width, height=rect.height)
            pdf_page.insert_image(rect, stream=png)
            img.close()

    # Widen every student page with a blank right-hand strip for the marks and
    # remarks. The original page goes on the left (its width recorded as the
    # content width so OCR coords still map onto the writing); the strip carries
    # all ticks/scores/remarks so they never sit on the writing or run off-page.
    _CONTENT_W.clear()
    widened = fitz.open()
    for pno in range(len(student_doc)):
        src = student_doc[pno]
        w, h = src.rect.width, src.rect.height
        strip = max(STRIP_MIN_WIDTH, w * STRIP_FRACTION)
        npage = widened.new_page(width=w + strip, height=h)
        npage.show_pdf_page(fitz.Rect(0, 0, w, h), student_doc, pno)
        # Divider line + a small header at the top of the strip.
        npage.draw_line(fitz.Point(w, 0), fitz.Point(w, h), color=RED, width=1.0)
        npage.insert_text((w + 12, 24), "MARKS / REMARKS", fontsize=8.5,
                          color=RED, fontname="hebo")
        _CONTENT_W[(id(widened), pno + 1)] = w
    student_doc.close()
    student_doc = widened
    _reset_strip_state(student_doc)

    # Group marks and annotations by page
    by_page: dict[int, list] = {}
    for q in report.questions:
        by_page.setdefault(q.page, []).append(q)
    ann_by_page: dict[int, list] = {}
    for a in report.annotations:
        ann_by_page.setdefault(a.page, []).append(a)

    line_index = _build_line_index(ocr_pages)

    # Fresh remark-band registry for this document so notes don't overlap.
    _reset_remark_bands(student_doc)

    # Pre-scan each page image for real ink so remarks avoid handwriting /
    # scribbles / crossed-out tables that the OCR never returned as text lines.
    _INK_BANDS[id(student_doc)] = {}
    for i in range(len(student_doc)):
        if i < len(student_pngs):
            _INK_BANDS[id(student_doc)][i + 1] = _ink_intervals_from_png(
                student_pngs[i], student_doc[i].rect.height)

    # Question marks first — this can draw across page boundaries, so it must
    # see the whole document, not one page at a time.
    _annotate_questions(student_doc, report.questions, line_index)

    # Inline annotations (crosses, strikethroughs) stay per-page.
    for i, page in enumerate(student_doc, start=1):
        anns = ann_by_page.get(i, [])
        if anns:
            _draw_inline_annotations(page, anns, line_index=line_index)

    out.insert_pdf(student_doc)
    student_doc.close()

    _summary_page(out, report)

    buf = BytesIO()
    out.save(buf, garbage=4, deflate=True)
    out.close()
    return buf.getvalue()
