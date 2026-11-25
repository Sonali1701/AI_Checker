# -*- coding: utf-8 -*-
"""Teacher-style layout for the evaluated PDF.

This is the layout the reviewer approved on 2026-09-16 on three ICSE SST copies
(see tools/teacher_mark for the hand-driven original):

* the product's EVALUATED report strip across the top of page 1;
* on every answer a tick (or cross) followed by the circled mark it stands for:
  a circled 1 on a correct MCQ, a circled 0.5 on a half point, a cross and a
  circled 0 on a wrong MCQ. One mark size per copy, taken from the student's own
  row pitch, so nothing is "sometimes big and sometimes small";
* marks only ever sit to the RIGHT of the answer: after the last line of the
  point when the pair fits, else the tick after the line and the circle just past
  the divider, else both just past the divider. Never in the left margin, never on
  the student's writing;
* a right margin that reads "Q7.c 1.5/3" beside each answer, with the reason
  under it only when marks were deducted, in the handwriting font.

The module owns geometry only. Scores, remarks and anchors come from the
GradeReport the grader produced; the wording rule (no semicolons, no dashes) is
applied by `wording.plain_report` before drawing. Placement is judged against the
product's own student-ink raster (pdf_renderer.student_ink_mask), the same raster
the release check reads, so the two cannot disagree about "on the writing".

Marks are drawn on a widened copy of the student's pages (sheet + margin, no
header) and the report strip is composed on top afterwards, exactly as the legacy
renderer does, so anything that intercepts the drawing primitives sees coordinates
in the student's own page space.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from io import BytesIO

import fitz
import pdf_safety
import numpy as np

from wording import plain_report, uncounted_note

STRIP_MIN_W = 215.0      # right margin, points, for an A4-sized sheet
STRIP_FRACTION = 0.36    # wider on big phone photos, in step with the handwriting
GUTTER = 62.0            # room past the divider for a tick + circle that did not fit
HEAD_SIZE = 15.0         # "Q7.c 1.5/3" in the margin, on an A4 sheet
REASON_SIZE = 14.5       # the reason under it
_KALAM_NAME = "Kalam"
_INK_LIMIT = 0.03        # share of a mark's band on the student's writing that counts as a
                         # collision, measured rule-free (see _band_ink): on a tilted phone
                         # photo the ruling residue alone read 4% beside clear paper
_RULE_ROW_SHARE = 0.25   # a pixel row this full across the band is a rule, not a word...
_RULE_RUN_OF_PITCH = 0.10  # ...when the run of such rows is at most this share of the row
                           # pitch in pixels (a rule was 1-2 px on an A4 scan, 7-8 px on a
                           # 1500pt phone photo; writing runs taller than a tenth of a row)
_STRIP_TOP = 26.0
_NOMINAL_PITCH = 26.0    # row pitch of the copies the sizes were tuned on
_SHRINK_LADDER = (1.0, 0.85, 0.72, 0.6, 0.5)   # margin type sizes, largest first

# A line that opens a numbered or lettered point, or a question label: the end of
# the previous point, so a tick never runs on into the next one. A bare "-" is NOT a
# bullet here: Mathpix writes a hyphenated continuation as "- s a highly ..." and
# "- Ses of minerals.", and those are the same point.
_POINT_START_RE = re.compile(
    r"^\s*(?:\(?\s*(?:[a-z]|[ivx]{1,4}|\d{1,2})\s*[).:\]]|q\s*\.?\s*\d|ans\w*\s*[-:.]?\s*[a-z0-9]?\b|[•*•→>]\s)",
    re.IGNORECASE)
# A line that closes a sentence: the point ends here. "e.g." and "i.e." do not.
_SENTENCE_END_RE = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\beg)(?<!\bex)[.!?]\s*$", re.IGNORECASE)
# An OCR line that really holds several rows: a newline or a LaTeX row break in its text.
_MULTIROW_TEXT_RE = re.compile(r"\n|\\\\|\\begin\{array\}")


# A question label in front of the point label: "10 a) Mountains", "Q7 c) i) Saptanga".
_QLABEL_PREFIX_RE = re.compile(r"^\s*(?:q\s*\.?\s*)?\d{1,2}\s*[).:]?\s+", re.IGNORECASE)


def _opens_point(text: str) -> bool:
    """Does this row open a numbered or bulleted point, with or without a question label?"""
    import pdf_renderer as pr
    text = text or ""
    if pr._ENUM_LINE_RE.match(text):
        return True
    rest = _QLABEL_PREFIX_RE.sub("", text, count=1)
    return rest != text and bool(pr._ENUM_LINE_RE.match(rest))


def strip_width(page_w: float) -> float:
    """The right margin for a sheet `page_w` points wide.

    KNOWN GAP: the margin type and the gutter grow with the handwriting
    (TeacherLayout.text_scale) but this width does not, so a narrow phone photo with
    3x type leaves about 110pt for the reason and _wrap_to_width breaks words in the
    middle. Scaling it (max(STRIP_MIN_W * text_scale, ...)) fixes that but changes the
    page geometry that tests/test_strip_layout.py and
    tests/test_student_release_repair.py assume through the renderer's strip
    constants, so it needs those expectations changed with it.
    """
    return max(STRIP_MIN_W, STRIP_FRACTION * float(page_w))


def needs_legacy_layout(report) -> bool:
    """True when the report carries a checking plan the teacher layout does not draw.

    CA and commerce ledgers are verified with checking points, section awards and
    award anchors (grading_quality certificates). Those have their own reviewed
    presentation in the legacy renderer; this layout is for prose answer sheets.
    """
    for cert in (getattr(report, "verified_step_boxes", None) or {}).values():
        if not isinstance(cert, dict):
            continue
        if cert.get("checking_points") or cert.get("award_anchor") or cert.get("award_scope") == "section":
            return True
    return False


# Rows of handwriting. OCR calls a row with an equation "math"; it is written on
# the ruling like any other row and a teacher ticks it the same way. Measured on the
# stored Physics copy 9ccee8a8 (Sep 16): with only "text" accepted, every step
# written as a formula went unmarked (25 release findings against 7 on the legacy
# path). Tables and diagrams are blocks, not rows: pdf_renderer sends a report
# that cites them to the legacy layout. (ai-checker-12, at the user's request.)
_WRITTEN_KINDS = ("text", "math")
# A table or diagram block no taller than this share of the page is a few rows of
# an answer and is marked where it ends, like a multi-row line. The legacy renderer
# refuses an unverified block above the same height (2 x 0.085); pdf_renderer
# sends a copy whose grader cited a taller block (a ledger) to that renderer.
# On 49a65640 the student's two-column Endogenic/Exogenic table (12% of the page)
# went unmarked here while the rest of the copy was clean. (ai-checker-12)
_BLOCK_KINDS = ("table", "diagram")
BLOCK_ROW_LIMIT = 0.17


def _paper_right(rgb, rect: fitz.Rect, scale: float, page_w: float) -> float:
    """Where the paper ends to the right of one written row, in page points.

    Walking right from the row's end, a column is background when under 30% of
    it looks like paper (at least 72% as bright as the paper just to its left,
    and no more coloured than the paper inside the row) or when over 35% of it is
    near black. The paper stops at the first 6pt run of background columns: a
    shadow, the desk, a printed book beside the notebook (d4d4853f page 2), a
    spiral binding (d4d4853f page 1). The brightness reference follows the paper
    as it goes, so a lighting gradient across the sheet (49a65640 page 2 fades to
    55% of its brightness) is not an edge; handwriting leaves paper between its
    strokes. No edge: page_w.
    """
    h, w = rgb.shape[:2]
    y0, y1 = max(0, int(rect.y0 * scale)), min(h, int(math.ceil(rect.y1 * scale)))
    a, b = max(0, int(rect.x0 * scale)), min(w, int(rect.x1 * scale))
    if y1 - y0 < 2 or b - a < 4:
        return page_w
    band = rgb[y0:y1].astype(np.int16)
    lum = band.mean(axis=2)
    sat = band.max(axis=2) - band.min(axis=2)
    inside_l, inside_s = lum[:, a:b], sat[:, a:b]
    bright = inside_l >= np.percentile(inside_l, 50)
    if not bright.any():
        return page_w
    ref = float(np.median(inside_l[bright]))
    paper_s = float(np.median(inside_s[bright]))
    col_l = np.percentile(lum, 70, axis=0)
    need = max(3, int(round(6 * scale)))
    bad = 0
    for x in range(b, w):
        paper = float(np.mean((lum[:, x] >= 0.72 * ref) & (sat[:, x] <= paper_s + 40)))
        dark = float(np.mean(lum[:, x] < 0.35 * ref))
        if paper < 0.3 or dark > 0.35:
            bad += 1
            if bad >= need:
                return (x - bad + 1) / scale
        else:
            bad = 0
            ref = 0.85 * ref + 0.15 * float(col_l[x])
    return page_w


class SimpleLine:
    """Just a box, for mathpix.spread_fold."""
    def __init__(self, bbox):
        self.bbox = bbox


@dataclass
class Line:
    lid: str
    page: int
    rect: fitz.Rect          # in the student's page points
    text: str
    cancelled: bool = False
    kind: str = "text"

    @property
    def yc(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


@dataclass
class Mark:
    """One drawn mark: what it is, where it went, what it stands for."""
    qid: str
    role: str                 # "answer" (whole answer / MCQ), "step" (criteria), "verdict" (bare cross)
    page: int
    kind: str                 # "tick" or "cross"
    value: float | None       # what the circle shows, None for a bare cross
    anchor: fitz.Rect         # the row the mark stands beside (page coords)
    line_id: str              # the line the grader anchored (what certificates name)
    verdict_bbox: fitz.Rect   # the tick / cross
    numeral_bbox: fitz.Rect | None
    criteria: tuple[int, ...] = ()
    maximum: float | None = None
    end_line_id: str = ""     # the line the mark was drawn beside (end of the point)
    question_total: bool = False


class TeacherLayout:
    def __init__(self, raw: fitz.Document, report, ocr_pages, mark_scale: float = 1.0,
                 kalam_path: str | None = None, safe_layout: bool = False,
                 reviewed_evidence: dict | None = None):
        self.raw = raw
        self.report = report
        self.kalam_path = kalam_path
        # The repair pass: a mark must be further from any writing before it may sit
        # on the page, so more of them move past the divider.
        self.ink_limit = _INK_LIMIT * (0.5 if safe_layout else 1.0)
        from preflight import _MARK_INK_BLOCK
        # A circled mark goes only where the release check's own disc measure would
        # pass it; the two read the same raster, so they cannot disagree.
        self.disc_limit = _MARK_INK_BLOCK * (0.5 if safe_layout else 1.0)
        self.lines: dict[str, Line] = {}
        self.by_page: dict[int, list[Line]] = {}
        pitches: list[float] = []
        for pg in ocr_pages or []:
            pno = pg.page
            if pno < 1 or pno > len(raw):
                continue
            page = raw[pno - 1]
            sx = page.rect.width / max(1, pg.image_width)
            sy = page.rect.height / max(1, pg.image_height)
            rows = []
            for ln in pg.lines:
                x0, y0, x1, y1 = ln.bbox
                line = Line(ln.line_id, pno, fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy),
                            ln.text or "", bool(getattr(ln, "cancelled", False)),
                            getattr(ln, "kind", "text") or "text")
                self.lines[line.lid] = line
                rows.append(line)
            rows.sort(key=lambda l: l.yc)
            self.by_page[pno] = rows
            centers = [l.yc for l in rows if l.kind in _WRITTEN_KINDS and not l.cancelled]
            for a, b in zip(centers, centers[1:]):
                if 9 <= b - a <= 60:
                    pitches.append(b - a)
        self.pitch = statistics.median(pitches) if pitches else 22.0
        # Local review locations are kept separate from OCR and verifier records.
        # They do not affect measured handwriting size or masquerade as provider
        # readings. The public renderer validates their source and marks first.
        self.reviewed_evidence = reviewed_evidence
        self.review_lines: dict[str, list[Line]] = {}
        self.review_checks: list[tuple] = []
        self.review_ids: set[str] = set()
        if reviewed_evidence is not None:
            from exam_package import normalize_qid
            for item in reviewed_evidence['questions']:
                qid = normalize_qid(item['qid'])
                owned = []
                for index, check in enumerate(item.get('checks', [])):
                    pno = check['page']
                    page = raw[pno-1]
                    l,t,r,b = check['bbox']
                    lid = f'faculty:{qid}:{index}'
                    line = Line(lid,pno,fitz.Rect(l*page.rect.width,t*page.rect.height,
                                               r*page.rect.width,b*page.rect.height),
                                check['quote'],False,'text')
                    self.lines[lid] = line
                    self.review_ids.add(lid)
                    owned.append(line)
                    self.review_checks.append((qid,index,check,line))
                self.review_lines[qid] = owned
        # The fold of a photographed open notebook, per page: no row is widened
        # across it and no mark crosses it (Sonakshi's Biology copy, 2026-09-30:
        # "3. (d)" was joined to the right page's row, and Q3's mark sat there).
        from mathpix import looks_like_spread, spread_fold
        self.folds: dict[int, float] = {}
        for pno, rows in self.by_page.items():
            page = raw[pno - 1]
            written = [SimpleLine(tuple(l.rect)) for l in rows
                       if l.kind in _WRITTEN_KINDS and not l.cancelled]
            if written and looks_like_spread(written, page.rect.width, page.rect.height):
                fold = spread_fold(written, page.rect.width)
                if fold is not None:
                    self.folds[pno] = fold
        scale = max(0.6, min(1.0, float(mark_scale or 1.0)))
        p = self.pitch * scale
        self.tick_h = 0.95 * p                       # inked height of a tick
        self.tick_s = self.tick_h / 1.6              # pdf_renderer._draw_tick size parameter
        self.tick_w = 1.5 * self.tick_s
        self.cross = 0.72 * p
        # The circle's floor keeps the numeral legible, but on very small writing a
        # circle taller than the row sits on the next row's answer: on Sonakshi's
        # Biology photo (11.5pt rows, 18pt circles) Q3's mark read as Q4's. Below
        # a 20pt pitch the floor follows the rows; above it nothing changes.
        floor = min(4.2, max(2.6, (0.5 * self.pitch - 1.5) / 1.8))
        self.circle_s = max(floor, (0.40 * p - 1.5) / 1.8)   # _draw_number_in_circle size
        self.text_scale = max(1.0, min(3.0, self.pitch / _NOMINAL_PITCH))
        self.strip_w = strip_width(raw[0].rect.width) if len(raw) else STRIP_MIN_W
        self.occupied: dict[int, list[tuple[float, float]]] = {}
        self.placed: dict[int, list[fitz.Rect]] = {}
        self.marks: list[Mark] = []
        self.seen_pages: list[int] = []
        self.unanchored: dict[str, list[int]] = {}
        self._ink: dict = {}
        self.entry_flags: dict[tuple[int, str], dict] = {}
        self._edge: dict[int, float] = {}
        # The report strip on top of page 1. Recomputed in build() once the wording
        # rule has rewritten the remarks it wraps.
        self.header_h = self._strip_height()

    def _strip_height(self) -> float:
        """Height of the report strip, a whole number of raster pixels at the ink DPI.

        The release check rasterises the finished page and the renderer rasterised the
        sheet alone; with the sheet on a pixel boundary the two rasters are identical,
        so what one calls ink the other does too (measured on the legacy path: at a
        fractional offset a faint rule was erased in one raster and kept in the other).
        """
        import pdf_renderer as pr
        total_w = (self.raw[0].rect.width if len(self.raw) else 0.0) + self.strip_w
        unit = 72.0 / pr._INK_DPI
        return math.ceil(pr._report_strip_height(self.report, total_w) / unit - 1e-9) * unit

    # ------------------------------------------------------------------ anchors
    def _region_lines(self, q) -> list[Line]:
        import pdf_renderer as pr
        if self.reviewed_evidence is not None:
            from exam_package import normalize_qid
            return list(self.review_lines.get(normalize_qid(q.qid), []))
        regions = getattr(self.report, "answer_regions", None) or {}
        reg = regions.get(q.qid.lower()) or regions.get(q.qid) or {}
        out = []
        for key in ("label_line_ids", "line_ids", "evidence_line_ids"):
            for cand in reg.get(key) or []:
                if cand in self.lines and self.markable(self.lines[cand]):
                    # A section heading the map swept into the region is not the
                    # answer: Physics 9ccee8a8 Q3's tick stood beside "Section-B".
                    # (ai-checker-12)
                    if cand != getattr(q, "anchor_line_id", "") and pr._is_header(self.lines[cand].text):
                        continue
                    out.append(self.lines[cand])
        return out

    def markable(self, line: Line) -> bool:
        """A row of writing, or a block short enough to be a few rows of one answer."""
        if line.cancelled:
            return False
        if line.kind in _WRITTEN_KINDS:
            return True
        if line.kind in _BLOCK_KINDS:
            return line.rect.height <= BLOCK_ROW_LIMIT * self.raw[line.page - 1].rect.height
        return False

    def block_entry_line(self, q) -> Line | None:
        """Where the margin score goes when the grader cited only a block.

        A ledger table or a diagram is no row to tick, but the question's score
        still belongs beside it: the stored Accounting copy 4b6e3d85 lost Q3's
        margin entry altogether because every citation was the table. The top row
        of the topmost cited block stands in for the entry's position.
        """
        regions = getattr(self.report, "answer_regions", None) or {}
        reg = regions.get(q.qid.lower()) or regions.get(q.qid) or {}
        ids = [str(getattr(q, "anchor_line_id", "") or "")]
        ids += [str(getattr(c, "step_line_id", "") or "") for c in getattr(q, "criteria", None) or []]
        for key in ("label_line_ids", "line_ids", "evidence_line_ids"):
            ids += [str(i) for i in reg.get(key) or []]
        found = [self.lines[i] for i in ids if i in self.lines and not self.lines[i].cancelled]
        if not found:
            return None
        top = min(found, key=lambda l: (l.page, l.rect.y0))
        r = top.rect
        row = fitz.Rect(r.x0, r.y0, r.x1, min(r.y1, r.y0 + self.pitch))
        return Line(top.lid, top.page, row, top.text, False, top.kind)

    def question_anchor(self, q) -> Line | None:
        lid = str(getattr(q, "anchor_line_id", "") or "")
        if self.reviewed_evidence is not None:
            return self.lines.get(lid) if lid in self.review_ids else None
        if lid in self.lines and self.markable(self.lines[lid]):
            return self.lines[lid]
        region = self._region_lines(q)
        if region:
            return region[0]
        for c in getattr(q, "criteria", None) or []:
            line = self.criterion_anchor(q, c)
            if line is not None:
                return line
        # An MCQ whose label the OCR garbled: the grader still reports the page and
        # the vertical position it read the answer at, so take the line nearest that
        # position when one sits within a row of it.
        if bool(getattr(q, "objective", False)):
            pno = int(getattr(q, "page", 0) or 0)
            frac = float(getattr(q, "y_fraction", 0.0) or 0.0)
            rows = self.by_page.get(pno) or []
            if rows and 0.0 < frac < 1.0:
                target = frac * self.raw[pno - 1].rect.height
                best = min(rows, key=lambda l: abs(l.yc - target))
                if abs(best.yc - target) <= 0.75 * self.pitch and best.kind in _WRITTEN_KINDS:
                    return best
        return None

    def criterion_anchor(self, q, c) -> Line | None:
        """The OCR line a criterion belongs to, or None. A struck-out line is None."""
        if self.reviewed_evidence is not None:
            lid = str(getattr(c,'step_line_id','') or '')
            return self.lines.get(lid) if lid in self.review_ids else None
        verified = (getattr(self.report, "verified_step_anchors", None) or {})
        idx = None
        try:
            idx = list(q.criteria).index(c)
        except (ValueError, AttributeError):
            pass
        found = None
        if idx is not None:
            cert = verified.get(f"{q.qid}:{idx}") or verified.get(f"{q.qid.lower()}:{idx}")
            if isinstance(cert, (list, tuple)) and cert and cert[0] in self.lines:
                found = self.lines[cert[0]]
        if found is None:
            lid = str(getattr(c, "step_line_id", "") or "")
            if lid in self.lines:
                found = self.lines[lid]
        if found is None:
            quote = " ".join(str(getattr(c, "anchor_quote", "") or getattr(c, "evidence", "") or "").split()).lower()
            if len(quote) >= 6:
                qpage = int(getattr(q, "page", 0) or 0)
                best, best_score, best_hits = None, 0.0, 0
                for pno, rows in self.by_page.items():
                    if qpage and abs(pno - qpage) > 1:
                        continue
                    for line in rows:
                        if line.kind not in _WRITTEN_KINDS or line.cancelled:
                            continue
                        text = " ".join(line.text.split()).lower()
                        if not text:
                            continue
                        score, hits = _overlap(quote, text)
                        if score > best_score:
                            best, best_score, best_hits = line, score, hits
                if best is not None and best_score >= 0.45 and best_hits >= 2:
                    found = best
        if found is None or not self.markable(found):
            return None
        return found

    def certified_rect(self, q, index: int, line: Line) -> fitz.Rect | None:
        """A verifier's box for this step inside `line`, in page points, if any.

        Ledger rows are one OCR block; the certificate's normalized box says which
        row of it the step is on, so two steps in one block get two marks.
        """
        if line.lid in self.review_ids:
            return line.rect  # explicit local review, never a provider certificate
        from exam_package import normalize_qid
        certs = getattr(self.report, "verified_step_boxes", None) or {}
        cert = certs.get(f"{normalize_qid(q.qid)}:{index}") or {}
        box = cert.get("bbox") if isinstance(cert, dict) else None
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            return None
        try:
            vals = [float(v) for v in box]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in vals):
            return None
        from presentation_layout import BLOCK_ENTRY_ROW, row_box_fits, row_box_limit
        page = self.raw[line.page - 1]
        w, h = page.rect.width, page.rect.height
        line_n = [line.rect.x0 / w, line.rect.y0 / h, line.rect.x1 / w, line.rect.y1 / h]
        if not row_box_fits(line_n, vals, block=row_box_limit(line) == BLOCK_ENTRY_ROW):
            return None
        return fitz.Rect(vals[0] * w, vals[1] * h, vals[2] * w, vals[3] * h)

    def point_end(self, start: Line, stop: set[str]) -> Line:
        """The last line of the point that begins at `start`.

        A teacher ticks where a point ends. The model anchors where it begins, so
        walk down the page while the rows stay close, are not another anchored
        point, do not open a new numbered point, question or heading, and the
        previous row has not already closed its sentence.
        """
        import pdf_renderer as pr
        if start.lid in self.review_ids:
            return start
        if start.kind not in _WRITTEN_KINDS:
            return start                      # a block ends where its own ink ends
        cur = start
        for nxt in self.by_page.get(start.page, []):
            if nxt.yc <= cur.yc or not self.same_column(start, nxt):
                continue
            if _SENTENCE_END_RE.search(cur.text):
                break
            if nxt.yc - cur.yc > 1.8 * self.pitch:
                break
            if nxt.lid in stop or nxt.cancelled or nxt.kind not in _WRITTEN_KINDS:
                break
            if _POINT_START_RE.match(nxt.text):
                break
            # A stray label or OCR fragment ("8 ii.", "A") is not part of the point.
            if sum(ch.isalpha() for ch in nxt.text) < 3:
                break
            try:
                if pr._is_header(nxt.text):
                    break
            except Exception:
                pass
            cur = nxt
        return cur

    def same_column(self, a: Line, b: Line) -> bool:
        """Rows can continue one another within one written column.

        Rows are walked in height order, which on a photo of two facing pages
        alternates between them: on the Parishram Chemistry spreads (20 Sept) the
        walk from Ayushi's "Ans-6- 2" on the left page ended on "well as from N side
        (isocynide)" on the right one, and Q6's mark was drawn there.

        A comparison on one sheet has no notebook fold. Its two disjoint columns
        are still separate answers, even when OCR lists them in height order.
        """
        if a.page != b.page:
            return False
        fold = self.folds.get(a.page)
        if fold is not None:
            if ((a.rect.x0 + a.rect.x1) / 2 < fold) != ((b.rect.x0 + b.rect.x1) / 2 < fold):
                return False
        overlap = min(a.rect.x1, b.rect.x1) - max(a.rect.x0, b.rect.x0)
        indent = abs(a.rect.x0 - b.rect.x0)
        # A merged OCR row starting in the left column can extend over the right
        # column. Its width alone must not make it a continuation of right-column
        # writing (the Capitalism/Socialism comparison is such a case).
        indent_limit = max(1.5 * self.pitch, 0.5 * min(a.rect.width, b.rect.width))
        return indent <= indent_limit and (
            overlap >= 0.2 * min(a.rect.width, b.rect.width)
            or indent <= 1.5 * self.pitch)

    def rows_above_in_point(self, start: Line, end: Line, stop: set[str]) -> list[Line]:
        """The point's own rows above its last row, nearest first (at most two).

        Where a teacher puts the mark when the last row runs to the paper's edge
        and the row under it is written: at the end of the point's previous row,
        not in the notes column. The user's review of the SST Test-05 copies had
        such pairs past the divider ("Akshad's Q7.d 1.5 award sat in the
        gutter"; Aaradhya Q10.d "ideal for food production."). (ai-checker-12)
        """
        rows = self.by_page.get(end.page, [])
        out: list[Line] = []
        if start.page == end.page and start.yc < end.yc - 0.3 * self.pitch:
            out = [o for o in rows if start.yc - 0.3 * self.pitch <= o.yc < end.yc - 0.3 * self.pitch
                   and not o.cancelled and o.kind in _WRITTEN_KINDS and self.same_column(end, o)]
            out.sort(key=lambda o: -o.yc)
            return out[:2]
        # Anchored on the last row itself: walk up while the rows continue one point.
        cur = end
        while len(out) < 2 and not _POINT_START_RE.match(cur.text or ""):
            above = [o for o in rows if o.yc < cur.yc - 0.3 * self.pitch
                     and cur.yc - o.yc <= 1.8 * self.pitch and self.same_column(end, o)]
            if not above:
                break
            prev = max(above, key=lambda o: o.yc)
            # A row that introduces a list ("(d). Ashoka's edicts:") is the answer's
            # heading, not part of the point under it.
            if (prev.lid in stop or prev.cancelled or prev.kind not in _WRITTEN_KINDS
                    or _SENTENCE_END_RE.search(prev.text or "")
                    or re.search(r"[:：]\s*[-–]?\s*$", prev.text or "")):
                break
            out.append(prev)
            cur = prev
        return out

    def row_rect(self, line: Line) -> fitz.Rect:
        """The row the mark stands beside: the line, widened to the row's writing
        when the line is a narrow stub such as a bare "(c)" label far to the left."""
        r = fitz.Rect(line.rect)
        W = self.raw[line.page - 1].rect.width
        # A row of a block the OCR returned as one region (an MCQ array split into
        # rows): the block encloses the row, and the row's writing runs to the
        # block's right edge, not to the end of the fragment the split recorded.
        # Only the block the row was split from: on the tilted d4d4853f page 2 the
        # real line "Mountain" sat inside the previous line's slanted box, took its
        # right end (242 to 541) and its mark dropped beside the next answer.
        # (ai-checker-12)
        parent = line.lid.split("#r")[0] if "#r" in line.lid else None
        for other in self.by_page.get(line.page, []):
            if (parent is not None and other.lid == parent and not other.cancelled
                    and other.rect.contains(r) and other.rect.height > 1.5 * r.height):
                r.x1 = max(r.x1, other.rect.x1)
        if r.x1 > 0.35 * W or (r.x1 - r.x0) > 0.12 * W:
            return r
        end = r.x1
        fold = self.folds.get(line.page)
        for other in self.by_page.get(line.page, []):
            if other.lid == line.lid or other.kind not in _WRITTEN_KINDS or other.cancelled:
                continue
            # Never across the fold of an open notebook: the rest of that row is
            # the other page.
            if fold is not None and r.x1 <= fold <= other.rect.x0:
                continue
            # The rest of a label's row starts to its RIGHT. A tilted neighbour row
            # that starts at the same margin is not it: the word "Mountain" on
            # d4d4853f took the slanted line above's end. (ai-checker-12)
            if other.rect.x0 < r.x1 - 4:
                continue
            overlap = min(r.y1, other.rect.y1) - max(r.y0, other.rect.y0)
            if overlap > 0.4 * min(r.height, other.rect.height):
                end = max(end, other.rect.x1)
        if end > r.x1 + 0.25 * W:
            r.x1 = end
        return r

    # ------------------------------------------------------------------ geometry
    def _page_mask(self, pno: int):
        """The product's own student-ink raster for this page, built once."""
        if pno not in self._ink:
            import pdf_renderer as pr
            try:
                page = self.raw[pno - 1]
                self._ink[pno] = pr.student_ink_mask(page, content_w=page.rect.width)
            except Exception:
                self._ink[pno] = (None, 1.0)
        return self._ink[pno]

    def _ink_fraction(self, pno: int, rect: fitz.Rect) -> float | None:
        """Share of `rect` (page coords) lying on the student's writing, or None.

        Rule-free: the ruling the ink mask failed to erase on a tilted photo is one
        or two pixel rows carrying most of the band's ink, while handwriting spreads
        over many rows. A short run of rows that are mostly ink across the band is
        dropped before the share is taken (measured on an SST copy: clear paper
        beside "Tamil" fell from 4.3% to 0.4%, the unrecorded "25 years" beside a
        truncated OCR row stayed at 8%).
        """
        mask, scale = self._page_mask(pno)
        if mask is None:
            return None
        return _band_ink(mask, scale, rect, self._rule_rows(scale))

    def _rule_rows(self, scale: float) -> int:
        """Longest run of full-width pixel rows still taken for a printed rule."""
        return max(3, int(round(_RULE_RUN_OF_PITCH * self.pitch * scale)))

    def _disc_ink(self, pno: int, bbox) -> float | None:
        """Ink under a circled mark, measured exactly as the release check measures it."""
        from preflight import _ink_under_mark
        mask, scale = self._page_mask(pno)
        if mask is None:
            return None
        return _ink_under_mark(mask, scale, bbox)

    def row_paper_right(self, line: Line) -> float:
        """The paper's right edge beside this row, on a photographed sheet.

        _sheet_right finds the last non-white column, which on a phone photo is
        the frame itself: on d4d4853f page 2 (a notebook beside a printed book)
        it said 750 while the paper ended near 600, the row ends ran on over the
        book's print, and every pair went to the strip. Each row's own edge is
        measured (_paper_right) and the median over the rows within three pitches
        is used, so one misread row cannot move a mark. (ai-checker-12)
        """
        pno = line.page
        page = self.raw[pno - 1]
        W = page.rect.width
        cache = self.__dict__.setdefault("_row_edge_cache", {})
        if pno not in cache:
            edges: dict[str, float] = {}
            try:
                if page.get_images():
                    pix = page.get_pixmap(dpi=72)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    rgb = arr[:, :, :3] if pix.n >= 3 else np.repeat(arr[:, :, :1], 3, axis=2)
                    scale = pix.width / W
                    for other in self.by_page.get(pno, []):
                        if other.kind in _WRITTEN_KINDS and not other.cancelled:
                            edges[other.lid] = _paper_right(rgb, other.rect, scale, W)
            except Exception:
                edges = {}
            cache[pno] = edges
        edges = cache[pno]
        if not edges:
            return W
        rows = self.by_page.get(pno, [])
        near = [edges[o.lid] for o in rows if o.lid in edges and abs(o.yc - line.yc) <= 3 * self.pitch]
        if len(near) < 3:
            near = list(edges.values())
        if len(near) < 3:
            return W
        return float(statistics.median(near))

    def edge_for(self, line: Line) -> float:
        """Right limit for marks beside this row: the sheet, and the paper along the row."""
        right = min(self._sheet_right(line.page), self.row_paper_right(line))
        fold = getattr(self, "folds", {}).get(line.page)
        if fold is not None and line.rect.x1 <= fold + 2:
            # The left page of an open notebook ends where the right page's writing
            # on this row begins (the blank strip at the fold is usable paper), or
            # at the fold when that row of the right page is blank.
            level = [o.rect.x0 for o in self.by_page.get(line.page, [])
                     if o.rect.x0 >= fold and o.kind in _WRITTEN_KINDS and not o.cancelled
                     and min(o.rect.y1, line.rect.y1) - max(o.rect.y0, line.rect.y0)
                     > 0.3 * min(o.rect.height, line.rect.height)]
            right = min(right, (min(level) - 2) if level else fold)
        # Side-by-side comparison columns can occur on a single sheet with no
        # fold. A left-column mark must stop before the actual right-column ink.
        neighbours = [o.rect.x0 for o in self.by_page.get(line.page, [])
                      if o.lid != line.lid and o.rect.x0 >= line.rect.x1
                      and o.kind in _WRITTEN_KINDS and not o.cancelled
                      and not self.same_column(line, o)
                      and abs(o.yc - line.yc) <= 1.8 * self.pitch]
        if neighbours:
            right = min(right, min(neighbours) - 2)
        return right

    def _sheet_right(self, pno: int) -> float:
        """Rightmost column of the photographed paper, measured before any mark is
        drawn (the legacy path's cap). A photo can catch the table beyond a torn
        edge, and the ink mask reads a uniform table as blank paper."""
        if pno not in self._edge:
            import pdf_renderer as pr
            page = self.raw[pno - 1]
            W = page.rect.width
            # _sheet_right_edge is a cache the legacy path fills; measure directly.
            try:
                self._edge[pno] = max(0.0, min(W, float(pr._measure_sheet_right(page, W))))
            except Exception:
                self._edge[pno] = W
        return self._edge[pno]

    def mark_row(self, line: Line, rect: fitz.Rect | None) -> fitz.Rect:
        """The row a mark stands beside.

        A verifier's certified box is used as it is. Otherwise: the last row of a
        multi-row OCR block (Mathpix returns a three-line point as ONE line whose
        box is its widest line; a pair at the block's centre and past that line sat
        beyond the torn edge of a photo), with its end walked along the ink to where
        the writing really stops (a box can also stop short of the words, "3) 21"
        for "Q5 (A) 25 years", and the pair landed on what the box missed). The walk
        continues while ink recurs within a word gap and stops at the last ink
        column, so a second column of answers further right is never joined.
        """
        if rect is not None:
            return fitz.Rect(rect)
        r = self.row_rect(line)
        # A block is several rows when its TEXT says so (a newline or LaTeX row
        # break) or when it is far taller than any one row could be. Height alone
        # is not evidence: on a big or tilted hand a single row's box came 71pt
        # tall on a 35pt pitch ("(Qui) 4 C) Horst") and its "last row" was the
        # "21 years" row below it.
        text_rows = _MULTIROW_TEXT_RE.search(line.text or "") is not None
        if not text_rows and line.kind in _WRITTEN_KINDS and r.height > 2.4 * self.pitch:
            # A one-row box that reaches down into the next OCR row: that part of it
            # is the next row. Jaskeerat's "Q1. (B) played most foully..." box ran
            # 2.7 pitches over the top of "Quo (C) the witches...", its "last row" was
            # Q2's, and Q1's tick sat on the Q2 line. (Parishram English, 20 Sept.)
            below = [o.rect.y0 for o in self.by_page.get(line.page, [])
                     if o.lid != line.lid and not o.cancelled and o.kind in _WRITTEN_KINDS
                     and r.y0 + 0.5 * self.pitch < o.rect.y0 < r.y1 < o.rect.y1
                     and self.same_column(line, o)
                     and min(r.x1, o.rect.x1) - max(r.x0, o.rect.x0) > 0.3 * min(r.width, o.rect.width)]
            if below:
                r.y1 = min(below)
        multi = text_rows or r.height > 2.4 * self.pitch
        if multi and r.height > 1.6 * self.pitch:
            r = fitz.Rect(r.x0, r.y1 - 0.9 * self.pitch, r.x1, r.y1)
        mask, scale = self._page_mask(line.page)
        if mask is None:
            return r
        r = self._settle_on_ink(r, mask, scale)
        h, w = mask.shape[:2]
        # The middle half of the row: its own x-height zone. The full box of a row
        # on a tilted photo overlaps the neighbours' ascenders and descenders, and
        # the walk then ran to the page edge along the next row's words.
        yc, qh = (r.y0 + r.y1) / 2, r.height / 4
        y0, y1 = max(0, int((yc - qh) * scale)), min(h, int(math.ceil((yc + qh) * scale)))
        if y1 <= y0:
            return r
        band = _rule_free(mask[y0:y1, :], self._rule_rows(scale))
        # A column carries writing when ink fills a real part of the row's core, not
        # just two or three pixel rows: a dash of tilted ruling the rule test missed
        # is long but thin. On 139e179a "Ex-Mt.Kilimanjaro." the walk ran from x 518
        # to 557 along such a dash, the pair no longer fit before the divider, and
        # the circle went to the gutter. (Changed by session ai-checker-12.)
        # A column is writing by SHAPE, not by count: thin cursive carries 1-4 ink
        # px per column in the row's core, the same as a tilted printed rule that
        # _rule_free could not classify (ai-checker-12's 139e179a "Ex-Mt.Kilimanjaro."
        # walked 40pt along one). The rule is one contiguous run of at most 3 px in
        # every column; a letter's vertical stroke is taller, or a column crosses
        # two strokes. A quarter of the band height as a bar was too high (cursive
        # columns rarely reach it) and a fixed count cannot tell the two apart.
        count = band.sum(axis=0)
        starts = band[1:] & ~band[:-1]
        runs = starts.sum(axis=0) + band[0].astype(int)
        thin = (count <= 3) & (runs <= 1)
        inked = (count >= 2) & ~thin
        gap_px = max(2, int(0.6 * self.pitch * scale))
        start = max(0, min(w, int(r.x1 * scale)))
        # A box stops short by a few words at most; further right is another row.
        reach = int(4 * self.pitch * scale)
        limit = max(start, min(w, int(self.edge_for(line) * scale), start + reach))
        end, blank = start, 0
        for xi in range(start, limit):
            if inked[xi]:
                end, blank = xi + 1, 0
            else:
                blank += 1
                if blank > gap_px:
                    break
        r.x1 = max(r.x1, end / scale)
        return r

    def _settle_on_ink(self, r: fitz.Rect, mask, scale: float) -> fitz.Rect:
        """The row's vertical extent read from its own ink near the row's end.

        An OCR box is loose on a big or tilted hand, and its centre can lie between
        two rows. Project the ink of the box's last two pitches of width onto y,
        cluster the ink rows (gaps up to 0.15 pitch join x-height and ascenders),
        and take the cluster nearest the box centre, preferring the taller one, as
        the row. Left as it is when there is no ink to read.
        """
        h, w = mask.shape[:2]
        x1 = max(0, min(w, int(r.x1 * scale)))
        x0 = max(0, min(x1, int((r.x1 - 2 * self.pitch) * scale)))
        y0 = max(0, min(h, int(r.y0 * scale)))
        y1 = max(y0, min(h, int(math.ceil(r.y1 * scale))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return r
        sub = _rule_free(mask[y0:y1, x0:x1], self._rule_rows(scale))
        rows = sub.sum(axis=1) > 0
        gap = max(1, int(0.15 * self.pitch * scale))
        runs: list[tuple[int, int]] = []
        i, n = 0, len(rows)
        while i < n:
            if not rows[i]:
                i += 1
                continue
            j = last = i
            while j < n:
                if rows[j]:
                    last = j
                    j += 1
                elif j - last <= gap:
                    j += 1
                else:
                    break
            runs.append((i, last + 1))
            i = j
        if not runs:
            return r
        centre = (y1 - y0) / 2
        a, b = min(runs, key=lambda ab: abs((ab[0] + ab[1]) / 2 - centre) - 0.25 * (ab[1] - ab[0]))
        ny0, ny1 = (y0 + a) / scale, (y0 + b) / scale
        if ny1 - ny0 < 0.4 * self.pitch:                # a dash or a dot: give it a row's body
            mid = (ny0 + ny1) / 2
            ny0, ny1 = mid - 0.3 * self.pitch, mid + 0.3 * self.pitch
        return fitz.Rect(r.x0, ny0, r.x1, ny1)

    def _clear(self, pno: int, rect: fitz.Rect, content_w: float, own: Line,
               ink_rect: fitz.Rect | None = None) -> bool:
        """True when `rect` is free of other marks and `ink_rect` (default `rect`)
        is free of writing."""
        for other in self.placed.get(pno, []):
            inter = other & rect
            if inter.is_empty:
                continue
            # Adjacent rows' marks may touch by a sliver, as a teacher's do; anything
            # more is a collision.
            tolerance = 0.005 if getattr(self,'reviewed_evidence',None) is not None else 0.12
            if abs(inter) > tolerance * min(abs(other), abs(rect)):
                return False
        if rect.x0 >= content_w:
            return True                       # past the divider: nothing to collide with
        ink = self._ink_fraction(pno, ink_rect if ink_rect is not None else rect)
        if ink is not None:
            return ink < self.ink_limit
        for line in self.by_page.get(pno, []):
            if line.lid == own.lid or line.kind not in _WRITTEN_KINDS:
                continue
            inter = line.rect & rect
            if (not inter.is_empty and inter.width > 0.3 * rect.width
                    and inter.height > 0.5 * rect.height):
                return False
        return True

    def place(self, line: Line, r: fitz.Rect, content_w: float, glyph_w: float,
              circle_w: float, radius: float,
              edge: float | None = None, stroke_height: float | None = None) -> tuple[float, float | None, float]:
        """x of the tick/cross, x-centre of the circle (None when no circle), and the
        vertical nudge applied to the row.

        Candidates in order of cost: the whole pair after the row, nudged right in
        small steps and up or down by a few points while it stays within a teacher's
        reach of the writing; then the tick after the row with the circle past the
        divider; then both past the divider.
        """
        yc0 = (r.y0 + r.y1) / 2
        gap = 0.22 * self.pitch
        h = max(self.tick_h if stroke_height is None else stroke_height, 2 * radius)
        W = content_w
        E = min(W, edge) if edge is not None else W      # the paper ends here, not at the divider
        # Up to two word gaps right: past a student's own tick or underline after
        # the answer (Physics 9ccee8a8 "Q1 (C) 50 W" with the student's check mark
        # 7pt after it), still inside the release check's reach of the row.
        shifts = (0, 6, 12, 18, 24, 36, 48)
        nudges = (0.0, 4.0, -4.0, 7.0, -7.0)
        candidates = sorted(((dx, dy) for dx in shifts for dy in nudges),
                            key=lambda c: c[0] + 1.6 * abs(c[1]))

        # The stroke's own footprint, not a band a whole row pitch tall. MEASURED
        # 2026-09-16 (139e179a page 3 "Ex-Mt.Kilimanjaro.", 5d94afe5 page 2 "over
        # mileion of years."): with a full-pitch band the next row's ascenders read
        # as 5 to 11% ink against the 3% limit, and the pair went to the strip
        # although the row had room. A teacher's tick after the word rides over
        # those ascenders; the circle keeps its own strict disc test below.
        # (Changed by session ai-checker-12 at the user's request.)
        # Reviewed passages can be adjacent while having opposite verdicts.
        # Reserve the full pen stroke for mark-to-mark collision checks so a
        # neighbouring cross cannot turn a valid tick into an apparent X. The
        # narrower source-ink probe below still measures handwriting separately.
        hb = (max(h,stroke_height or 1.05*self.tick_h) if getattr(self,'reviewed_evidence',None) is not None
              else min(h, 0.62 * self.pitch))

        def band(x0, w, dy):
            return fitz.Rect(x0, yc0 + dy - hb / 2, x0 + w, yc0 + dy + hb / 2)

        def tick_band(x0, dy):
            # The tick's own footprint: 0.6 of a row pitch, centred on the row. A
            # band a full pitch tall took in the next row's ascenders ("Ex-Mt.
            # Kilimanjaro." read 5-6.6% beside clear paper) and a teacher's tick
            # rides over those. The circle is judged separately, by the release
            # check's own disc measure (circle_clear).
            th = 0.6 * self.pitch
            return fitz.Rect(x0, yc0 + dy - th / 2, x0 + glyph_w, yc0 + dy + th / 2)

        def cx_of(gx):
            return gx + glyph_w + 4 + radius

        # Never past the foot or the top of the photo. Uday Bihar Board Accountancy
        # 343f6e52 (10 Oct 2026): Q9's last row ran to the bottom edge of the photo and
        # to the fold, so its pair went "under the row's end", off the page, and the
        # copy was held "drawing extends beyond the page".
        page_h = self.raw[line.page - 1].rect.height

        def on_page(x0, w, dy):
            return yc0 + dy - h / 2 >= 0.5 and yc0 + dy + h / 2 <= page_h - 0.5

        def circle_clear(cx, dy):
            if cx is None:
                return True
            # Circles never overlap one another: the release check reads each
            # circle's numeral back from its own box, and on the tight rows of
            # Aditya's SST Test-05 MCQs (16pt pitch, 18pt circles) the Q4 and Q5
            # circles overlapped, each read "1 1" and the copy was held.
            # (ai-checker-12)
            disc = fitz.Rect(cx - radius, yc0 + dy - radius, cx + radius, yc0 + dy + radius)
            for other in self.__dict__.get("_circle_boxes", {}).get(line.page, []):
                inter = other & disc
                if not inter.is_empty and inter.get_area() > 0.5:
                    return False
            if cx - radius >= W:
                return True                   # past the divider: blank margin
            ink = self._disc_ink(line.page, (cx - radius, yc0 + dy - radius,
                                             cx + radius, yc0 + dy + radius))
            return ink is None or ink < self.disc_limit

        for dx, dy in candidates:
            gx = r.x1 + gap + dx
            if (gx + glyph_w + circle_w <= E - 2 and on_page(gx, glyph_w + circle_w, dy)
                    and self._clear(line.page, band(gx, glyph_w + circle_w, dy), W, line,
                                    ink_rect=tick_band(gx, dy))
                    and circle_clear(cx_of(gx) if circle_w else None, dy)):
                return gx, (cx_of(gx) if circle_w else None), dy
        # Under the end of the row, when the row runs to the paper's edge: the
        # space a teacher uses next, before anything goes past the divider. The
        # user's review copies (d4d4853f page 2) had every full-width point's
        # circle in the strip while the row below its end was blank paper.
        # (Added by session ai-checker-12 at the user's request.)
        # Only for a row with no room after it: a row with room whose space is
        # taken by ink keeps the old fallbacks (a pair under "(C) 50 W" read as
        # Q2's).
        # The pair's centre sits half a mark below the row's own bottom, so the
        # release check (a reach of 1.2 row heights) still reads it as this row's;
        # and a spot that overlaps another written row's box is refused, since
        # there the mark reads as that row's (d4d4853f Q9.b stood on "which it",
        # 2.97% ink against the 3% limit).
        below = 0.5 * r.height + 0.5 * h + 1.0
        no_room = r.x1 + gap + glyph_w + circle_w > E - 2

        stops = getattr(self, "_stop_lids", set())

        def on_other_row(box):
            for other in self.by_page.get(line.page, []):
                if other.lid == line.lid or other.cancelled or other.kind not in _WRITTEN_KINDS:
                    continue
                if (other.rect & box).get_area() > 0.15 * box.get_area():
                    return True
                # Level with a row that starts another point or carries another
                # mark, the pair reads as that row's, even past its end: on 139e179a
                # the "Structural plain" pair sat after "Rivers and wind" and that
                # row's own pair went to the margin. A continuation of the same
                # point ("pillars and / rocks") is fine. (ai-checker-12)
                level = min(other.rect.y1, box.y1) - max(other.rect.y0, box.y0)
                if (level > 0.3 * box.height and other.rect.x0 < box.x1
                        and (other.lid in stops or _opens_point(other.text))):
                    return True
            return False

        # A reviewed rectangle may cover a whole paragraph. Moving below that
        # rectangle can place its total on the following answer, so keep these
        # explicit marks at the reviewed row or in its aligned gutter instead.
        reviewed = getattr(self,'reviewed_evidence',None) is not None
        for dx in ((0, 8, 16, 24, 32, 40) if no_room and not reviewed else ()):
            for dy in (below, below + 4.0, below + 8.0):
                gx = min(r.x1, E - 2) - glyph_w - circle_w - dx
                if gx <= r.x0 + 2:
                    break
                pair = fitz.Rect(gx, yc0 + dy - h / 2, gx + glyph_w + circle_w, yc0 + dy + h / 2)
                if on_other_row(pair) or not on_page(gx, glyph_w + circle_w, dy):
                    continue
                if (self._clear(line.page, band(gx, glyph_w + circle_w, dy), W, line,
                                ink_rect=tick_band(gx, dy))
                        and circle_clear(cx_of(gx) if circle_w else None, dy)):
                    return gx, (cx_of(gx) if circle_w else None), dy
        if circle_w:
            for dx, dy in candidates:
                gx = r.x1 + gap + dx
                if (gx + glyph_w <= E - 2 and on_page(gx, glyph_w, dy)
                        and self._clear(line.page, band(gx, glyph_w, dy), W, line,
                                        ink_rect=tick_band(gx, dy))):
                    return gx, W + 4 + radius, dy
        gx = W + 3
        gutter_x = [gx]
        if reviewed:
            step = glyph_w + circle_w + 4
            limit = W + GUTTER*self.text_scale - glyph_w - circle_w - 4
            gutter_x = [gx+i*step for i in range(8) if gx+i*step<=limit] or [gx]
        for gx in gutter_x:
            for dy in (0.0, 4.0, -4.0, 8.0, -8.0, -12.0, -16.0):
                if (on_page(gx, glyph_w + circle_w, dy)
                        and self._clear(line.page, band(gx, glyph_w + circle_w, dy), W, line)):
                    return gx, (cx_of(gx) if circle_w else None), dy
        if reviewed:
            raise ValueError(f'No collision-free reviewed mark position for {line.lid}')
        return gx, (cx_of(gx) if circle_w else None), 0.0

    # ------------------------------------------------------------------ drawing
    def circle_radius(self, value) -> float:
        """pdf_renderer._draw_number_in_circle draws a decimal in a slightly larger circle."""
        import pdf_renderer as pr
        text = pr._fmt_mark(float(value))
        return (1.9 if "." in text else 1.8) * self.circle_s + 1.5

    def draw_mark(self, sheet, line: Line, q, role: str, kind: str, value, seed: str,
                  criteria: tuple[int, ...] = (), maximum: float | None = None,
                  anchor_lid: str = "", rect: fitz.Rect | None = None,
                  question_total: bool = False, point_rows: list[Line] | None = None) -> Mark:
        import pdf_renderer as pr
        pno = line.page
        page = sheet[pno - 1]
        content_w = self.raw[pno - 1].rect.width
        r = self.mark_row(line, rect)

        def sizes(k: float) -> tuple[float, float, float]:
            """Circle radius, circle width and glyph width at k times the full size."""
            if value is None:
                rad, cw = 0.0, 0.0
            else:
                rad = (1.9 if "." in pr._fmt_mark(float(value)) else 1.8) * self.circle_s * k + 1.5
                cw = 2 * rad + 4
            return rad, cw, (self.tick_w if kind == "tick" else self.cross) * k

        reviewed = self.reviewed_evidence is not None
        placement_error = None
        for k in ((1.0,0.85,0.72) if reviewed else (1.0,)):
            radius, circle_w, glyph_w = sizes(k)
            try:
                gx, cx, dy = self.place(line, r, content_w, glyph_w, circle_w, radius,
                                        edge=self.edge_for(line),
                                        **({'stroke_height':(1.05*self.tick_h if kind=='tick' else self.cross)*k}
                                           if reviewed else {}))
                break
            except ValueError as exc:
                if not reviewed:
                    raise
                placement_error = exc
        else:
            raise placement_error

        def on_paper(gx_, cx_, rad):
            return gx_ < content_w and (cx_ is None or cx_ + rad <= content_w)

        # The last row has no room on the paper. A teacher does not write in the
        # notes column: the mark goes at the end of the point's previous row, and
        # when no row of the point has room at full size, a little smaller (as a
        # teacher writes in a tight space) before anything goes past the divider.
        # The user's review of the SST Test-05 copies: "Akshad's Q7.d 1.5 award sat
        # in the gutter"; Aaradhya Q10.d "ideal for food production." (ai-checker-12)
        if not on_paper(gx, cx, radius):
            rows = [] if rect is not None else list(point_rows or [])
            found = False
            for kk in (1.0, 0.85, 0.72):
                rad, cw, gw = sizes(kk)
                for row in ([line] if kk < 1.0 else []) + rows:
                    r2 = r if row is line else self.mark_row(row, None)
                    try:
                        g2, c2, d2 = self.place(row, r2, content_w, gw, cw, rad, edge=self.edge_for(row),
                            **({'stroke_height':(1.05*self.tick_h if kind=='tick' else self.cross)*kk}
                               if reviewed else {}))
                    except ValueError:
                        if not reviewed:
                            raise
                        continue  # retain the already collision-free gutter slot
                    if on_paper(g2, c2, rad):
                        line, r, gx, cx, dy = row, r2, g2, c2, d2
                        k, radius, circle_w, glyph_w = kk, rad, cw, gw
                        found = True
                        break
                if found:
                    break
        yc = (r.y0 + r.y1) / 2 + dy
        if kind == "tick":
            pr._draw_tick(page, gx, yc + 0.32 * self.tick_h * k, size=self.tick_s * k, seed=seed)
            vb = fitz.Rect(gx, yc - 0.55 * self.tick_h * k, gx + self.tick_w * k, yc + 0.5 * self.tick_h * k)
        else:
            pr._draw_cross(page, gx + self.cross * k / 2, yc, size=self.cross * k, seed=seed)
            vb = fitz.Rect(gx, yc - self.cross * k / 2, gx + self.cross * k, yc + self.cross * k / 2)
        nb = None
        placed = self.placed.setdefault(pno, [])
        placed.append(vb)
        if value is not None and cx is not None:
            pr._draw_number_in_circle(page, cx, yc, pr._fmt_mark(float(value)),
                                      size=self.circle_s * k, seed=seed)
            nb = fitz.Rect(cx - radius, yc - radius, cx + radius, yc + radius)
            placed.append(nb)        # separately: a spilled circle must not block the next row
            self.__dict__.setdefault("_circle_boxes", {}).setdefault(pno, []).append(nb)
        mark = Mark(q.qid, role, pno, kind, value, r, anchor_lid or line.lid, vb, nb,
                    tuple(criteria), maximum, line.lid, question_total)
        self.marks.append(mark)
        return mark

    def reserve(self, pno: int, preferred: float, height: float, page_h: float) -> float | None:
        """Top y of the free margin span nearest `preferred` that holds `height`, booked.

        Prefers at or below the answer (a reason reads downward from its row), else
        the nearest span above it. None, and nothing booked, when no span on the page
        holds it: the caller decides what to give up (type size, then the reason).
        """
        top, bottom = _STRIP_TOP, page_h - 8
        preferred = max(top, min(preferred, bottom - height))
        occ = sorted(self.occupied.setdefault(pno, []))
        free: list[tuple[float, float]] = []
        cursor = top
        for a, b in occ:
            if a - 4 > cursor:
                free.append((cursor, a - 4))
            cursor = max(cursor, b + 4)
        if bottom > cursor:
            free.append((cursor, bottom))
        best, best_cost = None, None
        for a, b in free:
            if b - a < height:
                continue
            y = max(a, min(preferred, b - height))
            cost = (0 if y >= preferred - 1 else 1, abs(y - preferred))
            if best_cost is None or cost < best_cost:
                best, best_cost = y, cost
        if best is None:
            return None
        self.occupied[pno].append((best, best + height))
        return best

    def _font(self):
        if getattr(self, "_font_obj", None) is None:
            self._font_obj = (fitz.Font(fontfile=self.kalam_path) if self.kalam_path
                              else fitz.Font("helv"))
        return self._font_obj

    def _entry_metrics(self, q, reason: str, shrink: float, with_reason: bool):
        """(head size, reason size, head lines, reason lines, height) of a margin entry."""
        import pdf_renderer as pr
        font = self._font()
        max_w = self.strip_w - 8 - GUTTER * self.text_scale
        head = (f"{pr._label(q.qid, self.report)}  correction" if getattr(q,'review_note',False)
                else f"{pr._label(q.qid, self.report)}  not counted" if getattr(q, "uncounted", False)
                else f"{pr._label(q.qid, self.report)}  {pr._fmt_mark(q.score)}/{pr._fmt_mark(q.max_score)}")
        compact_objective = (getattr(self,'reviewed_evidence',None) is not None
                             and bool(getattr(q,'objective',False)))
        local_scale = (getattr(self,'objective_type_scale',{}).get(q.page,1.0)
                       if compact_objective else 1.0)
        hs = HEAD_SIZE * self.text_scale * shrink * local_scale
        rs = REASON_SIZE * self.text_scale * shrink
        head_lines = pr._wrap_to_width(head, max_w, lambda t: pr._text_width(t, hs, lambda s: font.text_length(s, hs)))
        if not hasattr(self, "_part_totals"):
            from wording import report_part_totals
            self._part_totals = report_part_totals(self.report)
        part = self._part_totals.get(str(q.qid))
        if part and not getattr(q, "review_note", False):
            head_lines = pr._wrap_to_width(part, max_w, lambda t: pr._text_width(t, hs, lambda s: font.text_length(s, hs))) + head_lines
        # A Hindi or other Indian-script reason is measured and drawn shaped
        # (pdf_renderer._insert_text): the margin font draws it unshaped.
        reason_lines = (pr._wrap_to_width(reason, max_w, lambda t: pr._text_width(t, rs, lambda s: font.text_length(s, rs)))
                        if reason and with_reason else [])
        height = (len(head_lines)*hs*1.25+1 if compact_objective and not reason_lines else
                  len(head_lines)*hs*1.35
                  + (len(reason_lines)*rs*1.35+3 if reason_lines else 0)+6)
        return hs, rs, head_lines, reason_lines, height

    def page_shrink(self, entries: list[dict], page_h: float) -> float:
        """The largest type size at which every margin entry of a page fits.

        A page whose entries need more margin than it has (four wrong MCQs with a
        "Correct answer is ..." reason each, then the descriptive answers) used to
        get its last entries written over the earlier ones. A teacher who sees the
        margin filling up writes the whole page smaller instead.
        """
        supply = (page_h - 8) - _STRIP_TOP - 4 * max(0, len(entries) - 1)
        for shrink in _SHRINK_LADDER:
            demand = sum(self._entry_metrics(e["q"], e["reason"], shrink, True)[4] for e in entries)
            if demand <= supply:
                return shrink
        return _SHRINK_LADDER[-1]

    def draw_entry(self, sheet, line: Line, q, reason: str, preferred_yc: float | None = None,
                   base_shrink: float = 1.0) -> fitz.Rect:
        """'Q7.c 1.5/3' beside the answer, the reason under it when marks were lost.

        `base_shrink` is the page's type size (page_shrink). Smaller sizes are tried
        only when they bring the entry within a teacher's reach of its row; otherwise
        the largest size that fits anywhere on the page wins, since small type far
        from the row helps nobody. When nothing fits, smaller still, then without the
        reason (it is on the cover strip too), and as a last resort the score line is
        written at its row over whatever is there and the manifest says so
        ("margin_overflow").
        """
        import pdf_renderer as pr
        pno = line.page
        page = sheet[pno - 1]
        W = self.raw[pno - 1].rect.width
        H = page.rect.height
        if self.kalam_path:
            page.insert_font(fontname=_KALAM_NAME, fontfile=self.kalam_path)
            fname = _KALAM_NAME
        else:
            fname = "helv"
        x0 = W + GUTTER * self.text_scale
        yc = preferred_yc if preferred_yc is not None else line.yc
        ladder = [sz for sz in _SHRINK_LADDER if sz <= base_shrink + 1e-9] or [_SHRINK_LADDER[-1]]

        flags = {"reason_omitted": False, "margin_overflow": False}
        chosen = fallback = None
        for shrink in ladder[:3]:
            hs, rs, head_lines, reason_lines, height = self._entry_metrics(q, reason, shrink, True)
            want = yc - hs * 0.8
            top = self.reserve(pno, want, height, H)
            if top is None:
                continue
            if abs(top - want) <= 3 * self.pitch:
                chosen = (top, hs, rs, head_lines, reason_lines, height)
                break
            self.occupied[pno].pop()          # too far from its row: try smaller type
            if fallback is None:
                fallback = (top, hs, rs, head_lines, reason_lines, height)
        if chosen is None and fallback is not None:
            top, hs, rs, head_lines, reason_lines, height = fallback
            booked = self.reserve(pno, top, height, H)
            if booked is None:
                self.occupied[pno].append((top, top + height))
                booked = top
            chosen = (booked, hs, rs, head_lines, reason_lines, height)
        if chosen is None:
            attempts = [(sz, True) for sz in ladder[3:]] + [(ladder[-1], False)]
            for shrink, with_reason in attempts:
                hs, rs, head_lines, reason_lines, height = self._entry_metrics(
                    q, reason, shrink, with_reason)
                top = self.reserve(pno, yc - hs * 0.8, height, H)
                if top is not None:
                    flags["reason_omitted"] = bool(reason) and not with_reason
                    chosen = (top, hs, rs, head_lines, reason_lines, height)
                    break
        if chosen is None:
            hs, rs, head_lines, reason_lines, height = self._entry_metrics(
                q, reason, ladder[-1], False)
            top = max(_STRIP_TOP, min(yc - hs * 0.8, H - 8 - height))
            self.occupied.setdefault(pno, []).append((top, top + height))
            flags["reason_omitted"] = bool(reason)
            flags["margin_overflow"] = True
            chosen = (top, hs, rs, head_lines, reason_lines, height)
        top, hs, rs, head_lines, reason_lines, height = chosen
        self.entry_flags[(pno, q.qid)] = flags
        y = top + hs
        for ln in head_lines:
            pr._insert_text(page, (x0, y), ln, fontsize=hs, fontname=fname, color=pr.RED)
            y += hs * 1.35
        y += 3
        for ln in reason_lines:
            pr._insert_text(page, (x0, y), ln, fontsize=rs, fontname=fname, color=pr.RED)
            y += rs * 1.35
        return fitz.Rect(x0, top, W + self.strip_w - 8, top + height)

    # ------------------------------------------------------------------ build
    def _enumerated(self, lines: list[Line | None]) -> bool:
        """Do this answer's criteria sit on separate numbered or bulleted points?

        A teacher marks a list point by point and flowing prose once. Yes when at
        least half of the rows the criteria resolve to open a point, counting a
        question label in front of it ("10 a) Mountains formed by"); a lone point
        ("d) i) Earliest deciphered writing") counts and gets its own half mark, not
        a whole-answer circle. Prose rows, however many, get one mark.
        """
        distinct = {l.lid: l for l in lines if l is not None}
        bullets = sum(1 for l in distinct.values() if _opens_point(l.text))
        return bullets >= 1 and bullets * 2 >= len(distinct)

    def build(self) -> bytes:
        import pdf_renderer as pr
        report = self.report
        plain_report(report)
        self.header_h = self._strip_height()
        # 1. The sheet: the student's pages, each widened by the margin, no header.
        sheet = fitz.open()
        for pno in range(len(self.raw)):
            src = self.raw[pno]
            W, H = src.rect.width, src.rect.height
            page = sheet.new_page(width=W + self.strip_w, height=H)
            # Import the unrotated crop frame, then reproduce its display rotation.
            # Passing rotate on an already rotated source instead clips/distorts
            # quarter-turn pages. remove_rotation in PyMuPDF 1.24 also expands a
            # rotated CropBox to the MediaBox, so preserve it by restoring metadata.
            rotation = src.rotation
            try:
                if rotation:
                    src.set_rotation(0)
                pdf_safety.show_source_page(page, fitz.Rect(0, 0, W, H), self.raw, pno, rotate=-rotation)
            finally:
                if rotation:
                    src.set_rotation(rotation)
            page.draw_line((W, 0), (W, H), color=pr.RED, width=1)

        # 2. Anchors. Every anchored line belongs to some point; a point's extension
        # stops at any OTHER anchored line. The grader's question anchor is usually
        # the LAST line of the answer, so it must not stop its own question's points.
        anchors: dict[str, Line | None] = {}
        crit_anchor: dict[tuple[str, int], Line | None] = {}
        all_stops: set[str] = set()
        for q in report.questions:
            anchors[q.qid] = self.question_anchor(q)
            if anchors[q.qid] is not None:
                all_stops.add(anchors[q.qid].lid)
            for i, c in enumerate(getattr(q, "criteria", None) or []):
                line = self.criterion_anchor(q, c)
                crit_anchor[(q.qid, i)] = line
                if line is not None:
                    all_stops.add(line.lid)
        self._stop_lids = set(all_stops)

        manifest: list[dict] = []
        requirements: list[dict] = []
        entries: list[dict] = []
        for q in report.questions:
            attempted = bool(getattr(q, "attempted", True))
            criteria = list(getattr(q, "criteria", None) or [])
            qa = anchors[q.qid]
            objective = bool(getattr(q, "objective", False))
            crit_lines = [crit_anchor[(q.qid, i)] for i in range(len(criteria))]
            crit_lids = {l.lid for l in crit_lines if l is not None}
            own_stops = all_stops - ({qa.lid} if qa is not None and qa.lid not in crit_lids else set())
            live = [l for l in crit_lines if l is not None] + self._region_lines(q) + ([qa] if qa else [])
            entry_line = min(live, key=lambda l: (l.page, l.yc)) if live else self.block_entry_line(q)
            deducted = q.score < q.max_score - 1e-6
            reason = q.remark if deducted else ""
            style = str(getattr(q, "mark_style", "auto") or "auto")
            per_step = bool(criteria) and not objective and (
                style == "per_step" or (style == "auto" and self._enumerated(crit_lines)))

            if not attempted and q.score <= 0:
                if entry_line is not None:
                    entries.append({"line": entry_line, "q": q, "reason": q.remark or "Not attempted.",
                                    "objective": objective})
                continue

            if not per_step:
                # One mark for the whole answer, at its end; a bare cross beside each
                # element that earned nothing, so a wrong statement never reads as accepted.
                requirements.append({"qid": q.qid, "role": "total"})
                if qa is None and not live:
                    requirements.append({"qid": q.qid, "role": "answer", "reason": "no anchor line"})
                    self.unanchored.setdefault(q.qid, [])
                    if entry_line is not None:
                        entries.append({"line": entry_line, "q": q, "reason": reason, "objective": objective})
                    continue
                last = qa if objective or self.reviewed_evidence is not None else max(live, key=lambda l: (l.page, l.yc))
                end = last if objective else self.point_end(last, own_stops - {last.lid})
                kind = "tick" if q.score > 0 else "cross"
                m = self.draw_mark(sheet, end, q, "answer", kind, q.score, seed=f"{q.qid}:a",
                                   maximum=q.max_score, anchor_lid=(qa or last).lid,
                                   question_total=True,
                                   rect=end.rect if end.lid in self.review_ids else None,
                                   point_rows=None if objective else self.rows_above_in_point(
                                       last, end, own_stops - {last.lid, end.lid}))
                row = _row(m, q.score)
                if end.lid in self.review_ids:
                    row['location_source'] = 'faculty_source_review'
                    row['origin_review_sha256'] = self.reviewed_evidence['review_sha256']
                manifest.append(row)
                if not objective:
                    for i, c in enumerate(criteria):
                        line = crit_lines[i]
                        if line is None or line.lid == end.lid:
                            continue
                        if float(c.awarded or 0.0) > 0 or float(c.max or 0.0) <= 0:
                            continue
                        if str(getattr(c, "evidence_kind", "") or "") == "missing":
                            continue
                        rect = self.certified_rect(q, i, line)
                        mx = self.draw_mark(sheet, line, q, "verdict", "cross", None,
                                            seed=f"{q.qid}:x{i}", criteria=(i,), maximum=float(c.max or 0.0),
                                            anchor_lid=line.lid, rect=rect)
                        manifest.append(_row(mx, 0.0))
                entries.append({"line": entry_line, "q": q, "reason": reason, "objective": objective})
                continue

            # Step-marked answer: one mark per point, criteria that end on the same
            # row merge into one mark with their awards summed.
            groups: dict[str, dict] = {}
            order: list[str] = []
            orphan = [0.0, 0.0, []]      # awarded, max, criteria of steps with no line
            for i, c in enumerate(criteria):
                awarded, maximum = float(c.awarded or 0.0), float(c.max or 0.0)
                if str(getattr(c, "evidence_kind", "") or "") == "missing" or maximum <= 0:
                    continue          # omitted work has no line of its own; the reason covers it
                # Nothing earned and nothing cited (no line, no quote): the grader found
                # no writing for this point, whatever its evidence label says. There is
                # nothing on the sheet to cross; the margin reason carries the loss.
                # (d365ca25 Q7.c, 2546c5cf Q10.b; ai-checker-12)
                if (awarded <= 0 and not str(getattr(c, "step_line_id", "") or "").strip()
                        and not str(getattr(c, "anchor_quote", "") or "").strip()):
                    continue
                requirements.append({"qid": q.qid, "role": "step", "criterion": i})
                line = crit_lines[i]
                if line is None:
                    self.unanchored.setdefault(q.qid, []).append(i)
                    orphan[0] += awarded
                    orphan[1] += maximum
                    orphan[2].append(i)
                    continue
                rect = self.certified_rect(q, i, line)
                if rect is not None:
                    key = f"{line.lid}@{rect.y0:.0f}"
                    end = line
                else:
                    end = self.point_end(line, own_stops - {line.lid})
                    key = end.lid
                g = groups.get(key)
                if g is None:
                    g = groups[key] = {"line": end, "rect": rect, "awarded": 0.0, "max": 0.0,
                                       "idx": [], "start": line.lid}
                    order.append(key)
                g["awarded"] += awarded
                g["max"] += maximum
                g["idx"].append(i)
            # Groups keyed by OCR line can still end on one written row: on the
            # tilted 5d94afe5 page 2 the boxes of points (2) and (3) both ended on
            # the "extent of Mauryan empire" row, the two pairs were pushed past the
            # divider onto each other and neither circle could be read. Marks that
            # stand beside the same row merge, awards summed, as the key intends.
            # (ai-checker-12, at the user's request.)
            merged: list[str] = []
            for key in order:
                g = groups[key]
                if g["rect"] is None:
                    row = self.mark_row(g["line"], None)
                    g["row"] = row
                    host = None
                    for other_key in merged:
                        h = groups[other_key]
                        other = h.get("row")
                        if other is None or h["line"].page != g["line"].page:
                            continue
                        if not self.same_column(h["line"], g["line"]):
                            continue
                        overlap = min(other.y1, row.y1) - max(other.y0, row.y0)
                        if overlap > 0.5 * min(other.height, row.height):
                            host = h
                            break
                    if host is not None:
                        host["awarded"] += g["awarded"]
                        host["max"] += g["max"]
                        host["idx"].extend(g["idx"])
                        if row.x1 > host["row"].x1:
                            host["line"], host["row"] = g["line"], row
                        continue
                merged.append(key)
            order = merged
            # A step the page gives no line for still earned its marks. Drawn nowhere,
            # the circles on the page added up to less than the score: 1 against 2 on
            # fourteen of 55 stored copies, and the student cannot tell which total is
            # right (Radiant English/Physics review, 2026-09-30). Its award joins the
            # last mark drawn for the same answer, so the page adds up.
            if orphan[0] > 0 and order:
                last = max(order, key=lambda key: (groups[key]["line"].page, groups[key]["line"].yc))
                groups[last]["awarded"] += orphan[0]
                groups[last]["max"] += orphan[1]
                groups[last]["idx"].extend(orphan[2])
            for key in order:
                g = groups[key]
                kind, value = ("tick", g["awarded"]) if g["awarded"] > 0 else ("cross", None)
                start_line = self.lines.get(g["start"], g["line"])
                m = self.draw_mark(sheet, g["line"], q, "step", kind, value,
                                   seed=f"{q.qid}:{g['idx'][0]}", criteria=tuple(g["idx"]),
                                   maximum=g["max"], anchor_lid=g["start"], rect=g["rect"],
                                   point_rows=self.rows_above_in_point(
                                       start_line, g["line"], own_stops - {g["start"], g["line"].lid}))
                row = _row(m, g["awarded"])
                if g['line'].lid in self.review_ids:
                    row['location_source'] = 'faculty_source_review'
                    row['origin_review_sha256'] = self.reviewed_evidence['review_sha256']
                manifest.append(row)
            if entry_line is not None:
                entries.append({"line": entry_line, "q": q, "reason": reason, "objective": objective})

        # Explicit review ticks/crosses use the same red-pen drawing and spacing as
        # automatic checks. They carry no invented per-passage marks; the question
        # total above remains the only numerical award.
        if self.reviewed_evidence is not None:
            from exam_package import normalize_qid
            from types import SimpleNamespace
            questions = {normalize_qid(q.qid):q for q in report.questions}
            for qid,index,check,line in self.review_checks:
                q = questions[qid]
                if check.get('show_reason') and check.get('reason'):
                    note = SimpleNamespace(qid=q.qid,score=q.score,max_score=q.max_score,
                                           review_note=True)
                    entries.append({'line':line,'q':note,'reason':check['reason'],'objective':False})
                scored_ids = {q.anchor_line_id} if q.mark_style!='per_step' else {
                    c.step_line_id for c in q.criteria}
                if line.lid in scored_ids:
                    continue  # already bears a genuine total or priced criterion
                m = self.draw_mark(sheet,line,q,'faculty_check',
                                   'tick' if check['verdict']=='correct' else 'cross',
                                   None,seed=f'faculty:{qid}:{index}',rect=line.rect,
                                   anchor_lid=line.lid)
                row = _row(m,0.0)
                row.update(score=None,score_text=None,score_style='bare_verdict',
                           location_source='faculty_source_review',
                           origin_review_sha256=self.reviewed_evidence['review_sha256'],
                           review_check_index=index,source_quote=check['quote'],
                           review_reason=check.get('reason',''))
                manifest.append(row)

        # 3. Margin entries. MCQ blocks read down the margin in question order from the
        # block's top row (two columns of answers must not interleave); everything else
        # sits beside its own row. Reading order so a long reason pushes the next down.
        #
        # The margin follows the student's writing, not the question numbers: the
        # objective answers are read down the left column, then down the right one,
        # and a run of rows more than three rows from the last is a block of its own
        # that starts beside its own first row. (ai-checker-12, at the user's
        # request: "the question no. should be as the student writes on the sheet".)
        keyed: list[tuple[int, float, dict]] = []
        by_page_obj: dict[int, list[dict]] = {}
        self.objective_type_scale = {}
        if self.reviewed_evidence is not None:
            for pno in range(1,len(self.raw)+1):
                centers = sorted(e['line'].yc for e in entries
                                 if e['objective'] and e['line'].page==pno)
                gaps = [b-a for a,b in zip(centers,centers[1:]) if b-a>=6]
                if gaps:
                    # Compact native score labels must fit their actual MCQ rows,
                    # even when descriptive handwriting elsewhere is much larger.
                    self.objective_type_scale[pno] = min(1.0,max(0.35,
                        (min(gaps)-5)/(1.25*HEAD_SIZE*self.text_scale)))
        for e in entries:
            if e['objective'] and self.reviewed_evidence is not None:
                # The native report strip already contains each MCQ correction.
                # Repeating a long reason here shifts the next numbered score
                # beside another answer. Keep each compact score by its actual
                # reviewed answer row, without changing the award or top feedback.
                e['reason'] = ''
                e['yc'] = e['line'].yc
                keyed.append((e['line'].page,e['yc'],e))
            elif e["objective"]:
                by_page_obj.setdefault(e["line"].page, []).append(e)
            else:
                e["yc"] = e["line"].yc
                keyed.append((e["line"].page, e["yc"], e))
        for pno, objs in by_page_obj.items():
            W = self.raw[pno - 1].rect.width
            col = lambda e: 1 if e["line"].rect.x0 >= 0.45 * W else 0
            objs.sort(key=lambda e: (col(e), e["line"].yc))
            blocks: list[dict] = []
            for e in objs:
                yc = e["line"].yc
                cur = blocks[-1] if blocks else None
                joins = cur is not None and (
                    (col(e) == cur["col"] and yc - cur["bottom"] <= 3 * self.pitch)
                    or (col(e) != cur["col"]
                        and cur["top"] - self.pitch <= yc <= cur["bottom"] + self.pitch))
                if not joins:
                    cur = {"top": yc, "bottom": yc, "col": col(e), "items": []}
                    blocks.append(cur)
                cur["items"].append(e)
                cur["bottom"] = max(cur["bottom"], yc)
                cur["col"] = col(e)
            # A block starts beside its first row, or as high as it must for its
            # entries AND every block below it to fit above the page's foot, in
            # order and without overlapping. When the last entry found no room
            # below the previous one, reserve() put it in the nearest gap above,
            # and Q5 read above Q3 and Q4 on the Accountancy copy c0f259bb (Q3's
            # five-line reason and the Q4-Q5 block were lifted independently and
            # still collided). (ai-checker-12, 2026-09-17)
            H = self.raw[pno - 1].rect.height
            demands = [sum(self._entry_metrics(e["q"], e["reason"], 1.0, True)[4] + 4
                           for e in block["items"]) for block in blocks]
            latest = [0.0] * len(blocks)
            foot = H - 8
            for i in range(len(blocks) - 1, -1, -1):
                foot -= demands[i]
                latest[i] = foot
            prev_end = _STRIP_TOP
            for block, demand, late in zip(blocks, demands, latest):
                start = max(prev_end, min(block["top"], late))
                prev_end = start + demand
                for n, e in enumerate(block["items"]):
                    e["yc"] = start + 0.001 * n
                    keyed.append((pno, e["yc"], e))
        by_pno: dict[int, list[dict]] = {}
        for pno, yc, e in keyed:
            by_pno.setdefault(pno, []).append(e)
        shrink_of = {pno: self.page_shrink(es, self.raw[pno - 1].rect.height)
                     for pno, es in by_pno.items()}
        for pno in sorted(by_pno):
            # Every entry of a page in the order of its row, top to bottom, never
            # overlapping and never out of order. An entry that found no room under
            # the previous one used to be put in the nearest gap ABOVE by reserve():
            # on Akshad's SST copy (2026-09-18) Q9.a's entry sat above Q7.b's, three
            # answers up the page. Heights are taken at the page's type size; each
            # entry starts beside its row, or as high as it must for it and every
            # entry below it to fit above the foot, or just under the previous one.
            # (ai-checker-12)
            H = self.raw[pno - 1].rect.height
            shrink = shrink_of[pno]
            ordered = sorted(by_pno[pno], key=lambda item: item["yc"])
            metrics = [self._entry_metrics(item["q"], item["reason"], shrink, True) for item in ordered]
            latest: list[float] = []
            foot = H - 8
            for m in reversed(metrics):
                foot -= m[4] + 4
                latest.append(foot)
            latest.reverse()
            prev_end = _STRIP_TOP
            for e, m, late in zip(ordered, metrics, latest):
                hs, height = m[0], m[4]
                top = max(prev_end, min(e["yc"] - hs * 0.8, late))
                prev_end = top + height + 4
                box = self.draw_entry(sheet, e["line"], e["q"], e["reason"],
                                      preferred_yc=top + hs * 0.8, base_shrink=shrink)
                manifest.append({"qid": e["q"].qid, "role": "feedback", "page": pno,
                                 "bbox": [box.x0, box.y0, box.x1, box.y1],
                                 "score": None if getattr(e['q'],'review_note',False) else e["q"].score,
                                 "line_id": e["line"].lid,
                                 "margin_score": not getattr(e['q'],'review_note',False),
                                 "faculty_correction_note": bool(getattr(e['q'],'review_note',False)),
                                 "reason_continuation": False,
                                 **({'location_source':'faculty_source_review',
                                     'origin_review_sha256':self.reviewed_evidence['review_sha256']}
                                    if e['line'].lid in self.review_ids else {}),
                                 **self.entry_flags.get((pno, e["q"].qid), {})})

        # 3b. Extra "answer any N" attempts that the choice rule did not count: a
        # note beside each, never a score (strict_grading.effective_selection_policy).
        from types import SimpleNamespace
        for extra in list(getattr(report, "uncounted_choice", None) or []):
            line = self.lines.get(str(extra.get("anchor_line_id") or ""))
            if line is None:
                page_lines = self.by_page.get(int(extra.get("page") or 0)) or []
                line = next((l for l in page_lines if self.markable(l)), None)
            if line is None:
                continue
            stub = SimpleNamespace(qid=str(extra.get("qid") or ""), score=0.0, max_score=0.0,
                                   uncounted=True, objective=False, criteria=[])
            try:
                box = self.draw_entry(sheet, line, stub, uncounted_note(extra),
                                      base_shrink=1.0)
                manifest.append({"qid": stub.qid, "role": "uncounted", "page": line.page,
                                 "bbox": [box.x0, box.y0, box.x1, box.y1],
                                 "line_id": line.lid})
            except Exception as exc:  # noqa: BLE001 - a courtesy note is never worth a copy
                print(f"[layout] not-counted note for {stub.qid} not drawn ({exc})", flush=True)

        # 3c. A page of answer with no mark on it reads as a page nobody read. SME
        # review of the CA batch (2026-09-23): "Page no. 6 and 15 are not checked",
        # "Page no 2, 6 and 13 are not checked" - continuation and working-note pages
        # of answers marked elsewhere. A teacher leaves a plain tick at the end of such
        # a page to show it was read. It carries no number and changes no score.
        marked_pages = {int(row["page"]) for row in manifest
                        if row.get("role") in {"answer", "step", "verdict", "total", "continuation", "faculty_check"}}
        for pno in range(1, len(sheet) + 1):
            if pno in marked_pages:
                continue
            owned = []
            for q in report.questions:
                if not bool(getattr(q, "attempted", True)) or bool(getattr(q, "objective", False)):
                    continue
                rows = self._region_lines(q) + [line for (qid, _), line in crit_anchor.items()
                                                if qid == q.qid and line is not None]
                owned += [(line, q) for line in rows if line.page == pno and self.markable(line)]
            if len({line.lid for line, _ in owned}) < 2:
                continue            # a stray line or a heading is not a page of answer
            line, q = max(owned, key=lambda item: item[0].yc)
            try:
                self.draw_mark(sheet, line, q, "seen", "tick", None,
                               seed=f"{q.qid}:seen{pno}", anchor_lid=line.lid)
                self.seen_pages.append(pno)
            except Exception as exc:  # noqa: BLE001 - a courtesy tick is never worth a copy
                print(f"[layout] seen tick on page {pno} not drawn ({exc})", flush=True)

        # 3b. Word marks (language papers only): a red underline under each wrong
        # word that two independent readings agreed on, its correction above it
        # where the paper is clear. Drawn last, so they keep off every tick and
        # circled mark already placed. See language_marks.py.
        word_marks = list(getattr(report, "word_marks", None) or [])
        for pno in range(1, len(sheet) + 1):
            on_page = [m for m in word_marks if int(m.get("page") or 0) == pno]
            if not on_page:
                continue
            W, H = self.raw[pno - 1].rect.width, self.raw[pno - 1].rect.height
            pr.draw_word_marks(
                sheet[pno - 1], on_page, W, H, pitch=self.pitch,
                ink_fraction=lambda rect, _p=pno: self._ink_fraction(_p, rect),
                avoid=list(self.placed.get(pno, [])))

        # 4. Compose: the report strip above page 1, the marked sheets below.
        out = fitz.open()
        header_h = self.header_h
        report.render_page_layouts = {}
        for pno in range(len(sheet)):
            W, H = self.raw[pno].rect.width, self.raw[pno].rect.height
            header = header_h if pno == 0 else 0.0
            op = out.new_page(width=W + self.strip_w, height=H + header)
            if header:
                pr._draw_report_strip(op, 0, 0, W + self.strip_w, report)
                op.draw_line((0, header), (W + self.strip_w, header), color=pr.RED, width=1)
            op.show_pdf_page(fitz.Rect(0, header, W + self.strip_w, header + H), sheet, pno)
            layout = {
                "layout": "teacher",
                "margin_side": "right",
                "margin_bbox": [W, header, W + self.strip_w, header + H],
                "content_bbox": [0, header, W, header + H],
                # Where a tick and its circle sit when the pair did not fit after the
                # row: just past the divider, in step with the handwriting.
                "gutter_bbox": [W, header, W + GUTTER * self.text_scale, header + H],
            }
            # The ruling tilt this build erased at, so the release check re-erases at
            # the same angle. Only for pages this build rasterised: a page whose marks
            # all sit past the divider was never probed, and the check estimates.
            mask, _ = self._ink.get(pno + 1, (None, 1.0))
            if mask is not None:
                skew = pr._RULING_SKEW.get((id(self.raw), pno))
                if skew is not None:
                    layout["ruling_skew"] = float(skew)
            report.render_page_layouts[str(pno + 1)] = layout
        for row in manifest:
            if row["page"] != 1 or not header_h:
                continue
            for key in ("bbox", "anchor_bbox", "numeral_bbox", "verdict_bbox"):
                if row.get(key):
                    row[key][1] += header_h
                    row[key][3] += header_h
        sheet.close()

        report.render_manifest = manifest
        report.render_requirements = requirements
        try:
            report.unanchored_step_qids = sorted(self.unanchored)
        except (AttributeError, ValueError):
            pass
        buf = BytesIO()
        out.save(buf, garbage=4, deflate=True)
        out.close()
        return buf.getvalue()


def _rule_free(sub, max_rows: int = 3):
    """`sub` (a band of the ink mask) with short rule-like pixel-row runs blanked."""
    import numpy as np
    share = sub.mean(axis=1)
    rule = share >= _RULE_ROW_SHARE
    keep = np.ones(len(share), dtype=bool)
    i = 0
    while i < len(share):
        if not rule[i]:
            i += 1
            continue
        j = i
        while j < len(share) and rule[j]:
            j += 1
        if j - i <= max_rows:
            keep[i:j] = False
        i = j
    return sub if keep.all() else (sub & keep[:, None])


def _band_ink(mask, scale: float, rect: fitz.Rect, max_rows: int = 3) -> float:
    """Ink share of `rect` with short rule-like pixel-row runs treated as blank."""
    h, w = mask.shape[:2]
    x0, x1 = max(0, int(rect.x0 * scale)), min(w, int(math.ceil(rect.x1 * scale)))
    y0, y1 = max(0, int(rect.y0 * scale)), min(h, int(math.ceil(rect.y1 * scale)))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    sub = mask[y0:y1, x0:x1]
    return float(_rule_free(sub, max_rows).sum() / sub.size)


def _row(m: Mark, score: float) -> dict:
    bbox = m.verdict_bbox | m.numeral_bbox if m.numeral_bbox else m.verdict_bbox
    if m.kind == "cross":
        verdict = "wrong"
    elif m.maximum is not None and score < m.maximum - 1e-6:
        verdict = "partial"
    else:
        verdict = "correct"
    row = {"qid": m.qid, "role": m.role, "page": m.page, "kind": m.kind, "score": score,
           "line_id": m.line_id, "end_line_id": m.end_line_id, "criteria": list(m.criteria),
           "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
           "anchor_bbox": [m.anchor.x0, m.anchor.y0, m.anchor.x1, m.anchor.y1],
           "verdict": verdict,
           "verdict_bbox": [m.verdict_bbox.x0, m.verdict_bbox.y0, m.verdict_bbox.x1, m.verdict_bbox.y1],
           "numeral_bbox": ([m.numeral_bbox.x0, m.numeral_bbox.y0, m.numeral_bbox.x1, m.numeral_bbox.y1]
                            if m.numeral_bbox else None),
           "score_text": (f"{float(m.value):g}" if m.value is not None else None),
           "score_style": "circled_award" if m.numeral_bbox else "cross_only",
           "question_total": bool(m.question_total),
           "location_source": "ocr_line"}
    if len(m.criteria) == 1:
        row["criterion"] = m.criteria[0]
    return row


def _overlap(quote: str, text: str) -> tuple[float, int]:
    """Share of the quote's words found in the line, and how many matched."""
    qw = [w for w in re.findall(r"[a-z0-9]+", quote) if len(w) > 2]
    if not qw:
        return 0.0, 0
    tw = set(re.findall(r"[a-z0-9]+", text))
    hits = sum(1 for w in qw if w in tw)
    return hits / len(qw), hits


def build_teacher_pdf(student_doc: fitz.Document, report, ocr_pages, *, mark_scale: float = 1.0,
                      kalam_path: str | None = None, safe_layout: bool = False,
                      reviewed_evidence: dict | None = None) -> bytes:
    """Entry point used by pdf_renderer.build_evaluated_pdf."""
    return TeacherLayout(student_doc, report, ocr_pages, mark_scale, kalam_path,
                         safe_layout=safe_layout, reviewed_evidence=reviewed_evidence).build()
