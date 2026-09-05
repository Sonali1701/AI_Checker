"""Render the evaluated PDF: cover page + annotated student pages + summary page."""
from __future__ import annotations

import contextvars
import hashlib
import math
import os
import re
import threading
from io import BytesIO

import fitz  # PyMuPDF

from anchors import resolve_steps
from grader import GradeReport
from mathpix import PageOCR

RED = (0.80, 0.08, 0.10)
BLACK = (0, 0, 0)
# Backwards compat — anything that still imports GREEN gets red instead.
GREEN = RED

# Inline tick / cross / circled-mark size multiplier for the sheet being rendered.
# CA and commerce answers are dense, tabular and small-handwritten, so the default
# school-sized marks cover the working. A ContextVar (not a plain global) keeps the
# value per-thread, so two jobs rendering different papers can't clobber each other.
_MARK_SCALE: contextvars.ContextVar[float] = contextvars.ContextVar("mark_scale", default=1.0)
_SAFE_LAYOUT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "safe_layout", default=False)
_RENDER_LOCK = threading.Lock()


def _ms(size: float) -> float:
    """Scale an inline-mark dimension for the current sheet."""
    return size * _MARK_SCALE.get()


# A teacher's tick is as big as the answer it sits beside: a broad one across a
# spread-out line, a small one down the rows of a ledger. The nominal is the median
# OCR line height on a real copy; the clamp keeps a merged OCR block (which can come
# back hundreds of points tall) from drawing a tick across the whole page.
_NOMINAL_LINE_H = 22.0
_TICK_SCALE_MIN = 0.70
_TICK_SCALE_MAX = 1.55

# ...and a tick that stands for a WHOLE answer also grows with how much was written.
# A teacher's tick beside a one-line definition is not the tick they put on a page of
# working. Square-rooted, because a twelve-line answer does not earn four times the
# tick of a three-line one, and clamped at both ends so a long answer in large
# handwriting cannot compound into a tick drawn across the page.
_NOMINAL_ANSWER_LINES = 3.0
_LENGTH_SCALE_MIN = 0.75
_LENGTH_SCALE_MAX = 1.55
_TICK_TOTAL_MIN = 0.65
_TICK_TOTAL_MAX = 1.70


def _answer_length_scale(answer_lines: int) -> float:
    """How much a whole-answer tick grows with the length of the answer."""
    if not answer_lines or answer_lines <= 0:
        return 1.0
    factor = math.sqrt(answer_lines / _NOMINAL_ANSWER_LINES)
    return min(_LENGTH_SCALE_MAX, max(_LENGTH_SCALE_MIN, factor))


def _row_writing_end(by_lid: dict, page_num: int, y0: float, y1: float,
                     own_x0: float, own_x1: float, content_w: float) -> float:
    """Rescue a tick that would otherwise be stranded in the left margin.

    A step can be anchored to a stub — a bare "(c)" label sits in the OCR as its own
    line ending at x=208 — and placing the tick just past it left it in the left
    margin of a released Core Maths page while the working it marked ran on to 1199.

    Deliberately narrow. A ledger row is many short cells, and taking the widest line
    on the row would drag the tick off the amount it is about and out to the far edge
    of the table, which is wrong on exactly the papers that are mostly tables. So this
    only fires on the stranding symptom: the anchor ends in the left third of the page
    AND the row carries writing well beyond it.
    """
    # A label is NARROW as well as far left. Position alone is not enough: the
    # particulars cell of a ledger row also ends in the left third, and dragging its
    # tick to the far edge of the table would be wrong on exactly the papers that are
    # mostly tables.
    if own_x1 > 0.35 * content_w or (own_x1 - own_x0) > 0.12 * content_w:
        return own_x1
    end = own_x1
    for line in by_lid.values():
        if line.get("page") != page_num:
            continue
        overlap = min(y1, line["y1"]) - max(y0, line["y0"])
        if overlap > 0.4 * min(y1 - y0, line["y1"] - line["y0"]):
            end = max(end, line["x1"])
    return end if end > own_x1 + 0.25 * content_w else own_x1


def _tick_size_for_line(base: float, line_h: float, answer_lines: int = 0) -> float:
    """Scale an inline tick/cross to the writing it marks.

    `line_h` is the height of the line it sits on — how big this student writes.
    `answer_lines` is how many lines the answer runs to, and applies only to a mark
    standing for a WHOLE answer: a step tick marks one line, so its scope is that
    line however long the answer around it is.
    """
    factor = 1.0
    if line_h and line_h > 0:
        factor = min(_TICK_SCALE_MAX, max(_TICK_SCALE_MIN, line_h / _NOMINAL_LINE_H))
    if answer_lines and answer_lines > 0:
        factor = min(_TICK_TOTAL_MAX,
                     max(_TICK_TOTAL_MIN, factor * _answer_length_scale(answer_lines)))
    return _ms(base) * factor


# One hand marks the whole sheet. A step mark and a whole-answer mark may differ —
# the second stands for more — but not by so much that they read as two different
# markers. They were 8 and 13 (a 1.6x gap in drawn glyph size), and a real copy
# carried both on the SAME page. _STEP_MARK and _WHOLE_MARK keep that ratio honest,
# and the test suite pins it.
_STEP_MARK_SIZE = 8.0
_WHOLE_MARK_SIZE = 10.0
_MAX_MARK_SIZE_RATIO = 1.3


# ---------- Kalam font for handwritten remarks & marks ----------

import sys as _sys

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# When frozen by PyInstaller, bundled data (Kalam-Regular.ttf) lives in sys._MEIPASS, not
# next to this module — check there FIRST so the EXE uses Kalam instead of falling back to
# a Windows cursive font (Segoe Script).
_BUNDLE_DIR = getattr(_sys, "_MEIPASS", _PROJECT_DIR)

# Use Kalam font from the bundle / project folder.
_CURSIVE_FONT_CANDIDATES = [
    os.path.join(_BUNDLE_DIR, "Kalam-Regular.ttf"),   # bundled into the EXE
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
        '−': '-',      # U+2212 minus - NOT the ASCII hyphen, and base-14 has no glyph
        '–': '-',      # en dash
        '—': '-',      # em dash
        '×': 'x',      # multiplication
        '÷': '/',      # division
        '∈': ' in ',   # set membership
        '∉': ' not in ',
        '⊂': ' subset of ',
        '⊆': ' subset of ',
        '∪': ' union ',
        '∩': ' intersect ',
        '∅': '{}',     # empty set
        '∴': 'therefore',
        '∵': 'because',
        '≡': '=',
        '°': ' deg',
        '‘': "'", '’': "'", '“': '"', '”': '"',
        '…': '...',
        # Currency and capital Greek are outside WinAnsi, so the safety net below
        # DROPS them silently - and a dropped currency sign changes what a number
        # means. A commerce copy said "deduct ₹500" and the student read "deduct
        # 500"; a maths one said "Δx" and the student read "x".
        '₹': 'Rs.',   # rupee
        '€': 'EUR ',  # euro
        'Δ': 'delta ',
        '∆': 'delta ',
        'Σ': 'sum',
        'Ω': 'ohm',
        'Π': 'product',
        'μ': 'mu',
        'ρ': 'rho',
        'φ': 'phi',
        'χ': 'chi',
        'τ': 'tau',
        'η': 'eta',
    }

    result = text
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)

    # Safety net. The cover is drawn in the base-14 fonts, whose encoding is WinAnsi:
    # anything outside it renders as a hollow box, which is how a student came to read
    # an interval as "(-2,·)". Fold what decomposes to ASCII, drop what does not - a
    # missing character is bad, a box that looks like part of the maths is worse.
    try:
        result.encode("latin-1")
    except UnicodeEncodeError:
        import unicodedata
        folded = unicodedata.normalize("NFKD", result)
        result = "".join(
            ch for ch in folded
            if not unicodedata.combining(ch) and _encodable(ch)
        )
    # A spelled-out symbol brings its own spacing ("A ∪ B" -> "A  union  B"), and a
    # double space is visible in a proportional font.
    return re.sub(r"[ 	]{2,}", " ", result)


def _encodable(ch: str) -> bool:
    try:
        ch.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


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


def _break_long_token(word: str, max_width: float, measure) -> list[str]:
    """Break a single token that is wider than the column.

    The per-step score line is written "0.5+0.5+0.5+0.5=2/3" — one token with no
    spaces — so space-only wrapping could never break it and it ran off the edge of
    the marks strip. Break after a '+' where possible, else by character.
    """
    if measure(word) <= max_width:
        return [word]
    out, cur = [], ""
    for ch in word:
        if cur and measure(cur + ch) > max_width:
            cut = cur.rfind("+")
            if cut > 0 and cut >= len(cur) - 6:      # break just after a '+'
                out.append(cur[: cut + 1])
                cur = cur[cut + 1:]
            else:
                out.append(cur)
                cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def _wrap_to_width(text: str, max_width: float, measure) -> list[str]:
    """Wrap `text` to `max_width`, breaking over-long tokens too.

    THE single source of truth for wrapping. The code that DRAWS a strip entry and
    the code that RESERVES its height must wrap identically: when only the drawing
    side learned to break the score line, the reserved slot stayed one line tall and
    the remark was written straight over the wrapped remainder ("0.5+0+0.5+0.5+0+ /
    0.5=2/3" with "sales entry amount wrong" printed through it).
    """
    words = [piece
             for w in text.split()
             for piece in _break_long_token(w, max_width, measure)]
    lines: list[str] = []
    current = ""
    for w in words:
        trial = (current + " " + w).strip()
        if measure(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


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

    # Word-wrap via the shared helper so height reservation cannot disagree.
    lines = _wrap_to_width(text, max_width, _measure)

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
                    w_start: float, w_end: float, seed: str = "",
                    width_at=None) -> None:
    """Draw a polyline from pts, its width following `width_at` along the stroke.

    `width_at(g)` is passed the arc-length fraction 0..1 and returns a width; the
    default tapers linearly from w_start to w_end.

    The jitter is applied to the SHARED vertices, not to each sub-segment. Giving
    every sub-segment its own offset translated the pieces away from each other, so
    a tick came out with white notches punched through it where consecutive pieces
    no longer met. Round caps and joins close what is left at a width change.
    """
    if len(pts) < 2:
        return
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

    SUBDIV = 8  # sub-segments per polyline segment
    if width_at is None:
        def width_at(g):
            return w_start + (w_end - w_start) * g

    # One densified centreline, jittered once per vertex, so consecutive
    # sub-segments start exactly where the previous one ended.
    # The wobble is scaled by the local width. A flat 0.25pt of jitter is invisible
    # on a 5pt stroke and a visible kink on the half-point tip of a tick, which is
    # exactly where a real pen wobbles least.
    w_ref = max(w_start, w_end, 0.1)
    spine: list[tuple[float, float, float]] = []   # (x, y, arc fraction)
    travelled = 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        seg = seg_lens[i]
        last = SUBDIV if i == len(pts) - 2 else SUBDIV - 1
        for s in range(last + 1):
            t = s / SUBDIV
            k = i * SUBDIV + s
            g = (travelled + seg * t) / total
            amp = 0.28 * min(1.0, width_at(g) / w_ref)
            spine.append((
                ax + (bx - ax) * t + _wobble(seed or "s", k, amp=amp),
                ay + (by - ay) * t + _wobble(seed or "s", k + 99, amp=amp),
                g,
            ))
        travelled += seg

    for (x0, y0, g0), (x1, y1, g1) in zip(spine, spine[1:]):
        page.draw_line(
            fitz.Point(x0, y0), fitz.Point(x1, y1),
            color=RED, width=max(0.3, width_at((g0 + g1) / 2)),
            lineCap=1, lineJoin=1,
        )


def _draw_tick(page: fitz.Page, x: float, y: float, size: float = 26,
               seed: str = "") -> None:
    """Hand-drawn red tick: a short stroke down into the elbow, then a long flick
    that stays broad and lifts off to a fine point.

    The width used to fall away linearly across the WHOLE stroke, so the flick was
    already thin where a pen is still pressed hard. A real tick holds its weight up
    the flick and thins only as the hand lifts, which is what `_TICK_HOLD` and the
    exponent below draw.

    No two ticks are identical either. Length, height, elbow depth and pressure are
    jittered from the seed, because a column of forty identical ticks reads as a
    stamp rather than as marking. Every factor is <= 1.0 so the drawn tick still
    fits the cluster width its caller reserved for it.
    """
    seed = seed or f"t{x:.1f}{y:.1f}"
    stretch = 0.93 + _wobble(seed, 11, amp=0.07)   # how far the flick runs
    lift = 0.93 + _wobble(seed, 12, amp=0.07)      # how high it finishes
    dip = 0.95 + _wobble(seed, 13, amp=0.10)       # how deep the elbow sits
    heavy = 1.0 + _wobble(seed, 14, amp=0.16)      # pen pressure on this one

    p1 = (x, y - size * 0.25)
    p2 = (x + size * 0.30, y + size * 0.20 * dip)
    p3 = (x + size * 1.50 * stretch, y - size * 1.40 * lift)

    w_max = max(1.6, size * 0.20) * heavy
    w_head = w_max * 0.55          # the short stroke starts lighter
    w_tip = max(0.35, size * 0.035)

    d1 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    d2 = math.hypot(p3[0] - p2[0], p3[1] - p2[1])
    elbow = d1 / (d1 + d2) if (d1 + d2) > 0 else 0.5

    _TICK_HOLD = 0.50              # fraction of the flick drawn at full weight

    def width_at(g: float) -> float:
        if g <= elbow:
            # Pressure builds on the way down into the elbow.
            return w_head + (w_max - w_head) * (g / elbow if elbow > 0 else 1.0)
        t = (g - elbow) / (1.0 - elbow) if elbow < 1.0 else 1.0
        # Full weight for the first half of the flick, then a quadratic lift-off.
        # Quadratic on purpose: its slope is zero where the taper begins, so the
        # width does not kink there — a piecewise-linear fall left a visible bead
        # on the flick where the wide half butted against the thin one — and steep
        # where it ends, so the stroke still finishes on a point.
        u = max(0.0, (t - _TICK_HOLD) / (1.0 - _TICK_HOLD))
        return max(w_tip, w_max * (1.0 - 0.06 * t) * (1.0 - u * u))

    _tapered_stroke(page, [p1, p2, p3], w_max, w_tip, seed=seed, width_at=width_at)


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


_UNDERLINE_DROP = 6.0    # how far under the text's centre an underline sits


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


def _draw_underline(page: fitz.Page, x: float, y: float, width: float) -> None:
    """Hand-drawn red underline. `y` is where the line sits, not the text's centre.

    The caller works it out from the OCR line box, which is much taller than the
    glyphs it holds — dropping a fixed distance from the centre still lands inside
    the writing on a tall box.
    """
    seed = f"ul{x:.1f}{y:.1f}{width:.1f}"
    steps = max(8, int(width / 6))
    pts = []
    for i in range(steps + 1):
        t = i / steps
        pts.append((x + width * t, y + _wobble(seed, i, amp=0.5)))
    _tapered_stroke(page, pts, w_start=1.3, w_end=0.7, seed=seed)


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


# A ledger's Total / Balance c/d row carries no working of its own - the marks belong
# on the lines where the student actually did something. Nothing in the codebase knew
# about these rows, so the even-spread and push-down placement stages happily put a
# tick on "Total", which is what a reviewer flagged: "no mark will be awarded for
# 'Total' but for 'Interest on Drawings'".
_TOTAL_ROW_RE = re.compile(
    r"^\W*(?:(?:by|to)\s+)?(?:"
    r"grand\s+total|sub[\s-]*total|net\s+total|total"
    r"|bal(?:ance)?\s*(?:c\s*/?\s*d|b\s*/?\s*d|c\s*/?\s*f|b\s*/?\s*f)"
    r"|carried\s+(?:down|forward)|brought\s+(?:down|forward)"
    r"|c\s*/\s*d|b\s*/\s*d|c\s*/\s*f|b\s*/\s*f"
    r"|कुल|योग"
    r")\b",
    re.IGNORECASE)
# Words that make a criterion genuinely ABOUT the total, so a tick there is correct.
_TOTAL_CRITERION_RE = re.compile(
    r"\b(total|totalling|totaling|casting|cast|footing|balancing|grand\s+total)\b",
    re.IGNORECASE)


# line_ids the OCR pass flagged as struck through. Kept beside the line index rather
# than threaded through every signature, because the index is a flat tuple and every
# placement stage would otherwise need widening.
_CANCELLED_LIDS: set[str] = set()


def _is_cancelled_line(pl: dict) -> bool:
    """Whether this line is working the student crossed out."""
    return (pl or {}).get("lid", "") in _CANCELLED_LIDS


def _is_total_row(text: str) -> bool:
    """True for a Total / Grand Total / Balance c/d style row.

    Only the row's LABEL is tested, so "Total Debtors A/c" (a real account name) and
    prose that merely contains the word are not caught.
    """
    t = (text or "").strip()
    if not t:
        return False
    label = re.split(r"[\d₹|]", t, 1)[0]      # stop at the first figure or column rule
    if len(label.split()) > 4:                     # a sentence, not a row label
        return False
    label = label.strip()
    hit = _TOTAL_ROW_RE.match(label)
    if not hit:
        return False
    # The keyword must BE the label, not just start it: "Total Debtors A/c" is an
    # account in its own right and its line does carry working.
    return not re.search(r"[A-Za-zऀ-ॿ]", label[hit.end():])


def _is_total_criterion(c) -> bool:
    """Whether this criterion is about the total itself, so a tick there is right."""
    text = " ".join(str(getattr(c, f, "") or "") for f in ("description", "evidence"))
    return bool(_TOTAL_CRITERION_RE.search(text))


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
    if _heading_num(s) is not None:    # "Answer-4", "Ans 2", "Solution 3" labels
        return True
    return False


# ---------- Right-hand marks/remarks strip ----------
#
# Each student page is widened with a blank white strip on the right. All
# per-question ticks, scores, and remarks are written there — never on top of
# the student's writing, and never clipped at the page edge. `_CONTENT_W` records
# the ORIGINAL (left) content width per page so OCR pixel→PDF mapping still maps
# onto the writing area, not the widened page. `_STRIP_SLOTS` tracks occupied
# vertical intervals so an out-of-order anchor can still use free space above a
# later answer instead of being pushed beside the following answer.
STRIP_FRACTION = 0.24        # strip width as a fraction of the original page width
STRIP_MIN_WIDTH = 175.0      # but never narrower than this (points)
_STRIP_TOP = 26.0            # first entry keeps clear of the page's top edge
_CONTENT_W: dict[tuple[int, int], float] = {}
_STRIP_SLOTS: dict[int, dict[int, list[tuple[float, float]]]] = {}


def _content_width(page: fitz.Page) -> float:
    """Width of the student-writing area (original page width), excluding the
    right-hand marks strip. Falls back to full page width for un-widened pages."""
    return _CONTENT_W.get((id(page.parent), page.number + 1), page.rect.width)


def _reset_strip_state(doc: fitz.Document) -> None:
    _STRIP_SLOTS[id(doc)] = {}


def _reserve_strip_slot(doc: fitz.Document, page_num: int, y_pref: float,
                        height: float, page_h: float) -> float:
    """Reserve the free vertical slot nearest ``y_pref``.

    Question records are not guaranteed to arrive in page-y order (for example,
    a subpart may have an OCR anchor while its siblings do not).  A single
    forward-only cursor therefore displaced an earlier answer hundreds of points
    down the page.  Keeping occupied intervals lets a late-arriving earlier
    answer use the genuinely free space above existing annotations.
    """
    top = _STRIP_TOP
    bottom = max(top, page_h - 8.0)
    height = max(0.0, float(height))
    preferred = min(max(float(y_pref), top), max(top, bottom - height))
    gap = 6.0

    reg = _STRIP_SLOTS.setdefault(id(doc), {})
    occupied = sorted(reg.setdefault(page_num, []))
    free: list[tuple[float, float]] = []
    free_start = top
    for start, end in occupied:
        free_end = start - gap
        if free_end >= free_start:
            free.append((free_start, free_end))
        free_start = max(free_start, end + gap)
    if bottom >= free_start:
        free.append((free_start, bottom))

    candidates: list[float] = []
    for start, end in free:
        latest = end - height
        if latest >= start:
            candidates.append(min(max(preferred, start), latest))

    if candidates:
        y = min(candidates, key=lambda candidate: (abs(candidate - preferred), candidate))
    else:
        # An overfull strip is caught by rendered-copy preflight.  Keep the
        # annotation on-page so the failure is visible and diagnosable.
        y = preferred

    occupied.append((y, y + height))
    reg[page_num] = sorted(occupied)
    return y


def _strip_wrap_lines(text: str, max_w: float, size: float,
                      fontname: str = "helv") -> int:
    """Count how many wrapped lines `text` needs at width max_w — for height
    reservation. Measured slightly wide so we over-reserve rather than overlap."""
    def measure(txt: str) -> float:
        try:
            return fitz.get_text_length(txt, fontname=fontname, fontsize=size + 1)
        except Exception:
            return len(txt) * (size + 1) * 0.55

    return max(1, len(_wrap_to_width(text, max_w, measure)))


def _fmt_mark(v: float) -> str:
    """3 not 3.0, 2.5 stays 2.5 — how a teacher writes a mark."""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _draw_strip_entry(page: fitz.Page, qid: str, awarded: float,
                      max_score: float, remark: str, y_pref: float,
                      steps: list[float] | None = None,
                      verdict: str = "") -> None:
    """Write one question's score and feedback in the right-hand strip.

    Objective rows also receive a compact verdict glyph in this QID-labelled lane,
    where it cannot be mistaken for a neighbouring answer's verdict.
    """
    doc = page.parent
    rect = page.rect
    cw = _content_width(page)
    verdict = str(verdict or "").strip().lower()
    verdict_lane = 25.0 if verdict in {"correct", "wrong"} else 0.0
    x0 = cw + 12.0 + verdict_lane
    x1 = rect.width - 8.0
    max_w = max(110.0, x1 - x0)

    full = max_score > 0 and awarded >= max_score - 1e-6
    remark = (remark or "").strip()
    # Sanitize remark to avoid PDF encoding issues with special characters
    remark = _sanitize_for_pdf(remark)
    show_reason = bool(remark) and not full

    _fmt = _fmt_mark

    # Show step-by-step calculation if steps are provided
    if steps and len(steps) > 1:
        # Show calculation with denominator: 0.5+1=1.5/2
        calc_txt = "+".join(_fmt(s) for s in steps) + f"={_fmt(awarded)}/{_fmt(max_score)}" if max_score else _fmt(awarded)
        head = f"{qid}"
        score_txt = calc_txt
    else:
        score_txt = f"{_fmt(awarded)}/{_fmt(max_score)}" if max_score else _fmt(awarded)
        head = f"{qid}  {score_txt}".strip()

    # The whole strip entry is written in the handwriting font — a typed bold
    # "1(a) 2/2" next to cursive remarks was the single biggest "a computer did
    # this" tell on the page.
    head_size = 15.0
    head_line_h = head_size * 1.4
    reason_size = 14.5

    # If we have steps, show calculation on its own line. The calculation itself can
    # WRAP ("0.5+0.5+0.5+0.5+0.5+0.5=3/3" is wider than the strip), so reserve for the
    # lines it will really take — counted with the same wrapper that draws it.
    # A short reason rides on the head line. An MCQ block is five entries deep and
    # "Q1.ii 0/2" + "Ans: A" on separate lines needs seven lines where the strip has
    # room for five — the last two entries were reserved a slot off the bottom of the
    # page and silently never appeared, so two of the five answers came back with no
    # mark shown at all.
    if show_reason and not (steps and len(steps) > 1):
        merged = f"{head}  {remark}"
        if _strip_wrap_lines(merged, max_w, head_size) == 1:
            head, show_reason = merged, False
    reason_lines = _strip_wrap_lines(remark, max_w, reason_size) if show_reason else 0
    reason_h = (reason_lines * reason_size * 1.32 + 8) if show_reason else 0
    if steps and len(steps) > 1:
        calc_lines = _strip_wrap_lines(score_txt, max_w, head_size)
        block_h = head_line_h * (1 + calc_lines) + reason_h + 8
    else:
        block_h = head_line_h + reason_h + 8

    y = _reserve_strip_slot(doc, page.number + 1, y_pref - head_size, block_h, rect.height)

    # Objective verdicts live in a dedicated lane inside the widened margin. They
    # remain attached to an explicit QID/score but can never cover the student's
    # answer or be pushed onto a neighbouring MCQ row.
    if verdict == "correct":
        _draw_tick(page, cw + 9.0, y + head_size * 0.70,
                   size=_ms(13), seed=f"{qid}|strip|tick")
    elif verdict == "wrong":
        _draw_cross(page, cw + 16.0, y + head_size * 0.55,
                    size=_ms(10), seed=f"{qid}|strip|cross")

    # Question ID + score, handwritten like the rest of the margin.
    last_baseline = _draw_handwritten_remark(page, x0, y + head_size, head,
                                             max_width=max_w, size=head_size)

    if steps and len(steps) > 1:
        last_baseline = _draw_handwritten_remark(
            page, x0, y + head_line_h * 2, score_txt,
            max_width=max_w, size=head_size)

    if show_reason:
        # Follow the ACTUAL last baseline rather than assuming the line above was one
        # line tall — that assumption is what printed the remark over the wrapped
        # score calculation.
        _draw_handwritten_remark(page, x0, last_baseline + reason_size * 1.35,
                                 remark, max_width=max_w, size=reason_size)

    # Where this entry ends, so a caller laying out an ordered list can ask for the
    # space directly below it instead of "near y", which the slot search answers with
    # the nearest free gap in EITHER direction - and that is what shuffled a column of
    # MCQ verdicts out of question order.
    return y + block_h


# ---------- What counts as the student's own ink -------------------------------
# ONE definition, shared by mark placement and the pre-flight check, because two
# different notions of "ink" is exactly the bug they had between them: placement
# reasoned about OCR boxes while pre-flight measured pixels, so neither saw what the
# other did. Two dark things on a rendered page are not the student: the red marks we
# just drew, and the notebook's printed ruling.
#
# The ruling is the expensive one. MEASURED at 150 dpi on a phone photo of a ruled
# CBSE sheet: one printed rule crossing a mark's circle covers ~5.5% of it, and
# pre-flight blocks at 6% — so a mark sitting on perfectly blank paper was reported as
# "drawn over the student's writing, what is under it cannot be read". The same
# blindness meant the clearance search could not find a free disc anywhere on a ruled
# row, so it gave up and left every mark on top of the writing.
#
# Ruling is told from handwriting by horizontal EXTENT: a printed rule is one unbroken
# run across the sheet, while handwriting breaks every few pixels. Thickness cannot do
# it — a rule is ~2px and a pen stroke ~3-4px.
_RULE_MIN_RUN = 0.35     # an unbroken dark run this wide is ruling, not writing
_INK_DPI = 150


def _drop_printed_ruling(mask) -> None:
    """Clear runs long enough to be the page's printed ruling. In place."""
    import numpy as np

    height, width = mask.shape
    limit = max(24, int(width * _RULE_MIN_RUN))
    # A run that long needs at least that much coverage, so most rows are skipped.
    for y in np.flatnonzero(mask.mean(axis=1) >= _RULE_MIN_RUN):
        row = mask[y]
        edges = np.flatnonzero(np.diff(np.concatenate(
            ([0], row.astype(np.int8), [0]))))
        for start, end in zip(edges[0::2], edges[1::2]):
            if end - start >= limit:
                row[start:end] = False


def student_ink_mask(page, dpi: int = _INK_DPI):
    """(mask, scale) — True where the student's own pen is, in page pixels.

    `scale` converts PDF points to those pixels. Returns (None, 1.0) when the page
    cannot be rasterised, so every caller falls back to its previous behaviour
    rather than reporting a page as blank.
    """
    try:
        import numpy as np

        pm = page.get_pixmap(dpi=dpi)
        arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(
            pm.height, pm.width, pm.n)
        r = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 2].astype(np.int16)
        red = (r > 110) & (r > g + 55) & (r > b + 55)      # our own annotations
        mask = (r < 150) & (g < 150) & (b < 150) & ~red
        _drop_printed_ruling(mask)
        return mask, dpi / 72.0
    except Exception:
        return None, 1.0


def rect_ink_fraction(mask, scale: float, x0: float, y0: float,
                      x1: float, y1: float) -> float:
    """How much of this rectangle sits on the student's writing.

    1.0 for a rectangle off the page, so a mark is never relocated off the sheet.
    """
    if mask is None:
        return 0.0
    height, width = mask.shape
    a = int(max(0, x0 * scale)), int(max(0, y0 * scale))
    b = int(min(width, x1 * scale + 1)), int(min(height, y1 * scale + 1))
    if b[0] <= a[0] or b[1] <= a[1]:
        return 1.0
    return float(mask[a[1]:b[1], a[0]:b[0]].mean())


def disc_ink_fraction(mask, scale: float, cx: float, cy: float,
                      radius: float) -> float:
    """How much of the circle at (cx, cy) sits on the student's writing.

    1.0 for a disc entirely off the page, so a mark is never relocated off the sheet.
    """
    if mask is None or radius <= 0:
        return 0.0
    import numpy as np

    height, width = mask.shape
    px, py, pr = cx * scale, cy * scale, radius * scale
    x0, x1 = int(max(0, px - pr)), int(min(width, px + pr + 1))
    y0, y1 = int(max(0, py - pr)), int(min(height, py + pr + 1))
    if x1 <= x0 or y1 <= y0:
        return 1.0
    ys, xs = np.ogrid[y0:y1, x0:x1]
    inside = (xs - px) ** 2 + (ys - py) ** 2 <= pr * pr
    if not inside.any():
        return 1.0
    return float(mask[y0:y1, x0:x1][inside].mean())


def _build_line_index(ocr_pages: list[PageOCR] | None) -> dict[str, tuple]:
    """Map line_id -> (page_num, bbox_px, image_w, image_h, line_text)."""
    idx: dict[str, tuple] = {}
    # Rebuilt per document: a stale id from the previous sheet would silently
    # suppress a tick on this one.
    _CANCELLED_LIDS.clear()
    if not ocr_pages:
        return idx
    for p in ocr_pages:
        for line in p.lines:
            # A struck-out line keeps its geometry (a cross may still be drawn on it)
            # but is never a candidate for a TICK — see _is_cancelled_line.
            idx[line.line_id] = (line.page, line.bbox, p.image_width, p.image_height,
                                 line.text)
            if getattr(line, "cancelled", False):
                _CANCELLED_LIDS.add(line.line_id)
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


# Sentence boundary: ./!/?/] followed by whitespace and something that starts a new
# sentence ("]" so a bracketed [NOTICE ...] becomes its own bullet). Requiring a
# non-digit, non-lowercase next char keeps decimals ("64.5"), abbreviations ("e.g. this")
# and amounts ("Rs. 50") in ONE bullet instead of shredding them — a naive split('.')
# once printed "sum to 64." and "5 but..." as two bullets.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\]])\s+(?=[^\sa-z\d])")


def _split_remark_sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in (text or "").split("\n"):
        chunk = chunk.strip()
        if chunk:
            out.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(chunk) if s.strip())
    return out


# Third-person verb forms that follow "the student ..." in model remarks, mapped to the
# second-person form that must follow "you ...". Applied only to this curated list so a
# noun ending in -s is never mangled.
_THIRD_TO_SECOND = {
    "has": "have", "is": "are", "was": "were", "does": "do", "goes": "go",
    "demonstrates": "demonstrate", "shows": "show", "needs": "need", "misses": "miss",
    "makes": "make", "lacks": "lack", "uses": "use", "writes": "write", "gives": "give",
    "provides": "provide", "fails": "fail", "scores": "score", "applies": "apply",
    "understands": "understand", "follows": "follow", "presents": "present",
    "explains": "explain", "calculates": "calculate", "treats": "treat", "records": "record",
    "prepares": "prepare", "displays": "display", "exhibits": "exhibit", "attempts": "attempt",
    "answers": "answer", "knows": "know", "seems": "seem", "appears": "appear", "tends": "tend",
    "struggles": "struggle", "confuses": "confuse", "omits": "omit", "forgets": "forget",
    "ignores": "ignore", "states": "state", "mentions": "mention", "identifies": "identify",
    "computes": "compute", "handles": "handle", "performs": "perform", "commits": "commit",
    "leaves": "leave", "skips": "skip", "mixes": "mix", "reaches": "reach", "takes": "take",
    "includes": "include", "quotes": "quote", "cites": "cite", "requires": "require",
}
_STUDENT_REF_RE = re.compile(r"\b([Tt]he student)('s)?(?:\s+([A-Za-z]+))?")


def _personalize(text: str) -> str:
    """Rewrite 'the student ...' remarks in the second person, grammatically:
    'The student has' -> 'You have', "the student's" -> 'your', 'The student
    demonstrates' -> 'You demonstrate'. (A naive replace produced 'You has'.)"""

    def repl(m: re.Match) -> str:
        cap = m.group(1)[0] == "T"
        tail = (" " + m.group(3)) if m.group(3) else ""
        if m.group(2):                                   # possessive
            return ("Your" if cap else "your") + tail
        pron = "You" if cap else "you"
        verb = m.group(3)
        if verb:
            fixed = _THIRD_TO_SECOND.get(verb.lower())
            if fixed is not None:
                verb = fixed.capitalize() if verb[0].isupper() else fixed
            return f"{pron} {verb}"
        return pron

    return _STUDENT_REF_RE.sub(repl, text or "")


def _draw_bulleted_remarks(page: fitz.Page, x: float, y: float, text: str, *,
                          size: float = 11, color=BLACK, fontname: str = "helv",
                          max_width: float = 515) -> float:
    """Draw remarks as bullet points for the student. Splits by sentences and formats nicely."""
    if not text or not text.strip():
        return y

    # Address the student directly, grammatically.
    text = _personalize(text)

    sentences = _split_remark_sentences(text)

    line_height = size * 1.35
    indent = 15
    bullet_x = x + 5
    bullet_indent = x + indent

    for sentence in sentences:
        # Ensure each bullet reads as a sentence (bracketed notices keep their bracket).
        if not sentence.endswith(('.', '?', '!', ']')):
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


# Mathpix diagram/table region anchors ("P3D1") — opaque blocks the OCR could not
# transcribe line by line (hand-drawn ledgers, statements, figures).
_DIAG_REGION_RE = re.compile(r"^P\d+D\d+$")

# A line that OPENS a numbered or lettered point: "(a) ", "ii. ", "3) ", "- ".
# Anchored to the start on purpose - "(a)" cited mid-sentence is prose, and only a
# line that begins a point is a point a teacher would tick.
_ENUM_LINE_RE = re.compile(
    r"^\s*(?:\(\s*(?:[a-z]|[ivx]{1,4}|\d{1,2})\s*\)"
    r"|(?:[a-z]|[ivx]{1,4}|\d{1,2})\s*[).]"
    r"|[-\u2022*])\s+\S",
    re.IGNORECASE)


_MARK_PAD = 8.0          # breathing room between a mark and the writing beside it
_MIN_DROP_BAND = 13.0    # a gap below the row has to be this tall to hold a mark


def _row_cells(by_lid: dict, page_no: int, y: float, own_lid: str = "") -> list[dict]:
    """Every OTHER piece of writing sharing this row, left to right."""
    return sorted((pl for lid, pl in by_lid.items()
                   if pl["page"] == page_no and lid != own_lid
                   and pl["y0"] - 2 <= y <= pl["y1"] + 2),
                  key=lambda pl: pl["x0"])


def _free_gaps(cells: list[dict], lo: float, hi: float) -> list[tuple[float, float]]:
    """Spans between `lo` and `hi` with no writing in them, padded off the writing."""
    gaps: list[tuple[float, float]] = []
    cur = lo
    for pl in cells:
        left = pl["x0"] - _MARK_PAD
        if left > cur:
            gaps.append((cur, left))
        cur = max(cur, pl["x1"] + _MARK_PAD)
    if hi > cur:
        gaps.append((cur, hi))
    return gaps


def _drop_below(by_lid: dict, page_no: int, y: float) -> float:
    """How far to move a mark down to reach the blank band under this row.

    0.0 when there is no band tall enough — the caller then keeps its own y.
    """
    row = [pl for pl in by_lid.values()
           if pl["page"] == page_no and pl["y0"] - 2 <= y <= pl["y1"] + 2]
    if not row:
        return 0.0
    bottom = max(pl["y1"] for pl in row)
    # "Below" means any line off this row that starts at or under its bottom edge.
    # Testing y0 > bottom + 1 misses the row directly beneath, whose y0 sits level
    # with this row's y1 — and then a mark drops straight onto it.
    below = [pl["y0"] for pl in by_lid.values()
             if pl["page"] == page_no
             and not (pl["y0"] - 2 <= y <= pl["y1"] + 2)
             and pl["y0"] >= bottom - 2]
    next_top = min(below) if below else bottom + 34.0
    if next_top - bottom < _MIN_DROP_BAND:
        return 0.0
    return (bottom + next_top) / 2 - y


# Per-document, per-page student-ink rasters, built on first use so a page with no
# marks never pays for one. Keyed by id(doc) like the other build registries.
_MARK_INK: dict[int, dict[int, tuple]] = {}

_MARK_CLEAR_INK = 0.010  # leave measurement headroom: placement probes the full
                         # drawn circle while pre-flight probes its 11pt numeral core
_MARK_SLIDE_MAX = 130.0  # how far along its row a mark may travel from where the
                         # caller wanted it. A mark means nothing beside the wrong
                         # step, and an earlier repair that roamed 177pt into the
                         # margin read worse than the overlap it was fixing.
_MARK_LIFT_MAX = 30.0    # ...and how far up or down, when the row is full
_MARK_LEAST_BAD_GAIN = 0.01  # when nothing is clear, how much cleaner a spot has to
                             # be before the mark is moved to it at all

# What a teacher does when the answer runs to the edge of the page: they write the
# mark SMALLER and squeeze it in, rather than printing it over the words. Tried only
# after a full-size spot has been looked for everywhere on the line, because a normal
# mark a little further along still reads better than a tiny one.
# MEASURED on the CBSE Chemistry sheet: of 14 marks with nowhere to go, 9 sit on
# writing that a smaller circle clears outright — one went from 9% ink to 0%.
_MARK_SHRINKS = (1.0, 0.8, 0.65, 0.55)   # 0.55 is still legible next to the writing

# Fallback probe radius, for a caller that draws no circle at all (a bare tick).
# Anything that DOES draw one passes its real radius. Pre-flight's fixed 11pt is
# barely half the circle actually drawn (measured mean 19.1pt), and probing at 11
# declared spots clear that the real mark then covered: 38 of 43 marks sat on the
# writing whether or not the search ran, while the 11pt probe reported 14 of them.
# Probe what you draw.
_MARK_PROBE_R = 11.0


# Steps this build could not place on the student's writing, collected per copy.
# Builds are serialised by _RENDER_LOCK, so a plain dict is safe here for the same
# reason _CONTENT_W and _MARK_INK are. Read back onto the report at the end of the
# build: with no human reviewing held copies, an unplaced step has to become a
# re-marking trigger rather than a silently missing tick.
_UNANCHORED: dict[str, int] = {}


def _note_unanchored(qid: str, count: int) -> None:
    if count > 0:
        _UNANCHORED[str(qid or "?")] = _UNANCHORED.get(str(qid or "?"), 0) + count


def _page_ink(doc: "fitz.Document | None", page_no: int) -> tuple:
    """The student-ink raster for this page, built once per copy.

    `doc` is None in unit tests that exercise the OCR-cell reasoning on its own;
    that returns no mask, and the caller falls back to it.
    """
    if doc is None:
        return None, 1.0
    reg = _MARK_INK.setdefault(id(doc), {})
    if page_no not in reg:
        try:
            reg[page_no] = student_ink_mask(doc[page_no - 1])
        except Exception:
            reg[page_no] = (None, 1.0)
    return reg[page_no]


def _mark_ink(mask, scale: float, x: float, y: float,
              probe_dx: float, probe_r: float, probe_dy: float = 0.0) -> float:
    """How buried the mark would be if the cluster started at x.

    probe_dy matters for a bare tick: it is drawn from its anchor UP and to the
    right, so its ink sits well above the y the caller passes, and probing at that
    y measured empty paper while the tick itself lay across the writing.
    """
    return disc_ink_fraction(mask, scale, x + probe_dx, y + probe_dy, probe_r)


def _clear_x_for_mark(doc: "fitz.Document | None", by_lid: dict, page_no: int, y: float,
                      x_from: float, cluster_w: float, right_cap: float,
                      own_lid: str = "", probe_dx: float | None = None,
                      probe_r: float = _MARK_PROBE_R, probe_dy: float = 0.0,
                      lift_max: float = _MARK_LIFT_MAX) -> tuple[float, float, float]:
    """Find a spot on this row where a mark covers none of the student's writing.

    Marks were placed a fixed 12pt past the ANCHORED line's own right edge, with no
    look at what else sits on that row. In a ledger the row is several separate OCR
    cells — "To Intt on capital :" and "150 000" are different lines at the same y —
    so the mark landed inside the amount column and printed over the student's
    figures ("150 000" came out as "150(0.5)00"). On a sheet with a rough-work
    column the same rule dropped a tick into the rough work.

    Sliding right fixes that only while the row has room to the right. In a cash
    book or balance sheet the amounts column runs to the edge of the page, so there
    is none, and the old fallback returned the caller's overlapping x — printing the
    mark straight through the figure. On a real CA Foundation Accounting copy that
    put 6 of 27 marks on the student's own numbers, one of them turning 7,61,024
    into "76(1)24". Prose papers hid the bug: they have a clear right margin, and
    only 1 of 39 marks overlapped.

    Returns `(x, dy, shrink)` — the start x, how far to move the mark DOWN, and what
    to multiply its size by. Preference, in the order a teacher works: a clear spot at
    full size along this line, then the blank band above or below it, then the same
    search with a smaller mark, and only if the whole line is written over, the least
    buried spot going. A mark is never put out on the desk around a photographed page.
    """
    # OCR boxes are only a guess at where the writing is: Mathpix returns clean text
    # lines and misses how far real handwriting sprawls, so a "free gap" between two
    # cells is routinely full of the student's pen. Pre-flight then measured the
    # PIXELS and blocked the copy — placement and the gate were reading two different
    # pages. MEASURED on a ruled CBSE sheet: 28 of 40 marks landed on writing, up to
    # 90% buried. So the gaps below are treated as candidates, and the page raster
    # decides. Only a spot verified clear is used, so this can never place worse than
    # the caller asked for.
    mask, scale = _page_ink(doc, page_no)
    if probe_dx is None:
        probe_dx = cluster_w / 2
    if mask is not None:
        step = 3.0
        floor = 2.0
        rightward = [x_from + i * step for i in range(int(_MARK_SLIDE_MAX / step) + 1)]
        leftward = [x_from - (i + 1) * step for i in range(int(_MARK_SLIDE_MAX / step))]
        # Ink alone is not enough: in a ledger the gap between "150" and "000" IS
        # blank paper, and dropping the mark into it is the very defect this function
        # exists for. So a candidate must clear the OCR cells AND the pixels.
        # The cell rule guards ONE hazard: dropping the mark into the amounts column
        # of the row it is marking, where blank paper between "150" and "000" is not
        # free space. That is about the anchor row, so it is applied only there. It
        # used to be applied at every vertical offset as well, using the anchor row's
        # cells — and on a CA ledger, whose anchor row is written cell to cell, that
        # blocked every candidate at every offset: the search reported "nowhere to go"
        # with 23 clear spots one line up. MEASURED, marks left sitting on writing:
        # anchor-row cells everywhere 1 (CA) / 10 (Chemistry); cells re-looked-up per
        # row 0 / 12; cells on the anchor row only, ink alone above and below 0 / 8.
        _cells_at: dict[float, list] = {}

        def _in_a_cell(x: float, dy: float = 0.0) -> bool:
            if dy not in _cells_at:
                _cells_at[dy] = _row_cells(by_lid, page_no, y + dy, own_lid)
            return any(x < c["x1"] + _MARK_PAD and x + cluster_w > c["x0"] - _MARK_PAD
                       for c in _cells_at[dy])

        row = [x for x in rightward + leftward if floor <= x <= right_cap]
        start_ink = _mark_ink(mask, scale, x_from, y, probe_dx, probe_r, probe_dy)
        best = (x_from, 0.0, 1.0, start_ink)
        # Full size everywhere on the line first — a normal mark a little further
        # along reads better than a small one exactly on the spot. Only when the whole
        # line is written over does the mark get smaller, which is what a teacher does.
        for shrink in _MARK_SHRINKS:
            sdx, sr = probe_dx * shrink, probe_r * shrink
            band = [0.0] + sorted([d for k in range(4, int(lift_max) + 1, 3)
                                   for d in (k, -k)], key=abs)
            for dy in band:
                # Searched at full resolution throughout. Sampling the bands above and
                # below every other step ran the whole build 1.7s faster on an 8-page
                # copy and put two more marks back on the student's writing; rendering
                # is ~2% of what grading one copy costs, so the marks win.
                for x in row:
                    if dy == 0.0 and _in_a_cell(x, dy):
                        continue
                    got = _mark_ink(mask, scale, x, y + dy, sdx, sr, probe_dy * shrink)
                    if got <= _MARK_CLEAR_INK:
                        return x, dy, shrink
                    if dy == 0.0 and got < best[3]:
                        best = (x, 0.0, shrink, got)
        # Nowhere on this line is clear at any size. Take the LEAST buried spot rather
        # than the caller's, which on a phone photo is often past the last word and
        # out on the desk, where a red mark simply cannot be seen. Only move for a
        # real gain, so a mark never drifts across the page to win a percentage point.
        if start_ink - best[3] >= _MARK_LEAST_BAD_GAIN:
            return best[0], best[1], best[2]
        # else fall through to the OCR-cell reasoning below, which is what ran
        # before the raster existed.

    cells = _row_cells(by_lid, page_no, y, own_lid)
    if not cells:
        return x_from, 0.0, 1.0

    hi = right_cap + cluster_w          # the furthest right the cluster may END
    fits = [(a, b) for a, b in _free_gaps(cells, 2.0, hi) if b - a >= cluster_w]

    # 1. the first gap that can hold the mark at or after the caller's x.
    # Only ever rightward: the caller picked x_from to clear the anchor's OWN
    # writing, and that line is not in `cells`, so any gap to the left of x_from
    # looks empty here while actually sitting on top of the student's words.
    for a, b in fits:
        if b - max(a, x_from) >= cluster_w:
            return max(a, x_from), 0.0, 1.0
    # 2. the row is full to the right edge — go under it rather than through it
    dy = _drop_below(by_lid, page_no, y)
    if dy:
        return min(x_from, right_cap), dy, 1.0
    return x_from, 0.0, 1.0


def _render_question_marks(student_doc: fitz.Document, m, my_lines: list[dict],
                            by_lid: dict[str, dict],
                            placed_by_page: dict[int, list[float]] | None = None,
                            region_start: tuple[int, float] | None = None,
                            region_end: tuple[int, float] | None = None,
                            objective_queue: list | None = None) -> None:
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
    is_obj = (
        bool(getattr(m, "objective", False))
        or bool(str(getattr(m, "correct_option", "") or "").strip())
        or bool(str(getattr(m, "chosen_option", "") or "").strip())
    )
    safe_layout = _SAFE_LAYOUT.get()

    # Collect criteria for step-by-step marking
    criteria = list(getattr(m, "criteria", []) or [])
    # Filled in by the step loop with the marks it ACTUALLY put on the page, which is
    # what the strip is then allowed to print. See the note beside it for why the two
    # cannot be computed separately.
    drawn_steps: list[float] = []
    # Set wherever a tick or cross actually lands on the student's answer. The strip
    # draws its own tick only when this stays False — a teacher ticks an answer once,
    # and a released copy carried eight ticks down the page against eight more in the
    # margin, one pair per question.
    marked_on_sheet = False

    # A band runs up to the NEXT question's anchor, so the next question's first
    # line can bleed in (e.g. "Q6) Evaluate ... = 8..."). Keep THIS question's
    # own first line, but truncate at the next question's label so neither the
    # numerical test nor step placement is contaminated by a neighbour.
    # A line this question ANCHORED to is its own, even when it reads like a
    # question label: long CA answers carry "(a)", "(ii)", "Note 3" headings all
    # the way down, and truncating there threw away the rest of the answer.
    anchored_lids = {(getattr(c, "step_line_id", "") or "").strip() for c in criteria}
    anchored_lids.discard("")
    own_lines: list[dict] = []
    for i, pl in enumerate(my_lines):
        t = (pl.get("text") or "").strip().lower()
        if (i > 0 and pl.get("lid") not in anchored_lids
                and (_QHEADER_RE.match(t) or _QLABEL_LEAD_RE.match(t)
                     or _QLABEL_LEAD_HI_RE.match(t))):
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
    # An ENUMERATED answer is marked point by point too, even with no arithmetic in
    # it: "state five significances of a mission statement", "list three differences"
    # - each point is its own line and earns its own tick, which is how a teacher
    # marks one. The grader is told to set mark_style "per_step" for these, but when
    # it leaves the call to us a theory list has no maths symbol to look numerical
    # by, so the whole answer used to collect a single tick.
    # Three markers, not two: an "(a) ... (b)" aside inside a paragraph is prose.
    enumerated = sum(1 for pl in my_lines
                     if _ENUM_LINE_RE.match(str(pl.get("text") or "")))
    looks_enumerated = enumerated >= 3 and enumerated >= len(criteria) / 2.0
    real = [pl for pl in my_lines if not _is_header(pl.get("text") or "")]
    anchor_lines = real or my_lines

    # "Unattempted" = scored 0 with no student writing in the band, or the verdict says so.
    # Objective items carry an authoritative `attempted` flag from the visual reader.
    # Do not infer that an attempted-but-wrong MCQ was blank merely because OCR failed to
    # produce an anchor line.  That exact combination made Business Studies Q5 disappear
    # from the marks strip even though the report correctly scored it 0/1.
    _remark_l = (remark or "").lower()
    if is_obj:
        unattempted = aw <= 1e-6 and not bool(getattr(m, "attempted", True))
    else:
        unattempted = aw <= 1e-6 and (
            not real
            or any(k in _remark_l for k in
                   ("unattempt", "not attempt", "not graded", "no answer", "left blank", "blank answer"))
        )

    mark_style = str(getattr(m, "mark_style", "auto") or "auto").strip().lower()
    if mark_style == "per_step":
        base_step = len(criteria) > 1            # numerical OR enumerated points
    elif mark_style in ("single", "whole"):
        base_step = False                        # flowing prose — one tick + total
    else:                                        # "auto": infer from the content
        base_step = (len(criteria) > 1
                     and (looks_numerical or looks_enumerated))
    # Per-step ticks are only for BIG answers (>= 3 marks). A 1- or 2-mark question
    # gets a single tick + total — teachers don't break 2 marks into "1+1" on the page.
    MIN_MARKS_FOR_STEP = 3
    # Need somewhere to place the per-step marks; with no anchor lines fall back
    # to the single-mark branch (which handles the empty band via y_fraction).
    use_step_marks = (
        not safe_layout and not is_obj and base_step and bool(anchor_lines)
        and mx >= MIN_MARKS_FOR_STEP
    )

    if not anchor_lines:
        page_num = max(1, min(int(getattr(m, "page", 1) or 1), len(student_doc)))
        x_end = 200.0
        page = student_doc[page_num - 1]
    else:
        page_num = anchor_lines[0]["page"]
        page = student_doc[page_num - 1]

    rect = page.rect
    cw = _content_width(page)
    seed = f"{qid}|mark"

    def _owned_y_bounds(page_number: int, page_height: float) -> tuple[float, float]:
        """Vertical extent of this question's invisible answer box on one page."""
        low, high = 24.0, page_height - 20.0
        if region_start and page_number == region_start[0]:
            low = max(low, region_start[1] - 4.0)
        if region_end and page_number == region_end[0]:
            high = min(high, region_end[1] - 4.0)
        return low, max(low, high)

    # For step-marked problems (numerical work OR enumerated points / labelled
    # diagrams): tick EACH step/point on the sheet where the student earned it —
    # with the marks for that step circled right after — exactly like a teacher
    # going down a solution. A question's band can span several pages, so every
    # step is resolved and drawn page-by-page.
    if use_step_marks:
        real_band = sorted(
            (pl for pl in my_lines if not _is_header(pl.get("text") or "")),
            key=lambda r: (r["page"], r["y0"], r["x0"]),
        )
        # A "Total" / "Balance c/d" row is not working — it is the sum of the rows
        # above it. Keep those out of the pool the spread and push-down stages draw
        # from, so a tick lands on "Interest on Drawings" rather than on "Total".
        # real_band is kept as the fallback: a band that is ONLY totals still gets
        # marked instead of silently losing its ticks.
        working_band = [pl for pl in real_band
                        if not _is_total_row(pl.get("text") or "")
                        and not _is_cancelled_line(pl)]
        candidates = working_band or real_band or sorted(
            my_lines, key=lambda r: (r["page"], r["y0"], r["x0"]))

        # WHERE EACH STEP IS MARKED.
        #
        # This used to be three stages: honour the model's step_line_id; spread
        # whatever was left evenly down the band; then push any two steps that had
        # collided onto different lines. The middle stage was the problem. MEASURED
        # over 278 anchors on five archived copies, 23 named a line_id that is not in
        # this copy's OCR at all and 33 were reused inside one question — so a fifth
        # of all step marks reached the even spread, and were then positioned by
        # `(k + 0.5) * lines / n`. A tick placed by arithmetic still reads to the
        # student as "this line is right".
        #
        # `anchors.resolve_steps` replaces it. A step almost always NAMES what it is
        # about ("Sale proceeds 8000 x 14 = 1,12,000") and the student's line carries
        # it ("=112000"), so the line can be found from the step's own words. What it
        # cannot find, it leaves unplaced — and an unplaced step is drawn NOWHERE and
        # reported on the report for re-marking. See anchors.py.
        band_lids = {pl["lid"] for pl in my_lines}

        def _model_anchor_ok(lid: str, criterion) -> bool:
            pl = by_lid.get(lid)
            if pl is None or lid not in band_lids:
                return False
            text = pl.get("text") or ""
            if _is_header(text) or _is_cancelled_line(pl):
                return False
            # An anchor on the Total row is honoured only when the criterion is
            # genuinely about the total ("correct casting", "totals agree");
            # otherwise the step belongs on a working line and is re-homed.
            return not (_is_total_row(text) and not _is_total_criterion(criterion))

        resolutions = resolve_steps(criteria, candidates,
                                    model_anchor_ok=_model_anchor_ok)

        unplaced = [ci for ci, res in enumerate(resolutions) if not res.placed]
        if unplaced:
            _note_unanchored(qid, len(unplaced))

        assigned: dict[int, dict] = {}
        # Steps whose position is backed by evidence — the model named the line, or
        # the step's own wording found it. Only these are safe to mark when they
        # scored nothing: a zero drawn on a guessed line drifts away from the text it
        # is about, which is why zeros used to be dropped altogether.
        anchored_ci: set[int] = set()
        band_by_lid = {pl["lid"]: pl for pl in my_lines}
        for ci, res in enumerate(resolutions):
            if not res.placed:
                continue
            pl = band_by_lid.get(res.line_id)
            if pl is None:
                continue
            assigned[ci] = pl
            anchored_ci.add(ci)
        # Stage 3b: OCR returns a ledger / table / statement as ONE opaque region
        # ("P3D1"). Several steps resolved to it would merge into a single tick at its
        # middle, leaving a full-page investment account with one lonely "4". Fan
        # them down the region instead — one tick per row of working, beside the
        # amounts column, the way an examiner ticks down a ledger.
        by_region: dict[str, list[int]] = {}
        for ci, pl in assigned.items():
            if _DIAG_REGION_RE.match(str(pl.get("lid", ""))):
                by_region.setdefault(pl["lid"], []).append(ci)
        for rlid, cis in by_region.items():
            if len(cis) < 2:
                continue
            region = assigned[cis[0]]
            step_h = (region["y1"] - region["y0"]) / (len(cis) + 1)
            for k, ci in enumerate(sorted(cis)):
                yc = region["y0"] + step_h * (k + 1)
                assigned[ci] = {**region, "lid": f"{rlid}#{k}", "y0": yc - 8, "y1": yc + 8}

        # Group the assigned criteria BY their resolved line. Several steps that
        # land on the same line (e.g. a 5-mark answer written on one line, or two
        # steps the OCR merged) become ONE tick + the COMBINED marks for that
        # line — a teacher ticks a line once, not five times on top of itself.
        groups: dict[str, dict] = {}
        for ci in assigned:
            pl = assigned[ci]
            g = groups.get(pl["lid"])
            if g is None:
                g = {"pl": pl, "awarded": 0.0, "max": 0.0, "anchored": False}
                groups[pl["lid"]] = g
            g["awarded"] += float(getattr(criteria[ci], "awarded", 0) or 0)
            g["max"] += float(getattr(criteria[ci], "max", 0) or 0)
            g["anchored"] = g["anchored"] or ci in anchored_ci

        # Stage 4: draw one mark per line, on its OWN page, nudging down on the
        # rare occasion two lines' marks would still overlap vertically. Share the
        # cross-question registry so step marks don't collide with other questions'.
        drawn_by_page = placed_by_page
        ordered_groups = sorted(
            groups.values(), key=lambda g: (g["pl"]["page"], g["pl"]["y0"]))
        drew_positive = False
        # What the strip is allowed to advertise. A reviewer on the Business Laws copy
        # wrote "step marking is not proper" against Q1.b.i, which printed "1.5+1.5=3/4"
        # beside a page carrying two marks and one unmarked (wrong) provision paragraph.
        # `steps` was built from EVERY criterion up at the top of this function while
        # this loop separately skips zero steps it could not anchor, so the two could
        # disagree — the student counts the marks on the page and cannot reach the sum
        # in the margin. Collect what actually lands, and let the strip print that.
        # Pages this question actually put a mark on. Its band can run past the end
        # of the answer onto the NEXT question's page, and the total used to follow
        # the band, landing beside a different question's heading with nothing to say
        # whose score it was — page 11 of a real copy read "3) b) Ans  5.5/6" when
        # Q3.b was out of 4.
        marked_pages: set[int] = set()
        for gi, g in enumerate(ordered_groups):
            pl = g["pl"]
            awarded = g["awarded"]
            max_step = g["max"]
            # A step that scored nothing is still marked — but ONLY where the model
            # named its line. The margin prints the whole chain ("1+0+1+1=3/4"), so a
            # zero drawn nowhere leaves the student counting three ticks against four
            # terms, unable to find the step that failed. Zeros on a GUESSED line are
            # still dropped: those drift away from the text they are about (the Q17
            # problem), and a mark in the wrong place is worse than none.
            # A wholly-wrong question gets ONE cross after the loop instead.
            zero_step = awarded <= 1e-6
            if zero_step and not (g.get("anchored") and max_step > 0):
                continue

            pg = student_doc[pl["page"] - 1]
            cwp = _content_width(pg)
            sstep = f"{seed}|g{gi}"

            # Find a vertical slot >= 30pt from every mark already on this page,
            # preferring the step's own line. Search downward first; if the page
            # tail is full, search back upward — never clamp several marks onto
            # the same y (overprint) or off the page (draws nothing).
            floor_y, ceiling = _owned_y_bounds(pl["page"], pg.rect.height)
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
            tick_size = _tick_size_for_line(20, pl["y1"] - pl["y0"])
            circle_size = _ms(_STEP_MARK_SIZE)
            circle_r = circle_size * (1.9 if is_dec else 1.8) + 1.5
            cluster_w = tick_size * 1.5 + 6 + circle_r * 2

            # Place the tick just past where the writing ends, but never let the
            # cluster cross the right edge onto the marks strip: the right cap
            # wins over the left floor when the line itself starts far right.
            right_cap = cwp - 6 - cluster_w
            writing_end = _row_writing_end(by_lid, pl["page"], pl["y0"], pl["y1"],
                                           pl["x0"], pl["x1"], cwp)
            tick_x = min(writing_end + 12 + _wobble(sstep, 0, amp=5), right_cap)
            floor = pl["x0"] + 2
            tick_x = max(floor, tick_x) if floor <= right_cap else right_cap
            tick_x = max(2.0, tick_x)
            # ...and move clear of anything else written on this row, dropping into
            # the band below when the row is written edge to edge (a ledger line).
            # Where the NUMERAL will sit inside the cluster, which is what the
            # clearance probe has to test — the tick ahead of it is a thin glyph and
            # is not what buries a word.
            fully_earned = max_step > 0 and awarded >= max_step - 1e-6
            # A zero step needs the same room as a ticked one: its CROSS goes where
            # the tick would, to the left of the circled mark.
            mark_dx = (tick_size * 1.5 + circle_r + 6
                       if (fully_earned or zero_step) else circle_r)
            tick_x, tick_dy, shrink = _clear_x_for_mark(
                student_doc, by_lid, pl["page"], y_mark, tick_x, cluster_w, right_cap,
                own_lid=pl["lid"], probe_dx=mark_dx, probe_r=circle_r)
            y_mark = max(floor_y, min(y_mark + tick_dy, ceiling))
            # A tight spot gets a smaller mark, not a mark over the words.
            tick_size *= shrink
            circle_size *= shrink
            mark_dx *= shrink

            # A TICK MEANS CORRECT. Only draw one when the line earned everything it
            # was worth; a partially credited step gets its circled mark alone. The
            # gate used to be "awarded > 0", so a step scored 1 of 4 carried a full
            # teacher's tick — a reviewer flagged exactly that: "the wrong answer has
            # been marked with a tick, and marks awarded, though a remark is given".
            if fully_earned:
                _draw_tick(pg, tick_x, y_mark, size=tick_size, seed=sstep + "t")
                marked_on_sheet = True
            elif zero_step:
                # A point that earned NOTHING is wrong, and a teacher crosses it. It
                # used to get its circled "0" and nothing else, so on the page it read
                # exactly like a point that had simply been marked lightly. The
                # reviewer said so twice on one copy — "cross is not there for wrong
                # point", and "the student's wrong point should be crossed/marked
                # clearly... the evaluator should not leave all five attempted points
                # appearing equally accepted".
                _draw_cross(pg, tick_x, y_mark, size=tick_size * 0.85, seed=sstep + "x")
                marked_on_sheet = True
            mark_x = tick_x + mark_dx
            _draw_number_in_circle(pg, mark_x, y_mark, mark_text,
                                   size=circle_size, seed=sstep)
            marked_pages.add(pl["page"])
            drawn_steps.append(awarded)
            if not zero_step:
                drew_positive = True

        # Whole question earned nothing but WAS attempted → ONE clean cross on its
        # first answer line (a teacher crosses an attempted-but-wrong answer once).
        # The per-step loop above already skips zero steps, so this is the only zero
        # cross — never a column. Skip only when unattempted (no writing to cross —
        # the margin strip + remark convey that).
        #
        # `drew_positive` alone used to stand for "every step scored zero", because
        # every criterion was placed somewhere no matter what. Steps can now go
        # unplaced, so an 8/10 answer whose ticks all failed to resolve would have
        # been crossed out. The score itself is the thing being asserted here, so
        # test it directly.
        if not drew_positive and aw <= 1e-6 and anchor_lines and not unattempted:
            first = anchor_lines[0]
            fpg = student_doc[first["page"] - 1]
            fx = max(2.0, min(first["x1"] + 12, _content_width(fpg) - 40))
            fy = (first["y0"] + first["y1"]) / 2
            _draw_cross(fpg, fx, fy, size=_ms(24), seed=seed + "x0")

        # The question's TOTAL, written at the HEAD of the answer — where the human
        # examiner puts it. On the marked copy for this very paper the evaluator wrote
        # "10/10" beside "Answer - 2", "10/10" beside "Answer - 3", "4/5" beside
        # "Answer - 4(a)": always at the top, never at the foot.
        #
        # Writing it where the answer ENDED put it next to the following answer's
        # heading, because that is what sits there: Q2's total landed on page 3 above
        # "Answer - 3", Q5(b)'s on page 7 above "Answer - 1". It also split a question
        # across pages — Q4(b)'s total was written on page 6 while its strip entry
        # stayed on page 5, so page 6 showed a bare "Q4b 4.5/5" against an empty strip
        # — and it left page 1 with no mark at all, since every criterion the model
        # anchored for Q2 pointed at pages 2 and 3. At the head the total sits beside
        # its own strip entry, on the page the answer starts.
        # Written as "3/3", never circled, so it cannot be mistaken for a step mark.
        if anchor_lines and not unattempted:
            last = sorted(anchor_lines, key=lambda p: (p["page"], p["y0"]))[0]
            tpg = student_doc[last["page"] - 1]
            tcw = _content_width(tpg)
            # Label it. A bare "5.5/6" beside a heading reads as that heading's score;
            # "Q3.a 5.5/6" cannot be misread whatever else shares the line.
            total_txt = f"{qid} {_fmt_mark(aw)}/{_fmt_mark(mx)}".strip()
            size = _ms(15)
            width = fitz.get_text_length(total_txt, fontname="hebo", fontsize=size) + 6
            ty = (last["y0"] + last["y1"]) / 2
            placed_t = placed_by_page.setdefault(last["page"], [])
            # Bounded to this question's own last line. Keeping the total on the right
            # PAGE is not enough: the walk below only stopped at the foot of the page,
            # so Q5(b)'s total stepped 26pt at a time off the end of its answer and
            # came to rest inside the MCQ block underneath. It then sat in `placed`,
            # where it collided with the first MCQ and pushed every tick and cross in
            # that block one answer down — the option the student got RIGHT ended up
            # carrying a cross. An overlap on its own line is the lesser evil.
            line_h_t = max(14.0, last["y1"] - last["y0"])
            ty_limit = last["y1"] + line_h_t
            while any(abs(ty - py) < 26 for py in placed_t) and ty + 26 <= ty_limit:
                ty += 26
            placed_t.append(ty)
            tx = max(2.0, min(last["x1"] + 14, tcw - 6 - width))
            tx, t_dy, t_shrink = _clear_x_for_mark(
                student_doc, by_lid, last["page"], ty, tx, width, tcw - 6 - width,
                # This is a text label, not a small circled numeral.  Probe the
                # whole label width; probing only a 12pt disc at its centre let
                # the ends of labels such as ``Q16 5/6`` print through an answer
                # even though the centre happened to sit over blank paper.
                own_lid=last["lid"], probe_dx=width / 2,
                probe_r=max(size * 0.8, width * 0.52),
                lift_max=min(_MARK_LIFT_MAX, line_h_t * 0.5))
            ty += t_dy
            size *= t_shrink
            try:
                tpg.insert_text((tx, ty + size * 0.35), total_txt, fontsize=size,
                                color=RED, fontname="hebo")
            except Exception:
                pass
    else:
        # Single mark question: draw one tick (or cross).
        # OBJECTIVE (MCQ / assertion-reason / match): anchor to the question's OWN
        # FIRST answer line — a 1-line MCQ must be marked on its own line, never on
        # the band's last line (which can bleed onto the next answer or a section
        # header → the MCQ-drift bug). DESCRIPTIVE answers mark where the writing
        # ENDS (last line). Either way keep the mark on the first answer line's page:
        # an out-of-order band can bleed onto a LATER page holding another section.
        # Bound for the anti-overlap nudge: a line-anchored mark may move at most
        # ~one line-height from its anchor, so it can never cascade onto a different
        # answer. None == unbounded (the y_fraction fallback has no real line).
        y_lo = y_hi = None
        if anchor_lines:
            first_pg = anchor_lines[0]["page"]
            same_page = [l for l in anchor_lines if l["page"] == first_pg]
            band = same_page or anchor_lines
            anchor = band[0] if is_obj else band[-1]
            mark_page = student_doc[anchor["page"] - 1]
            mcw = _content_width(mark_page)
            y_mark = (anchor["y0"] + anchor["y1"]) / 2
            x_end = anchor["x1"]
            mark_page_num = anchor["page"]
            line_h = max(14.0, anchor["y1"] - anchor["y0"])
            y_lo, y_hi = y_mark - line_h, y_mark + line_h
        else:
            mark_page = page
            mcw = cw
            y_mark = float(getattr(m, "y_fraction", 0.1) or 0.1) * rect.height
            x_end = 200.0
            mark_page_num = page_num
            # No line was resolved, so there is no handwriting to size against:
            # 0 tells _tick_size_for_line to use the sheet's default size.
            line_h = 0.0

        # Stagger vertically against every mark already placed on this page (across
        # ALL questions) so dense pages — e.g. the MCQ block — never overlap. The
        # search is BOUNDED to the mark's own line window so an MCQ tick/cross stays
        # on its answer instead of being shoved onto the next question's line.
        placed = placed_by_page.setdefault(mark_page_num, [])
        floor_y, ceiling = _owned_y_bounds(mark_page_num, mark_page.rect.height)
        y_mark = max(floor_y, min(y_mark, ceiling))
        thr = 16 if is_obj else 26
        step = 16 if is_obj else 30

        def _coll(yy: float) -> bool:
            return any(abs(yy - py) < thr for py in placed)

        if not is_obj and _coll(y_mark):
            lo = floor_y if y_lo is None else max(floor_y, y_lo)
            hi = ceiling if y_hi is None else min(ceiling, y_hi)
            slot = None
            down = y_mark
            while down <= hi and _coll(down):
                down += step
            if down <= hi:
                slot = down
            else:
                up = y_mark
                while up >= lo and _coll(up):
                    up -= step
                if up >= lo:
                    slot = up
            # Nothing free within the window → keep the mark on its own line and
            # accept a small overlap, rather than drifting it onto another answer.
            if slot is not None:
                y_mark = slot
        if not is_obj:
            placed.append(y_mark)

        # Objective verdicts are rendered only in the QID-labelled margin lane.
        if not is_obj:
            tick_size = _tick_size_for_line(32, line_h,
                                            answer_lines=len(anchor_lines))
            cluster_w = tick_size * 1.6
            tick_x = max(2.0, min(x_end + 20, mcw - 6 - cluster_w))
            # Same clearance as a step mark. Without it a whole-question mark lands
            # in the amounts column of a ledger row exactly as the step marks did.
            if anchor_lines:
                tick_x, whole_dy, w_shrink = _clear_x_for_mark(
                    student_doc, by_lid, mark_page_num, y_mark, tick_x, cluster_w,
                    mcw - 6 - cluster_w, own_lid=anchor.get("lid", ""),
                    probe_dx=tick_size * 0.75, probe_r=tick_size * 0.85,
                    # The bound this branch already sets for itself: "a line-anchored
                    # mark may move at most ~one line-height from its anchor, so it
                    # can never cascade onto a different answer".
                    lift_max=min(_MARK_LIFT_MAX, line_h))
                y_mark += whole_dy
                tick_size *= w_shrink
            if safe_layout:
                pass
            elif aw <= 1e-6:  # Zero marks
                if not unattempted:   # attempted-but-wrong → cross; unattempted → nothing
                    _draw_cross(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
                    marked_on_sheet = True
            else:
                # Same rule for a whole descriptive answer: tick only for full marks,
                # otherwise the score alone. Half a mark with a tick beside it reads
                # to a student as "this was right".
                if mx > 0 and aw >= mx - 1e-6:
                    _draw_tick(mark_page, tick_x, y_mark, size=tick_size, seed=seed)
                    marked_on_sheet = True
                # The authoritative score is already printed in the margin. Do not
                # circle it over the student's final line, where it can obscure a
                # value or make a partial answer appear fully accepted.

            # A part-marked answer carries ONE score, so the element that earned
            # nothing is invisible unless it is struck on its own line. The reviewer
            # asked for exactly this twice — "cross is not there for wrong point", and
            # "the student's wrong point should be crossed... not left appearing
            # equally accepted". The per-step path already crosses its zeros; a
            # question scored as a single mark did not, so on Q1(a)(i) the invented
            # provision was reduced to nothing and still looked accepted on the page.
            for zc in (() if safe_layout else (getattr(m, "criteria", None) or [])):
                if float(getattr(zc, "awarded", 0) or 0) > 1e-6:
                    continue
                zl = by_lid.get((getattr(zc, "step_line_id", "") or "").strip())
                if not zl or zl["page"] != mark_page_num:
                    continue
                zpg = student_doc[zl["page"] - 1]
                zw = _ms(20)
                zx = max(2.0, min(zl["x1"] + 12, _content_width(zpg) - 8 - zw))
                zy = (zl["y0"] + zl["y1"]) / 2
                zx, zdy, zsh = _clear_x_for_mark(
                    student_doc, by_lid, zl["page"], zy, zx, zw * 1.6,
                    _content_width(zpg) - 6 - zw * 1.6, own_lid=zl["lid"],
                    probe_dx=zw * 0.75, probe_r=zw * 0.85, probe_dy=-zw * 0.55,
                    lift_max=0.0)
                _draw_cross(zpg, zx, zy + zdy, size=zw * zsh, seed=seed + "z" + zl["lid"])

    # Show calculation/remark in the strip. Only show the per-step breakdown
    # ("1+1=2/2") when this question was actually marked step-by-step on the
    # sheet — otherwise (descriptive / empty band) show the plain score so the
    # strip never advertises steps the sheet doesn't show.
    if unattempted:
        # Nothing of this question exists on the sheet, so there is no right page for
        # a margin note — it would land on page 1 beside some other answer ("Q5.a 0/5
        # Unattempted" next to Answer-2). The cover's "Where marks were lost" lists it.
        return
    y_strip = (anchor_lines[0]["y0"] + anchor_lines[0]["y1"]) / 2 if anchor_lines else rect.height * 0.5
    # Print the marks the sheet actually carries, and only when they reconcile to the
    # score the strip is claiming. A breakdown the student cannot follow on the page is
    # worse than no breakdown: they count the ticks, come up short, and have no way to
    # tell whether the missing mark was lost or forgotten.
    shown = [s for s in drawn_steps]
    if not (shown and abs(sum(shown) - aw) <= 0.001):
        shown = []
    # The strip's tick is a FALLBACK, not a second opinion. It exists for the answers
    # the sheet cannot carry a mark for — objective items, which are marked only in
    # this lane so a tick cannot drift onto a neighbouring option, and margin-only
    # release mode. When the sheet already shows the verdict, the strip prints the
    # score and the reason and leaves the ticking to the page.
    verdict = ""
    if not marked_on_sheet:
        if mx > 0 and aw >= mx - 1e-6:
            verdict = "correct"
        elif aw <= 1e-6:
            verdict = "wrong"
    steps_shown = shown if (use_step_marks and len(shown) > 1) else None
    if is_obj and objective_queue is not None:
        # An MCQ verdict is a LIST, and a list has an order. Placing each one beside
        # its own anchor put them wherever the answers happened to fall: this paper
        # writes Ans 1-4 down the left of the page and Ans 5-8 down the right, so the
        # pairs share a row, collide in the strip, and come out "Q8, Q6, Q4, Q2, Q5,
        # Q7" with two of them pushed onto the next page. Hand them to the caller and
        # let it lay them out in question order once every anchor is known.
        objective_queue.append({
            "page": page, "qid": qid, "awarded": aw, "max": mx, "remark": remark,
            "y": y_strip, "steps": steps_shown, "verdict": verdict,
        })
        return
    _draw_strip_entry(page, qid, aw, mx, remark, y_strip,
                      steps=steps_shown, verdict=verdict)
    return


# "Answer-2", "Ans 3", "Answer No. 4" — the label a student writes above each answer.
# group(1) is the number; the caller also checks the line is mostly just this label.
_ANSWER_HEADING_RE = re.compile(
    r"^(?:answer|ans|उत्तर)\s*(?:no\.?)?\s*[-–—:.)#]*\s*(\d{1,2})\b",
    re.IGNORECASE)


def _heading_num(text: str) -> int | None:
    """The answer number when `text` is an 'Answer-N' heading, else None.

    Headings are short labels — the line must be mostly the label itself, so
    prose like "answer 4 marks were given because..." never registers. Mathpix
    sometimes returns a handwritten heading wrapped in a LaTeX array
    ('\\[\\begin{array}{l}\\text { Answer-2 } ...'), so the first \\text{} chunk
    is tested too."""
    candidates = [text.strip()]
    chunks = re.findall(r"\\text\s*\{([^{}]*)\}", text)
    if chunks:
        candidates.append(chunks[0].strip())
    for t in candidates:
        m = _ANSWER_HEADING_RE.match(t)
        if m and len(t) <= 28 and len(t[m.end():].strip()) <= 10:
            return int(m.group(1))
    return None


def _answer_headings(lines_by_page: dict[int, list[dict]]) -> list[dict]:
    """Every 'Answer-N' heading the student wrote, in document order."""
    out: list[dict] = []
    for pn in sorted(lines_by_page):
        for pl in lines_by_page[pn]:
            num = _heading_num(pl.get("text") or "")
            if num is not None:
                out.append({"page": pn, "y0": pl["y0"], "y1": pl["y1"],
                            "num": num, "lid": pl["lid"]})
    return out


def _snap_to_answer_headings(student_doc: fitz.Document, questions: list,
                             lines_by_page: dict[int, list[dict]],
                             by_lid: dict[str, dict]) -> None:
    """Validate every question's anchor against the student's own 'Answer-N'
    headings and snap the ones that drifted.

    The model occasionally anchors a question inside the PREVIOUS answer's
    working (e.g. Q4's mark landing in Q3's cash-book totals). The handwritten
    heading is ground truth for where answer N starts, so: if a question's
    current anchor falls outside every region belonging to its own number, move
    it to just below that number's last heading (last, because students strike
    out a false start and rewrite the heading further on)."""
    headings = _answer_headings(lines_by_page)
    if not headings:
        return

    def region_end(i: int) -> tuple[int, float]:
        if i + 1 < len(headings):
            nxt = headings[i + 1]
            return (nxt["page"], nxt["y0"])
        last = len(student_doc)
        return (last, student_doc[last - 1].rect.height + 1000.0)

    def region_content(i: int) -> int:
        """Real (non-heading, non-header) lines inside heading i's region. A
        struck-out false start ('Answer-6 / Hari', rewritten on the next page)
        has almost none — the rewrite's region holds the actual answer."""
        start = (headings[i]["page"], headings[i]["y1"])
        end = region_end(i)
        n = 0
        for pn in range(headings[i]["page"], min(end[0], len(student_doc)) + 1):
            for pl in lines_by_page.get(pn, []):
                mid = (pn, (pl["y0"] + pl["y1"]) / 2)
                if start < mid < end and not _is_header(pl.get("text") or ""):
                    n += 1
        return n

    by_num: dict[int, list[int]] = {}
    for i, h in enumerate(headings):
        by_num.setdefault(h["num"], []).append(i)

    for q in questions:
        m = re.match(r"\s*[Qq]?\s*(\d{1,2})", str(getattr(q, "qid", "") or ""))
        if not m:
            continue
        idxs = by_num.get(int(m.group(1)))
        if not idxs:
            continue                      # student never labelled this answer
        best = max(idxs, key=region_content)
        # Where does the model currently place this question?
        pl = by_lid.get(getattr(q, "anchor_line_id", "") or "")
        if pl:
            pos = (pl["page"], (pl["y0"] + pl["y1"]) / 2)
        else:
            pn = max(1, min(int(getattr(q, "page", 1) or 1), len(student_doc)))
            pos = (pn, float(getattr(q, "y_fraction", 0.5) or 0.5)
                   * student_doc[pn - 1].rect.height)
        h = headings[best]
        if (h["page"], h["y0"] - 6) <= pos < region_end(best):
            continue                      # anchor already inside the real answer
        page_h = student_doc[h["page"] - 1].rect.height
        q.page = h["page"]
        q.anchor_line_id = ""             # re-resolve from the heading downward
        q.y_fraction = min(0.995, (h["y1"] + 2.0) / page_h)


def _objective_lane_key(qid: str) -> tuple:
    """Paper order for an MCQ list: Q2 before Q10, Q5.a before Q5.b."""
    match = re.match(r"\s*[Qq]?\s*(\d+)", qid or "")
    suffix = "".join(ch for ch in (qid or "") if ch.isalpha()).lower().lstrip("q")
    return (int(match.group(1)) if match else 9999, suffix)


def _draw_objective_lane(student_doc: fitz.Document, queue: list[dict]) -> None:
    """Draw every objective verdict as one ordered list per page.

    Each entry keeps the page its own answer is on - an MCQ verdict must never move
    to another sheet - but WITHIN that page they are laid out in question order from
    the first entry's position downwards, so the column reads Q1, Q2, Q3... instead
    of the order their anchors happened to resolve in.
    """
    if not queue:
        return
    by_page: dict[int, list[dict]] = {}
    for entry in queue:
        by_page.setdefault(entry["page"].number, []).append(entry)

    # An MCQ block is answered in one place, so a verdict that resolved somewhere else
    # on its own has drifted. On this paper the student headed answers "Ans 1) b)" and
    # "Ans 3 (a)", and the objective Q1 and Q3 anchored to THOSE - landing their
    # verdicts on pages 2 and 3 while the other six sat on page 1 beside the actual
    # MCQ answers. A page carrying a single stray verdict hands it back to the page
    # holding the block; a genuine split (four and four, say) keeps both.
    if len(by_page) > 1:
        home = max(by_page, key=lambda pn: (len(by_page[pn]), -pn))
        for page_number in [pn for pn in by_page if pn != home]:
            if len(by_page[page_number]) >= 2:
                continue
            home_page = by_page[home][0]["page"]
            for entry in by_page.pop(page_number):
                entry["page"] = home_page
                entry["y"] = max(e["y"] for e in by_page[home])
                by_page[home].append(entry)
    for entries in by_page.values():
        entries.sort(key=lambda e: _objective_lane_key(e["qid"]))
        # Start the list where its earliest answer sits, then let the strip's own slot
        # reservation stack the rest under it in the order handed over.
        cursor = min(e["y"] for e in entries)
        for entry in entries:
            bottom = _draw_strip_entry(
                entry["page"], entry["qid"], entry["awarded"], entry["max"],
                entry["remark"], cursor, steps=entry["steps"],
                verdict=entry["verdict"])
            if bottom is not None:
                cursor = bottom + 4.0


def _annotate_questions(student_doc: fitz.Document, questions: list,
                        line_index: dict) -> None:
    """Per-question annotator. Each question's band spans from its anchor to
    the next question's anchor (possibly crossing page breaks), and its
    criteria can be placed on any page in that band."""
    if not questions:
        return

    objective_queue: list[dict] = []

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

    # The student's own "Answer-N" headings are ground truth for where each
    # answer lives — snap any model anchor that drifted into another answer.
    _snap_to_answer_headings(student_doc, questions, lines_by_page, by_lid)

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

    def own_anchor_span(q) -> tuple[int, float] | None:
        """Furthest (page, y) this question's OWN criteria explicitly anchor to.

        None means the model located nothing of this question on the sheet — which
        is what an unattempted question looks like."""
        best = None
        for c in (getattr(q, "criteria", None) or []):
            pl = by_lid.get((getattr(c, "step_line_id", "") or "").strip())
            if pl is None:
                continue
            pos = (pl["page"], (pl["y0"] + pl["y1"]) / 2)
            if best is None or pos > best:
                best = pos
        return best

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

        # A long answer routinely runs PAST the point where the next question was
        # anchored — most often because that next question was never attempted and
        # still got a nominal position on this page. Left alone, the band stops
        # there, every criterion anchored beyond it is rejected as out-of-band, and
        # Stage 2 drags those ticks back onto the first page: a 100-mark CA answer
        # spanning pages 2-4 got all 23 ticks on page 2 and left 3-4 unmarked.
        # Extend the band to the furthest line THIS question actually anchored,
        # stepping over following questions that located nothing of their own.
        span = own_anchor_span(q)
        if span is not None and qi + 1 < len(qs_sorted) and span > (end_page, end_y):
            j = qi + 1
            while (j < len(qs_sorted) and own_anchor_span(qs_sorted[j]) is None
                   and q_anchor(qs_sorted[j]) <= span):
                j += 1
            limit = q_anchor(qs_sorted[j]) if j < len(qs_sorted) else None
            if limit is None or limit > span:
                end_page, end_y = span[0], span[1] + 8
            else:
                end_page, end_y = limit

        my_lines: list[dict] = []
        for pn in range(start_page, end_page + 1):
            for pl in lines_by_page.get(pn, []):
                ymid = (pl["y0"] + pl["y1"]) / 2
                if pn == start_page and ymid < start_y - 4:
                    continue
                if pn == end_page and qi + 1 < len(qs_sorted) and ymid >= end_y - 4:
                    continue
                my_lines.append(pl)

        _render_question_marks(
            student_doc, q, my_lines, by_lid, placed_by_page,
            region_start=(start_page, start_y),
            region_end=(end_page, end_y),
            objective_queue=objective_queue,
        )

    _draw_objective_lane(student_doc, objective_queue)


# A strikethrough cancels a WRONG WORD OR PHRASE. Nothing a student writes justifies a
# red line across half the page, and a reviewer reading one assumes the sentence under it
# was wrong. Real copies came back with a strike spanning the full writing width,
# cancelling a correct statement of law, because `width_fraction` arrived as 1.0.
_STRIKE_MAX_FRACTION = 0.45


def _draw_inline_annotations(page: fitz.Page, annotations: list,
                             line_index: dict | None = None,
                             earned_line_ids: set | None = None) -> None:
    """Mark wrong words ON the student's writing: ✗ for objective slips, and a
    strikethrough through the offending word/phrase. `circle` annotations are
    rendered as strikethroughs too (a circle over busy handwriting reads as
    clutter; a line through the wrong word is clearer).

    A strike is only drawn where it can be placed on the actual wrong words. If the
    target word is not on the target line, or the line earned its marks, the
    annotation is dropped: a misplaced strike cancels work the student got right,
    which is worse than no annotation at all.
    """
    rect = page.rect
    cw = _content_width(page)  # writing-area width (exclude the marks strip)
    line_index = line_index or {}
    earned_line_ids = earned_line_ids or set()
    for a in annotations:
        # A strike CANCELS; an underline says "look at this". The model asks for a
        # `circle` when it wants the student to NOTICE a content error — a wrong
        # figure, a wrong date, a mislabelled item — work the student must still
        # read. Rendering that as a strike told them it had been deleted, and on a
        # real copy it crossed out a correct statement of law. Circles underline now.
        is_strike = a.type == "strikethrough"
        is_underline = a.type in ("circle", "underline")
        spans_text = is_strike or is_underline
        # Never cancel a line that scored its marks — the sheet would contradict
        # itself. An underline is a pointer, not a verdict, so it is still allowed.
        if is_strike and a.target_line_id and a.target_line_id in earned_line_ids:
            continue
        x = y = None
        width = None
        y_under = None          # where an underline sits, if we know the line box
        if a.target_line_id and a.target_line_id in line_index:
            _, bbox_px, img_w, img_h, line_text = line_index[a.target_line_id]
            x0, y0, x1, y1 = _pdf_coords_from_line(page, bbox_px, img_w, img_h)
            y = (y0 + y1) / 2
            y_under = y1 - 2.0
            if a.target_word and line_text.count(a.target_word) > 1:
                # The word occurs more than once on this line and nothing says which
                # one is meant. _word_x_in_line takes the FIRST, and the first is
                # usually the CORRECT use — "Purchases 3,21,700 add omitted
                # Purchases 400" would be marked on the legitimate entry, up to
                # 278pt from the error. Say nothing rather than accuse the right one.
                continue
            if a.target_word and a.target_word in line_text:
                x = _word_x_in_line(line_text, a.target_word, x0, x1)
                # sized to the target word
                if spans_text:
                    word_frac = len(a.target_word) / max(1, len(line_text))
                    width = max(15.0, word_frac * (x1 - x0))
                    # re-center start at left edge of word
                    x = x - width / 2
            elif a.target_word:
                # Named a word that is not on this line, so we cannot say WHICH words
                # are wrong. Marking the whole line instead is how correct sentences
                # end up cancelled.
                continue
            else:
                x = (x0 + x1) / 2
                if spans_text:
                    width = x1 - x0
                    x = x0
        else:
            x = a.x_fraction * cw
            y = a.y_fraction * rect.height
            width = max(20.0, a.width_fraction * cw) if spans_text else None

        if spans_text and width is not None:
            width = min(width, _STRIKE_MAX_FRACTION * cw)

        seed = f"a{a.page}{a.target_word or ''}{x:.1f}{y:.1f}"
        if a.type == "cross":
            size = 11 + _wobble(seed, 0, amp=2.5)
            _draw_cross(page, x, y, size=size, seed=seed)
        elif is_underline:
            _draw_underline(page, x,
                            y_under if y_under is not None else y + _UNDERLINE_DROP,
                            width or 40.0)
        else:
            _draw_strikethrough(page, x, y, width or 40.0)


# ---------- Report strip across the TOP of page 1 ----------
# Replaces the old standalone cover + summary pages. A wide LANDSCAPE band sits on top of
# the first answer-sheet page (same width as the sheet), so the page just gets taller. The
# report content is laid out in three columns to fit the short band: Remarks | Question-wise
# Totals | Where marks were lost, under an EVALUATED banner + total score.
_RS_PAD = 20.0          # outer padding
_RS_GUT = 26.0          # gutter between columns
_RS_BANNER_H = 40.0
_RS_TOTAL_SIZE = 19.0
_RS_HEAD_SIZE = 13.5
_RS_BODY_SIZE = 11.5
_RS_LINE = 16.5         # generous per-line height for measuring (>= helpers' real line height)


def _rs_columns(x0: float, W: float) -> tuple[float, list[float]]:
    """Return (column width, [x of each of the 3 columns]) for a strip starting at x0."""
    col_w = (W - 2 * _RS_PAD - 2 * _RS_GUT) / 3
    xs = [x0 + _RS_PAD + i * (col_w + _RS_GUT) for i in range(3)]
    return col_w, xs


def _rs_wrap_count(text: str, max_w: float, size: float, fontname: str = "helv") -> int:
    words = (text or "").split()
    if not words:
        return 0
    line, n = "", 0
    for w in words:
        trial = (line + " " + w).strip()
        if fitz.get_text_length(trial, fontname=fontname, fontsize=size) > max_w:
            n += 1
            line = w
        else:
            line = trial
    return n + (1 if line else 0)


def _rs_bullet_lines(text: str, max_w: float, size: float) -> int:
    if not text or not text.strip():
        return 0
    text = _personalize(text)
    total = 0
    for s in _split_remark_sentences(text):
        if not s.endswith(('.', '?', '!', ']')):
            s += '.'
        total += max(1, _rs_wrap_count(s, max_w - 20, size))
    return total


def _report_strip_lost(report: GradeReport) -> list:
    return [q for q in report.questions
            if q.score < q.max_score and (getattr(q, "remark", "") or "").strip()]


def _report_strip_height(report: GradeReport, W: float) -> float:
    """Height the top report strip needs for this report at width W (tallest of 3 columns)."""
    col_w, _ = _rs_columns(0, W)
    body_start = _RS_PAD + _RS_BANNER_H + 8 + _RS_TOTAL_SIZE + 14
    c1 = 1 + _rs_bullet_lines(report.overall_remarks, col_w, _RS_BODY_SIZE)
    c2 = 1 + len(report.section_totals)
    lost = _report_strip_lost(report)
    c3 = (1 + sum(_rs_wrap_count(f"{q.qid}: {q.remark}", col_w, _RS_BODY_SIZE) for q in lost)) if lost else 0
    body_lines = max(c1, c2, c3, 1)
    return body_start + body_lines * _RS_LINE + _RS_PAD


def _draw_report_strip(page: fitz.Page, x0: float, y0: float, W: float, report: GradeReport) -> None:
    """Draw the whole report into a landscape band at the top of page 1: EVALUATED banner +
    total score across the top, then three columns (Remarks | Question-wise Totals | Where
    marks were lost)."""
    col_w, xs = _rs_columns(x0, W)

    page.draw_rect(fitz.Rect(x0 + _RS_PAD, y0 + _RS_PAD, x0 + W - _RS_PAD, y0 + _RS_PAD + _RS_BANNER_H),
                   color=RED, width=1.5)
    tw = fitz.get_text_length("EVALUATED", fontname="hebo", fontsize=24)
    page.insert_text((x0 + (W - tw) / 2, y0 + _RS_PAD + _RS_BANNER_H * 0.68), "EVALUATED",
                     fontsize=24, color=RED, fontname="hebo")
    ty = y0 + _RS_PAD + _RS_BANNER_H + 8 + _RS_TOTAL_SIZE
    # No teacher writes "16.0 out of 20.0". The trailing .0 is a float artefact and
    # it is the first thing a reader notices about a machine-marked sheet.
    page.insert_text((x0 + _RS_PAD, ty),
                     f"Total Score: {_fmt_mark(report.total_score)} / "
                     f"{_fmt_mark(report.max_total)}",
                     fontsize=_RS_TOTAL_SIZE, color=RED, fontname="hebo")
    by = ty + 14   # body (columns) start y

    # Column 1 — Remarks
    page.insert_text((xs[0], by), "Remarks:", fontsize=_RS_HEAD_SIZE, color=BLACK, fontname="hebo")
    _draw_bulleted_remarks(page, xs[0], by + _RS_LINE,
                           _sanitize_for_pdf(report.overall_remarks),
                           size=_RS_BODY_SIZE, color=BLACK, max_width=col_w)

    # Column 2 — Question-wise Totals
    page.insert_text((xs[1], by), "Question-wise Totals:", fontsize=_RS_HEAD_SIZE, color=BLACK, fontname="hebo")
    yy = by + _RS_LINE
    # Name the sections when the paper has both. Otherwise "Q1: 2/2" (an MCQ) sits
    # directly above "Q1a: 1/5" (a different question in another part) with nothing
    # to say they are not the same question's parts.
    kinds = {bool(sec.get("objective")) for sec in report.section_totals}
    label_sections = len(kinds) > 1
    shown_kind = None
    for sec in report.section_totals:
        kind = bool(sec.get("objective"))
        if label_sections and kind != shown_kind:
            page.insert_text((xs[1], yy),
                             "Objective" if kind else "Descriptive",
                             fontsize=_RS_HEAD_SIZE - 1, color=(0.42, 0.42, 0.42),
                             fontname="hebo")
            yy += _RS_LINE
            shown_kind = kind
        page.insert_text((xs[1], yy),
                         f"{sec.get('qid', '?')}: {_fmt_mark(sec.get('score', 0))} / "
                         f"{_fmt_mark(sec.get('max_score', 0))}",
                         fontsize=_RS_BODY_SIZE, color=RED, fontname="hebo")
        yy += _RS_LINE

    # Column 3 — Where marks were lost
    lost = _report_strip_lost(report)
    if lost:
        page.insert_text((xs[2], by), "Where marks were lost:", fontsize=_RS_HEAD_SIZE, color=RED, fontname="hebo")
        yy = by + _RS_LINE
        for q in lost:
            yy = _draw_text(page, xs[2], yy,
                            _sanitize_for_pdf(f"{q.qid}: {q.remark}"),
                            size=_RS_BODY_SIZE,
                            color=RED, fontname="helv", max_width=col_w)


def build_evaluated_pdf(student_pdf_bytes: bytes, student_filename: str,
                        student_pngs: list[bytes], report: GradeReport,
                        ocr_pages: list[PageOCR] | None = None,
                        mark_scale: float = 1.0,
                        safe_layout: bool = False) -> bytes:
    """Build the evaluated PDF and return its bytes.

    If the student submission was a PDF, we annotate the original pages directly.
    Otherwise we build PDF pages from the student PNGs.

    mark_scale shrinks (or grows) the inline ticks, crosses and circled marks —
    CA/commerce sheets are dense and small-handwritten, so they need smaller marks
    than a school sheet for the working to stay readable underneath.
    """
    token = _MARK_SCALE.set(max(0.3, min(2.0, float(mark_scale or 1.0))))
    layout_token = _SAFE_LAYOUT.set(bool(safe_layout))
    try:
        # The renderer keeps per-document registries in module-level dicts (content
        # widths, strip cursors, ink/remark bands) and clears one of them wholesale at
        # the start of a build. Two concurrent builds — two server jobs, or a parallel
        # evaluation run — would corrupt each other's geometry, so builds are serialized.
        # Rendering is cheap next to the model calls, so this costs nothing noticeable.
        with _RENDER_LOCK:
            return _build_evaluated_pdf(student_pdf_bytes, student_filename,
                                        student_pngs, report, ocr_pages)
    finally:
        _SAFE_LAYOUT.reset(layout_token)
        _MARK_SCALE.reset(token)


def _build_evaluated_pdf(student_pdf_bytes: bytes, student_filename: str,
                         student_pngs: list[bytes], report: GradeReport,
                         ocr_pages: list[PageOCR] | None = None) -> bytes:
    # Recompute total AND max from per-question scores so the cover page never
    # disagrees with the question-wise breakdown. Also rebuild section_totals
    # from the same source of truth. (The model sometimes drops not-attempted
    # questions from max_total while still listing them at 0/max — recomputing
    # the denominator here keeps the percentage honest.)
    report.total_score = round(sum(q.score for q in report.questions), 2)
    # ...but NOT on a paper with internal choice. There the counted script leaves out
    # the alternatives the student did not take, so the drawn rows sum to less than
    # the paper: skipping one of the two required alternatives on the Corporate Laws
    # master test made a 50-mark paper come out "of 40", which is not a smaller paper,
    # it is ten marks the student lost. The denominator was bound to the official
    # total upstream by the strict reconcile; leave it alone.
    if not list(getattr(report, "choice_candidate_qids", None) or []):
        report.max_total = round(sum(q.max_score for q in report.questions), 2)
    # Rebuild section_totals keyed by the top-level question (Q1, Q2, ...)
    sec_acc: dict[str, dict] = {}
    for q in report.questions:
        top = q.qid.split(".")[0].split("(")[0].strip() or q.qid
        bucket = sec_acc.setdefault(top, {"qid": top, "score": 0.0, "max_score": 0.0,
                                          "objective": True})
        bucket["score"] += q.score
        bucket["max_score"] += q.max_score
        bucket["objective"] = bucket["objective"] and bool(
            getattr(q, "objective", False)
            or str(getattr(q, "correct_option", "") or "").strip()
            or str(getattr(q, "chosen_option", "") or "").strip())
    if sec_acc:
        # Paper order, objective section first. A CA paper numbers BOTH parts from 1 -
        # Part I is MCQ Q1..Q8 and Part II is Q1(a)..Q4(b) - so ordering on the number
        # alone left every sub-part tied with its MCQ namesake, and Python's stable
        # sort then kept the grader's own order. The cover read "Q1, Q1b, Q1a, Q1c,
        # Q2, Q3, Q3a": two different Q1s adjacent and the sub-parts shuffled.
        def sec_key(d):
            qid = d["qid"]
            digits = "".join(ch for ch in qid if ch.isdigit())
            suffix = "".join(ch for ch in qid if ch.isalpha()).lower().lstrip("q")
            return (0 if d.get("objective") else 1,
                    int(digits) if digits else 9999, suffix)
        report.section_totals = [
            {"qid": d["qid"],
             "score": round(d["score"], 2),
             "max_score": round(d["max_score"], 2),
             "objective": bool(d.get("objective"))}
            for d in sorted(sec_acc.values(), key=sec_key)
        ]

    out = fitz.open()

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
    _UNANCHORED.clear()
    # Cleared wholesale, not by id: the pages marks are placed on belong to the
    # `widened` document built just below, and a freed id() is handed straight back
    # out by CPython — so keying the reset on any one document let the NEXT sheet
    # inherit this one's ink masks and place its marks against the wrong page.
    # Safe because _RENDER_LOCK serialises builds, exactly as for _CONTENT_W.
    _MARK_INK.clear()
    widened = fitz.open()
    for pno in range(len(student_doc)):
        src = student_doc[pno]
        w, h = src.rect.width, src.rect.height
        strip = max(STRIP_MIN_WIDTH, w * STRIP_FRACTION)
        npage = widened.new_page(width=w + strip, height=h)
        npage.show_pdf_page(fitz.Rect(0, 0, w, h), student_doc, pno)
        # Divider line only — a printed "MARKS / REMARKS" header on every page
        # made the sheet look machine-annotated. Teachers just rule a margin.
        npage.draw_line(fitz.Point(w, 0), fitz.Point(w, h), color=RED, width=1.0)
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

    # Question marks first — this can draw across page boundaries, so it must
    # see the whole document, not one page at a time.
    _annotate_questions(student_doc, report.questions, line_index)
    # Steps that found no line on the student's writing were drawn nowhere. Hand
    # that back so the release path can re-mark the question instead of shipping a
    # score the page cannot account for.
    try:
        report.unanchored_step_qids = sorted(_UNANCHORED)
    except (AttributeError, ValueError):
        pass

    # Inline annotations (crosses, strikethroughs) stay per-page. Lines that earned
    # their step marks are off limits to a strike — see _draw_inline_annotations.
    earned_line_ids = {c.step_line_id for q in report.questions
                       for c in (getattr(q, "criteria", None) or [])
                       if getattr(c, "met", False) and getattr(c, "step_line_id", None)}
    if not _SAFE_LAYOUT.get():
        for i, page in enumerate(student_doc, start=1):
            anns = ann_by_page.get(i, [])
            if anns:
                _draw_inline_annotations(page, anns, line_index=line_index,
                                         earned_line_ids=earned_line_ids)

    # First page gets the report as a wide LANDSCAPE strip across the TOP (same width as the
    # sheet), replacing the old standalone cover + summary pages. The already-annotated first
    # page (with its right marks strip) is placed below the strip; the rest copy as-is.
    n = len(student_doc)
    if n == 0:
        _draw_report_strip(out.new_page(width=842, height=400), 0, 0, 842, report)
    else:
        sw = student_doc[0].rect.width
        sh = student_doc[0].rect.height
        strip_h = _report_strip_height(report, sw)
        p0 = out.new_page(width=sw, height=strip_h + sh)
        _draw_report_strip(p0, 0, 0, sw, report)
        p0.draw_line(fitz.Point(0, strip_h), fitz.Point(sw, strip_h), color=RED, width=1.0)
        p0.show_pdf_page(fitz.Rect(0, strip_h, sw, strip_h + sh), student_doc, 0)
        if n > 1:
            out.insert_pdf(student_doc, from_page=1, to_page=n - 1)
    student_doc.close()

    buf = BytesIO()
    out.save(buf, garbage=4, deflate=True)
    out.close()
    return buf.getvalue()
