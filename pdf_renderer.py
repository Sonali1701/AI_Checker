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
import pdf_safety

from anchors import resolve_steps
from exam_package import normalize_qid
from grader import GradeReport
from mathpix import PageOCR, is_scanner_watermark, word_target_bbox

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
_RENDER_REPORT = contextvars.ContextVar("render_report", default=None)


def _label(qid, report=None) -> str:
    """The question as the student's paper prints it (wording.question_labels)."""
    from wording import question_label, question_labels
    report = report if report is not None else _RENDER_REPORT.get()
    if report is not None:
        try:
            return question_labels(report).get(str(qid), question_label(qid))
        except Exception:  # noqa: BLE001 - a label is never worth a copy
            pass
    return question_label(qid)


def _part_total_head(report, qid) -> str:
    """"Q4(A)  2.25/2.5" above the first sub-part of a split part (wording.part_totals)."""
    if report is None:
        return ""
    try:
        from wording import report_part_totals
        return report_part_totals(report).get(str(qid), "")
    except Exception:  # noqa: BLE001 - a part total is never worth a copy
        return ""


def _shown_total(report) -> float:
    """The total the student reads: whole marks on a CA paper (ICAI practice)."""
    from wording import whole_mark_total
    total = float(getattr(report, "total_score", 0) or 0)
    return whole_mark_total(total) if getattr(report, "whole_total", False) else total
_OCR_WORDS = contextvars.ContextVar("render_ocr_words", default=None)
# Pages of writing the OCR read nothing from (no lines, not marked blank), by number.
_UNREAD_PAGES = contextvars.ContextVar("render_unread_pages", default=None)
_STRIP_CANVAS = contextvars.ContextVar("render_strip_canvas", default=None)


def _record_draw(qid, role, page, bbox, *, line=None, criteria=(), score=None, numeral_bbox=None):
    report = _RENDER_REPORT.get()
    if report is None or not report.evidence_contract:
        return
    # A mark at the end of an unverified table, or one carrying steps that found no
    # line, is drawn so the page adds up, but it is no step's own row: the release
    # check must still see those rows as unmarked (_visual_step_line, orphan steps).
    covers = list(criteria)
    if line and line.get('_location_source') in {'block_end', 'unplaced_steps'}:
        criteria = ()
    report.render_manifest.append({
        "qid": qid, "role": role, "page": page, "bbox": list(bbox),
        "line_id": line["lid"] if line else "", "criteria": list(criteria),
        "score": score,
        "numeral_bbox": list(numeral_bbox) if numeral_bbox else None,
        "anchor_bbox": [line[k] for k in ("x0", "y0", "x1", "y1")] if line else None,
    })
    if covers and not criteria:
        report.render_manifest[-1]["covers_criteria"] = covers
    if line:
        item = report.render_manifest[-1]
        item['location_source'] = line.get('_location_source', 'ocr_line')
        original = (_OCR_WORDS.get() or {}).get(line["lid"])
        if original:
            ocr_page, ocr_line = original
            item["anchor_normalized"] = [ocr_line.bbox[0] / ocr_page.image_width,
                                         ocr_line.bbox[1] / ocr_page.image_height,
                                         ocr_line.bbox[2] / ocr_page.image_width,
                                         ocr_line.bbox[3] / ocr_page.image_height]
            source_w = ocr_line.bbox[2] - ocr_line.bbox[0]
            source_h = ocr_line.bbox[3] - ocr_line.bbox[1]
            if source_w > 0 and source_h > 0:
                width = (line["x1"] - line["x0"]) / source_w * ocr_page.image_width
                height = (line["y1"] - line["y0"]) / source_h * ocr_page.image_height
                item["mark_normalized"] = [bbox[0] / width, bbox[1] / height,
                                           bbox[2] / width, bbox[3] / height]


def _ms(size: float) -> float:
    """Scale an inline-mark dimension for the current sheet."""
    return size * _MARK_SCALE.get()


def _doc_mark_scale() -> float:
    """How much bigger this copy's marks must be than a nominal A4 page's.

    MEASURED 2026-09-16 on the ten copies sent for review: a phone photo comes in as
    a 1860x2000pt page whose handwriting is 85pt tall, four times an A4 copy's, but
    every mark size in here is an absolute number of points calibrated against A4.
    The ticks and circled marks on those copies came out a fifth of the size of the
    writing they marked - "the ticks and circled marks should be of correct size".

    Scales UP only: on an A4 copy this is 1.0 and nothing moves.
    """
    line_h = _DOC_LINE_H.get()
    if not line_h or line_h <= 0:
        return 1.0
    scale = max(1.0, min(_DOC_SCALE_MAX, line_h / _NOMINAL_LINE_H))
    pitch = _DOC_ROW_PITCH.get()
    if pitch and pitch > 0:
        # A mark grows with the handwriting but never past the gap between two
        # lines of it: on densely written evidence rows a larger mark simply lands
        # on the working (tests/test_on_sheet_scores, test_evidence_quality).
        scale = max(1.0, min(scale, pitch / _NOMINAL_LINE_H))
    return scale


# The median OCR line height of the copy being drawn: one handwriting size for one
# booklet, so every tick on it comes out of the same hand. Zero falls back to the
# per-line behaviour (callers outside build_evaluated_pdf, and unit tests).
_DOC_LINE_H: contextvars.ContextVar[float] = contextvars.ContextVar(
    "doc_line_h", default=0.0)
_DOC_ROW_PITCH: contextvars.ContextVar[float] = contextvars.ContextVar(
    "doc_row_pitch", default=0.0)
# A copy whose handwriting is four times an A4 copy's gets marks four times as big.
_DOC_SCALE_MAX = 4.5
_UNIFORM_MARK_SIZES = True    # see build_evaluated_pdf
# The teacher layout (teacher_layout.py) is what the reviewer approved on 2026-09-16:
# tick + circled mark on every answer, marks only to the right, margin entries
# 'Q7.c 1.5/3' with the reason, plain wording. False falls back to the legacy layout.
TEACHER_LAYOUT = True
# One base for every inline tick and cross, before this copy's handwriting scale.
_INLINE_TICK_BASE = 15.0


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


# A tick stands for the answer it marks, so it is never taller than that answer.
# _draw_tick runs 1.4 * size ABOVE its anchor, so on the three stacked one-line MCQs
# of a Class 10 Physics copy a full-size tick climbed into the option above it and the
# block read as one scribble. Only ever binds on SHORT answers: a six-line answer
# spans far more than its own tick.
_TICK_RISE = 1.4
_TICK_MIN_SIZE = 12.0


def _doc_tick_size() -> float:
    """The one tick for this copy: what a whole answer gets, and what a step and an
    MCQ get too, so every stroke on the sheet comes from the same hand.

    MEASURED 2026-09-16 on the SST review batch: the MCQ tick was sized from its
    line (19pt), the step tick was a flat 8pt, and the two sat a row apart on the
    same page - the reviewer's first words were "the ticks are sometimes big and
    sometimes small".
    """
    document = _DOC_LINE_H.get()
    if not document or document <= 0:
        return 12.0
    return _tick_capped_to_answer(_tick_size_for_line(32, document),
                                  [{"y0": 0.0, "y1": document}])


def _verdict_size_ladder() -> list[float]:
    """This copy's tick first, smaller on a crowded row, the old 8pt as the floor."""
    size = _doc_tick_size()
    ladder = [size, size * .8, size * .65, 8.0]
    out: list[float] = []
    for value in ladder:
        if all(abs(value - seen) > .5 for seen in out):
            out.append(value)
    return sorted(out, reverse=True)


def _verdict_lead() -> float:
    """Room to leave after the writing for the tick that precedes a circled step mark."""
    return _doc_tick_size() * 1.5 + 6.0


def _tick_capped_to_answer(size: float, anchor_lines: list) -> float:
    """Shrink a whole-answer tick that would out-reach the answer it belongs to.

    A ONE-LINE answer is capped by this student's usual line height rather than by
    the height OCR happened to return for that particular line. Both are "the size
    of one line", but only the first is the same on every row: per-line capping put
    ticks of 14.7, 18.9, 15.4 and 21.3 points down one MCQ block, and the reviewer
    said so - "the ticks are sometimes big and sometimes small".
    """
    if not anchor_lines:
        return size
    span = (max(l["y1"] for l in anchor_lines)
            - min(l["y0"] for l in anchor_lines))
    document = _DOC_LINE_H.get()
    if document and len(anchor_lines) <= 1:
        span = document
    if span <= 0:
        return size
    return min(size, max(_TICK_MIN_SIZE, span / _TICK_RISE))


# Every mark already drawn on the answer, per page, so the next one can be kept
# off it. Marks are the only thing here that must not collide with each other: they
# may sit on the student's writing (a teacher's do), but two marks on top of one
# another are unreadable and there is no way to tell which answer either scored.
_MARK_BOXES: dict[int, list["fitz.Rect"]] = {}


def _tick_path_box(x: float, y: float, size: float) -> "fitz.Rect":
    """The path _draw_tick follows: up and to the right of its anchor, no pen."""
    return fitz.Rect(x, y - _TICK_RISE * size, x + 1.5 * size, y + 0.2 * size)


def _tick_box(x: float, y: float, size: float) -> "fitz.Rect":
    """What _draw_tick covers: up and to the right of its anchor.

    The path, not the pen: the stroke is 0.2 x size wide at its heaviest, and a
    box that included it could never sit clear of a neighbouring MCQ column and
    within reach of its own row at once (tests/test_mcq_scores). The release
    check reads the strokes back allowing for that fringe (check_render_manifest).
    """
    return _tick_path_box(x, y, size)


def _cross_box(x: float, y: float, size: float) -> "fitz.Rect":
    """_draw_cross is centred on its anchor."""
    h = size / 2.0
    return fitz.Rect(x - h, y - h, x + h, y + h)


def _note_mark(page_no: int, box: "fitz.Rect") -> None:
    _MARK_BOXES.setdefault(page_no, []).append(+box)


def _fit_beside_marks(page_no: int, x: float, y: float, size: float,
                      right_cap: float, *, cross: bool = False,
                      dy_max: float = 0.0) -> tuple[float, float, float]:
    """Shrink, then if need be move, a mark clear of the ones already drawn.

    Returns (size, x, dy). Shrinking first because a smaller tick is still obviously
    a tick, where a tick shunted 40pt to the right of its answer is a tick belonging
    to nothing - the complaint this whole placement pass exists to answer. `dy_max`
    is how far DOWN the caller will let the mark travel and still call it the same
    answer's mark; on a stacked MCQ block that is worth more than a slide, because
    the marks stay in one column the way a teacher's do.

    Every candidate is tested AFTER the right cap is applied to it. The cap used to
    be applied to the winner instead, so on a three-deep MCQ stack the routine slid
    Q3's tick clear of Q2's cross, hit the edge of the photographed sheet, and pulled
    the tick straight back on top of the cross it had just escaped - the marks came
    out overlapping anyway, which is the one thing this function exists to prevent.
    """
    boxes = _MARK_BOXES.get(page_no) or []
    if not boxes:
        return size, x, 0.0

    def hits(sz: float, xx: float, dy: float) -> bool:
        box = (_cross_box(xx, y + dy, sz) if cross
               else _tick_box(xx, y + dy, sz))
        return any(box.intersects(b) for b in boxes)

    sz = size
    while True:
        width = sz if cross else 1.5 * sz
        hi = max(2.0, right_cap - width)
        here = min(x, hi)
        # In the order a teacher would accept a compromise: same spot, a shade lower
        # on the same answer, past the mark in the way, then before it.
        cands = [(here, 0.0)]
        if dy_max > 0:
            cands += [(here, d) for d in (dy_max * 0.5, dy_max)]
        for b in boxes:
            cands.append((min(b.x1 + 6.0, hi), 0.0))
            cands.append((min(max(2.0, b.x0 - width - 6.0), hi), 0.0))
        clear = [(xx, dy) for xx, dy in cands
                 if xx >= 2.0 and not hits(sz, xx, dy)]
        if clear:
            # The least disturbance that works, x first: a mark that stayed on its
            # own line reads as that line's verdict.
            xx, dy = min(clear, key=lambda c: (abs(c[0] - x), abs(c[1])))
            return sz, xx, dy
        if sz <= _TICK_MIN_SIZE + 1e-6:
            # Nothing clear anywhere on this row, even at the smallest legible size.
            # Keep the mark on its own answer and accept the overlap: a mark exiled
            # from the answer it belongs to is the worse of the two failures.
            return sz, here, 0.0
        sz = max(_TICK_MIN_SIZE, sz * 0.85)


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



def _page_point_heights(student_pdf_bytes: bytes, student_pngs) -> list[float]:
    """Height in POINTS of each student page as it will be drawn."""
    try:
        with fitz.open("pdf", student_pdf_bytes) as doc:
            heights = [page.rect.height for page in doc]
        if heights:
            return heights
    except Exception:  # noqa: BLE001 - an image submission, not a PDF
        pass
    sizes = []
    for png in list(student_pngs or []):
        try:
            with fitz.open("png", png) as doc:
                sizes.append(doc[0].rect.height)
        except Exception:  # noqa: BLE001
            sizes.append(842.0)
    return sizes


def _median_line_height(ocr_pages, page_heights=None) -> float:
    """The copy's typical handwriting height, in PDF points (0 when unknown).

    OCR bboxes are in image PIXELS, so they are converted with this page's own
    height in points — not a nominal A4 842, which is wrong by a third on a phone
    photo page (these live copies are 1210pt tall) and shrank every mark to match.
    Merged blocks (a whole paragraph returned as one line) are dropped before the
    median so they cannot inflate every mark on the copy.
    """
    heights: list[float] = []
    pages = list(page_heights or [])
    for index, page in enumerate(list(ocr_pages or [])):
        image_h = float(getattr(page, "image_height", 0) or 0)
        if image_h <= 0:
            continue
        to_points = (pages[index] if index < len(pages) else (pages[0] if pages else 842.0)) / image_h
        for line in list(getattr(page, "lines", None) or []):
            bbox = getattr(line, "bbox", None) or (0, 0, 0, 0)
            height = (float(bbox[3]) - float(bbox[1])) * to_points
            if 4.0 <= height <= 90.0:
                heights.append(height)
    if not heights:
        return 0.0
    heights.sort()
    return heights[len(heights) // 2]



def _doc_row_pitch(ocr_pages, page_heights=None) -> float:
    """The copy's typical gap between consecutive written lines, in points."""
    gaps: list[float] = []
    pages = list(page_heights or [])
    for index, page in enumerate(list(ocr_pages or [])):
        image_h = float(getattr(page, "image_height", 0) or 0)
        if image_h <= 0:
            continue
        to_points = (pages[index] if index < len(pages) else (pages[0] if pages else 842.0)) / image_h
        centres = sorted((float(line.bbox[1]) + float(line.bbox[3])) / 2 * to_points
                         for line in list(getattr(page, "lines", None) or []))
        gaps.extend(b - a for a, b in zip(centres, centres[1:]) if 6.0 <= b - a <= 120.0)
    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def _tick_size_for_line(base: float, line_h: float, answer_lines: int = 0) -> float:
    """Scale an inline tick/cross to the writing it marks.

    ONE SIZE PER COPY. The size follows this student's handwriting — the median OCR
    line height of the whole booklet (`_DOC_LINE_H`) — not the height of the single
    line a mark happens to sit on, and not how long the answer is.

    MEASURED 2026-09-16, on ten released copies sent for review: per-line scaling put
    a small tick on Q1 and a tick nearly twice its size on Q2-Q5 of the same MCQ
    block, because OCR had returned those lines at different heights. A teacher's
    hand does not change size line to line, and the first thing the reviewer said was
    that the ticks are "sometimes big and sometimes small". `line_h` and
    `answer_lines` are kept in the signature because callers still pass what they
    are marking, and a document-level factor is only available while rendering one.
    """
    document = _DOC_LINE_H.get()
    reference = document if document else line_h
    factor = 1.0
    if reference and reference > 0:
        ceiling = _DOC_SCALE_MAX if document else _TICK_SCALE_MAX
        factor = min(ceiling, max(_TICK_SCALE_MIN, reference / _NOMINAL_LINE_H))
    if answer_lines and answer_lines > 0 and not document:
        factor = min(_TICK_TOTAL_MAX,
                     max(_TICK_TOTAL_MIN, factor * _answer_length_scale(answer_lines)))
    size = _ms(base) * factor
    pitch = _DOC_ROW_PITCH.get()
    if document and pitch and pitch > 0:
        # Once per copy, not once per row: a tick inks about _TICK_RISE above its
        # anchor, so keep it inside one line of this student's writing.
        size = min(size, max(_TICK_MIN_SIZE, pitch / _TICK_RISE))
    return size


# A CROSS IS THE TICK'S OPPOSITE NUMBER, AND ONE HAND DRAWS THEM THE SAME SIZE.
# The two primitives do not take size the same way: `_draw_tick(s)` inks roughly
# 1.5s x 1.6s (the flick runs up and to the right of the anchor), while
# `_draw_cross(s)` inks exactly s x s centred on it. So every call site that passed
# the cross a FRACTION of the tick's size — 0.7 here, 0.85 there, a flat 18 or 20
# elsewhere — drew a cross at well under half the tick's linear extent.
#
# MEASURED on the released Class 10 Physics copy: the Q1 MCQ tick spans 25.1pt and
# the Q2 cross, one line below it, spans 12.8. Synthetically it is worse — a 19.3pt
# tick against a 9.1pt cross, a mark smaller than the student's own handwriting.
# Convert from the tick instead of guessing a fraction, so the pair always reads as
# one marker's hand however the tick was sized.
_CROSS_TO_TICK = 1.35     # cross extent ~= 0.85 x the tick's inked height
_CROSS_MIN_SIZE = 11.0    # below this a cross reads as a scratch, not a verdict


def _cross_size_for_tick(tick_size: float) -> float:
    """The cross that reads as the same hand as a tick of `tick_size`."""
    return max(_CROSS_MIN_SIZE, tick_size * _CROSS_TO_TICK)


# One hand marks the whole sheet. A step mark and a whole-answer mark may differ —
# the second stands for more — but not by so much that they read as two different
# markers. They were 8 and 13 (a 1.6x gap in drawn glyph size), and a real copy
# carried both on the SAME page. _STEP_MARK and _WHOLE_MARK keep that ratio honest,
# and the test suite pins it.
_STEP_MARK_SIZE = 8.0
_STEP_MARK_MIN_SIZE = 5.5    # below this the circled numeral stops being readable
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
        # Composition and inverse: "(f∘g)" read "(fg)" and "f⁻¹(x)" read "f1(x)".
        '∘': ' o ',
        '⁻¹': ' inverse',
        'ᵀ': ' transpose',
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
    # The letters of an Indian script or Urdu are kept: they are drawn shaped in a
    # font that has them (_insert_text). Dropping them left a Hindi copy's remarks as
    # "· Q19 , , , ." and "Q15: ." (Parishram Hindi, 2026-10-10).
    try:
        result.encode("latin-1")
    except UnicodeEncodeError:
        import unicodedata
        kept = []
        for ch in result:
            if _SCRIPT_RE.match(ch):
                kept.append(ch)
                continue
            kept.extend(part for part in unicodedata.normalize("NFKD", ch)
                        if not unicodedata.combining(part) and _encodable(part))
        result = "".join(kept)
    # A spelled-out symbol brings its own spacing ("A ∪ B" -> "A  union  B"), and a
    # double space is visible in a proportional font.
    return re.sub(r"[ 	]{2,}", " ", result)


# Indian scripts (Devanagari to Malayalam), Arabic script for Urdu, and the joiners
# they use. The base-14 fonts have none of them, and drawing them needs shaping.
_SCRIPT_RE = re.compile("[\u0600-\u06FF\u0750-\u077F\u0900-\u0DFF\uFB50-\uFDFF"
                        "\uFE70-\uFEFF\u200C\u200D]")


def _has_script(text: str | None) -> bool:
    return bool(_SCRIPT_RE.search(text or ""))


def _text_width(text: str, size: float, measure) -> float:
    """Width of `text`: Latin runs by `measure`, script letters at about 0.6 em
    (vowel signs that sit on a letter take no width of their own)."""
    if not _has_script(text):
        return measure(text)
    import unicodedata
    width, latin = 0.0, []
    for ch in text:
        if _SCRIPT_RE.match(ch):
            if unicodedata.category(ch) not in ("Mn", "Me", "Cf"):
                width += 0.6 * size
        else:
            latin.append(ch)
    return width + (measure("".join(latin)) if latin else 0.0)


def _insert_text(page: fitz.Page, point, text: str, *, fontsize: float, color,
                 fontname: str = "helv") -> None:
    """page.insert_text, except that a line holding an Indian script or Urdu is laid
    out shaped (HarfBuzz, through PyMuPDF's HTML box) in a font that has the script.
    `point` is the baseline start, as for insert_text."""
    if not _has_script(text):
        page.insert_text(point, text, fontsize=fontsize, color=color, fontname=fontname)
        return
    import html as _html
    x, y = float(point[0]), float(point[1])
    width = _text_width(text, fontsize,
                        lambda s: fitz.get_text_length(s, fontname="helv", fontsize=fontsize))
    rect = fitz.Rect(x, y - fontsize * 1.05, x + width + fontsize * 3, y + fontsize * 0.75)
    r, g, b = (int(round(255 * float(c))) for c in (tuple(color) + (0, 0, 0))[:3])
    css = (f"* {{font-family: sans-serif; font-size: {fontsize:.2f}px; "
           f"color: rgb({r},{g},{b}); line-height: 1.15; margin: 0; padding: 0; "
           "white-space: nowrap;}")
    try:
        page.insert_htmlbox(rect, _html.escape(text), css=css, scale_low=0)
    except Exception:  # noqa: BLE001 - an unshaped line beats a missing one
        page.insert_text(point, text, fontsize=fontsize, color=color, fontname=fontname)


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
    def _latin_width(s: str) -> float:
        try:
            if fontfile:
                return fitz.get_text_length(s, fontname=fontname,
                                            fontsize=size, fontfile=fontfile)
            return fitz.get_text_length(s, fontname=fontname, fontsize=size)
        except Exception:
            return len(s) * size * 0.55

    def _measure(s: str) -> float:
        return _text_width(s, size, _latin_width)

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
            _insert_text(page, (x, cy), ln, fontsize=size, color=RED, fontname=fontname)
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


# The share of a correction's box that may already carry the student's ink. The
# correct spelling is written above the word only into clear paper; otherwise the
# underline stands alone, which is what a teacher in a hurry does anyway.
_WORD_MARK_INK_LIMIT = 0.02


def draw_word_marks(page: fitz.Page, marks: list[dict], page_w: float, page_h: float, *,
                    pitch: float = 22.0, ink_fraction=None, avoid=None) -> list[dict]:
    """Underline each verified wrong word (language_marks.verify_word_slips) and,
    where clear paper allows, write the correct word above it.

    `marks` carry the word's box in OCR image pixels with that image's size, so the
    same record draws on either layout. `ink_fraction(rect)` returns the share of a
    page-coordinate rect lying on the student's writing (None = unknown, treated as
    occupied); `avoid` is a list of rects already taken by ticks and circled marks.
    Returns what was drawn, for the render record. Never raises on one bad mark.
    """
    drawn: list[dict] = []
    taken = [fitz.Rect(r) for r in (avoid or [])]
    for mark in marks or []:
        try:
            iw, ih = mark["image_size"]
            sx, sy = page_w / max(1.0, float(iw)), page_h / max(1.0, float(ih))
            x0, y0, x1, y1 = mark["bbox"]
            word = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
            if word.is_empty or word.x1 > page_w + 1 or word.y1 > page_h + 1:
                continue
            under_y = word.y1 + max(1.0, 0.06 * word.height)
            _draw_underline(page, word.x0, under_y, word.width)
            record = {"line_id": mark.get("line_id", ""), "written": mark.get("written", ""),
                      "correct": mark.get("correct", ""),
                      "underline": [word.x0, under_y - 1.5, word.x1, under_y + 1.5],
                      "correction_bbox": None}
            correct = _sanitize_for_pdf(str(mark.get("correct") or "").strip())
            # The bundled handwriting font carries Latin glyphs only; a Devanagari
            # correction would print as boxes, so those words are underlined only.
            if correct and correct.isascii() and ink_fraction is not None:
                size = max(6.5, min(11.0, 0.42 * float(pitch or 22.0)))
                fontname, fontfile = _register_cursive_font(page)
                try:
                    width = (fitz.get_text_length(correct, fontname=fontname, fontsize=size,
                                                  fontfile=fontfile) if fontfile
                             else fitz.get_text_length(correct, fontname=fontname, fontsize=size))
                except Exception:
                    width = len(correct) * size * 0.55
                left = max(2.0, min(page_w - width - 2.0, (word.x0 + word.x1) / 2 - width / 2))
                box = fitz.Rect(left - 1, word.y0 - size * 1.15, left + width + 1, word.y0 - 0.5)
                share = ink_fraction(box) if box.y0 >= 1 else None
                if (share is not None and share <= _WORD_MARK_INK_LIMIT
                        and not any(box.intersects(r) for r in taken)):
                    page.insert_text((left, box.y1 - size * 0.22), correct, fontsize=size,
                                     color=RED, fontname=fontname)
                    taken.append(box)
                    record["correction_bbox"] = [box.x0, box.y0, box.x1, box.y1]
            taken.append(fitz.Rect(word.x0, under_y - 2, word.x1, under_y + 2))
            drawn.append(record)
        except Exception as exc:  # noqa: BLE001 - an underline is never worth a copy
            print(f"[language-marks] a word mark was skipped ({type(exc).__name__}: {exc})",
                  flush=True)
    return drawn


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
# The same label in words: "Question no 04", "Ques. 3", "Q.no 4", "Solution no 01".
# MEASURED on the MP Board Core Math copies (22 Oct 2026): students head every answer
# "Question no N", and a one-mark answer's circled 1 went on the NEXT question's
# heading - five marks on one copy.
_QWORD_HEADER_RE = re.compile(
    r"^(?:q(?:ue?s(?:tion)?|n)?|sol(?:ution|n)?|ans(?:wer)?)\s*\.?\s*"
    r"(?:no|number|num)?\s*\.?\s*[-–—:#]*\s*\d{1,3}\s*[).:\-]*$")

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
    if is_scanner_watermark(s):        # "Scanned with OKEN Scanner" under the photo
        return True
    # Long lines past 22 chars are treated as answer lines — they almost
    # always contain real content past the label.
    if len(sl) > 22:
        return False
    if sl.startswith(_HEADER_PREFIXES):
        return True
    if _QHEADER_RE.match(sl):          # bare "Q2", "Q2)", "Q.27)", "Q 1."
        return True
    if _QWORD_HEADER_RE.match(sl):     # "Question no 04", "Solution no 01"
        return True
    if _QHEADER_HI_RE.match(sl):       # bare "प्रश्न", "प्रश्न १", "प्र. 2"
        return True
    if sl.startswith((_SECTION_PREFIX, "part")) and len(sl) <= 14:
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


# ---------- Margin score canvas ----------
#
# Answer marks are drawn in original student coordinates. Question scores and
# remarks use a separate canvas with a virtual strip to the right, which is
# composed on the RIGHT in the finished PDF. `_CONTENT_W` records the original
# content width so OCR pixel→PDF mapping still maps onto the writing area.
# `_STRIP_SLOTS` tracks occupied
# vertical intervals so an out-of-order anchor can still use free space above a
# later answer instead of being pushed beside the following answer.
STRIP_FRACTION = 0.24        # strip width as a fraction of the original page width
STRIP_MIN_WIDTH = 175.0      # but never narrower than this (points)
_STRIP_TOP = 26.0            # first entry keeps clear of the page's top edge
_CONTENT_W: dict[tuple[int, int], float] = {}
_STRIP_SLOTS: dict[int, dict[int, list[tuple[float, float]]]] = {}


_SHEET_RIGHT: dict[tuple[int, int], float] = {}


def _sheet_right_edge(page: fitz.Page, content_w: float) -> float:
    """Where the student's SHEET ends, which is NOT where the page ends.

    A phone photo of a notebook is embedded in an A4 page with white space around
    it. MEASURED on a live Class 10 Physics copy: the paper occupied x 113..481 of
    a 595pt page. Marks were placed against the PAGE width, so a tick for a line
    whose ink ran to 478 was drawn at 492 - floating in the blank margin BESIDE the
    sheet rather than on the answer. Short lines looked right and long ones did not,
    which is exactly how it was reported ("the ticks are not on the actual answer").

    An ordinary full-page scan reports its own width, so nothing moves for it.
    """
    return _SHEET_RIGHT.get((id(page.parent), page.number), content_w)


def _measure_sheet_right(page: fitz.Page, content_w: float) -> float:
    """Scan for the rightmost non-white column of the sheet. Must run BEFORE any
    mark is drawn, or a mark already sitting in the margin would extend the edge."""
    # A digital answer has no photographed sheet edge. Its last text column
    # must not be mistaken for the edge of its available paper.
    if not page.get_images():
        return content_w
    try:
        pixmap = page.get_pixmap(
            dpi=36, clip=fitz.Rect(0, 0, content_w, page.rect.height))
    except Exception:
        return content_w
    width, height, n, data = pixmap.width, pixmap.height, pixmap.n, pixmap.samples
    if width <= 0 or height <= 0:
        return content_w
    for x in range(width - 1, -1, -1):
        for y in range(0, height, 3):
            i = (y * width + x) * n
            if not (data[i] > 244 and data[i + 1] > 244 and data[i + 2] > 244):
                return min(content_w, (x + 1) / width * content_w)
    return content_w


def _mark_area_right(page: fitz.Page) -> float:
    """Right limit for anything drawn on the answer: the sheet, not the page."""
    return _sheet_right_edge(page, _content_width(page))


def _content_width(page: fitz.Page) -> float:
    """Original student-page width, excluding the temporary annotation gutter."""
    return _CONTENT_W.get((id(page.parent), page.number + 1), page.rect.width)


def _reset_strip_state(doc: fitz.Document) -> None:
    _STRIP_SLOTS[id(doc)] = {}


def _reserve_strip_slot(doc: fitz.Document, page_num: int, y_pref: float,
                        height: float, page_h: float,
                        forward_only: bool = False, allow_overlap: bool = True) -> float | None:
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
    if forward_only:
        # An ordered list only ever moves DOWN the page. The nearest free slot can
        # be above the previous entry, and on a live copy that read the MCQ column
        # back as "Q4, Q3, Q1, Q2" - the margin order is what a student follows.
        ahead = [c for c in candidates if c >= preferred - 1e-6]
        if ahead:
            candidates = ahead

    if candidates:
        y = min(candidates, key=lambda candidate: (abs(candidate - preferred), candidate))
    elif not allow_overlap:
        return None
    else:
        # An overfull strip is caught by rendered-copy preflight ("feedback-overlap").
        # Keep the annotation on-page so the failure is visible and diagnosable.
        y = preferred

    occupied.append((y, y + height))
    reg[page_num] = sorted(occupied)
    return y


def _strip_wrap_lines(text: str, max_w: float, size: float,
                      fontname: str = "helv") -> int:
    """Count how many wrapped lines `text` needs at width max_w — for height
    reservation. Measured slightly wide so we over-reserve rather than overlap."""
    def latin(txt: str) -> float:
        try:
            return fitz.get_text_length(txt, fontname=fontname, fontsize=size + 1)
        except Exception:
            return len(txt) * (size + 1) * 0.55

    # Measured as the note is drawn: Helvetica has no Devanagari, so a four line Hindi
    # note was given room for one and the notes on a page printed over each other
    # (KVS PGT Economics, 22 Oct 2026, pages 3, 6, 7 and 8).
    def measure(txt: str) -> float:
        return _text_width(txt, size + 1, latin)

    return max(1, len(_wrap_to_width(text, max_w, measure)))


def _fmt_mark(v: float) -> str:
    """3 not 3.0, 2.5 stays 2.5 — how a teacher writes a mark."""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _visual_step_line(doc, report, qid, index, criterion, line):
    """Refine a certified OCR region, without inventing rows in opaque tables."""
    cert = report.verified_step_boxes.get(f"{normalize_qid(qid)}:{index}", {})
    page = doc[line['page'] - 1]
    from presentation_layout import (BLOCK_ENTRY_ROW, ocr_evidence_box, normalized_box,
                                     row_box_fits, row_box_limit)
    source = (_OCR_WORDS.get() or {}).get(line['lid'])
    verified = report.verified_step_anchors.get(f'{normalize_qid(qid)}:{index}') == [line['lid'], criterion.anchor_quote]
    measured = ocr_evidence_box(*source, criterion.anchor_quote, visually_verified=verified) if source else None
    if cert.get('line_id') == line['lid'] and cert.get('quote') == criterion.anchor_quote:
        if cert.get('verdict') in {'correct', 'wrong', 'partial', 'missing'}:
            line = {**line, '_verdict': cert['verdict']}
        box = normalized_box(source[0], measured[0]) if measured else cert.get('bbox', [])
        w, h = _content_width(page), page.rect.height
        line_n = [line['x0'] / w, line['y0'] / h, line['x1'] / w, line['y1'] / h]
        if (len(box) == 4 and all(isinstance(v, (int, float)) for v in box)
                and row_box_fits(line_n, box, block=row_box_limit(
                    source[1] if source else line) == BLOCK_ENTRY_ROW)):
            refined = fitz.Rect(box[0] * w, box[1] * h, box[2] * w, box[3] * h)
            return {**line, **dict(zip(('x0', 'y0', 'x1', 'y1'), refined)),
                    '_visual_anchor': True, '_location_source': measured[1] if measured else 'visual_verifier'}
    if measured:
        box = normalized_box(source[0], measured[0])
        return {**line, **dict(zip(('x0', 'y0', 'x1', 'y1'),
                (box[0] * _content_width(page), box[1] * page.rect.height,
                 box[2] * _content_width(page), box[3] * page.rect.height))), '_location_source': measured[1]}
    # No certificate, and the quote is not locatable among the OCR words. The
    # grader still named THIS line and its text carries the quote - that is what
    # got the step placed at all (anchors._resolve_evidenced_steps) - so the line
    # itself is the evidence row. It used to be refused outright, and the copies
    # released on 2026-09-16 without a verifier pass came back with most of their
    # steps unmarked: "verdict-not-visible" 151 times across ten copies. Struck-out
    # work is never marked, and a block taller than a couple of rows of this hand
    # is still refused: a mark beside its middle would sit on nothing in particular.
    if source and getattr(source[1], 'cancelled', False):
        return None
    if line['y1'] - line['y0'] > page.rect.height * .085 * (2.0 if source else 1.0):
        if _DIAG_REGION_RE.match(str(line.get('lid', ''))):
            # A table or drawing the grader cited, with no verified row inside it. It
            # used to be refused, and nothing else drew the step: the margin said
            # "Q11 2.5/3" over one circled 0.5, and a page holding only a Q13 table
            # carried no mark at all (Parishram Geography, 8 Oct 2026). A teacher
            # ticks a table where it ends. Every step citing the region resolves to
            # the same row there, so they draw as ONE mark with their awards added;
            # no row inside the table is guessed.
            # A map or table that fills the page leaves no room beside its end (the
            # Geography Q15 world map was 97 percent of the page): the mark goes
            # inside its lower right corner instead, as a teacher marks on the map.
            x1, y1 = line['x1'], line['y1']
            if x1 > .85 * _content_width(page):
                x1 = line['x0'] + .7 * (line['x1'] - line['x0'])
            if y1 > .95 * page.rect.height:
                y1 = line['y0'] + .9 * (line['y1'] - line['y0'])
            row = min(y1 - line['y0'], page.rect.height * .03)
            return {**line, 'x1': x1, 'y0': y1 - row, 'y1': y1, '_location_source': 'block_end'}
        return None  # Release preflight will require a verified row, not a guessed midpoint.
    return {**line, '_location_source': 'ocr_line_unverified' if source else 'ocr_line'}


def _margin_reason(reason: str, remark: str) -> str:
    """A margin note in the same plain words as the cover, in the copy's language.

    The second examiner's reasons reached the margin raw while the cover was cleaned:
    semicolons, "Z_eff", and English notes on a Hindi copy beside Hindi remarks
    (teaching review, 22 Oct 2026, EMRS PGT Hindi Q9 and Q11)."""
    from wording import plain_sentences, strip_rule_talk
    text = plain_sentences(strip_rule_talk(reason or "")) or str(reason or "").strip()
    if remark and _has_script(remark) and not _has_script(text):
        return _sanitize_for_pdf(plain_sentences(strip_rule_talk(remark)) or text)
    # The margin font has no maths symbols either: "A∪B" and "(f∘g)" were drawn as
    # "A□B" and "(f□g)" beside the answers (MH Board Maths, 22 Oct 2026) while the
    # cover read "A union B".
    return _sanitize_for_pdf(text)


def _below_answer_end(end: dict, page_bottom: float) -> dict:
    """The row just under an answer's last line, for a missing point's 0: its own row,
    not beside a sentence another step was ticked for. The last line itself when the
    answer ends at the foot of the page. It keeps the last line's id, so the mark is
    still recorded against this answer's own writing."""
    height = max(8.0, float(end["y1"]) - float(end["y0"]))
    below = {**end, "y0": float(end["y1"]) + 2.0, "y1": float(end["y1"]) + 2.0 + height,
             "_location_source": "missing_at_answer_end"}
    if below["y1"] > float(page_bottom) - 4.0:
        return {**end, "_location_source": "missing_at_answer_end"}
    return below


def _certified_target_line(doc, target, by_lid):
    """Resolve a separately verified checking point or award ending."""
    from presentation_layout import valid_visual_box, ocr_evidence_box, normalized_box
    line = by_lid.get(target.get('line_id'))
    source = (_OCR_WORDS.get() or {}).get(target.get('line_id'))
    if not line or not source:
        return None
    p, raw = source
    box = target.get('bbox')
    if box:
        if not valid_visual_box(p, raw, box):
            return None
    else:
        measured = ocr_evidence_box(p, raw, target.get('quote', ''), visually_verified=True)
        if not measured:
            return None
        box = normalized_box(p, measured[0])
    page = doc[line['page'] - 1]
    w, h = _content_width(page), page.rect.height
    return {**line, **dict(zip(('x0','y0','x1','y1'), (box[0]*w,box[1]*h,box[2]*w,box[3]*h))),
            '_location_source': 'verified_checking_plan', '_verdict': target.get('verdict'),
            '_deduction_reason': target.get('reason', '')}


def _checking_certificate(report, qid, index, criterion):
    cert = report.verified_step_boxes.get(f'{normalize_qid(qid)}:{index}', {})
    if cert.get('line_id') != criterion.step_line_id or cert.get('quote') != criterion.anchor_quote:
        return {}
    return cert


# The lines each question was actually read on, decided once per copy by
# _annotate_questions (see _lines_the_grader_read); the mapped region is the
# fallback for callers outside a build.
_OWNED_LIDS: dict[str, set[str]] = {}


def _owned_lids(report, qid: str) -> set[str]:
    if qid in _OWNED_LIDS:
        return _OWNED_LIDS[qid]
    from grading_quality import answer_line_ids
    question = next((q for q in report.questions if q.qid == qid), None)
    if question is None:
        return set()
    return set(answer_line_ids(report, question))


def _sheet_slot_allowed(qid, line, box, page):
    """Reject drift and slots belonging to another question, including its heading."""
    from presentation_layout import near_evidence
    if not near_evidence(box, [line[k] for k in ('x0', 'y0', 'x1', 'y1')]):
        return False
    report = _RENDER_REPORT.get()
    sources = _OCR_WORDS.get() or {}
    own = set(_owned_lids(report, qid)) if report else set()
    original = sources.get(line['lid'])
    if not report or not original:
        return True
    from grading_quality import answer_mark_pages
    w, h = _content_width(page), page.rect.height
    for q in report.questions:
        if q.qid == qid or line['page'] not in answer_mark_pages(report, q):
            continue
        for lid in _owned_lids(report, q.qid):
            if lid in own or lid not in sources or sources[lid][0].page != line['page']:
                continue
            p, other = sources[lid]
            other_box = fitz.Rect(other.bbox[0]*w/p.image_width, other.bbox[1]*h/p.image_height,
                                  other.bbox[2]*w/p.image_width, other.bbox[3]*h/p.image_height)
            if not box.intersects(other_box):
                continue
            # OCR boxes are generous: the rows above and below overlap this one by a
            # few points, and on a live copy every slot beside "b) Sangam" was refused
            # for grazing the boxes of "a) Dhana Nanda" and "c) intermontane". A slot
            # is on another answer when it shares most of its height with that
            # answer's row - the second column of a two-column MCQ block - not when
            # it touches the next row's padding. A block several rows tall is a
            # merged OCR region, not a row anyone is beside.
            own_h = max(1.0, box.y1 - box.y0)
            other_h = max(1.0, other_box.y1 - other_box.y0)
            typical = _DOC_LINE_H.get()
            if typical and other_h > 2.5 * typical:
                continue
            overlap = min(box.y1, other_box.y1) - max(box.y0, other_box.y0)
            if overlap >= .5 * min(own_h, other_h):
                return False
    return True


# The step's own evidence says its figure is wrong ("Gross salary should be
# Rs.8,16,150, not Rs.10,53,610", "is incorrect", "instead of"). "Carried forward"
# evidence is a method step on the student's own figure and stays a tick.
_WRONG_FIGURE_RE = re.compile(
    r"\bshould be\b|\binstead of\b|\b(?:is|are|was|were)\s+(?:wrong|incorrect)\b|\bincorrect(?:ly)?\b"
    r"|\bwrongly\b|\bmiscalculat|\bnot\s+(?:rs\.?|₹)\s*[\d(]|,\s*not\s+(?:rs\.?\s*|₹\s*)?[\d(]", re.IGNORECASE)
_CARRIED_FORWARD_RE = re.compile(r"carr(?:ied|y|ies)\s+forward|\bECF\b|own figure", re.IGNORECASE)


def evidence_names_wrong_figure(criteria, indices) -> bool:
    """True when one of these criteria's evidence calls the student's figure wrong.

    CA faculty review (17 Oct 2026): "Incorrect salary amount is marked correct,
    despite remarks stating it is incorrect", "PPE working note is incorrect but it
    is marked correct". A partially paid step was drawn with the same tick as a
    correct one."""
    for i in indices:
        try:
            text = str(getattr(criteria[i], "evidence", "") or "")
        except (IndexError, TypeError):
            continue
        if _WRONG_FIGURE_RE.search(text) and not _CARRIED_FORWARD_RE.search(text):
            return True
    return False


def _draw_sheet_verdict(doc, qid, line, verdict, *, criteria=(), role='verdict', score=None):
    """A separate, evidence-local checking stroke; score numerals never cover ink."""
    page = doc[line['page'] - 1]
    # The award controls the glyph when it is known. Keep the verdict separately
    # for feedback; individual certified checking points may have no numeric award.
    # On a CA paper a step the examiner called wrong is crossed even when it kept
    # part marks (CA faculty review, 17 Oct 2026: wrong figures read as accepted);
    # its circled award still shows what it kept. Other categories keep one tick
    # for any positive award, as their reviewers asked.
    ca_paper = bool(getattr(_RENDER_REPORT.get(), "whole_total", False))
    if score is None or (ca_paper and verdict == 'wrong'):
        wrong = verdict == 'wrong' or (score is not None and score <= 1e-6)
    else:
        wrong = score <= 1e-6
    report = _RENDER_REPORT.get()
    point = line.get('_checking_point')
    if point and report:
        anchor = [line[k] for k in ('x0','y0','x1','y1')]
        for recorded in report.render_manifest:
            if (recorded.get('qid') == qid and recorded.get('page') == line['page']
                    and recorded.get('verdict') == verdict and recorded.get('anchor_bbox') == anchor):
                recorded['criteria'] = sorted(set(recorded.get('criteria', [])) | set(criteria))
                recorded.setdefault('checking_points', []).append(point)
                return True
    right = min(_content_width(page), _mark_area_right(page)) - 4
    cy = (line['y0'] + line['y1']) / 2
    page_no = line['page']
    # This row's own marks so far. The circled step award is planned before its
    # stroke, and used to take the slot beside the words; the stroke then fell
    # back to the LEFT of the line - every ticked step on a live copy carried its
    # tick in the left margin. The stroke now goes between the words and the
    # circle (the circle leaves it room, see _verdict_lead), or after the circle.
    row_marks = sorted((b for b in _MARK_BOXES.get(page_no, [])
                        if b.y0 <= cy <= b.y1 and b.x0 >= line['x0']),
                       key=lambda b: b.x0)
    for tick in _verdict_size_ladder():
        cross = _cross_size_for_tick(tick)
        pen = 0.12 * tick + 1.0
        if wrong:
            width, half_h = cross + 2 * pen + 2, cross / 2 + pen + 2
        else:
            width = tick * 1.5 + 2 * pen + 2
            half_h = tick * .8 + pen + 2
        starts = [min(line['x1'] + 3, right - width)]
        if point and line['x1'] - line['x0'] >= width + 2:
            # On explicitly identified evidence, a teacher's stroke may touch the
            # writing. Keep it on that entry rather than crossing into another column.
            starts.insert(0, min(line['x1'] - width, right - width))
        # After the writing; after the row's own circled mark; back over the end
        # of the words; and the left of the line only when nothing else will do.
        starts += [min(b.x1 + 3, right - width) for b in row_marks]
        starts += [max(3, min(line['x1'] + 3 - dx, right - width)) for dx in (20, 36, 52)]
        starts.append(max(3, line['x0'] - width - 3))
        for x in starts:
            box = fitz.Rect(x - 1, cy - half_h, x + width, cy + half_h)
            if not page.rect.contains(box) or box.x1 > right:
                continue
            if not _sheet_slot_allowed(qid, line, box, page):
                continue
            # Strokes on neighbouring rows may touch top to bottom, as a
            # teacher's do; only a real overlap is a collision.
            core = box + (0, tick * .15, 0, -tick * .15)
            if any(core.intersects(b) for b in _MARK_BOXES.get(page_no, [])):
                continue
            if wrong:
                _draw_cross(page, x + pen + cross / 2, cy, size=cross, seed=qid + str(criteria))
            else:
                # Positive partial credit uses the same single tick as full credit.
                # The circled award and deduction reason explain the lost marks.
                _draw_tick(page, x + pen, cy + .6 * tick, size=tick, seed=qid + str(criteria))
            _note_mark(page_no, box)
            _record_draw(qid, role, page_no, box, line=line, criteria=criteria, score=score)
            report = _RENDER_REPORT.get()
            if report is not None and report.evidence_contract:
                report.render_manifest[-1]['verdict'] = verdict
                if point:
                    report.render_manifest[-1]['checking_points'] = [point]
                if line.get('_deduction_reason'):
                    report.render_manifest[-1]['deduction_reason'] = line['_deduction_reason']
            return True
    _note_unanchored(qid, 1)
    return False


def _draw_sheet_score(doc, qid, line, score, *, role, criteria=(), label="",
                      total=False, cross=False, plans=None, lead: float = 0.0):
    """Find clear paper on the evidence row; never put awarded marks in the strip.

    `lead` is room kept free right after the writing for the checking stroke that
    goes with this score, so the row reads "answer  tick  circled mark", the order
    a teacher writes them in. When nothing fits with the room kept, it is given up.
    """
    from preflight import _ink_under_mark, _MARK_INK_LIMIT
    from presentation_layout import score_candidates
    page = doc[line["page"] - 1]
    right = min(_content_width(page), _mark_area_right(page)) - 4
    # `ink_scale` converts points to raster pixels. It was named `scale` and then
    # overwritten by the mark scale below, so every ink probe here read the
    # wrong pixels and passed - a numeral could land on solid black
    # (tests/test_on_sheet_scores, test_evidence_quality).
    mask, ink_scale = _page_ink(doc, line["page"])
    font = fitz.Font("hebo")
    text = _fmt_mark(score)
    # This copy's size first, then the A4 sizes as the fallback ladder: a bigger
    # circle that finds no clear paper shrinks to what used to fit, never to nothing.
    grow = _doc_mark_scale()
    base = (11.0, 10.0, 9.0, 8.0) if role == 'step' else (11.0, 10.0, 9.0)
    sizes = sorted({round(size * grow, 2) for size in base} | set(base), reverse=True)
    leads = (lead, 0.0) if lead > 0 else (0.0,)
    # Nothing is drawn while the spots are tried, so the page's text is looked up once.
    printed = _page_text_boxes(page)
    for fs in sizes:
        k = fs / 11.0
        text_w = font.text_length(text, fontsize=fs)
        radius = max((6.5 if role == 'step' else 9.5) * k,
                     text_w / 2 + (2 if role == 'step' and fs <= 8 else 3) * k, fs * .8)
        width = 2 * radius

        def _candidates(width=width, radius=radius):
            for lead_now in leads:
                anchor_box = [line['x0'], line['y0'], line['x1'] + lead_now, line['y1']]
                yield from score_candidates(anchor_box, width, 2 * radius,
                                            at_end=role == 'total' or bool(line.get('_award_ending')))
        for candidate in _candidates():
                x, cy = candidate[0], (candidate[1] + candidate[3]) / 2
                cx = x + radius
                circle = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius)
                box = fitz.Rect(x, circle.y0, circle.x1, circle.y1)
                if box.x0 < 3 or box.x1 > right or box.y0 < 3 or box.y1 > page.rect.height - 3:
                    continue
                if not _sheet_slot_allowed(qid, line, box, page):
                    continue
                if any(box.intersects(b + (-2, -2, 2, 2)) for b in _MARK_BOXES.get(line["page"], [])):
                    continue
                # Test the whole mark and the text core. A mostly empty circle can
                # otherwise conceal a dark student numeral underneath its centre.
                core = fitz.Rect(cx - text_w / 2 - 1, cy - fs * .6,
                                 cx + text_w / 2 + 1, cy + fs * .6)
                if _boxes_touch(printed, circle + (-1, -1, 1, 1)):
                    continue
                if mask is not None and any(
                        _ink_under_mark(mask, ink_scale, probe + (dx, ddy, dx, ddy)) >= _MARK_INK_LIMIT
                        for probe in (box, core)
                        for dx, ddy in ((0, 0), (-.5, 0), (.5, 0), (0, -.5), (0, .5))):
                    continue
                _note_mark(line["page"], box)
                plan = dict(qid=qid, line=line, score=score, role=role, criteria=criteria,
                            total=total, cross=cross, box=box, circle=circle,
                            cx=cx, cy=cy, radius=radius, text=text, text_w=text_w, fs=fs)
                if plans is not None:
                    plans.append(plan)
                else:
                    _paint_sheet_score(doc, plan)
                return True
    return False


def _paint_sheet_score(doc, plan):
    """Paint a reserved slot only after the whole question's layout is settled."""
    p = plan
    page = doc[p['line']['page'] - 1]
    if p['cross']:
        _draw_cross(page, p['cx'], p['cy'], size=p['radius'] * 1.4,
                    seed=p['qid'] + str(p['criteria']))
    else:
        page.draw_circle((p['cx'], p['cy']), p['radius'], color=RED, width=1.2)
        page.insert_text((p['cx'] - p['text_w'] / 2, p['cy'] + p['fs'] * .35),
                         p['text'], fontsize=p['fs'], fontname='hebo', color=RED)
    _record_draw(p['qid'], p['role'], p['line']['page'], p['box'], line=p['line'],
                 criteria=p['criteria'], score=p['score'],
                 numeral_bbox=None if p['cross'] else p['circle'])
    report = _RENDER_REPORT.get()
    if report is not None and report.evidence_contract:
        report.render_manifest[-1].update(question_total=p['total'], score_text=p['text'],
            score_style='cross_only' if p['cross'] else 'circled_award')


def _draw_sheet_total(doc, q, lines):
    """Put an unlabelled circled award at the end of a single-mark answer."""
    # A heading the map swept into the region ("Part II", "a.") is not the end of
    # the answer: a live copy carried Q6.e's circled 1 on the "Part II" line.
    body = [line for line in lines if not _is_header(line.get('text', ''))] or list(lines)
    last_page = max((line['page'] for line in body), default=0)
    last_y = max((line['y1'] for line in body if line['page'] == last_page), default=0)
    report = _RENDER_REPORT.get()
    by_lid = {line['lid']: line for line in lines}
    checking, endings = [], []
    if report:
        for ci, criterion in enumerate(q.criteria):
            cert = _checking_certificate(report, q.qid, ci, criterion)
            for target in cert.get('checking_points', []):
                located = _certified_target_line(doc, target, by_lid)
                if located:
                    checking.append(located)
            if cert.get('award_anchor'):
                located = _certified_target_line(doc, cert['award_anchor'], by_lid)
                if located:
                    endings.append(located)
    final_check = max(((p['page'], p['y1']) for p in checking), default=(0, 0))
    # Explicit complete-work endings outrank padded OCR table contours. Never
    # put a total before a later certified entry or on a previous answer page.
    for line in sorted(endings, key=lambda p: (p['page'],p['y1']), reverse=True):
        if line['page'] == last_page and (line['page'],line['y1'] + 2) >= final_check:
            if _draw_sheet_score(doc,q.qid,line,q.score,role='total',total=True):
                return True
    # A tight checked ending near the OCR boundary is safer than a whole-table
    # corner, which can sit on a dark binding or a vertical account rule.
    for line in sorted(checking, key=lambda p: (p['page'],p['y1']), reverse=True):
        if line['page'] == last_page and line['y1'] >= last_y - 15:
            if _draw_sheet_score(doc,q.qid,line,q.score,role='total',total=True):
                return True
    for line in sorted((row for row in body if row['page'] == last_page and row['y1'] >= last_y - 10),
                       key=lambda row: (row['y1'], row['y0']), reverse=True):
        if _draw_sheet_score(doc, q.qid, line, q.score, role="total", total=True):
            return True
    _note_unanchored(q.qid, 1)
    return False


def _draw_continuation_label(doc, qid, lines):
    """A plain tick, with no number, at the end of an answer's continuation page.

    A page of answer with no mark on it reads as a page nobody checked. SME review
    of the CA batch (2026-09-23): "Page no. 6 and 15 are not checked"; and again on
    the Parishram Geography copies (8 Oct 2026), where the page holding the end of
    Q13 carried nothing. The teacher layout has drawn this seen tick since then; the
    legacy layout drew nothing. Question labels still belong only to the margin, and
    the tick carries no score, so it changes no total and no circle count."""
    rows = [line for line in lines if (line.get('text') or '').strip()]
    if len(rows) < 2:
        return False                      # a stray line or a heading is not a page of answer
    last = max(rows, key=lambda line: (line['y1'], line['x1']))
    return _draw_sheet_verdict(doc, qid, last, 'correct', role='continuation', score=None)


def _draw_strip_entry(page: fitz.Page, qid: str, awarded: float,
                      max_score: float, remark: str, y_pref: float,
                      steps: list[float] | None = None,
                      verdict: str = "", show_score: bool = True,
                      forward_only: bool = False, label_only: bool = False,
                      note: str = "") -> None:
    """Write one question's score and feedback on the separate margin canvas.

    Objective rows also receive a compact verdict glyph in this QID-labelled lane,
    where it cannot be mistaken for a neighbouring answer's verdict. With
    show_score False and label_only True the entry names its question without the
    score, so a reason on another page of the answer still says whose it is.
    """
    canvas = _STRIP_CANVAS.get()
    if canvas is not None:
        page = canvas[page.number]
    doc = page.parent
    rect = page.rect
    cw = _content_width(page)
    verdict = str(verdict or "").strip().lower()
    verdict_lane = 25.0 if verdict in {"correct", "wrong"} else 0.0
    # Step marks may occupy the near edge of this same margin. Keep feedback
    # clear of them inside the existing strip, without adding a second gutter.
    report = _RENDER_REPORT.get()
    if report is not None and report.evidence_contract:
        # Keep both views: circled awards on the answer and a labelled total in
        # the right margin, including full-credit answers with no feedback.
        x0, x1 = cw + 12, rect.width - 8
        max_w = x1 - x0
        # The margin text grows with the copy the way its marks do (12pt type on a
        # 1860pt-wide phone photo is unreadable), but only as far as the strip can
        # still wrap it.
        size = 12.0 * min(_doc_mark_scale(),
                          max(1.0, (rect.width - cw) / STRIP_MIN_WIDTH))
        score_text = f"{_fmt_mark(awarded)}/{_fmt_mark(max_score)}" if max_score else _fmt_mark(awarded)
        head = f"{_label(qid)}  {score_text}"
        full = max_score > 0 and awarded >= max_score - 1e-6
        reason = _sanitize_for_pdf((remark or "").strip()) if not full else ""
        # Put the fraction in the short heading. The arithmetic can wrap at
        # spaces between terms without splitting a denominator onto another line.
        parts = ([head] if show_score else [_label(qid)] if label_only else []) + ([reason] if reason else [])
        if note:
            # A page note (an extra attempt, a page no question was matched to): its
            # own words, no score and no reason.
            parts = [_sanitize_for_pdf(note)]
        part_total = _part_total_head(report, qid) if show_score else ""
        if part_total:
            parts.insert(0, part_total)
        # A full margin is written smaller, as a teacher does, and a step's extra
        # reason (its score note is already on this page, and the cover lists the
        # question) gives way rather than be written over another note. Uday Bihar
        # Board Accountancy 3319af12 (10 Oct 2026): Q12's seven notes and Q13's were
        # printed over each other down page 2 and the copy was released.
        y = None
        for factor in (1.0, 0.85, 0.72):
            trial = size * factor
            height = sum(_strip_wrap_lines(part, max_w, trial) * trial * 1.4 + 6
                         for part in parts) + 8
            y = _reserve_strip_slot(doc, page.number + 1, y_pref - trial, height, rect.height,
                                    forward_only=forward_only, allow_overlap=False)
            if y is not None:
                size = trial
                break
        if y is None:
            if not show_score:
                return None
            y = _reserve_strip_slot(doc, page.number + 1, y_pref - size, height, rect.height,
                                    forward_only=forward_only)
        baseline = y + size
        for part in parts:
            baseline = _draw_handwritten_remark(page, x0, baseline, part, max_width=max_w, size=size)
            baseline += size * 1.5
        _record_draw(qid, "feedback", page.number + 1, (x0, y, x1, y + height), score=awarded)
        report.render_manifest[-1]["margin_score"] = show_score
        report.render_manifest[-1]["reason_continuation"] = not show_score
        report.render_manifest[-1]["label_only"] = bool(label_only and not show_score)
        return y + height
    mark_edge = max((row["bbox"][2] for row in getattr(report, "render_manifest", [])
                     if row["page"] == page.number + 1
                     and row["role"] in {"answer", "step"}
                     and row["bbox"][2] > cw), default=cw)
    x0 = max(cw + 12.0, mark_edge + 8.0) + verdict_lane
    x1 = rect.width - 8.0
    max_w = max(60.0, x1 - x0)

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
        head = f"{_label(qid)}"
        score_txt = calc_txt
    else:
        score_txt = f"{_fmt(awarded)}/{_fmt(max_score)}" if max_score else _fmt(awarded)
        head = f"{_label(qid)}  {score_txt}".strip()

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

    part_total = _part_total_head(report, qid) if show_score else ""
    if part_total:
        block_h += head_line_h
    y = _reserve_strip_slot(doc, page.number + 1, y_pref - head_size, block_h, rect.height,
                            forward_only=forward_only)
    if part_total:
        _draw_handwritten_remark(page, x0, y + head_size, part_total,
                                 max_width=max_w, size=head_size)
    top = y
    y = y + head_line_h if part_total else y

    # Objective verdicts live in a dedicated lane inside the widened margin. They
    # remain attached to an explicit QID/score but can never cover the student's
    # answer or be pushed onto a neighbouring MCQ row.
    if verdict == "correct":
        _draw_tick(page, cw + 9.0, y + head_size * 0.70,
                   size=_ms(13), seed=f"{qid}|strip|tick")
    elif verdict == "wrong":
        _draw_cross(page, cw + 16.0, y + head_size * 0.55,
                    size=_cross_size_for_tick(_ms(13)), seed=f"{qid}|strip|cross")

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
    _record_draw(qid, "total", page.number + 1, (x0, top, x1, top + block_h), score=awarded)
    return top + block_h


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
_RULE_THIN_RUN = 0.16    # ...and one this wide is ruling when it is thin (see _erase_long_runs)
_INK_DPI = 150


def _drop_printed_ruling(mask, skew: float | None = None,
                         content_px: int | None = None, bridge=None,
                         centre: tuple[float, float] | None = None) -> float:
    """Clear runs long enough to be the page's printed ruling. In place.

    Returns the tilt the rules were found at, so the release check can erase the
    same rules from its own raster of the same page instead of estimating again
    and, one time in ten, landing a quarter degree away and keeping them.
    """
    plain = mask.copy()
    original = mask.copy()
    if bridge is not None:
        original |= bridge
    _erase_long_runs(mask, original, band=2, pad=1, content_px=content_px, plain=plain)
    angle = _ruling_skew(mask, content_px) if skew is None else float(skew)
    _drop_skewed_ruling(mask, angle, content_px, bridge, centre)
    return angle


def _erase_long_runs(mask, original, pad: int = 0, band: int = 0,
                     content_px: int | None = None, plain=None) -> None:
    """Erase every run at least a third of the SHEET wide. In place.

    `band` lets a run wander up or down that many pixel rows and still count as
    one run - a rule at a fifth of a degree of tilt crosses a pixel row every
    300 pixels, and no single row holds it. Rows are OR-ed together for the
    search; handwriting has no run that long in any few rows either. Gaps of a
    few pixels are closed first: a faint rule thresholds to dashes.

    `content_px` is the sheet's width in this raster. The raster also holds the
    blank margin strip beside the sheet, and a rule two-thirds of the way across
    the sheet is well under a third of the whole raster. `plain` is the student's
    ink alone, without the red bridge pixels `original` may carry: the "blank rows
    above and below" and "dark band" tests must not see our own tick beside a
    rule and keep the rule for it (849bbe95 page 1, Q6.d, 2026-09-16: erased on
    the unmarked sheet, kept on the finished copy).
    """
    import numpy as np

    plain = original if plain is None else plain
    height, width = mask.shape
    sheet_w = int(content_px) if content_px and 0 < content_px <= width else width
    limit = max(24, int(sheet_w * _RULE_MIN_RUN))
    search = original
    if band > 0:
        search = original.copy()
        for shift in range(1, band + 1):
            search[shift:] |= original[:-shift]
            search[:-shift] |= original[shift:]
    try:
        import cv2
        reach = max(2, sheet_w // 400)
        closed = cv2.morphologyEx(search.astype(np.uint8), cv2.MORPH_CLOSE,
                                  np.ones((1, 2 * reach + 1), np.uint8))
        search = closed.astype(bool)
    except ImportError:  # pragma: no cover - cv2 is in requirements
        pass
    coverage = search[:, :sheet_w].mean(axis=1)
    # A faint or dashed rule never reaches a third of the sheet in one run. It is
    # still a rule when the run is a sixth of the sheet AND thin: blank rows a few
    # pixels above and below it. Handwriting is never that thin, an underline is
    # and may go; a filled cell or a scanner border fails the blank-rows test.
    thin_limit = max(24, int(sheet_w * _RULE_THIN_RUN))
    for y in np.flatnonzero(coverage >= _RULE_THIN_RUN):
        row = search[y]
        edges = np.flatnonzero(np.diff(np.concatenate(
            ([0], row.astype(np.int8), [0]))))
        for start, end in zip(edges[0::2], edges[1::2]):
            length = end - start
            if length < thin_limit:
                continue
            # Scanner borders and filled cells also form long runs. A dark band
            # that persists several pixels vertically is not a notebook rule and
            # must remain unavailable for score placement.
            if any(0 <= yy < height and plain[yy, start:end].mean() > .75
                   for yy in (y - 6, y + 6)):
                continue
            if length < limit:
                above = plain[max(0, y - band - 4):max(0, y - band - 1), start:end]
                below = plain[min(height, y + band + 2):min(height, y + band + 5), start:end]
                if ((above.size and above.mean() > .15) or (below.size and below.mean() > .15)):
                    continue
            mask[max(0, y - pad - band):y + pad + band + 1, start:end] = False


def _ruling_skew(mask, content_px: int | None = None) -> float:
    """The tilt, in degrees, that lines this photo's printed rules up with pixel rows.

    Tried in quarter-degree steps either side of level on a reduced mask of the
    sheet itself (`content_px` columns; the blank margin strip beside it would
    only dilute every row). The score is the sum of squared row coverages: a rule
    lying along a pixel row scores as one dark row, a rule crossing twenty rows as
    twenty faint ones, so the tilt that straightens the rules wins clearly, and
    a level page wins at zero. Between near-equal tilts the smaller one is kept.
    Zero when nothing on the page lines up (an unruled sheet).
    """
    import cv2
    import numpy as np

    sheet = mask[:, :content_px] if content_px else mask
    height, width = sheet.shape
    shrink = max(1.0, max(height, width) / 2000.0)
    w, h = max(8, int(width / shrink)), max(8, int(height / shrink))
    small = (cv2.resize(sheet.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_AREA) > 40)
    small = small.astype(np.uint8)
    centre = (w / 2.0, h / 2.0)
    reach = max(2, w // 400)
    closing = np.ones((1, 2 * reach + 1), np.uint8)
    limit = max(24, int(w * _RULE_MIN_RUN))

    def erasable_rows(turned) -> int:
        """Rows the run test would erase at this tilt: the same closing and
        two-row OR-band as _erase_long_runs, so the estimate optimises exactly
        what the eraser can then take out."""
        band = turned.copy()
        band[1:] |= turned[:-1]
        band[:-1] |= turned[1:]
        band[2:] |= turned[:-2]
        band[:-2] |= turned[2:]
        band = cv2.morphologyEx(band, cv2.MORPH_CLOSE, closing)
        count = 0
        # Full-width runs only. MEASURED 2026-09-16 (139e179a page 3): counting the
        # thin tier here too let tilted handwriting outvote the rules, and the
        # estimate went to +2 degrees on a level page.
        for y in np.flatnonzero(band.mean(axis=1) >= _RULE_MIN_RUN):
            row = band[y]
            edges = np.flatnonzero(np.diff(np.concatenate(([0], row, [0]))))
            if any(end - start >= limit for start, end in zip(edges[0::2], edges[1::2])):
                count += 1
        return count

    scored = []
    for step in range(-16, 17):
        angle = step * 0.25
        turned = small if step == 0 else cv2.warpAffine(
            small, cv2.getRotationMatrix2D(centre, angle, 1.0), (w, h),
            flags=cv2.INTER_NEAREST, borderValue=0)
        scored.append((erasable_rows(turned), angle))
    best = max(count for count, _ in scored)
    if best < 3:
        return 0.0
    close = [angle for count, angle in scored if count >= best]
    return min(close, key=abs)


def _drop_skewed_ruling(mask, angle: float, content_px: int | None = None,
                        bridge=None, centre: tuple[float, float] | None = None) -> None:
    """Erase printed rules that a tilted photo breaks into short runs. In place.

    A phone photo is rarely square to the page. At one degree of tilt a rule
    crosses a pixel row every 57 pixels, so no single row holds a run a third of
    the page wide and the row test keeps none of it. MEASURED 2026-09-16 on the
    SST review batch: the ink mask of a tilted ruled notebook was dashed rules
    edge to edge, every circle candidate on every row probed "ink" underneath,
    and the copy went out with almost no circled marks on it - "missing number".

    So the mask is turned level, the same row test runs on it, and what that
    erased is turned back. Dark bands (a filled cell, a scanner border) still
    stay, by the same test; a row of separate pen strokes is never a rule
    because no tilt makes it one continuous run.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - cv2 is in requirements
        return
    if abs(angle) < 0.2:
        return
    height, width = mask.shape
    # About the sheet's centre, not the raster's. MEASURED 2026-09-16 (849bbe95
    # page 1, Q6.d): turned about the raster centre, a rule near the erase
    # threshold moved a few columns differently under the cover strip than on
    # the sheet alone, was erased in one raster and kept in the other, and the
    # release check reported a numeral on writing the renderer had found clear.
    centre = tuple(float(v) for v in centre) if centre else (width / 2.0, height / 2.0)
    forward = cv2.getRotationMatrix2D(centre, angle, 1.0)
    backward = cv2.getRotationMatrix2D(centre, -angle, 1.0)
    upright = cv2.warpAffine(mask.astype(np.uint8), forward, (width, height),
                             flags=cv2.INTER_NEAREST, borderValue=0).astype(bool)
    plain = upright.copy()
    before = upright.copy()
    if bridge is not None:
        before |= cv2.warpAffine(bridge.astype(np.uint8), forward, (width, height),
                                 flags=cv2.INTER_NEAREST, borderValue=0).astype(bool)
    _erase_long_runs(upright, before, pad=1, band=2, content_px=content_px, plain=plain)
    erased = cv2.dilate((before & ~upright).astype(np.uint8), np.ones((3, 3), np.uint8))
    back = cv2.warpAffine(erased, backward, (width, height),
                          flags=cv2.INTER_NEAREST, borderValue=0)
    mask &= ~back.astype(bool)


# The tilt each page's ruling was erased at, by (document id, page index), so the
# copy can carry it to the release check (see render_page_layouts["ruling_skew"]).
_RULING_SKEW: dict[tuple[int, int], float] = {}


def student_ink_mask(page, dpi: int = _INK_DPI, *, skew: float | None = None,
                     content_w: float | None = None, content_top: float = 0.0):
    """(mask, scale) — True where the student's own pen is, in page pixels.

    `scale` converts PDF points to those pixels. Returns (None, 1.0) when the page
    cannot be rasterised, so every caller falls back to its previous behaviour
    rather than reporting a page as blank. `skew` is the ruling tilt to erase at
    when it is already known (the release check, reading it back from the copy);
    `content_w` is the sheet's width in points, so the tilt is estimated on the
    sheet and not on the blank margin strip beside it.
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
        # A shaded notebook can have blank paper darker than 150. Treating that
        # entire background as ink leaves nowhere to put a score. Require local
        # contrast for mid-tones, retaining very dark strokes and filled regions.
        from PIL import Image, ImageFilter
        gray = Image.fromarray(arr[:, :, :3]).convert("L")
        luminance = np.asarray(gray, dtype=np.int16)
        background = np.asarray(gray.filter(ImageFilter.GaussianBlur(
            radius=max(2, dpi / 12))), dtype=np.int16)
        foreground = (luminance < 70) | (luminance < background - 12)
        mask = (r < 150) & (g < 150) & (b < 150) & foreground & ~red
        content_px = int(content_w * dpi / 72.0) if content_w else None
        # Our own red marks are not ink, but they cross the printed rules. On the
        # finished copy a circle crossing a rule cut it into two runs each just
        # under a third of the sheet, the rule was kept as "ink", and the numeral
        # the renderer had placed on clear paper was reported as covering writing.
        # For the ruling search, red counts as "could be rule underneath".
        # `content_top` is where the sheet starts on this page (the cover strip's
        # height on page 1 of a finished copy): the de-skew turns about the SHEET's
        # centre, so the same rule lands on the same pixels whether the sheet is
        # rasterised alone or under the strip.
        top_px = content_top * dpi / 72.0
        page_rect = getattr(page, "rect", None)
        sheet_h = ((page_rect.height * dpi / 72.0) if page_rect is not None else mask.shape[0]) - top_px
        centre = ((content_px or mask.shape[1]) / 2.0, top_px + sheet_h / 2.0)
        angle = _drop_printed_ruling(mask, skew=skew, content_px=content_px, bridge=red,
                                     centre=centre)
        try:
            _RULING_SKEW[(id(page.parent), page.number)] = angle
        except Exception:  # noqa: BLE001 - a page with no parent document
            pass
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
        _insert_text(page, (x, y), text, fontsize=size, color=color, fontname=fontname)
        return y + size * 1.2

    # naive word wrap
    words = text.split()
    line = ""
    line_height = size * 1.25

    def width(s: str) -> float:
        return _text_width(s, size, lambda t: fitz.get_text_length(t, fontname=fontname,
                                                                   fontsize=size))
    for w in words:
        trial = (line + " " + w).strip()
        # estimate width
        if width(trial) > max_width:
            _insert_text(page, (x, y), line, fontsize=size, color=color, fontname=fontname)
            y += line_height
            line = w
        else:
            line = trial
    if line:
        _insert_text(page, (x, y), line, fontsize=size, color=color, fontname=fontname)
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
        if not sentence.endswith(('.', '?', '!', ']', '।', '॥')):
            sentence = sentence + '.'

        # Word wrap each sentence
        words = sentence.split()
        lines_for_sentence = []
        current_line = ""

        for w in words:
            trial = (current_line + " " + w).strip()
            # estimate width (accounting for bullet space)
            if _text_width(trial, size, lambda t: fitz.get_text_length(
                    t, fontname=fontname, fontsize=size)) > max_width - indent - 5:
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
                _insert_text(page, (bullet_indent, y), line, fontsize=size, color=color,
                             fontname=fontname)
            else:
                # Continuation lines are indented to match first line
                _insert_text(page, (bullet_indent, y), line, fontsize=size, color=color,
                             fontname=fontname)
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

# Per-document, per-page boxes of the printed characters, for the "is there text under
# this circle" test, keyed and cleared like _MARK_INK. page.get_textbox() read the
# whole page's text again for every candidate spot: MEASURED 51 ms a probe on a typed
# spreadsheet page, and a mark with no clear paper tries several thousand spots
# (every size, gap and row offset), so a PW Skills copy linking a Google Sheet (12 pages
# of figures) sat in mark placement for over 25 minutes on 13 Nov 2026. The boxes are
# read again only when the page's content or annotations change, so text this build
# writes on the page still counts, exactly as it did through get_textbox().
_PAGE_TEXT: dict[int, dict[int, tuple]] = {}


def _page_text_boxes(page: "fitz.Page"):
    """The page's printed, non-blank characters as an (n, 4) array of boxes, read again
    only when the page's content or annotations changed. Asking costs a look at the
    page's content list (about 0.7 ms on a page with hundreds of streams), so a caller
    probing many spots with nothing drawn in between asks once and reuses the array."""
    import numpy as np
    key = (tuple(page.get_contents()), tuple(page.annot_xrefs()))
    pages = _PAGE_TEXT.setdefault(id(page.parent), {})
    cached = pages.get(page.number)
    if cached is None or cached[0] != key:
        boxes = [char["bbox"] for block in page.get_text("rawdict")["blocks"]
                 for line in block.get("lines", ()) for span in line.get("spans", ())
                 for char in span.get("chars", ()) if str(char.get("c", "")).strip()]
        cached = (key, np.asarray(boxes, dtype=float).reshape(-1, 4))
        pages[page.number] = cached
    return cached[1]


def _boxes_touch(boxes, rect) -> bool:
    """Whether any of the boxes overlaps rect (sharing only an edge is not overlap)."""
    import numpy as np
    if not len(boxes):
        return False
    x0, y0, x1, y1 = fitz.Rect(rect)
    return bool(np.any((boxes[:, 0] < x1) & (boxes[:, 2] > x0) & (boxes[:, 1] < y1) & (boxes[:, 3] > y0)))


def _text_under(page: "fitz.Page", rect: "fitz.Rect") -> bool:
    """Whether any printed, non-blank character on the page touches rect: the answer
    `page.get_textbox(rect).strip()` gave, for one text read per page version."""
    return _boxes_touch(_page_text_boxes(page), rect)

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
            reg[page_no] = student_ink_mask(doc[page_no - 1],
                                            content_w=_content_width(doc[page_no - 1]))
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


def _is_objective_mark(m) -> bool:
    """Whether this question is an objective (MCQ / true-false) item.

    Shared so the anchor pass and the marking pass cannot disagree: they did, and an
    MCQ was anchored as prose and then marked as objective.
    """
    return (
        bool(getattr(m, "objective", False))
        or bool(str(getattr(m, "correct_option", "") or "").strip())
        or bool(str(getattr(m, "chosen_option", "") or "").strip())
    )



_ROW_NUMBER_RE = re.compile(r"^\s*\(?\s*(\d{1,3})\s*[).:\]\-]")
# "Q. 4 (A) Primary Data", "Ques: -2-(D)", "- No -1:-(A)": a row the student
# labelled with Q, Ques, No or Ans and its number.
_Q_ROW_NUMBER_RE = re.compile(
    r"^\W*(?:(?:Q(?:ue(?:s(?:tion)?)?)?|[A-Z]ues?|No|Ans)\W{0,4}){1,2}(\d{1,3})(?!\d)", re.I)


def _row_number(text: str) -> int | None:
    match = _ROW_NUMBER_RE.match(str(text or "")) or _Q_ROW_NUMBER_RE.match(str(text or ""))
    return int(match.group(1)) if match else None


def _objective_item_number(qid: str) -> int:
    """The number a student writes beside an MCQ: 4 for "Q4", and 4 for "Q1.4" too,
    where the paper numbers its MCQs (i) to (x) inside question 1. Uday Bihar Board
    Economics (10 Oct 2026): Q1.1 to Q1.10 all counted as "1", so all ten took the
    first row of the MCQ table and nine copies' ticks were never drawn."""
    item = re.fullmatch(r"\s*[Qq]?\s*\d+\.(\d+)\s*", qid or "")
    return int(item.group(1)) if item else _objective_lane_key(qid)[0]


def _page_row_pitch(by_lid: dict | None, line: dict) -> float:
    """The row pitch on THIS page, from its other OCR lines; 0 when unknown.

    The booklet median (_DOC_ROW_PITCH) can be a page of dense prose while the
    MCQ page is double spaced: on d365ca25 the median was 36pt and the MCQ rows
    were 66pt apart, so rows cut at the median fell between the answers.
    """
    if not by_lid:
        return 0.0
    centres = sorted((other["y0"] + other["y1"]) / 2 for other in by_lid.values()
                     if other.get("page") == line.get("page") and other.get("lid") != line.get("lid"))
    gaps = sorted(b - a for a, b in zip(centres, centres[1:]) if 8.0 <= b - a <= 300.0)
    return gaps[len(gaps) // 2] if len(gaps) >= 3 else 0.0


def _ink_row_bands(doc, line: dict, count: int) -> list[tuple[float, float]] | None:
    """The first `count` written rows inside a merged OCR block, from its own ink.

    The block's rows are read from the block itself, not from the page's other
    OCR lines: on the live 1b51d418 the array named five rows while its box ran
    on over the five fill-in rows below (eleven rows in 437pt), and the page's
    other boxes each wrapped two rows, so any pitch taken from them was double
    the truth and an even split put Q2's mark two rows down.

    Ink runs a few pixels apart are one row (the dot of an i, a rule remnant);
    runs further apart than a third of a row's own ink height are separate rows.
    The rows are trusted when they stack regularly (each spacing within 0.6 to
    1.5 of the median); the first `count` of them are the named rows. Anything
    else (fragmented ink, fewer bands than rows) returns None and the caller
    splits the block evenly.
    """
    mask, scale = _page_ink(doc, line["page"])
    if mask is None:
        return None
    import numpy as np
    height, width = mask.shape
    x0, x1 = max(0, int(line["x0"] * scale)), min(width, int(line["x1"] * scale) + 1)
    y0, y1 = max(0, int(line["y0"] * scale)), min(height, int(line["y1"] * scale) + 1)
    if x1 - x0 < 4 or y1 - y0 < count * 4:
        return None
    profile = mask[y0:y1, x0:x1].mean(axis=1)
    inked = list(profile > 0.02) + [False]
    bands: list[list[int]] = []
    begin = None
    for i, on in enumerate(inked):
        if on and begin is None:
            begin = i
        elif not on and begin is not None:
            bands.append([begin, i])
            begin = None
    tiny = max(2, int(3.0 * scale))
    merged: list[list[int]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= tiny:
            merged[-1][1] = band[1]
        else:
            merged.append(band)
    merged = [band for band in merged if band[1] - band[0] >= 3]
    if not merged:
        return None
    heights = sorted(b - a for a, b in merged)
    typical = heights[len(heights) // 2]
    rows: list[list[int]] = []
    for band in merged:
        if rows and band[0] - rows[-1][1] < 0.34 * typical:
            rows[-1][1] = band[1]
        else:
            rows.append(band)
    if len(rows) < count:
        return None
    starts = [a for a, _ in rows]
    spacings = [b - a for a, b in zip(starts, starts[1:])]
    if spacings:
        median = sorted(spacings)[len(spacings) // 2]
        if median <= 0 or any(not (0.6 * median <= gap <= 1.5 * median) for gap in spacings):
            return None
    rows = rows[:count]
    pad = max(1.0, 0.08 * typical / scale)
    return [((y0 + a) / scale - pad, (y0 + b) / scale + pad) for a, b in rows]


def _array_rows(doc, line: dict, by_lid: dict | None = None) -> list[dict]:
    """Split an OCR line that is really a stack of rows into one line per row.

    A numbered MCQ list comes back from Mathpix as ONE line holding a LaTeX array,
    "1) (B) \\ 2) (C) \\ ...", or as one line with newlines, with a
    bounding box several rows tall. Five questions then anchor to that one block
    and every one of their marks lands beside its middle row (a live copy: Q1's
    tick beside "3) (B)", Q4's cross in the margin of the same row, and nothing at
    all beside rows 1, 2, 4 and 5). The rows keep the block's line ID - it is the
    block the report cites - and take their own vertical extent from the ink, or
    from an even split of the block, or, when the block runs on past the named
    rows (rows taller than 1.6 pitches), one page row pitch each from the top.
    """
    text = str(line.get("text") or "")
    if "\\\\" not in text and "\n" not in text.strip():
        return []
    from mathpix import readable_ocr_text
    # A tabular is a stack of rows too: its \begin line and its \hline rules are not
    # rows (Uday Bihar Board Economics: "\begin{tabular}" became row 0 of ten MCQs).
    body = re.sub(r"\\begin\{(?:array|tabular)\}(?:\[[^\]]*\])?\{[^}]*\}"
                  r"|\\end\{(?:array|tabular)\}|\\hline|\\\[|\\\]", " ", text)
    parts = [readable_ocr_text(part) for part in re.split(r"\\\\|\n", body)]
    parts = [part for part in parts if part]
    # A label word OCR set before the array ("\text { Ques } \begin{array} - No -1:-(A)
    # \\ Ques: -2-(D) ...") is the start of the first row, not a row of its own.
    # Economics f5d29451 (12 Oct 2026): counted as a row, it moved every tick of
    # Q1 to Q5 down one row, Q1 had none and Q3 had two.
    if (len(parts) >= 2 and not re.search(r"\d", parts[0]) and len(parts[0].split()) <= 2
            and not re.search(r"\(\s*[A-Da-d]\s*\)", parts[0])):
        parts = [f"{parts[0]} {parts[1]}"] + parts[2:]
    if len(parts) < 2:
        return []
    count = len(parts)
    block_h = line["y1"] - line["y0"]
    bands = _ink_row_bands(doc, line, count)
    if bands is None:
        row_h = block_h / count
        bands = [(line["y0"] + i * row_h, line["y0"] + (i + 1) * row_h) for i in range(count)]
    bands = _tighten_rows(doc, line, bands)
    rows = []
    for i, part in enumerate(parts):
        y0, y1 = bands[i]
        rows.append({**line, "text": part, "y0": y0, "y1": y1, "_array_row": i,
                     "_row_label": _row_number(part)})
    return rows


def _tighten_rows(doc, line: dict, slots: list) -> list:
    """Shrink each row slot to the ink it holds, so a row is as tall as its writing.

    An even split of a double-spaced block gives slots of 86pt for 40pt of
    handwriting; a layout that sizes marks by the booklet's row pitch then takes
    such a slot for a multi-row block and marks its bottom, below the answer.
    A slot with no ink keeps its full extent.
    """
    mask, scale = _page_ink(doc, line["page"])
    if mask is None:
        return slots
    import numpy as np
    height, width = mask.shape
    x0, x1 = max(0, int(line["x0"] * scale)), min(width, int(line["x1"] * scale) + 1)
    if x1 - x0 < 4:
        return slots
    out = []
    for y0, y1 in slots:
        a, b = max(0, int(y0 * scale)), min(height, int(y1 * scale) + 1)
        if b - a < 4:
            out.append((y0, y1))
            continue
        inked = np.flatnonzero(mask[a:b, x0:x1].mean(axis=1) > 0.03)
        if len(inked) < 3 or (inked[-1] - inked[0]) / scale < 6.0:
            out.append((y0, y1))
            continue
        pad = 2.0
        out.append((max(y0, (a + inked[0]) / scale - pad), min(y1, (a + inked[-1] + 1) / scale + pad)))
    return out


def _objective_row(doc, m, own: dict | None, by_lid: dict) -> dict | None:
    """The row of a merged MCQ block that carries THIS question's number."""
    report = _RENDER_REPORT.get()
    if report is not None:
        from evidence_repair import _independently_located_objective
        if _independently_located_objective(report, m):
            return None  # The actual answer cell was already located visually.
    number = _objective_item_number(str(getattr(m, "qid", "") or ""))
    if number >= 9999:
        return None
    if own is not None:
        pool = [own]
    else:
        page_hint = int(getattr(m, "page", 0) or 0)
        pool = [line for line in by_lid.values() if line["page"] == page_hint]
    for line in pool:
        rows = _array_rows(doc, line, by_lid)
        sharing: list[int] = []
        if line is own and len(rows) >= 2:
            # The questions anchored to this block, in question order. A question
            # already moved onto one of the block's rows still counts as sharing.
            report = _RENDER_REPORT.get()
            sharing = sorted(_objective_item_number(str(q.qid))
                             for q in (report.questions if report else [])
                             if _is_objective_mark(q)
                             and (getattr(q, "anchor_line_id", "") or "").strip().split("#r")[0] == own["lid"])
        # One row for each question that cites the block: the rows are those
        # questions in the order the student wrote them, whatever OCR made of their
        # numbers. Uday Bihar Board Economics 99186fe1 (10 Oct 2026): ten MCQ rows
        # read "Q. 1", "Q. 2.", "A. 8.", "Q. 4.", "B. S.", "18.6.", "Q. 7", "D. 8",
        # "8. 9", "Q. 10"; by label, Q8 would have taken Q9's row.
        if sharing and len(sharing) == len(rows) and len(set(sharing)) == len(sharing):
            return rows[sharing.index(number)] if number in sharing else None
        # One row for each MCQ of the paper's list (1 to 10, numbered without a gap),
        # though one of them was anchored elsewhere: row k is MCQ k. Economics
        # 99186fe1 (10 Oct 2026): Q1 sat on its own line, the other nine shared the
        # ten-row table, and Q6, whose row OCR read "18.6.", was never placed.
        if line is own and len(rows) >= 2:
            listed = sorted(_objective_item_number(str(q.qid))
                            for q in (report.questions if report else [])
                            if _is_objective_mark(q) and getattr(q, "attempted", True))
            if (len(listed) == len(rows) and len(set(listed)) == len(listed)
                    and listed == list(range(listed[0], listed[0] + len(listed)))
                    and number in listed):
                return rows[listed.index(number)]
        for row in rows:
            if row.get("_row_label") == number:
                return row
        if sharing and all(row.get("_row_label") is None for row in rows):
            # No row carries a number OCR could read ("Quiz B) Prakrit" for "Que 2
            # B) Prakrit"): the questions take the rows in question order.
            if number in sharing and sharing.index(number) < len(rows):
                return rows[sharing.index(number)]
    return None


def _draw_mcq_verdict(doc, page, qid, anchor, by_lid, awarded, maximum, tick_size, seed):
    """Circle earned marks beside the answer; a zero award uses a cross alone."""
    from preflight import _ink_under_mark, _MARK_INK_LIMIT
    page_no = page.number + 1
    text = f"{awarded:g}"
    line_h = max(14.0, anchor["y1"] - anchor["y0"])
    y = (anchor["y0"] + anchor["y1"]) / 2
    content_w = _content_width(page)
    font = fitz.Font("hebo")
    render_report = _RENDER_REPORT.get()
    strict = bool(render_report is not None and render_report.evidence_contract)
    mask, scale = _page_ink(doc, page_no)
    # One numeral size for the copy, from its handwriting rather than from whatever
    # height OCR returned for this one line; the A4 sizes stay as the fallback.
    grow = _doc_mark_scale()
    reference = _DOC_LINE_H.get() or line_h
    ladder = [1.0, .85, .7]
    if grow > 1.01:
        ladder += [1.0 / grow, .85 / grow, .7 / grow]
    # The answers written just above and below this one, across the same part of the
    # page. MEASURED 2026-10-14 (Vidyapeeth Class 8 Maths, 36 copies): MCQ rows 20pt
    # apart, marks 25pt tall and free to slide 8pt off their row, so on five of the
    # seven released copies a tick or a circled 0 read as the next answer's. In such
    # a block the mark is sized to the row and kept on it.
    pitch = min((abs((line["y0"] + line["y1"]) / 2 - y) for line in by_lid.values()
                 if line["page"] == page_no and line["lid"] != anchor["lid"]
                 and min(line["x1"], anchor["x1"]) - max(line["x0"], anchor["x0"]) > 0
                 and abs((line["y0"] + line["y1"]) / 2 - y) > 4), default=0.0)
    dense = 0 < pitch < 40
    if dense:
        ladder += [.6, .5]
    # A wrong answer whose question needs its circled total gets the 0 beside its
    # cross here, as the approved copies show it. Left to the total pass, the 0 went
    # wherever there was room, often under the row, beside the next answer.
    zero_circle = render_report is not None and any(
        r.get("qid") == qid and r.get("role") == "total"
        for r in render_report.render_requirements)
    for shrink in ladder:
        size = max(_TICK_MIN_SIZE, tick_size * shrink)
        k = grow * shrink
        fs = max(_ms(9), min(_ms(16), reference * .48)) * k
        text_w = font.text_length(text, fontsize=fs)
        text_h = (font.ascender - font.descender) * fs
        radius = max(text_w / 2 + 3 * k, text_h / 2 + 2 * k, 8 * k)
        full = maximum > 0 and awarded >= maximum - 1e-6
        wrong = awarded <= 1e-6
        circled = not wrong or zero_circle
        glyph_w = _cross_size_for_tick(size) if wrong else size * 1.5
        gap = 6 if glyph_w and not wrong else (3 if circled else 0)
        height = max(2 * radius if circled else 0.0, glyph_w if wrong else size * 1.1)
        if dense and height > .92 * pitch and shrink != ladder[-1]:
            continue
        # MCQ columns can share a row. The next column is another answer, not
        # a continuation of this option; only use this answer's own endpoint.
        right = anchor["x1"] + 8
        from presentation_layout import horizontal_reach
        reach = horizontal_reach([anchor["x0"], anchor["y0"], anchor["x1"], anchor["y1"]])
        starts = [right + offset for offset in (0, 4, 8, 12, 20, 28, 36, 48, 64, 80)
                  if offset + 8 <= reach - 2]
        # A full-width answer can use the immediately adjacent added margin.
        # Never send its score hundreds of points away into the summary column.
        if not strict and content_w - anchor["x1"] < 48:
            starts.append(content_w + 4)
        offsets = [0, -min(4, line_h * .2), min(4, line_h * .2),
                   -min(8, line_h * .4), min(8, line_h * .4)]
        if dense:
            room = max(1.0, (.92 * pitch - height) / 2)
            offsets = [dy for dy in offsets if abs(dy) <= room]
        # In a tight two-column row, use the corner beside this answer rather
        # than crossing the next answer or moving to the left of the question.
        next_columns = [line["x0"] for line in by_lid.values()
                        if line["page"] == page_no and line["lid"] != anchor["lid"]
                        and line["x0"] > anchor["x1"] and line["y0"] <= y <= line["y1"]]
        if next_columns:
            starts = [x for x in starts if x < min(next_columns)]
            # Just clear of the row, by the height of the glyph this pass draws.
            # Just clear of the row and still within the reach the release check
            # allows a mark from its evidence; a glyph too tall for that shrinks.
            # Not in a dense block: there, off the row is on the next answer.
            if not dense:
                offsets.extend([anchor["y1"] + 9.8 - y, anchor["y0"] - 9.8 - y])
        for x in starts:
            for dy in offsets:
                cy = y + dy
                cx = x + glyph_w + gap + radius
                numeral = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius) if circled else None
                # The path, for deciding what the mark touches; the path plus the
                # pen, for the record the release check reads the strokes back from.
                if not wrong:
                    tight = _tick_path_box(x, cy + .6 * size, size)
                    glyph = tight
                elif wrong:
                    tight = glyph = _cross_box(x + glyph_w / 2, cy, glyph_w)
                else:
                    tight = glyph = +numeral
                cluster = glyph | numeral if numeral is not None else glyph
                touching = tight | numeral if numeral is not None else tight
                allowed = (fitz.Rect(0, 0, min(content_w, _mark_area_right(page)), page.rect.height)
                           if strict else page.rect)
                if not allowed.contains(cluster) or cluster.x0 < 2:
                    continue
                # Marks must not overlap each other, but a 2pt cordon around every
                # one of them is wider than the gap an MCQ block leaves between its
                # rows (22pt rows, ~18pt marks), so each award knocked the next one
                # out of the column. Touching is the limit; overlapping is not.
                if any(cluster.intersects(box) for box in _MARK_BOXES.get(page_no, [])):
                    continue
                # In a dense block the OCR boxes of neighbouring rows overlap this row
                # by a few points (Q8 281-304pt, Q9 300-326pt), so a mark centred on its
                # own row grazes the next one everywhere. Grazing is not sitting on it.
                if any(line["page"] == page_no and line["lid"] != anchor["lid"]
                       and touching.intersects(fitz.Rect(line["x0"], line["y0"], line["x1"], line["y1"]))
                       and (not dense or min(touching.y1, line["y1"]) - max(touching.y0, line["y0"])
                            > .25 * touching.height)
                       for line in by_lid.values()):
                    continue
                if numeral is not None and numeral.intersects(fitz.Rect(anchor["x0"], anchor["y0"], anchor["x1"], anchor["y1"])):
                    continue
                if numeral is not None and mask is not None:
                    # Adding the report header changes raster rounding by a
                    # fraction of a point. Require clearance at those offsets
                    # too, using the same ink limit as the finished-PDF check.
                    if any(_ink_under_mark(mask, scale,
                               tuple(numeral + (dx, dy, dx, dy))) >= _MARK_INK_LIMIT
                           for dx, dy2 in ((0, 0), (-.5, 0), (.5, 0), (0, -.5), (0, .5))):
                        continue
                if not wrong:
                    _draw_tick(page, x, cy + .6 * size, size=size, seed=seed)
                elif wrong:
                    _draw_cross(page, x + glyph_w / 2, cy, size=glyph_w, seed=seed)
                if numeral is not None:
                    page.draw_circle((cx, cy), radius, color=RED, width=1.2)
                    page.insert_text((cx - text_w / 2, cy - text_h / 2 + font.ascender * fs),
                                     text, fontname="hebo", fontsize=fs, color=RED)
                _note_mark(page_no, cluster)
                _record_draw(qid, "answer", page_no, cluster, line=anchor,
                             score=awarded, numeral_bbox=numeral)
                report = _RENDER_REPORT.get()
                if report is not None and report.evidence_contract:
                    if anchor.get("_row_label") is not None:
                        report.render_manifest[-1]["array_row_label"] = anchor["_row_label"]
                    report.render_manifest[-1]["score_text"] = text if circled else ""
                    report.render_manifest[-1]["score_style"] = (
                        "cross_and_zero" if wrong and circled else
                        "cross_only" if wrong else "circled_award")
                    report.render_manifest[-1]["question_total"] = circled
                    report.render_manifest[-1].update(
                        verdict='wrong' if wrong else ('correct' if full else 'partial'), verdict_bbox=list(glyph))
                return True
    # An answer written to the edge of the page leaves no room after its first line.
    # MEASURED on a Parishram Geography copy (8 Oct 2026): Q7 "Both statement I and
    # statement II are / correct." got its tick but no circled 1, so the margin said
    # 1/1 over a page showing no number. The answer's own next line ("correct.") has
    # room: mark the answer where it ends, as a teacher does.
    if not anchor.get("_continued"):
        following = _answer_continuation_row(anchor, by_lid)
        if following is not None and _draw_mcq_verdict(
                doc, page, qid, {**following, "_continued": True}, by_lid,
                awarded, maximum, tick_size, seed):
            # The answer does run on to that line: say so in the region the release
            # check reads, so the mark is not reported as outside the answer.
            if render_report is not None:
                region = render_report.answer_regions.setdefault(normalize_qid(qid), {})
                lids = list(region.get("line_ids") or [])
                if anchor["lid"] not in lids:
                    lids.append(anchor["lid"])
                if following["lid"] not in lids:
                    lids.append(following["lid"])
                region["line_ids"] = lids
            return True
    if strict:
        verdict = 'wrong' if awarded <= 1e-6 else ('correct' if awarded >= maximum - 1e-6 else 'partial')
        _draw_sheet_verdict(doc, qid, anchor, verdict, score=awarded)
        if _draw_sheet_score(doc, qid, anchor, awarded, role="answer",
                             cross=awarded <= 1e-6, total=awarded > 1e-6):
            return True
    _note_unanchored(qid, 1)
    return False


_ROW_LABEL_START_RE = re.compile(r"^\s*(?:q(?:ue)?(?:stion)?\.?\s*|ans(?:wer)?\.?\s*|[a-z]\s*)?\d{1,3}\s*[.):\-]",
                                 re.IGNORECASE)


def _answer_continuation_row(anchor: dict, by_lid: dict) -> dict | None:
    """The line right under `anchor` that carries on the same answer, or None.

    Only the next row of writing, starting no further left than the answer's own
    text, and never a line that opens with a question number of its own."""
    height = max(14.0, anchor["y1"] - anchor["y0"])
    below = [line for line in by_lid.values()
             if line["page"] == anchor["page"] and line["lid"] != anchor["lid"]
             and not _DIAG_REGION_RE.match(str(line.get("lid", "")))
             and line["y0"] >= anchor["y0"] + .5 * height
             and line["y0"] - anchor["y1"] <= 1.2 * height
             and line["x0"] >= anchor["x0"] - 10]
    if not below:
        return None
    nxt = min(below, key=lambda line: line["y0"])
    if _ROW_LABEL_START_RE.match(str(nxt.get("text") or "")):
        return None
    # Nor the next answer of a list: another question's own row, or a row opening with
    # its own part letter or number ("(C) (4) 1.46", "Q3 (1)", "2 (1)", "B - 1"). CA FM
    # b9cfb6d3 (22 Oct 2026): six of eight MCQ marks went one row down, each onto the
    # next question's answer, whenever their own row had no room.
    if _OWN_ROW_LABEL_RE.match(str(nxt.get("text") or "")):
        return None
    # Nor a heading: state-board copies of 22 Oct 2026 carried MCQ marks on the
    # "Section B" / "Section 2" line under the last MCQ of a section.
    if _is_header(str(nxt.get("text") or "")):
        return None
    report = _RENDER_REPORT.get()
    own = str(anchor.get("lid") or "").split("#r")[0]
    if any((getattr(q, "anchor_line_id", "") or "").strip().split("#r")[0] == nxt["lid"].split("#r")[0] != own
           for q in (getattr(report, "questions", None) or [])):
        return None
    return nxt


_OWN_ROW_LABEL_RE = re.compile(
    r"^\W{0,3}(?:(?:q(?:ue)?(?:stion)?|ans(?:wer)?|sol(?:ution)?)\.?\s*(?:no\.?\s*)?\d{1,3}\b"
    r"|\d{1,3}\s*[.):\-(]"
    r"|\(?\s*[a-h]\s*[)=\-:]|\(\s*[a-h]\s*\)"
    r"|(?:section|part)\s*[-–—:.]?\s*(?:[a-e]|[ivx]{1,4}|\d{1,2})\b)", re.IGNORECASE)


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
    is_obj = _is_objective_mark(m)
    render_report = _RENDER_REPORT.get()
    strict = bool(render_report is not None and render_report.evidence_contract)
    safe_layout = _SAFE_LAYOUT.get() and not strict

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
    if not strict:
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
    if is_obj:
        # An MCQ answer is ONE line - the one carrying the chosen option - and the
        # band heuristics above are built for prose that runs many lines. Trust the
        # question's own anchor and stop there. Two ways the band gets this wrong:
        #   * it comes back EMPTY, because "Q.1 (D) 05W" reads as a question header
        #     and q_anchor snapped past it onto the NEXT question's line;
        #   * it OVER-RUNS into the following lines when the next question's heading
        #     is misread ("Q.4" came back as "0.4"), so the tick is sized for an
        #     82pt span instead of the 24pt line it marks.
        own = by_lid.get((getattr(m, "anchor_line_id", "") or "").strip())
        row = _objective_row(student_doc, m, own, by_lid)
        if row is not None:
            anchor_lines = [row]
        elif own is not None:
            anchor_lines = [own]
        elif anchor_lines:
            anchor_lines = anchor_lines[:1]

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
    # An ICAI copy shows each step's marks, prose answers included. CA faculty review
    # (18 Oct 2026, Corporate Laws 1e3c181a): "Step marking missing in Q 1(a)", a law
    # answer of several steps drawn as ticks and one total.
    if not is_obj and len(criteria) > 1 and bool(getattr(render_report, "whole_total", False)):
        base_step = True
    # Per-step ticks are only for BIG answers (>= 3 marks). A 1- or 2-mark question
    # gets a single tick + total — teachers don't break 2 marks into "1+1" on the page.
    MIN_MARKS_FOR_STEP = 3
    # Need somewhere to place the per-step marks; with no anchor lines fall back
    # to the single-mark branch (which handles the empty band via y_fraction).
    use_step_marks = (
        not safe_layout and not is_obj and base_step and bool(anchor_lines)
        and (strict or mx >= MIN_MARKS_FOR_STEP)
    )
    if strict:
        unattempted = not bool(getattr(m, "attempted", True))
        if unattempted:
            return
        if not base_step or is_obj:
            render_report.render_requirements.append({"qid": qid, "role": "total"})
        if base_step and not is_obj:
            render_report.render_requirements.extend(
                {"qid": qid, "role": "step", "criterion": i}
                for i, criterion in enumerate(criteria) if criterion.max > 0)

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

        verified = [i for i, criterion in enumerate(criteria)
                    if strict and render_report.verified_step_anchors.get(
                        f"{normalize_qid(qid)}:{i}") == [criterion.step_line_id, criterion.anchor_quote]]
        resolutions = resolve_steps(criteria, candidates,
                                    model_anchor_ok=_model_anchor_ok,
                                    require_evidence=strict, verified_indices=verified)

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
            if strict:
                from presentation_layout import composite_criterion
                certificate = _checking_certificate(render_report, qid, ci, criteria[ci])
                if composite_criterion(criteria[ci]) and not certificate.get('checking_points'):
                    _note_unanchored(qid, 1)
                    continue  # Old representative-row certificates do not cover a whole section.
                pl = _visual_step_line(student_doc, render_report, qid, ci, criteria[ci], pl)
                if pl is None:
                    _note_unanchored(qid, 1)
                    continue
                if certificate.get('checking_points'):
                    pl = {**pl, '_has_checking_plan': True}
                if not pl.get('_verdict'):
                    criterion = criteria[ci]
                    entry_verdict = ('missing' if criterion.evidence_kind == 'missing' else
                                     'wrong' if criterion.awarded <= 1e-6 else
                                     'correct' if criterion.awarded >= criterion.max - 1e-6 else 'partial')
                    pl = {**pl, '_verdict': entry_verdict}
                # A table-format criterion assesses the header row as a whole,
                # not just the one OCR word quoted to identify that header.
                if pl.get('_visual_anchor') and re.search(r'\bformat\b', criteria[ci].description, re.I):
                    original_region = band_by_lid[res.line_id]
                    pl = {**pl, '_score_row_extent': (original_region['x0'], original_region['x1'])}
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
            if strict:
                continue  # One detected region is not evidence of evenly spaced rows.
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
            group_key = (pl['lid'], pl['x0'], pl['y0'], pl['x1'], pl['y1'])
            certificate = _checking_certificate(render_report, qid, ci, criteria[ci]) if strict else {}
            if certificate.get('checking_points'):
                group_key = (*group_key, ci)
            if strict and pl.get('_visual_anchor') and not certificate.get('checking_points'):
                for key, existing in groups.items():
                    other = existing['pl']
                    from presentation_layout import same_written_entry
                    if (other['lid'] == pl['lid'] and other.get('_visual_anchor')
                            and not other.get('_has_checking_plan')
                            and same_written_entry(pl, other)):
                        group_key = key
                        existing['pl'] = {**other, 'x0': min(pl['x0'], other['x0']),
                                          'x1': max(pl['x1'], other['x1']),
                                          'y0': max(pl['y0'], other['y0']), 'y1': min(pl['y1'], other['y1'])}
                        break
            g = groups.get(group_key)
            if g is None:
                g = {"pl": pl, "awarded": 0.0, "max": 0.0, "anchored": False, "criteria": []}
                groups[group_key] = g
            g["awarded"] += float(getattr(criteria[ci], "awarded", 0) or 0)
            g["max"] += float(getattr(criteria[ci], "max", 0) or 0)
            g["anchored"] = g["anchored"] or ci in anchored_ci
            g["criteria"].append(ci)
            if pl.get('_verdict') != g['pl'].get('_verdict'):
                g['pl'] = {**g['pl'], '_verdict': 'partial'}

        # A step that EARNED marks but found no line (its quote did not match a
        # misread OCR line, "Thban settlements" for "Urban settlements") was drawn
        # nowhere: the margin read "Q14 1/5" over a page with no mark at all, and
        # "Q11 2.5/3" over one circled 0.5 (Parishram Geography, 8 Oct 2026). As on
        # the teacher layout, its award joins the last mark drawn for the same
        # answer, so the circles add up to the margin; with no mark at all it goes on
        # the answer's own first line. Zero steps stay where the grader put them.
        orphans = [ci for ci, c in enumerate(criteria)
                   if ci not in assigned and float(getattr(c, "max", 0) or 0) > 0
                   and float(getattr(c, "awarded", 0) or 0) > 0]
        # On a CA paper a step is ticked where its working is (ICAI step marking).
        # CA faculty review (23 Oct 2026, FM 8bc28fde): "Sol 2 tick marks were
        # missing", the Kp and Kd steps (3.5 and 2.5 marks) had garbled OCR quotes
        # and no certificate, so both joined the circled 7 beside the cost of equity.
        # A step whose own cited line is one of this answer's lines is drawn there; a
        # block of several rows (a worked interpolation) where its working ends.
        # Every category, not CA only: commerce SME review (29 Oct 2026, Online
        # Applied Maths Q9, Q11 and Q12) "mark awarded in the question is 1 but written
        # 2", one circled 2 where two 1-mark steps stood on two lines; the 21 and 22
        # Oct runs show it on MP and Maharashtra Maths copies too.
        if orphans:
            own_lines = {pl["lid"]: pl for pl in my_lines}
            for ci in list(orphans):
                cited = own_lines.get((getattr(criteria[ci], "step_line_id", "") or "").strip())
                if cited is None or _is_header(cited.get("text") or "") or _is_cancelled_line(cited):
                    continue
                c = criteria[ci]
                page = student_doc[cited["page"] - 1]
                x1, y1 = cited["x1"], cited["y1"]
                if x1 > .85 * _content_width(page):
                    x1 = cited["x0"] + .7 * (cited["x1"] - cited["x0"])
                y0 = y1 - min(y1 - cited["y0"], page.rect.height * .03)
                pl = {**cited, "x1": x1, "y0": y0, "y1": y1, "_location_source": "cited_step_line",
                      "_verdict": "correct" if float(c.awarded) >= float(c.max) - 1e-6 else "partial"}
                key = (pl["lid"], pl["x0"], pl["y0"], pl["x1"], pl["y1"])
                g = groups.get(key)
                if g is None:
                    g = {"pl": pl, "awarded": 0.0, "max": 0.0, "anchored": True, "criteria": []}
                    groups[key] = g
                g["awarded"] += float(c.awarded)
                g["max"] += float(c.max)
                g["criteria"].append(ci)
                assigned[ci] = g["pl"]
                orphans.remove(ci)
        if orphans:
            hosts = sorted((g for g in groups.values() if g["awarded"] > 0),
                           key=lambda g: (g["pl"]["page"], g["pl"]["y0"]))
            host = hosts[-1] if hosts else None
            if host is None:
                own = by_lid.get((getattr(m, "anchor_line_id", "") or "").strip())
                if own is None or own["lid"] not in {pl["lid"] for pl in my_lines}:
                    own = anchor_lines[0] if anchor_lines else None
                if own is not None:
                    host = {"pl": {**own, "_location_source": "unplaced_steps"}, "awarded": 0.0,
                            "max": 0.0, "anchored": True, "criteria": list(orphans)}
                    groups[("orphans", own["lid"])] = host
            if host is not None:
                for ci in orphans:
                    host["awarded"] += float(criteria[ci].awarded)
                    host["max"] += float(criteria[ci].max)
                    # The circle now carries this step's award, so it is one of the
                    # circle's criteria: the release check compared the two and held
                    # every such copy as "this step shows 2 but its criteria award 1".
                    if ci not in host["criteria"]:
                        host["criteria"].append(ci)
                    assigned[ci] = host["pl"]
                host["pl"] = {**host["pl"], "_verdict": (
                    "correct" if host["awarded"] >= host["max"] - 1e-6 else "partial")}

        # Stage 4: draw one mark per line, on its OWN page, nudging down on the
        # rare occasion two lines' marks would still overlap vertically. Share the
        # cross-question registry so step marks don't collide with other questions'.
        drawn_by_page = placed_by_page
        ordered_groups = sorted(
            groups.values(), key=lambda g: (g["pl"]["page"], g["pl"]["y0"]))
        # A step that scored nothing is never crossed on a line another step of this
        # answer was paid for: the student reads a cross on a sentence they were also
        # ticked for as "this is wrong". The zero is a missing point, and a teacher
        # writes it where the answer ends. MEASURED 2026-10-22 (teaching review, 12
        # copies): crosses or circled 0s on credited, correct lines on 8 of them.
        credited_lids = {g["pl"].get("lid") for g in ordered_groups if g["awarded"] > 1e-6}
        answer_end = candidates[-1] if candidates else None

        # HOW CLOSE TOGETHER THIS STUDENT'S LINES ARE.
        #
        # A step mark that does not FIT between two lines of working cannot sit on
        # its own line: the anti-overlap search shoves it 36pt down, the next one
        # further still, and by the fourth step of a five-line solution the marks
        # have walked off the bottom of the answer. Measured on a two-page optics
        # numerical: step 3's mark landed on step 4's line and step 4's cross landed
        # ~100pt below the last line of working, against nothing at all.
        #
        # The cause was that the circled step mark was the ONE mark on the sheet
        # that never scaled with the handwriting — a flat 31.8pt across, wider than
        # the 28pt line pitch of ordinary writing — while the separation demanded
        # between two marks was a flat 30pt, also wider than that pitch. So the
        # marks are sized against the pitch, and the separation against the marks.
        gaps = []
        prev = None
        for _pl in candidates:
            if prev is not None and _pl["page"] == prev["page"]:
                d = _pl["y0"] - prev["y0"]
                if 6.0 <= d <= 120.0:
                    gaps.append(d)
            prev = _pl
        row_pitch = (sorted(gaps)[len(gaps) // 2] if gaps
                     else _NOMINAL_LINE_H * 1.3)
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
        pending_verdicts = []
        score_plans = []
        reserved_before = {p: list(boxes) for p, boxes in _MARK_BOXES.items()}
        crowded_steps = False
        section_total = False
        point_requirements = []
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
            missing_step = strict and all(criteria[i].evidence_kind == "missing" for i in g["criteria"])
            if zero_step and not (g.get("anchored") and max_step > 0):
                continue
            moved_to_end = False
            if (strict and zero_step and answer_end is not None
                    and pl.get("lid") in credited_lids):
                missing_step = True
                moved_to_end = True
                pl = _below_answer_end(answer_end, student_doc[answer_end["page"] - 1].rect.height)

            if strict:
                verdict = pl.get('_verdict') or ('wrong' if zero_step else ('correct' if awarded >= max_step - 1e-6 else 'partial'))
                # Part marks on a figure the examiner's own evidence calls wrong: crossed,
                # with the circled award (CA faculty review, 17 Oct 2026).
                if (verdict == 'partial' and bool(getattr(render_report, "whole_total", False))
                        and evidence_names_wrong_figure(criteria, g['criteria'])):
                    verdict = 'wrong'
                ci = g['criteria'][0]
                certificate = _checking_certificate(render_report, qid, ci, criteria[ci])
                points = certificate.get('checking_points', [])
                # The same rule for a zero step's certified crosses: when every one of
                # them lands on a line another step was paid for, the step is a missing
                # point under the answer (EMRS PGT Hindi Q9, History Q12, 22 Oct 2026).
                if zero_step and points and answer_end is not None:
                    targets = [_certified_target_line(student_doc, t, by_lid) for t in points]
                    if all(t is None or t.get('lid') in credited_lids for t in targets):
                        points = []
                        missing_step = True
                        moved_to_end = True
                        pl = _below_answer_end(answer_end, student_doc[answer_end["page"] - 1].rect.height)
                if moved_to_end:
                    # Its own award anchor would put the zero back on the credited line.
                    certificate = {k: v for k, v in certificate.items() if k != 'award_anchor'}
                if points:
                    for pi, target in enumerate(points):
                        point_id = f'{ci}:{pi}'
                        point_requirements.append({'qid': qid, 'role': 'verdict', 'criterion': ci,
                                                   'checking_point': point_id})
                        target_line = _certified_target_line(student_doc, target, by_lid)
                        if target_line:
                            target_line['_checking_point'] = point_id
                            # A step paid in full has nothing wrong in it: two crosses
                            # sat on a 0.5/0.5 step of a 4/4 answer, one over a correct
                            # "28×3 = 84" (KVS PRT Music Q7, 22 Oct 2026).
                            kind = ('correct' if target['verdict'] == 'wrong'
                                    and awarded >= max_step - 1e-6 else target['verdict'])
                            pending_verdicts.append((target_line, kind, [ci], 'verdict', None))
                    end = certificate.get('award_anchor')
                    if certificate.get('award_scope') == 'section' and not end:
                        section_total = True
                        continue
                    score_line = _certified_target_line(student_doc, end, by_lid) if end else pl
                    if score_line and end:
                        score_line['_award_ending'] = True
                    if zero_step:
                        # A zero step has checking strokes, but no extra zero circle.
                        drawn_steps.append(0.)
                        # Preserve the legacy zero-award requirement without painting twice.
                        if pending_verdicts:
                            vl, vk, vi, _, _ = pending_verdicts[-1]
                            pending_verdicts[-1] = (vl, vk, vi, 'step', 0)
                        continue
                    if score_line and _draw_sheet_score(student_doc, qid, score_line, awarded, role='step',
                            criteria=g['criteria'], plans=score_plans):
                        drawn_steps.append(awarded)
                        marked_on_sheet = True
                    else:
                        crowded_steps = True
                    continue
                if zero_step and not missing_step and verdict == 'wrong':
                    pending_verdicts.append((pl, 'wrong', g['criteria'], 'step', 0))
                    marked_pages.add(pl['page'])
                    drawn_steps.append(0.)
                    continue
                score_line = ({**pl, 'x0': pl['_score_row_extent'][0], 'x1': pl['_score_row_extent'][1]}
                              if pl.get('_score_row_extent') else pl)
                if certificate.get('award_anchor'):
                    ending = _certified_target_line(student_doc, certificate['award_anchor'], by_lid)
                    if ending:
                        score_line = {**ending, '_award_ending': True}
                stroke_pending = not missing_step and verdict != 'missing'
                if stroke_pending:
                    pending_verdicts.append((pl, verdict, g['criteria'], 'verdict', awarded))
                if _draw_sheet_score(student_doc, qid, score_line, awarded, role="step",
                                     criteria=g["criteria"], cross=False, plans=score_plans,
                                     lead=_verdict_lead() if stroke_pending and not certificate.get('award_anchor') else 0.0):
                    marked_on_sheet = True
                    marked_pages.add(pl["page"])
                    drawn_steps.append(awarded)
                    drew_positive = drew_positive or not zero_step
                else:
                    crowded_steps = True
                continue

            pg = student_doc[pl["page"] - 1]
            cwp = _content_width(pg)
            sstep = f"{seed}|g{gi}"

            # Size the mark BEFORE choosing its slot — how much room two marks need
            # between them is a fact about how big they are, and computing the slot
            # first is what pinned the separation to a constant.
            #
            # _draw_number_in_circle uses a larger radius for decimals (half marks
            # like "0.5"), so the exact text has to be known here too.
            mark_text = (str(int(awarded)) if float(awarded).is_integer()
                         else f"{awarded:g}")
            is_dec = "." in mark_text
            line_h = pl["y1"] - pl["y0"]
            tick_size = _tick_size_for_line(20, line_h)
            # The circle scales with the handwriting like every other mark on the
            # sheet, and never grows wider than the gap between two lines of it.
            circle_size = _tick_size_for_line(_STEP_MARK_SIZE, line_h)
            r_factor = 1.9 if is_dec else 1.8
            fits_pitch = (row_pitch * 0.94 - 3.0) / (2.0 * r_factor)
            circle_size = max(_STEP_MARK_MIN_SIZE, min(circle_size, fits_pitch))
            circle_r = circle_size * r_factor + 1.5
            cluster_w = tick_size * 1.5 + 6 + circle_r * 2

            # Find a vertical slot clear of every mark already on this page,
            # preferring the step's own line. Search downward first; if the page
            # tail is full, search back upward — never clamp several marks onto
            # the same y (overprint) or off the page (draws nothing).
            floor_y, ceiling = _owned_y_bounds(pl["page"], pg.rect.height)
            placed = drawn_by_page.setdefault(pl["page"], [])
            y_mark = max(floor_y, min((pl["y0"] + pl["y1"]) / 2, ceiling))
            sep = max(12.0, circle_r + 3.0)

            def _collides(yy: float, _sep: float = sep) -> bool:
                return any(abs(yy - py) < _sep for py in placed)

            if _collides(y_mark):
                down = y_mark
                while down <= ceiling and _collides(down):
                    down += sep * 1.2
                if down <= ceiling:
                    y_mark = down
                else:
                    up = y_mark
                    while up >= floor_y and _collides(up):
                        up -= sep * 1.2
                    y_mark = max(floor_y, up)
            placed.append(y_mark)

            # Place the tick just past where the writing ends, but never let the
            # cluster cross the right edge onto the marks strip: the right cap
            # wins over the left floor when the line itself starts far right.
            # Bounded by the SHEET, not the page: past its edge the mark is not on
            # the student's answer at all, it is floating in the blank surround.
            right_cap = min(cwp, _mark_area_right(pg)) - 6 - cluster_w
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
            # A zero step needs the same room as a ticked one: its CROSS goes where
            # the tick would, to the left of the circled mark.
            mark_dx = tick_size * 1.5 + circle_r + 6
            tick_x, tick_dy, shrink = _clear_x_for_mark(
                student_doc, by_lid, pl["page"], y_mark, tick_x, cluster_w, right_cap,
                own_lid=pl["lid"], probe_dx=mark_dx, probe_r=circle_r)
            y_mark = max(floor_y, min(y_mark + tick_dy, ceiling))
            # A tight spot gets a smaller mark, not a mark over the words.
            tick_size *= shrink
            circle_size *= shrink
            mark_dx *= shrink
            if strict:
                # Keep the mark on the evidence row. If the writing leaves no space,
                # use its adjacent margin at this same height; do not drift vertically.
                y_mark = (pl["y0"] + pl["y1"]) / 2
                desired_x = writing_end + 12
                if desired_x + cluster_w <= cwp - 6:
                    tick_x = max(tick_x, desired_x)
                # Pinning back to the evidence row can undo the clearance search.
                # Recheck the final numeral with the same probe as the release
                # gate, moving only along this row before using the margin.
                from preflight import _ink_under_mark, _MARK_INK_BLOCK
                mask, mask_scale = _page_ink(student_doc, pl["page"])
                radius = circle_size * r_factor + 1.5
                clear_x = None
                for offset in range(0, 49, 4):
                    candidate = tick_x + offset
                    cx = candidate + mark_dx
                    box = fitz.Rect(cx - radius, y_mark - radius, cx + radius, y_mark + radius)
                    if box.x1 > cwp - 6:
                        break
                    if (_ink_under_mark(mask, mask_scale, box) < _MARK_INK_BLOCK
                            and not any(box.intersects(b) for b in _MARK_BOXES.get(pl["page"], []))):
                        clear_x = candidate
                        break
                if clear_x is not None:
                    tick_x = clear_x
                if (_SAFE_LAYOUT.get() or tick_dy or shrink < 1
                        or desired_x + cluster_w > cwp - 6 or clear_x is None):
                    tick_x = cwp + 4
                    circle_size = min(circle_size, 5.0)
                    tick_size = min(tick_size, 12.0)
                    mark_dx = tick_size * 1.5 + circle_size * 1.9 + 6

            # Any positive award gets one tick, including partial credit. Only a
            # completely wrong, attempted zero-credit step gets a cross.
            if not zero_step:
                _draw_tick(pg, tick_x, y_mark, size=tick_size, seed=sstep + "t")
                _note_mark(pl["page"], _tick_box(tick_x, y_mark, tick_size))
                marked_on_sheet = True
            elif zero_step and not missing_step:
                # A point that earned NOTHING is wrong, and a teacher crosses it. It
                # used to get its circled "0" and nothing else, so on the page it read
                # exactly like a point that had simply been marked lightly. The
                # reviewer said so twice on one copy — "cross is not there for wrong
                # point", and "the student's wrong point should be crossed/marked
                # clearly... the evaluator should not leave all five attempted points
                # appearing equally accepted".
                x_size = _cross_size_for_tick(tick_size)
                _draw_cross(pg, tick_x, y_mark, size=x_size, seed=sstep + "x")
                _note_mark(pl["page"], _cross_box(tick_x, y_mark, x_size))
                marked_on_sheet = True
            mark_x = tick_x + mark_dx
            if zero_step and not missing_step:
                radius = x_size / 2
                _record_draw(qid, "step", pl["page"], _cross_box(tick_x, y_mark, x_size),
                             line=pl, criteria=g["criteria"], score=0)
            else:
                _draw_number_in_circle(pg, mark_x, y_mark, mark_text,
                                       size=circle_size, seed=sstep)
                radius = circle_size * (1.9 if "." in mark_text else 1.8) + 1.5
                _record_draw(qid, "step", pl["page"],
                             (tick_x, y_mark - radius, mark_x + radius, y_mark + radius),
                             line=pl, criteria=g["criteria"], score=awarded,
                             numeral_bbox=(mark_x - radius, y_mark - radius, mark_x + radius, y_mark + radius))
            if strict and tick_x >= cwp:
                _STRIP_SLOTS.setdefault(id(student_doc), {}).setdefault(pl["page"], []).append(
                    (y_mark - radius, y_mark + radius))
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
        # Reserve all numerals first. A tick on one row must not consume the only
        # safe score slot on the next row in a tightly ruled ledger.
        if strict and (crowded_steps or section_total) and all(i in assigned for i, c in enumerate(criteria) if c.max > 0):
            # Crowding is a layout choice, not an evidence failure. Discard the
            # ENTIRE question's step-circle plan before painting any of it.
            _MARK_BOXES.clear()
            _MARK_BOXES.update(reserved_before)
            score_plans.clear()
            drawn_steps = [aw]
            render_report.render_requirements = [
                r for r in render_report.render_requirements if r['qid'] != qid]
            presentation_mode = 'section_question_total' if section_total else 'crowded_question_total'
            render_report.render_requirements.append(
                {'qid': qid, 'role': 'total', 'presentation_mode': presentation_mode})
            render_report.render_requirements.extend(
                {'qid': qid, 'role': 'verdict', 'criterion': i}
                for i, c in enumerate(criteria) if c.max > 0 and c.evidence_kind != 'missing')
            pending_verdicts = [(line, verdict, indices, 'verdict', None)
                                for line, verdict, indices, _, _ in pending_verdicts]
            if _draw_sheet_total(student_doc, m, my_lines):
                render_report.render_manifest[-1]['presentation_mode'] = presentation_mode
                marked_on_sheet = True
        else:
            for plan in score_plans:
                _paint_sheet_score(student_doc, plan)
        if strict:
            render_report.render_requirements.extend(point_requirements)
        for verdict_line, verdict_kind, indices, verdict_role, verdict_score in pending_verdicts:
            if _draw_sheet_verdict(student_doc, qid, verdict_line, verdict_kind,
                                   criteria=indices, role=verdict_role, score=verdict_score):
                marked_on_sheet = True

        if (not strict and drawn_steps and not drew_positive and aw <= 1e-6
                and anchor_lines and not unattempted):
            first = anchor_lines[0]
            fpg = student_doc[first["page"] - 1]
            fx = max(2.0, min(first["x1"] + 12, _mark_area_right(fpg) - 40))
            fy = (first["y0"] + first["y1"]) / 2
            # Sized off the writing it crosses, like every tick on the sheet. A flat
            # 18pt was the one mark on the copy that ignored how big the student
            # writes, so it came out doll-sized on a spread-out hand and heavy on a
            # cramped one.
            fsize = _cross_size_for_tick(_tick_capped_to_answer(
                _tick_size_for_line(32, first["y1"] - first["y0"],
                                    answer_lines=len(anchor_lines)),
                anchor_lines))
            _draw_cross(fpg, fx, fy, size=fsize, seed=seed + "x0")
            _note_mark(first["page"], _cross_box(fx, fy, fsize))
            marked_on_sheet = True

        # Strict copies receive a separately labelled end-of-answer total below;
        # the margin retains the question score and step arithmetic as well.

    # A step-marked question whose steps ALL failed to resolve has, at this point,
    # nothing on it. `anchors.resolve_steps` deliberately leaves a step it cannot
    # find unplaced rather than guessing a line, and on an answer where every step
    # goes unplaced that correct decision compounded into an answer the student
    # opens to find completely unmarked - the one outcome no teacher would hand
    # back. Fall through to the whole-answer mark instead: it says less than a
    # column of step ticks, but it says the answer was read.
    if strict and not is_obj and (not use_step_marks or not drawn_steps):
        verdict = 'wrong' if aw <= 1e-6 else ('correct' if aw >= mx - 1e-6 else 'partial')
        located_answers = []
        has_checking_plan = False
        for ci, criterion in enumerate(criteria):
            cert = _checking_certificate(render_report, qid, ci, criterion)
            if cert.get('checking_points'):
                has_checking_plan = True
                for pi, target in enumerate(cert['checking_points']):
                    point_id = f'{ci}:{pi}'
                    render_report.render_requirements.append({'qid':qid, 'role':'verdict',
                        'criterion':ci, 'checking_point':point_id})
                    line = _certified_target_line(student_doc, target, by_lid)
                    if line:
                        line['_checking_point'] = point_id
                        if _draw_sheet_verdict(student_doc, qid, line, target['verdict'], criteria=[ci], role='answer'):
                            marked_on_sheet = True
                continue
            source_line = by_lid.get(criterion.step_line_id)
            if source_line and source_line['lid'] in {l['lid'] for l in my_lines}:
                located = _visual_step_line(student_doc, render_report, qid, ci, criterion, source_line)
                if located:
                    located_answers.append(located)
        for answer_line in ([] if has_checking_plan else located_answers if criteria else anchor_lines):
            if _is_header(answer_line.get('text', '')):
                continue
            if _draw_sheet_verdict(student_doc, qid, answer_line,
                                   answer_line.get('_verdict') or verdict, role='answer', score=aw):
                marked_on_sheet = True
                break
        if not marked_on_sheet:
            _note_unanchored(qid, 1)
    elif not use_step_marks or not drawn_steps:
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
            if not is_obj and not strict and aw > 1e-6:
                # A part-marked answer's one score goes on its last row that is not
                # a wrong element, so every wrong row can carry its own cross: the
                # score row never also takes a cross (no tick and cross on one row),
                # and a wrong provision on the last line was left looking accepted
                # (Business Laws Q1(a)(i) reviewer complaint).
                wrong_rows = {(getattr(c, "step_line_id", "") or "").strip() for c in criteria
                              if float(getattr(c, "awarded", 0) or 0) <= 1e-6}
                clean = [line for line in band if line["lid"] not in wrong_rows]
                if clean:
                    anchor = clean[-1]
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
        # Objective rows join the ledger too, so a later answer's mark is nudged
        # clear of an MCQ tick instead of being drawn through it. Objective rows
        # themselves are never nudged - see the bounded search above.
        placed.append(y_mark)

        # EVERY question is marked on the sheet - objective included. It used to be
        # "if not is_obj", so an MCQ carried a verdict in the margin lane and nothing
        # at all beside the student's own answer. The reviewer asked for the opposite:
        # "make sure we tick each question and give marks like a teacher." The one
        # remaining reason to leave the sheet clean is margin-only release mode, which
        # this block already tested for internally.
        if strict and not anchor_lines:
            # Nothing on the sheet was located for this answer. A tick at the
            # report's y_fraction is a tick on whatever happens to be there - on
            # a live copy, the notebook's printed header. The margin carries the
            # score, and the release path is told.
            _note_unanchored(qid, 1)
        elif not safe_layout:
            # One size per copy: the document's scale, not this row's height, and
            # no per-answer length factor. A tight row is handled by MOVING the
            # mark, never by drawing a different-sized one.
            tick_size = _tick_size_for_line(32, line_h)
            tick_size = _tick_capped_to_answer(tick_size, anchor_lines)
            cluster_w = tick_size * 1.6
            mark_right = min(mcw, _mark_area_right(mark_page))
            if strict:
                mark_right = mcw
            # A teacher's tick goes where the WRITING ends. The anchor may be a stub
            # ("(c)" is its own OCR line) while the working runs on across the row.
            if anchor_lines:
                x_end = max(x_end, _row_writing_end(
                    by_lid, mark_page_num, anchor["y0"], anchor["y1"],
                    anchor["x0"], x_end, mcw))
            # No ink search for the GLYPH. Ratified: "there is no issue if ticks land
            # on student's text as teacher also do the same." Hunting for blank paper
            # slid a tick up to _MARK_SLIDE_MAX away from the words it was marking,
            # which read as a tick belonging to nothing - the complaint that started
            # this. A circled MARK still searches (see the step path): a numeral over
            # a numeral is unreadable, where a stroke over a word is just marking.
            tick_x = max(2.0, min(x_end + 12, mark_right - 6 - cluster_w))
            # How far the mark may drop and still be read as THIS answer's mark:
            # within its own line, and never past the end of the question's band.
            # 8pt is the width of the window a reader uses to decide which line a
            # mark belongs to (the objective suite pins it); stay inside it.
            dy_room = max(0.0, min(max(line_h, 14.0) * 0.25, 7.0, ceiling - y_mark))
            if is_obj and anchor_lines and not unattempted:
                boxes_before = len(_MARK_BOXES.get(mark_page_num, []))
                stranded_before = _UNANCHORED.get(str(qid or "?"), 0)
                marked_on_sheet = _draw_mcq_verdict(
                    student_doc, mark_page, qid, anchor, by_lid, aw, mx, tick_size, seed)
                if not marked_on_sheet and aw <= 1e-6:
                    # A wrong answer is crossed on its own row. When the row had no
                    # clear paper the cross and 0 went to the margin lane beside the
                    # photo: Bihar Board English, SME review 29 Oct 2026 (row 38),
                    # "wrong questions are marked separately ... they should be on the
                    # same page", while the right ones beside them were ticked on their
                    # lines. A teacher crosses where the answer ends, over the words
                    # if need be (the ratified rule above); the score stays in the
                    # margin entry, as for any other wrong answer.
                    if len(_MARK_BOXES.get(mark_page_num, [])) == boxes_before:
                        x_size = _cross_size_for_tick(tick_size)
                        x_size, cross_x, dy = _fit_beside_marks(
                            mark_page_num, tick_x, y_mark, x_size,
                            mark_right - 6, cross=True, dy_max=dy_room)
                        _draw_cross(mark_page, cross_x, y_mark + dy, size=x_size, seed=seed)
                        _note_mark(mark_page_num, _cross_box(cross_x, y_mark + dy, x_size))
                        _record_draw(qid, "answer", mark_page_num,
                                     _cross_box(cross_x, y_mark + dy, x_size),
                                     line=anchor, score=aw)
                    # Its cross is on the row (drawn here, or by the verdict pass when
                    # only the circled 0 found no room), so the lane draws none.
                    marked_on_sheet = True
                    if stranded_before:
                        _UNANCHORED[str(qid or "?")] = stranded_before
                    else:
                        _UNANCHORED.pop(str(qid or "?"), None)
            elif aw <= 1e-6:  # Zero marks
                if not unattempted:   # attempted-but-wrong → cross; unattempted → nothing
                    x_size = _cross_size_for_tick(tick_size)
                    x_size, tick_x, dy = _fit_beside_marks(
                        mark_page_num, tick_x, y_mark, x_size,
                        mark_right - 6, cross=True, dy_max=dy_room)
                    _draw_cross(mark_page, tick_x, y_mark + dy, size=x_size, seed=seed)
                    _note_mark(mark_page_num, _cross_box(tick_x, y_mark + dy, x_size))
                    _record_draw(qid, "answer", mark_page_num,
                                 _cross_box(tick_x, y_mark + dy, x_size),
                                 line=anchor if anchor_lines else None, score=aw)
                    marked_on_sheet = True
            else:
                # Positive whole-answer awards always carry a tick. Keep the
                # partial award circled beside it so the amount remains explicit.
                if mx > 0 and aw >= mx - 1e-6:
                    tick_size, tick_x, dy = _fit_beside_marks(
                        mark_page_num, tick_x, y_mark, tick_size, mark_right - 6,
                        dy_max=dy_room)
                    _draw_tick(mark_page, tick_x, y_mark + dy, size=tick_size, seed=seed)
                    _note_mark(mark_page_num,
                               _tick_box(tick_x, y_mark + dy, tick_size))
                    _record_draw(qid, "answer", mark_page_num,
                                 _tick_box(tick_x, y_mark + dy, tick_size),
                                 line=anchor if anchor_lines else None, score=aw)
                    marked_on_sheet = True
                else:
                    mark_text = (str(int(aw)) if float(aw).is_integer()
                                 else f"{aw:g}")
                    circle_size = _ms(_WHOLE_MARK_SIZE) * _doc_mark_scale()
                    circle_r = circle_size * (1.9 if "." in mark_text else 1.8) + 1.5
                    tick_w = tick_size * 1.5
                    cluster_w = tick_w + 6 + circle_r * 2
                    cx0 = max(2.0, min(tick_x, mark_right - 6 - cluster_w))
                    # A numeral over a numeral is unreadable where a tick over a word
                    # is just marking, so this one does search for clear paper.
                    cx, cdy, csh = _clear_x_for_mark(
                        student_doc, by_lid, mark_page_num, y_mark, cx0,
                        cluster_w, mark_right - 6 - cluster_w,
                        own_lid=(anchor["lid"] if anchor_lines else ""),
                        probe_dx=tick_w + 6 + circle_r, probe_r=circle_r)
                    # Never LEFT of where the answer ends. The clearance search
                    # looks both ways, and on a line written edge to edge - where
                    # there is no room to its right - it walked the numeral 20pt
                    # back INTO the sentence it was marking. This mark closes the
                    # answer, so past the writing is the only side it belongs on;
                    # the drop and the shrink it also returns still apply.
                    cx = max(cx, cx0)
                    cy = max(floor_y, min(y_mark + cdy, ceiling))
                    if strict and (cdy or _SAFE_LAYOUT.get()):
                        cx, cy = mcw + 4, y_mark
                    tick_y = cy + .6 * tick_size * csh
                    _draw_tick(mark_page, cx, tick_y, size=tick_size * csh, seed=seed)
                    numeral_x = cx + (tick_w + 6 + circle_r) * csh
                    _draw_number_in_circle(mark_page, numeral_x, cy,
                                           mark_text, size=circle_size * csh,
                                           seed=seed + "c")
                    numeral_box = fitz.Rect(numeral_x - circle_r * csh, cy - circle_r * csh,
                                            numeral_x + circle_r * csh, cy + circle_r * csh)
                    cluster = _tick_box(cx, tick_y, tick_size * csh) | numeral_box
                    _note_mark(mark_page_num, cluster)
                    _record_draw(qid, "answer", mark_page_num,
                                 cluster,
                                 line=anchor if anchor_lines else None, score=aw,
                                 numeral_bbox=numeral_box)
                    marked_on_sheet = True

            # A part-marked answer carries ONE score, so the element that earned
            # nothing is invisible unless it is struck on its own line. The reviewer
            # asked for exactly this twice — "cross is not there for wrong point", and
            # "the student's wrong point should be crossed... not left appearing
            # equally accepted". The per-step path already crosses its zeros; a
            # question scored as a single mark did not, so on Q1(a)(i) the invented
            # provision was reduced to nothing and still looked accepted on the page.
            # Only for a PART-marked answer. When the whole question scored zero it
            # has just been crossed once above, and running this too drew a SECOND
            # cross on the same answer - four crosses piled onto the two lines of a
            # Class 10 Q6, one of them landing on the word "between". The comment
            # below predates the per-step cross and its "the per-step path already
            # crosses its zeros" premise no longer holds here.
            for zc in (() if aw <= 1e-6 or strict
                       else (getattr(m, "criteria", None) or [])):
                if float(getattr(zc, "awarded", 0) or 0) > 1e-6:
                    continue
                zl = by_lid.get((getattr(zc, "step_line_id", "") or "").strip())
                if not zl or zl["page"] != mark_page_num:
                    continue
                if anchor_lines and zl["lid"] == anchor["lid"]:
                    # This row already carries the positive whole-answer award.
                    # Keep separate wrong-row checks, but never add a conflicting
                    # cross to the same partial-credit cluster.
                    continue
                zpg = student_doc[zl["page"] - 1]
                zw = _cross_size_for_tick(
                    _tick_size_for_line(20, zl["y1"] - zl["y0"]))
                zx = max(2.0, min(zl["x1"] + 12, _mark_area_right(zpg) - 8 - zw))
                zy = (zl["y0"] + zl["y1"]) / 2
                zx, zdy, zsh = _clear_x_for_mark(
                    student_doc, by_lid, zl["page"], zy, zx, zw * 1.6,
                    _mark_area_right(zpg) - 6 - zw * 1.6, own_lid=zl["lid"],
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
    if strict:
        if not (base_step and not is_obj) and not any(row.get("qid") == qid and row.get("question_total")
                   for row in render_report.render_manifest):
            _draw_sheet_total(student_doc, m, my_lines)
        # Feedback follows the first actual scoring evidence, not an isolated
        # title at the foot of the preceding page (the supplied Q6 failure).
        evidence_rows = [row for row in render_report.render_manifest
                         if row["qid"] == qid and row["role"] in {"step", "answer", "verdict", "total"}]
        # Reasons follow their actual error, including errors on earlier pages.
        local_reasons = [row for row in evidence_rows if row.get('deduction_reason')]
        for ci, criterion in enumerate(criteria):
            cert = _checking_certificate(render_report, qid, ci, criterion)
            reason = cert.get('deduction_reason', '')
            if (criterion.awarded >= criterion.max or not reason
                    or any(ci in row.get('criteria', []) for row in local_reasons)):
                continue
            context_rows = [row for row in evidence_rows if ci in row.get('criteria', [])]
            located = _certified_target_line(student_doc, cert, by_lid)
            if located:
                local_reasons.append({'page':located['page'], 'line_id':located['lid'],
                    'anchor_bbox':[located[k] for k in ('x0','y0','x1','y1')],
                    'criteria':[ci], 'deduction_reason':reason})
            elif context_rows:
                context = max(context_rows, key=lambda row: (row['page'], row['bbox'][1]))
                local_reasons.append({**context, 'deduction_reason': reason})
        if not local_reasons and aw < mx:
            wrong_rows = [row for row in evidence_rows if row.get('verdict') == 'wrong']
            if wrong_rows:
                local_reasons = [{**wrong_rows[0], 'deduction_reason': remark}]
        if local_reasons:
            # More than three reasons for one answer on one page are one note: the
            # question's own remark, beside its first error, with the crosses still on
            # every entry. Q12 of 3319af12 carried seven "No transaction-wise entry
            # shows ..." notes for an answer that showed final totals only.
            per_page: dict[int, list] = {}
            for row in local_reasons:
                per_page.setdefault(row['page'], []).append(row)
            merged = []
            for rows_on_page in per_page.values():
                distinct = list(dict.fromkeys(row['deduction_reason'] for row in rows_on_page))
                if len(distinct) > 3:
                    first = min(rows_on_page, key=lambda r: ((r.get('anchor_bbox') or r['bbox'])[1]))
                    merged.append({**first, 'deduction_reason': (remark or '').strip() or distinct[0]})
                else:
                    merged.extend(rows_on_page)
            local_reasons = merged
            seen_reasons = set()
            labelled_pages = set()
            # The question's score is written ONCE: beside its circled total when it
            # has one, else at its first reason. Every other page of the answer names
            # the question without the score. CA faculty review (17 Oct 2026): "Q4c
            # marks allotted twice on page 3 and 4", the same 1/2 printed on both.
            total_pages = sorted({r['page'] for r in evidence_rows if r.get('question_total')})
            reason_pages = {r['page'] for r in local_reasons}
            score_page = next((p for p in total_pages if p in reason_pages), None)
            if score_page is None and not total_pages:
                score_page = min(reason_pages) if reason_pages else None
            score_shown = False
            for reason_row in sorted(local_reasons, key=lambda r: (r['page'], (r.get('anchor_bbox') or r['bbox'])[1])):
                signature = (reason_row['page'], reason_row['deduction_reason'])
                if signature in seen_reasons:
                    continue
                seen_reasons.add(signature)
                reason_page = student_doc[reason_row['page'] - 1]
                box = reason_row.get('anchor_bbox') or reason_row['bbox']
                give_score = not score_shown and reason_row['page'] == score_page
                drawn = _draw_strip_entry(reason_page, qid, aw, mx,
                                          _margin_reason(reason_row['deduction_reason'], remark),
                                          (box[1]+box[3])/2, show_score=give_score,
                                          label_only=not give_score and reason_row['page'] not in labelled_pages)
                score_shown = score_shown or give_score
                labelled_pages.add(reason_row['page'])
                if drawn is not None:
                    render_report.render_manifest[-1]['reason_line_id'] = reason_row.get('line_id')
                    render_report.render_manifest[-1]['reason_page'] = reason_row['page']
            for total_row in evidence_rows:
                if total_row.get('question_total') and total_row['page'] not in reason_pages:
                    box = total_row['bbox']
                    _draw_strip_entry(student_doc[total_row['page']-1], qid, aw, mx, '', (box[1]+box[3])/2,
                                      show_score=not score_shown, label_only=score_shown)
                    score_shown = True
            return
        if evidence_rows:
            first_row = max(evidence_rows, key=lambda row: (row["page"], row["bbox"][1]))
            page = student_doc[first_row["page"] - 1]
            box = first_row['bbox'] if first_row.get('question_total') else first_row.get('anchor_bbox')
            if box:
                anchor_lines = [dict(zip(('x0', 'y0', 'x1', 'y1'), box))]
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
    item = re.fullmatch(r"\s*[Qq]?\s*\d+\.(\d+)\s*", qid or "")
    return (int(match.group(1)) if match else 9999, int(item.group(1)) if item else 0, suffix)


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
    report = _RENDER_REPORT.get()
    if len(by_page) > 1 and not (report is not None and report.evidence_contract):
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
                verdict=entry["verdict"], forward_only=True)
            if bottom is not None:
                cursor = max(cursor, bottom + 4.0)


def _lines_the_grader_read(report, q, lines: list[dict], by_lid: dict) -> list[dict]:
    """Trim a guessed answer region to the rows the grader actually cited.

    The answer map assigns every OCR line to a question before grading, and when
    it guesses ("inferred", or "located" on a weak label match) it guesses big.
    MEASURED 2026-09-16 on the SST review batch: a one-mark fill-in answer, "c.
    Intermountain", was mapped to the LAST line of page 1 plus the first ten
    lines of page 2 - another question's answer - so its circled mark and its
    margin remark came out beside "Depositional plains" a page away, while the
    grader had cited the right line all along. A neighbour's guessed region ran
    on into "Part II" and the next question's opening line, and its total was
    circled there.

    A region the map located from the student's own label, and which holds every
    line the grader cited, is kept whole: the map and the grader agree, and a
    prose answer that carries on to the next page keeps its continuation.
    Otherwise the question owns the lines the grader cited (where the cited quote
    really is on that line), the mapped lines between them, and the mapped lines
    that follow the last citation on the same page up to a heading or a line
    another question cites - the rest of the answer - and nothing on any other
    page. (The map's own confidence number is OCR confidence, 0.0 on a clean
    synthetic page, so it says nothing about whether the label match was right.)
    """
    from anchors import _quote_score
    region = report.answer_regions.get(normalize_qid(q.qid), {})
    mapped = set(region.get("line_ids", []))
    cited: list[dict] = []
    for criterion in (getattr(q, "criteria", None) or []):
        line = by_lid.get((getattr(criterion, "step_line_id", "") or "").strip())
        if line is None or any(line["lid"] == c["lid"] for c in cited):
            continue
        if _quote_score(getattr(criterion, "anchor_quote", "") or "", line.get("text") or "") > 0:
            cited.append(line)
    # A line another question's grader cited, with its quote on that line, is that
    # question's answer whatever the map says: on a live copy the map ran Q7.b on
    # across the page break into "a) The mountains which are formed...", which
    # Q10.a's grader had cited, and every slot beside Q10.a's answer was then
    # refused as "beside Q7.b".
    claimed: set[str] = set()
    for other in report.questions:
        if other.qid == q.qid:
            continue
        for criterion in (getattr(other, "criteria", None) or []):
            line = by_lid.get((getattr(criterion, "step_line_id", "") or "").strip())
            if line is not None and _quote_score(getattr(criterion, "anchor_quote", "") or "",
                                                 line.get("text") or "") > 0:
                claimed.add(line["lid"])
    own_cited = {line["lid"] for line in cited}
    lines = [line for line in lines if line["lid"] not in claimed or line["lid"] in own_cited]
    trusted = (str(region.get("status") or "") == "located"
               and all(line["lid"] in mapped for line in cited))
    if trusted or not cited:
        return lines
    order = lambda line: (line["page"], line["y0"], line["x0"])  # noqa: E731
    first = min((line["page"], line["y0"]) for line in cited)
    last = max((line["page"], line["y1"]) for line in cited)
    mine = {line["lid"] for line in lines}
    keep = {line["lid"] for line in cited}
    for line in lines:
        if first <= (line["page"], line["y0"]) and (line["page"], line["y1"]) <= (last[0], last[1] + 2):
            keep.add(line["lid"])
    theirs: set[str] = set()
    for other in report.questions:
        if other.qid == q.qid:
            continue
        theirs.update((getattr(c, "step_line_id", "") or "").strip()
                      for c in (getattr(other, "criteria", None) or []))
        theirs.add((getattr(other, "anchor_line_id", "") or "").strip())
    theirs.discard("")
    tail = sorted((line for line in by_lid.values()
                   if line["page"] == last[0] and line["y0"] > last[1] - 2), key=order)
    # The rest of the answer follows its last cited row closely; a mapped row that
    # sits a blank band further down is another answer the map swept in.
    heights = sorted(line["y1"] - line["y0"] for line in by_lid.values() if line["y1"] > line["y0"])
    typical = _DOC_LINE_H.get() or (heights[len(heights) // 2] if heights else 20.0)
    previous_bottom = last[1]
    for line in tail:
        if line["lid"] not in mine or line["lid"] in theirs or _is_header(line.get("text") or ""):
            break
        if line["y0"] - previous_bottom > 1.8 * typical:
            break
        keep.add(line["lid"])
        previous_bottom = max(previous_bottom, line["y1"])
    chosen = {line["lid"]: line for line in lines if line["lid"] in keep}
    for line in cited:
        chosen.setdefault(line["lid"], line)
    return sorted(chosen.values(), key=order)


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
    render_report = _RENDER_REPORT.get()
    strict = bool(render_report is not None and render_report.evidence_contract)
    if not strict:
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
        # An MCQ's answer line IS its label - "Q.1 (D) 25W" - so the header test
        # below would snap it onto the NEXT question's line, and region_start would
        # then floor its tick there. It has exactly one line and this is it.
        if pl and (not _is_header(pl["text"]) or _is_objective_mark(q)):
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

    # Decide every question's lines before drawing any, so a slot test for one
    # question sees the others' lines as they will actually be drawn.
    owned_lines: dict[str, list[dict]] = {}
    _OWNED_LIDS.clear()
    if strict:
        from grading_quality import answer_mark_pages, answer_line_ids
        for q in qs_sorted:
            pages = answer_mark_pages(render_report, q)
            lines = [by_lid[lid] for lid in answer_line_ids(render_report, q)
                     if lid in by_lid and by_lid[lid]["page"] in pages]
            owned_lines[q.qid] = _lines_the_grader_read(render_report, q, lines, by_lid)
        for q in qs_sorted:
            _OWNED_LIDS[q.qid] = {line["lid"] for line in owned_lines[q.qid]}

    # Shared page->[y] registry so marks from DIFFERENT questions on the same page
    # (the MCQ block especially) stagger vertically instead of overlapping.
    placed_by_page: dict[int, list[float]] = {}

    for qi, q in enumerate(qs_sorted):
        if strict:
            lines = owned_lines[q.qid]
            _render_question_marks(student_doc, q, lines, by_lid, placed_by_page,
                                   objective_queue=objective_queue)
            if q.attempted:
                marked_pages = {r["page"] for r in render_report.render_manifest
                                if r["qid"] == q.qid
                                and r["role"] in {"answer", "step", "total", "verdict"}}
                # A prose answer can span pages while earning one overall mark.
                # Bracket its continuation to show it was read, without inventing
                # a second score or implying every line deserves a correctness tick.
                for pn in sorted({line["page"] for line in lines} - marked_pages):
                    continuation = [line for line in lines if line["page"] == pn
                                    and not _is_header(line["text"])]
                    if not continuation or not marked_pages:
                        continue
                    page = student_doc[pn - 1]
                    # A continuation label identifies the answer without a
                    # bracket that resembles an incomplete or clipped mark.
                    _draw_continuation_label(student_doc, q.qid, continuation)
            continue
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
    if strict:
        _note_pages_nobody_marked(student_doc, by_lid)


# A page needs this many rows of writing (not headings) before it counts as a page
# of answer that must carry a mark.
_UNMARKED_PAGE_MIN_ROWS = 3


def _continued_answer(report, by_lid, pno: int) -> str:
    """The question a page of writing with no mark on it continues, or "".

    Its own lines name it when the answer map gave them to a question. Otherwise a page
    with no question of its own carries on the answer that ended the page before: on
    the teaching copies of 21 Oct 2026, the explanations of both Python modes (CS Q9,
    page 10), the end of an essay (History Q4, page 6) and a conclusion (Geography Q9,
    page 12) were each labelled "Not evaluated", although the marks paid for them."""
    regions = [r for r in (getattr(report, "answer_regions", None) or {}).values()
               if str(r.get("status") or "") == "located" and r.get("qid")]
    attempted = {normalize_qid(q.qid) for q in report.questions if bool(getattr(q, "attempted", True))}
    regions = [r for r in regions if normalize_qid(r["qid"]) in attempted]
    on_page = {}
    for region in regions:
        count = sum(1 for lid in region.get("line_ids") or []
                    if lid in by_lid and int(by_lid[lid]["page"]) == pno)
        if count:
            on_page[region["qid"]] = count
    if on_page:
        return max(on_page, key=on_page.get)
    if any(pno in [int(p) for p in r.get("pages") or []] for r in regions):
        return ""
    last, best = "", None
    for region in regions:
        for lid in region.get("line_ids") or []:
            line = by_lid.get(lid)
            if line is None or int(line["page"]) != pno - 1:
                continue
            if best is None or float(line["y1"]) > best:
                last, best = region["qid"], float(line["y1"])
    return last


def _note_pages_nobody_marked(student_doc, by_lid) -> list[int]:
    """Write a margin note on every page of writing that carries no mark at all.

    CA faculty review (17 Oct 2026): "some pages are not checked", "second last page
    is not checked as no comments, no marks allotted there, even no ZERO mentioned",
    "no comment is provided for the extra attempted question, despite it being
    unchecked". A continuation page of a marked answer already gets its seen tick
    (_draw_continuation_label); these are the pages no question claimed. An extra
    attempt says it was not counted; any other page says it was not evaluated, and on
    a CA paper the copy goes to the faculty with the page named. Returns the pages."""
    report = _RENDER_REPORT.get()
    if report is None:
        return []
    marked = {int(r.get("page") or 0) for r in report.render_manifest
              if r.get("role") in {"answer", "step", "total", "verdict", "continuation", "feedback"}}
    rows_by_page: dict[int, list[dict]] = {}
    for line in by_lid.values():
        text = (line.get("text") or "").strip()
        if len(text) >= 3 and not _is_header(text):
            rows_by_page.setdefault(int(line["page"]), []).append(line)
    extras = {int(x.get("page") or 0): x for x in (getattr(report, "uncounted_choice", None) or [])}
    # The same page photographed twice (CA faculty review, 18 Oct 2026: "Q1a repeated so
    # it should be marked as repeated"): the second photo says so instead of "not
    # evaluated". One that carries marks anyway is named for the faculty on a CA copy.
    from repeated_pages import repeated_pages
    repeats = repeated_pages({pno: [line.get("text") or "" for line in rows]
                              for pno, rows in rows_by_page.items()})
    ca_paper = bool(getattr(report, "whole_total", False))
    for pno, earlier in sorted(repeats.items()):
        reason = (f"Page {pno} repeats page {earlier} (the same writing photographed twice) and "
                  "carries marks. Check that nothing on it is paid twice.")
        if pno in marked and ca_paper and reason not in report.review_reasons:
            report.review_reasons.append(reason)
    noted: list[int] = []
    # A page of writing the OCR read nothing from (not a blank page) has no rows at
    # all, so it was passed over in silence. CA faculty review (23 Oct 2026, FM
    # 1bc804b5): "Last page is not checked", a dark sideways photo of page 5.
    for pno in sorted(_UNREAD_PAGES.get() or ()):
        if pno in marked or pno in rows_by_page or not 1 <= pno <= len(student_doc):
            continue
        y = student_doc[pno - 1].rect.height * 0.12
        if _draw_strip_entry(student_doc[pno - 1], "", 0.0, 0.0, "", y, show_score=False,
                             note="Not evaluated: this page could not be read.") is not None:
            noted.append(pno)
        if ca_paper:
            reason = (f"Page {pno} could not be read (no writing was found on the photo), so it "
                      "carries no mark. Check it by hand.")
            if reason not in report.review_reasons:
                report.review_reasons.append(reason)
    for pno in sorted(rows_by_page):
        rows = rows_by_page[pno]
        if pno in marked or len(rows) < _UNMARKED_PAGE_MIN_ROWS or not 1 <= pno <= len(student_doc):
            continue
        top = min(rows, key=lambda line: line["y0"])
        extra = extras.get(pno)
        if pno in repeats:
            note = f"Repeated page. The same writing is evaluated on page {repeats[pno]}."
            qid = ""
        elif extra is not None:
            from wording import uncounted_note
            note = f"{_label(str(extra.get('qid') or ''), report)} not counted. {uncounted_note(extra)}"
            qid = str(extra.get("qid") or "")
        elif _continued_answer(report, by_lid, pno):
            qid = _continued_answer(report, by_lid, pno)
            note = f"{_label(qid, report)} continued."
        else:
            note = "Not evaluated: this page could not be matched to a question."
            qid = ""
            if bool(getattr(report, "whole_total", False)):
                reason = (f"Page {pno} has writing that no question was matched to, so it carries "
                          "no mark. Check which answer it belongs to.")
                if reason not in report.review_reasons:
                    report.review_reasons.append(reason)
        if _draw_strip_entry(student_doc[pno - 1], qid, 0.0, 0.0, "", (top["y0"] + top["y1"]) / 2,
                             show_score=False, note=note) is not None:
            noted.append(pno)
    return noted


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
            target_page, bbox_px, img_w, img_h, line_text = line_index[a.target_line_id]
            if target_page is not None and target_page != a.page:
                continue
            x0, y0, x1, y1 = _pdf_coords_from_line(page, bbox_px, img_w, img_h)
            y = (y0 + y1) / 2
            y_under = y1 - 2.0
            measured = None
            source = (_OCR_WORDS.get() or {}).get(a.target_line_id)
            if source and a.target_word:
                measured = word_target_bbox(source[0], source[1], a.target_word)
            strict = bool(_RENDER_REPORT.get() and _RENDER_REPORT.get().evidence_contract)
            if strict and not measured:
                continue
            if measured:
                wx0, wy0, wx1, wy1 = _pdf_coords_from_line(page, measured, img_w, img_h)
                x, y = wx0, (wy0 + wy1) / 2
                y_under, width = wy1, wx1 - wx0
            elif a.target_word and line_text.count(a.target_word) > 1:
                # The word occurs more than once on this line and nothing says which
                # one is meant. _word_x_in_line takes the FIRST, and the first is
                # usually the CORRECT use — "Purchases 3,21,700 add omitted
                # Purchases 400" would be marked on the legitimate entry, up to
                # 278pt from the error. Say nothing rather than accuse the right one.
                continue
            elif a.target_word and a.target_word in line_text:
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
            if _RENDER_REPORT.get() and _RENDER_REPORT.get().evidence_contract:
                continue
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


def _rs_scale(W: float) -> float:
    """How much bigger than its A4 design the cover strip is drawn on a page W wide.

    The type sizes below were set for a scanned A4 sheet plus its margin (about
    955pt). A phone photo comes in at 1500-2300pt and the same 11.5pt remarks on it
    are unreadable once the copy is viewed at page width.
    """
    return max(1.0, min(3.0, W / 955.0))


def _rs_columns(x0: float, W: float, k: float = 1.0) -> tuple[float, list[float]]:
    """Return (column width, [x of each of the 3 columns]) for a strip starting at x0."""
    col_w = (W - 2 * _RS_PAD * k - 2 * _RS_GUT * k) / 3
    xs = [x0 + _RS_PAD * k + i * (col_w + _RS_GUT * k) for i in range(3)]
    return col_w, xs


def _rs_wrap_count(text: str, max_w: float, size: float, fontname: str = "helv") -> int:
    words = (text or "").split()
    if not words:
        return 0
    line, n = "", 0
    for w in words:
        trial = (line + " " + w).strip()
        # Measured as _draw_text lays it out: Helvetica has no Devanagari, so the
        # Hindi cover was measured a third too short and its "Where marks were lost"
        # ran out of the box and under the first page (EMRS PGT Hindi, 22 Oct 2026).
        if _text_width(trial, size, lambda t: fitz.get_text_length(
                t, fontname=fontname, fontsize=size)) > max_w:
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
        if not s.endswith(('.', '?', '!', ']', '।', '॥')):
            s += '.'
        total += max(1, _rs_wrap_count(s, max_w - 20, size))
    return total


def _report_strip_lost(report: GradeReport) -> list:
    return [q for q in report.questions
            if q.score < q.max_score and ((getattr(q, "remark", "") or "").strip() or _ca_unattempted(q, report))]


def _ca_unattempted(q, report) -> bool:
    return bool(getattr(report, "whole_total", False)) and not bool(getattr(q, "attempted", True))


def _strip_lost_text(q, report) -> str:
    """One "Where marks were lost" line. On a CA copy a question the student did not
    write says so in one form, whatever the grader wrote for it. CA faculty review
    (18 Oct 2026, Business Laws f87b56cc): "Q2(c): No Supreme Court significance answer
    supplied" was to read "Unattempted, no answer provided for Q2(c), 0/6 marks"."""
    if _ca_unattempted(q, report):
        return f"{_label(q.qid, report)}: Not attempted. No answer was provided, 0/{_fmt_mark(q.max_score)} marks."
    return f"{_label(q.qid, report)}: {q.remark}"


def _report_strip_height(report: GradeReport, W: float) -> float:
    """Height the top report strip needs for this report at width W (tallest of 3 columns)."""
    k = _rs_scale(W)
    col_w, _ = _rs_columns(0, W, k)
    body_start = (_RS_PAD + _RS_BANNER_H + 8 + _RS_TOTAL_SIZE + 14) * k
    c1 = 1 + _rs_bullet_lines(report.overall_remarks, col_w, _RS_BODY_SIZE * k)
    c2 = 1 + len(report.section_totals)
    if _kinds_restart_numbering(report.section_totals):
        c2 += 2
    lost = _report_strip_lost(report)
    c3 = (1 + sum(_rs_wrap_count(_strip_lost_text(q, report), col_w, _RS_BODY_SIZE * k)
                  for q in lost)) if lost else 0
    body_lines = max(c1, c2, c3, 1)
    return body_start + body_lines * _RS_LINE * k + _RS_PAD * k


def _draw_report_strip(page: fitz.Page, x0: float, y0: float, W: float, report: GradeReport) -> None:
    """Draw the whole report into a landscape band at the top of page 1: EVALUATED banner +
    total score across the top, then three columns (Remarks | Question-wise Totals | Where
    marks were lost)."""
    k = _rs_scale(W)
    col_w, xs = _rs_columns(x0, W, k)
    pad, gut, banner_h = _RS_PAD * k, _RS_GUT * k, _RS_BANNER_H * k
    total_size, head_size, body_size, line_h = (_RS_TOTAL_SIZE * k, _RS_HEAD_SIZE * k,
                                                _RS_BODY_SIZE * k, _RS_LINE * k)

    page.draw_rect(fitz.Rect(x0 + pad, y0 + pad, x0 + W - pad, y0 + pad + banner_h),
                   color=RED, width=1.5)
    tw = fitz.get_text_length("EVALUATED", fontname="hebo", fontsize=24 * k)
    page.insert_text((x0 + (W - tw) / 2, y0 + pad + banner_h * 0.68), "EVALUATED",
                     fontsize=24 * k, color=RED, fontname="hebo")
    ty = y0 + pad + banner_h + 8 * k + total_size
    # No teacher writes "16.0 out of 20.0". The trailing .0 is a float artefact and
    # it is the first thing a reader notices about a machine-marked sheet.
    page.insert_text((x0 + pad, ty),
                     f"Total Score: {_fmt_mark(_shown_total(report))} out of "
                     f"{_fmt_mark(report.max_total)}",
                     fontsize=total_size, color=RED, fontname="hebo")
    by = ty + 14 * k   # body (columns) start y

    # Column 1 — Remarks
    page.insert_text((xs[0], by), "Remarks:", fontsize=head_size, color=BLACK, fontname="hebo")
    _draw_bulleted_remarks(page, xs[0], by + line_h,
                           _sanitize_for_pdf(report.overall_remarks),
                           size=body_size, color=BLACK, max_width=col_w)

    # Column 2 — Question-wise Totals
    page.insert_text((xs[1], by), "Marks awarded:", fontsize=head_size, color=BLACK, fontname="hebo")
    yy = by + line_h
    # Name the sections when the paper has both. Otherwise "Q1: 2/2" (an MCQ) sits
    # directly above "Q1a: 1/5" (a different question in another part) with nothing
    # to say they are not the same question's parts.
    label_sections = _kinds_restart_numbering(report.section_totals)
    shown_kind = None
    for sec in report.section_totals:
        kind = bool(sec.get("objective"))
        if label_sections and kind != shown_kind:
            page.insert_text((xs[1], yy),
                             "Objective" if kind else "Descriptive",
                             fontsize=head_size - 1, color=(0.42, 0.42, 0.42),
                             fontname="hebo")
            yy += line_h
            shown_kind = kind
        page.insert_text((xs[1], yy),
                         f"{_label(sec.get('qid', '?'), report)}: {_fmt_mark(sec.get('score', 0))}"
                         f"/{_fmt_mark(sec.get('max_score', 0))}",
                         fontsize=body_size, color=RED, fontname="hebo")
        yy += line_h

    # Column 3 — Where marks were lost
    lost = _report_strip_lost(report)
    if lost:
        page.insert_text((xs[2], by), "Where marks were lost:", fontsize=head_size, color=RED, fontname="hebo")
        yy = by + line_h
        for q in lost:
            yy = _draw_text(page, xs[2], yy,
                            _sanitize_for_pdf(_strip_lost_text(q, report)),
                            size=body_size,
                            color=RED, fontname="helv", max_width=col_w)


_BLOCK_KINDS = ("table", "diagram")


def _cites_block_evidence(report, ocr_pages) -> bool:
    """True when a graded answer's evidence is a tall table or diagram block.

    The teacher layout ticks rows of writing. A table or a diagram is one OCR block;
    its steps are placed from the visual verifier's row boxes and checking plans,
    which the legacy renderer draws and its CA and commerce suites cover. Measured
    on the stored Accounting copy 4b6e3d85 (Sep 16): on the teacher layout Q2's
    table steps and all of Q3 went unmarked even with verified row boxes.

    Only what the grader cited counts. A diagram that merely sits inside an
    answer's region (Physics 9ccee8a8, Q6.i) is drawn around; that copy is clean
    on the teacher layout and has seven findings on the legacy one.

    A short block (a small table or diagram inside a prose answer, at most
    teacher_layout.BLOCK_ROW_LIMIT of the page) is marked on the teacher layout
    like a few rows of writing; on 49a65640 legacy drew ticks left of the text.
    """
    try:
        from teacher_layout import BLOCK_ROW_LIMIT as limit
    except ImportError:
        limit = 0.17
    tall = set()
    for page in ocr_pages or []:
        height = float(getattr(page, "image_height", 0) or 0)
        for line in page.lines:
            if (getattr(line, "kind", "text") or "text") not in _BLOCK_KINDS:
                continue
            if height <= 0 or (line.bbox[3] - line.bbox[1]) > limit * height:
                tall.add(line.line_id)
    for q in getattr(report, "questions", None) or []:
        if not getattr(q, "attempted", True):
            continue
        ids = [str(getattr(q, "anchor_line_id", "") or "")]
        ids += [str(getattr(c, "step_line_id", "") or "") for c in getattr(q, "criteria", None) or []]
        if any(i in tall for i in ids):
            return True
    return False


def build_evaluated_pdf(student_pdf_bytes: bytes, student_filename: str,
                        student_pngs: list[bytes], report: GradeReport,
                        ocr_pages: list[PageOCR] | None = None,
                        mark_scale: float = 1.0,
                        safe_layout: bool = False,
                        reviewed_evidence: dict | None = None) -> bytes:
    """Build the evaluated PDF and return its bytes.

    If the student submission was a PDF, we annotate the original pages directly.
    Otherwise we build PDF pages from the student PNGs.

    mark_scale shrinks (or grows) the inline ticks, crosses and circled marks —
    CA/commerce sheets are dense and small-handwritten, so they need smaller marks
    than a school sheet for the working to stay readable underneath.
    """
    if reviewed_evidence is not None:
        _validate_reviewed_evidence(student_pdf_bytes, report, reviewed_evidence)
    token = _MARK_SCALE.set(max(0.3, min(2.0, float(mark_scale or 1.0))))
    layout_token = _SAFE_LAYOUT.set(bool(safe_layout))
    # One handwriting size for one booklet: the median height of its own OCR lines,
    # in PDF points. Every tick, cross and circled mark on this copy is drawn from
    # it, so they come out of the same hand instead of following whatever height OCR
    # happened to return for each line.
    # One mark size per copy, scaled to its handwriting, is the answer to "the
    # ticks are sometimes big and sometimes small". Every drawing routine tries
    # this copy's size first and falls back down a ladder to the A4 sizes, so a
    # dense row still gets the mark it used to get rather than none.
    page_heights = _page_point_heights(student_pdf_bytes, student_pngs) if _UNIFORM_MARK_SIZES else []
    doc_line_token = _DOC_LINE_H.set(
        _median_line_height(ocr_pages, page_heights) if _UNIFORM_MARK_SIZES else 0.0)
    pitch_token = _DOC_ROW_PITCH.set(
        _doc_row_pitch(ocr_pages, page_heights) if _UNIFORM_MARK_SIZES else 0.0)
    report_token = _RENDER_REPORT.set(report)
    words = {line.line_id: (page, line) for page in ocr_pages or [] for line in page.lines}
    words_token = _OCR_WORDS.set(words)
    unread_token = _UNREAD_PAGES.set(frozenset(
        int(page.page) for page in ocr_pages or []
        if not page.lines and not getattr(page, "blank", False)))
    strip_canvas = fitz.open()
    strip_token = _STRIP_CANVAS.set(strip_canvas)
    try:
        # The renderer keeps per-document registries in module-level dicts (content
        # widths, strip cursors, ink/remark bands) and clears one of them wholesale at
        # the start of a build. Two concurrent builds — two server jobs, or a parallel
        # evaluation run — would corrupt each other's geometry, so builds are serialized.
        # Rendering is cheap next to the model calls, so this costs nothing noticeable.
        with _RENDER_LOCK:
            report.render_manifest = []
            report.render_requirements = []
            report.render_page_layouts = {}
            # Both layouts draw from the rows the grader actually cited, not from
            # the answer map's guesses (evidence_repair.py). A repair that cannot
            # run leaves the report exactly as graded.
            try:
                from evidence_repair import repair_report_evidence
                repair_doc = _open_student_doc(student_pdf_bytes, student_filename, student_pngs)
                try:
                    if reviewed_evidence is None:
                        repair_report_evidence(report, list(ocr_pages or []), repair_doc)
                finally:
                    repair_doc.close()
                words.update({line.line_id: (page, line)
                              for page in ocr_pages or [] for line in page.lines})
            except Exception as exc:  # noqa: BLE001 - never hold a copy over its bookkeeping
                import logging
                logging.getLogger(__name__).warning("evidence repair skipped: %s", exc)
            use_teacher = False
            if TEACHER_LAYOUT or reviewed_evidence is not None:
                try:
                    import teacher_layout
                    # Reports carrying a verifier's checking plan (CA and commerce
                    # ledgers: checking points, section awards, award anchors) have
                    # their reviewed presentation in the legacy layout.
                    use_teacher = (reviewed_evidence is not None or
                           (not teacher_layout.needs_legacy_layout(report)
                           and not _cites_block_evidence(report, ocr_pages)))
                except ImportError:
                    if reviewed_evidence is not None:
                        raise
                    use_teacher = False
            if use_teacher:
                _prepare_report_totals(report)
                student_doc = _open_student_doc(student_pdf_bytes, student_filename, student_pngs)
                try:
                    review_kwargs = ({'reviewed_evidence': reviewed_evidence}
                                     if reviewed_evidence is not None else {})
                    result = teacher_layout.build_teacher_pdf(
                        student_doc, report, list(ocr_pages or []),
                        mark_scale=_MARK_SCALE.get(), kalam_path=_CURSIVE_FONT_FILE,
                        safe_layout=bool(safe_layout), **review_kwargs)
                finally:
                    student_doc.close()
            else:
                # The legacy layout has no margin note for an extra "answer any N"
                # attempt that was not counted, so the cover remarks say it.
                uncounted = list(getattr(report, "uncounted_choice", None) or [])
                extras = [_label(str(x.get("qid") or ""), report) for x in uncounted]
                if extras and "not counted" not in (report.overall_remarks or ""):
                    from wording import uncounted_note
                    names = ", ".join(extras[:-1]) + (" and " if len(extras) > 1 else "") + extras[-1]
                    report.overall_remarks = (
                        (report.overall_remarks or "").rstrip() + " " + names
                        + (" were" if len(extras) > 1 else " was")
                        + " not counted. " + uncounted_note(uncounted[0])).strip()
                result = _build_evaluated_pdf(student_pdf_bytes, student_filename,
                                              student_pngs, report, ocr_pages)
            report.render_manifest_pdf_sha256 = hashlib.sha256(result).hexdigest()
            return result
    finally:
        _SAFE_LAYOUT.reset(layout_token)
        _DOC_LINE_H.reset(doc_line_token)
        _DOC_ROW_PITCH.reset(pitch_token)
        _MARK_SCALE.reset(token)
        _RENDER_REPORT.reset(report_token)
        _OCR_WORDS.reset(words_token)
        _UNREAD_PAGES.reset(unread_token)
        _STRIP_CANVAS.reset(strip_token)
        strip_canvas.close()


def _validate_reviewed_evidence(student_pdf_bytes, report, evidence):
    """Check an explicit local review without manufacturing provider certificates.

    This optional rendering path is for saved evaluations corrected by a reviewer.
    It never changes automatic release decisions or claims a new grading run.
    """
    if not isinstance(evidence, dict):
        raise ValueError('Reviewed evidence must be a source-bound record')
    if evidence.get('render_source_sha256') != hashlib.sha256(student_pdf_bytes).hexdigest():
        raise ValueError('Reviewed evidence does not bind this rendered student PDF')
    if (not evidence.get('reviewer') or evidence.get('all_questions_reviewed') is not True
            or evidence.get('unresolved_issues')):
        raise ValueError('A complete explicit review is required')
    for key in ('review_sha256', 'original_source_sha256', 'official_sha256'):
        value = evidence.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
            raise ValueError('Missing reviewed source provenance')
    with fitz.open(stream=student_pdf_bytes, filetype='pdf') as source:
        count = len(source)
    if sorted(evidence.get('original_pages_reviewed', [])) != list(range(1, count+1)):
        raise ValueError('Every original page needs review')
    expected = {normalize_qid(q.qid):q for q in report.questions}
    if len(expected) != len(report.questions):
        raise ValueError('Duplicate corrected evaluation questions')
    questions = evidence.get('questions', [])
    if len(questions) != len(expected) or {normalize_qid(q.get('qid','')) for q in questions} != set(expected):
        raise ValueError('Reviewed questions do not match the saved evaluation')
    for item in questions:
        q = expected[normalize_qid(item['qid'])]
        if (item.get('score') != q.score or item.get('max_score') != q.max_score
                or item.get('attempted') is not q.attempted
                or item.get('objective') is not q.objective):
            raise ValueError('Reviewed marks do not match the corrected evaluation')
        checks = item.get('checks', [])
        if q.attempted and not checks:
            raise ValueError('Attempted work needs explicit reviewed locations')
        for check in checks:
            page, box = check.get('page'), check.get('bbox')
            if (isinstance(page, bool) or not isinstance(page,int) or not 1 <= page <= count
                    or not isinstance(box,list) or len(box)!=4
                    or any(isinstance(v,bool) or not isinstance(v,(int,float))
                           or not math.isfinite(v) or not 0 <= v <= 1 for v in box)
                    or not box[0]<box[2] or not box[1]<box[3]):
                raise ValueError('Invalid source-reviewed location')
            quote = check.get('quote')
            if (check.get('verdict') not in ('correct','wrong')
                    or not isinstance(quote, str) or not quote.strip()):
                raise ValueError('Reviewed locations need an actual quote and verdict')
            reason = check.get('reason')
            if check['verdict']=='wrong' and (not isinstance(reason,str) or not reason.strip()):
                raise ValueError('Wrong reviewed work needs a correction')
        owned_ids = {f"faculty:{normalize_qid(q.qid)}:{i}" for i in range(len(checks))}
        if q.attempted and q.anchor_line_id not in owned_ids:
            raise ValueError('Question award must use its own reviewed location')
        if q.attempted:
            anchor_index = int(q.anchor_line_id.rsplit(':',1)[1])
            if q.page != checks[anchor_index]['page']:
                raise ValueError('Question page must match its reviewed location')
        for criterion in q.criteria:
            if criterion.evidence_kind != 'missing' and criterion.step_line_id not in owned_ids:
                raise ValueError('Criterion must use its own reviewed location')


def _kinds_restart_numbering(sections) -> bool:
    """Whether the objective and descriptive questions share printed numbers (a CA paper's
    Part I MCQ Q1..Q8 and Part II Q1(a)..: "QB1" prints as Q1), so the cover has to keep
    them apart under their own headings."""
    numbers: dict[bool, set[str]] = {True: set(), False: set()}
    for sec in sections:
        digits = "".join(ch for ch in str(sec.get("qid") or "") if ch.isdigit())
        if digits:
            numbers[bool(sec.get("objective"))].add(digits.lstrip("0") or "0")
    return bool(numbers[True] & numbers[False])


def _prepare_report_totals(report: GradeReport) -> None:
    """Recompute the copy's total, denominator and section totals from its questions.

    Shared by both layouts so the cover strip can never disagree with the
    question-wise breakdown.
    """
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
    sec_acc: dict[tuple[bool, str], dict] = {}
    for q in report.questions:
        top = q.qid.split(".")[0].split("(")[0].strip() or q.qid
        objective = _is_objective_mark(q)
        bucket = sec_acc.setdefault((objective, top), {"qid": top, "score": 0.0, "max_score": 0.0,
                                                       "objective": objective})
        bucket["score"] += q.score
        bucket["max_score"] += q.max_score
    if sec_acc:
        # Paper order, objective section first. A CA paper numbers BOTH parts from 1 -
        # Part I is MCQ Q1..Q8 and Part II is Q1(a)..Q4(b) - so ordering on the number
        # alone left every sub-part tied with its MCQ namesake, and Python's stable
        # sort then kept the grader's own order. The cover read "Q1, Q1b, Q1a, Q1c,
        # Q2, Q3, Q3a": two different Q1s adjacent and the sub-parts shuffled.
        # Kinds are kept apart only where their numbers restart. MEASURED 2026-10-22 (MP
        # Board commerce): a paper numbering Q1 to Q11 straight through, with True/False
        # and fill-ins at Q3 to Q6, listed them after the MCQs under "Descriptive".
        split = _kinds_restart_numbering(sec_acc.values())

        def sec_key(d):
            qid = d["qid"]
            # A paper in lettered sections numbers each one from 1, and its ids carry
            # the letter before the number. MEASURED 2026-10-23 (UP LT Grade Home
            # Science, QA1..QA10 and QB1..QB10): ordering on the number first made the
            # cover read "Part A Q1, Part B Q1, Part A Q2, Part B Q2".
            lettered = re.match(r"(?i)q([a-z])\d", qid)
            section = lettered.group(1).lower() if lettered else ""
            digits = "".join(ch for ch in qid if ch.isdigit())
            suffix = "".join(ch for ch in qid if ch.isalpha()).lower().lstrip("q")
            return ((0 if d.get("objective") else 1) if split else 0,
                    section, int(digits) if digits else 9999, suffix)
        report.section_totals = [
            {"qid": d["qid"],
             "score": round(d["score"], 2),
             "max_score": round(d["max_score"], 2),
             "objective": bool(d.get("objective"))}
            for d in sorted(sec_acc.values(), key=sec_key)
        ]



def _open_student_doc(student_pdf_bytes: bytes, student_filename: str,
                      student_pngs: list[bytes]) -> fitz.Document:
    """The student's pages as a PDF: the upload itself, or one page per PNG."""
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

    return student_doc


def _build_evaluated_pdf(student_pdf_bytes: bytes, student_filename: str,
                         student_pngs: list[bytes], report: GradeReport,
                         ocr_pages: list[PageOCR] | None = None) -> bytes:
    _prepare_report_totals(report)

    out = fitz.open()

    student_doc = _open_student_doc(student_pdf_bytes, student_filename, student_pngs)

    # Keep answer annotations in original student coordinates. Score entries go
    # on a separate canvas, composed to the RIGHT after all placement is complete.
    # This preserves OCR geometry and avoids cutting circles at a page boundary.
    _CONTENT_W.clear()
    _UNANCHORED.clear()
    # Cleared wholesale, not by id: the pages marks are placed on belong to the
    # `widened` document built just below, and a freed id() is handed straight back
    # out by CPython — so keying the reset on any one document let the NEXT sheet
    # inherit this one's ink masks and place its marks against the wrong page.
    # Safe because _RENDER_LOCK serialises builds, exactly as for _CONTENT_W.
    _MARK_INK.clear()
    _PAGE_TEXT.clear()
    _MARK_BOXES.clear()
    _SHEET_RIGHT.clear()
    widened = fitz.open()
    strip_canvas = _STRIP_CANVAS.get()
    for pno in range(len(student_doc)):
        src = student_doc[pno]
        w, h = src.rect.width, src.rect.height
        strip = max(STRIP_MIN_WIDTH, w * STRIP_FRACTION)
        npage = widened.new_page(width=w + strip, height=h)
        pdf_safety.show_source_page(npage, fitz.Rect(0, 0, w, h), student_doc, pno)
        # Measured HERE: before the divider is ruled and before any mark is drawn.
        # The divider sits on x=w and bleeds into a clip that ends there, so measuring
        # after it reported the full page width every time and the cap never engaged.
        _SHEET_RIGHT[(id(widened), npage.number)] = _measure_sheet_right(npage, w)
        _CONTENT_W[(id(widened), pno + 1)] = w
        margin_page = strip_canvas.new_page(width=w + strip, height=h)
        _CONTENT_W[(id(strip_canvas), pno + 1)] = w
        margin_page.draw_line((w, 0), (w, h), color=RED, width=.5)
    student_doc.close()
    student_doc = widened
    _reset_strip_state(student_doc)
    _reset_strip_state(strip_canvas)

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
    if not _SAFE_LAYOUT.get() and not report.evidence_contract:
        for i, page in enumerate(student_doc, start=1):
            anns = ann_by_page.get(i, [])
            if anns:
                _draw_inline_annotations(page, anns, line_index=line_index,
                                         earned_line_ids=earned_line_ids)
    # Word marks on language papers (language_marks.py) are code-verified, so they
    # are drawn on the strict contract too. This layout has no ink probe to hand,
    # so it underlines only and leaves the correction off the page.
    word_marks = list(getattr(report, "word_marks", None) or [])
    if word_marks and not _SAFE_LAYOUT.get():
        for i, page in enumerate(student_doc, start=1):
            on_page = [m for m in word_marks if int(m.get("page") or 0) == i]
            if on_page:
                content_w = _CONTENT_W.get((id(student_doc), i), page.rect.width)
                draw_word_marks(page, on_page, content_w, page.rect.height)

    # Attach a single score strip directly to each original page. Step marks that
    # extend past the paper edge share this strip with the reserved feedback area.
    n = len(student_doc)
    if n == 0:
        _draw_report_strip(out.new_page(width=842, height=400), 0, 0, 842, report)
    else:
        for pno, source_page in enumerate(student_doc):
            cw = _content_width(source_page)
            content_right = cw
            margin_w = source_page.rect.width - cw
            h = source_page.rect.height
            sw = margin_w + content_right
            header = _report_strip_height(report, sw) if pno == 0 else 0
            if header:
                # A whole number of raster pixels at the ink DPI. The release check
                # rasterises the finished page and the renderer rasterised the sheet
                # alone; with the sheet on a pixel boundary the two rasters of it are
                # identical, so what one calls ink the other does too. MEASURED
                # 2026-09-16: at a fractional offset a faint printed rule fell from
                # 43% row coverage to 31%, was erased in one raster and kept in the
                # other, and twelve numerals on one copy were reported as covering
                # writing that the renderer had probed and found clear.
                unit = 72.0 / _INK_DPI
                header = math.ceil(header / unit - 1e-9) * unit
            page = out.new_page(width=sw, height=h + header)
            if header:
                _draw_report_strip(page, 0, 0, sw, report)
                page.draw_line((0, header), (sw, header), color=RED, width=1)
            pdf_safety.show_source_page(page, fitz.Rect(0, header, sw, header + h), student_doc, pno)
            pdf_safety.show_source_page(page, fitz.Rect(content_right, header, sw, header + h),
                                        strip_canvas, pno, clip=fitz.Rect(cw, 0, cw + margin_w, h))
            page.draw_line((content_right, header), (content_right, header + h), color=RED, width=1)
            report.render_page_layouts[str(pno + 1)] = {
                "margin_side": "right", "margin_bbox": [content_right, header, sw, header + h],
                "content_bbox": [0, header, cw, header + h],
                "ruling_skew": _RULING_SKEW.get((id(student_doc), pno), 0.0),
            }
            for item in report.render_manifest:
                if item["page"] != pno + 1:
                    continue
                dx = content_right - cw if item["role"] == "total" else 0
                for key in ("bbox", "anchor_bbox", "numeral_bbox", "verdict_bbox"):
                    if item.get(key):
                        item[key][0] += dx
                        item[key][2] += dx
                        item[key][1] += header
                        item[key][3] += header
    student_doc.close()

    buf = BytesIO()
    out.save(buf, garbage=4, deflate=True)
    out.close()
    return buf.getvalue()
