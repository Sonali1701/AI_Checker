# -*- coding: utf-8 -*-
"""teacher_mark.py - redraw a checked answer sheet the way a teacher marks it.

Inputs: the raw student PDF, the cached Mathpix OCR (line geometry only) and a
hand-verified marking plan. No provider calls.

Layout follows the product's evaluated PDF exactly: the EVALUATED report strip on
top of page 1 (drawn by pdf_renderer itself), the student's pages below with a tick
or cross AND a circled mark beside every answer, and a right margin that reads
"Q7.c 2.5/3" with the reason under it whenever marks were deducted. One mark size
per copy, taken from the student's own row pitch.
"""
from __future__ import annotations

import statistics
import sys

import fitz

sys.path.insert(0, r"C:/Users/vs510/PycharmProjects/AI_Checker/AI_Checker")
import pdf_renderer as pr  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from wording import part_totals, question_label  # noqa: E402

RED = pr.RED
STRIP_W = 215.0     # right margin
GUTTER = 62.0       # a tick + circle that does not fit on the page sits here
KALAM = r"C:/Users/vs510/PycharmProjects/AI_Checker/AI_Checker/Kalam-Regular.ttf"
_KALAM = fitz.Font(fontfile=KALAM)
HEAD_SIZE = 15.0    # "Q7.c 2.5/3" in the margin, as in the product
REASON_SIZE = 14.5
OBJECTIVE = {"Q1", "Q2", "Q3", "Q4", "Q5"}   # the SST plans; others say "objective": true


def _objective(q: dict, explicit: bool = False) -> bool:
    """A plan that flags any question "objective" is followed exactly. On a CA paper
    Q2 to Q5 are long answers, and the SST set above made them MCQs."""
    if explicit:
        return bool(q.get("objective", False))
    return bool(q.get("objective", q["qid"] in OBJECTIVE))


def display_label(q: dict, explicit: bool = False) -> str:
    """The question as the paper prints it ("Q2(B)"), as the CA SMEs asked."""
    return q.get("label") or question_label(q["qid"], _objective(q, explicit))


def check_wording(plan: dict) -> None:
    """No semicolons, no dashes anywhere the student will read."""
    texts = [plan.get("remarks", "")]
    for q in plan["questions"]:
        texts.append(q.get("reason", ""))
    for note in plan.get("page_notes") or []:
        texts.append(note.get("note", ""))
    for t in texts:
        for bad in (";", "-", "\u2013", "\u2014"):
            if bad in t:
                raise ValueError(f"wording contains {bad!r}: {t!r}")


class Sheet:
    def __init__(self, raw_pdf: str, ocr: list[dict], plan: dict):
        check_wording(plan)
        self.raw = fitz.open(raw_pdf)
        self.plan = plan
        self.lines: dict[str, tuple[int, fitz.Rect]] = {}
        self.scale: dict[int, tuple[float, float]] = {}
        pitches = []
        for pg in ocr:
            pno = pg["page"]
            page = self.raw[pno - 1]
            sx = page.rect.width / pg["image_width"]
            sy = page.rect.height / pg["image_height"]
            self.scale[pno] = (sx, sy)
            centers = []
            for ln in pg["lines"]:
                x0, y0, x1, y1 = ln["bbox"]
                r = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
                self.lines[ln["line_id"]] = (pno, r)
                if ln.get("kind", "text") == "text":
                    centers.append((r.y0 + r.y1) / 2)
            centers.sort()
            for a, b in zip(centers, centers[1:]):
                if 9 <= b - a <= 60:
                    pitches.append(b - a)
        self.pitch = statistics.median(pitches) if pitches else 22.0
        p = self.pitch
        self.tick_h = 0.95 * p                      # inked height of a tick
        self.tick_s = self.tick_h / 1.6             # _draw_tick's size parameter
        self.tick_w = 1.5 * self.tick_s
        self.cross = 0.72 * p
        # _draw_number_in_circle: radius = 1.8 * size + 1.5. Aim at 0.8 x pitch across.
        self.circle_s = max(4.2, (0.40 * p - 1.5) / 1.8)
        self.circle_r = 1.8 * self.circle_s + 1.5
        self.header_h = 0.0
        # Phone photos are often 2 to 3 times an A4 page wide; the margin and its text
        # grow with the page so a note reads the same size as on an A4 scan.
        self.k = max(1.0, self.raw[0].rect.width / 595.0)
        self.occupied: dict[int, list[tuple[float, float]]] = {}
        self.report = self.build_report()

    # ----- the report the product's strip drawer expects -------------------
    def build_report(self) -> GradeReport:
        qs = []
        self.explicit = any("objective" in q for q in self.plan["questions"])
        for q in self.plan["questions"]:
            anchor = q.get("at") or (q["marks"][0][1] if q.get("marks") else None)
            pno = self.rect_of(anchor)[0] if anchor is not None else 1
            objective = _objective(q, self.explicit)
            qs.append(QuestionMark(
                qid=q["qid"], score=float(q["score"]), max_score=float(q["max"]),
                page=pno, y_fraction=0.0, remark=q.get("reason", ""),
                objective=objective, attempted=bool(q.get("marks")) or q["score"] > 0,
                is_correct=q["score"] >= q["max"]))
        sections: dict[str, dict] = {}
        for q in qs:
            top = q.qid.split(".")[0]
            sec = sections.setdefault(top, {"qid": top, "score": 0.0, "max_score": 0.0,
                                            "objective": (False if self.explicit
                                                          else top in OBJECTIVE)})
            sec["objective"] = sec["objective"] or q.objective
            sec["score"] += q.score
            sec["max_score"] += q.max_score
        ordered = sorted(sections.values(),
                         key=lambda s: (0 if s["objective"] else 1,
                                        int("".join(ch for ch in s["qid"] if ch.isdigit()) or 0)))
        return GradeReport(
            total_score=round(sum(q.score for q in qs), 2),
            max_total=round(sum(q.max_score for q in qs), 2),
            overall_remarks=self.plan["remarks"],
            section_totals=ordered, questions=qs,
            # ICAI shows whole-mark totals (CA SME ruling, 2026-09-23).
            whole_total=bool(self.plan.get("whole_total", False)))

    # ----- geometry --------------------------------------------------------
    def rect_of(self, anchor) -> tuple[int, fitz.Rect]:
        """anchor is a line id, ('xy', page, x0, y0, x1, y1) in OCR pixels, or
        ('pt', page, x0, y0, x1, y1) in the raw page's PDF points (a copy with no OCR)."""
        if isinstance(anchor, str):
            pno, r = self.lines[anchor]
            r = fitz.Rect(r)
        elif anchor[0] == "pt":
            _, pno, x0, y0, x1, y1 = anchor
            r = fitz.Rect(x0, y0, x1, y1)
        else:
            _, pno, x0, y0, x1, y1 = anchor
            sx, sy = self.scale[pno]
            r = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
        if pno == 1:
            r = r + (0, self.header_h, 0, self.header_h)
        return pno, r

    def reserve(self, pno: int, top: float, height: float, bottom_cap: float) -> float:
        """Return a top y at or below `top` that does not overlap earlier entries."""
        occ = sorted(self.occupied.setdefault(pno, []))
        y = top
        for a, b in occ:
            if y < b and y + height > a:
                y = b + 4
        y = min(y, bottom_cap - height)
        self.occupied[pno].append((y, y + height))
        return y

    # ----- marks on the student's answer -----------------------------------
    def draw_mark(self, page, r: fitz.Rect, W: float, kind: str, value, seed: str):
        """A tick or cross beside the answer, then the circled mark it stands for.

        Marks stay on the RIGHT of the answer: after the line when the whole pair
        fits, else the tick after the line and the circle just past the divider,
        else both just past the divider. Never in the left margin.
        """
        yc = (r.y0 + r.y1) / 2
        gap = 0.22 * self.pitch
        circle_w = (2 * self.circle_r + 4) if value is not None else 0.0
        glyph_w = self.tick_w if kind == "tick" else self.cross
        if r.x1 + gap + glyph_w + circle_w <= W - 2:
            gx, cx = r.x1 + gap, r.x1 + gap + glyph_w + 4 + self.circle_r
        elif r.x1 + gap + glyph_w <= W - 2:
            gx, cx = r.x1 + gap, W + 4 + self.circle_r
        else:
            gx, cx = W + 3, W + 3 + glyph_w + 4 + self.circle_r
        if kind == "tick":
            pr._draw_tick(page, gx, yc + 0.32 * self.tick_h, size=self.tick_s, seed=seed)
        else:
            pr._draw_cross(page, gx + self.cross / 2, yc, size=self.cross, seed=seed)
        if value is not None:
            pr._draw_number_in_circle(page, cx, yc, pr._fmt_mark(float(value)),
                                      size=self.circle_s, seed=seed)

    # ----- the right margin ------------------------------------------------
    def draw_entry(self, page, pno: int, r: fitz.Rect, W: float, H: float, q: dict):
        """'Q7.c 2.5/3' beside the answer, and the reason under it if marks were lost.

        Drawn directly in Kalam (the product's handwriting font) so the wrap used
        for measuring is the wrap that gets drawn.
        """
        page.insert_font(fontname="Kalam", fontfile=KALAM)
        head_size, reason_size = HEAD_SIZE * self.k, REASON_SIZE * self.k
        x0 = W + GUTTER * self.k
        max_w = W + STRIP_W * self.k - 8 * self.k - x0
        head = (f"{display_label(q, self.explicit)}  "
                f"{pr._fmt_mark(q['score'])}/{pr._fmt_mark(q['max'])}")
        reason = (q.get("reason") or "").strip() if q["score"] < q["max"] else ""
        head_lines = pr._wrap_to_width(head, max_w, lambda t: _KALAM.text_length(t, head_size))
        # The total of a split part above its first sub-part: "Q4(A)  2.25/2.5" over
        # "Q4(A)(i)  1.25/1.5" (Auditing SME, 2026-10-09: "In Q 4a marking should
        # also be mentioned"). Same rule as the product's margin (wording.part_totals).
        if not hasattr(self, "_part_totals"):
            self._part_totals = part_totals(
                (x["qid"], display_label(x, self.explicit), x["score"], x["max"],
                 not x.get("uncounted")) for x in self.plan["questions"])
        part = self._part_totals.get(str(q["qid"]))
        if part:
            head_lines = pr._wrap_to_width(part, max_w,
                                           lambda t: _KALAM.text_length(t, head_size)) + head_lines
        reason_lines = (pr._wrap_to_width(reason, max_w,
                                          lambda t: _KALAM.text_length(t, reason_size))
                        if reason else [])
        height = (len(head_lines) * head_size * 1.35
                  + (len(reason_lines) * reason_size * 1.35 + 3 if reason_lines else 0) + 6)
        yc = (r.y0 + r.y1) / 2
        top = self.reserve(pno, yc - head_size * 0.8, height, H)
        y = top + head_size
        for ln in head_lines:
            page.insert_text((x0, y), ln, fontsize=head_size, fontname="Kalam", color=RED)
            y += head_size * 1.35
        y += 3
        for ln in reason_lines:
            page.insert_text((x0, y), ln, fontsize=reason_size, fontname="Kalam", color=RED)
            y += reason_size * 1.35

    def draw_note(self, page, pno: int, r: fitz.Rect, W: float, H: float, text: str):
        """A margin note with no marks, for a page that carries no answer mark."""
        page.insert_font(fontname="Kalam", fontfile=KALAM)
        size = REASON_SIZE * self.k
        x0 = W + GUTTER * self.k
        max_w = W + STRIP_W * self.k - 8 * self.k - x0
        lines = pr._wrap_to_width(text, max_w, lambda t: _KALAM.text_length(t, size))
        height = len(lines) * size * 1.35 + 6
        yc = (r.y0 + r.y1) / 2
        y = self.reserve(pno, yc - size * 0.8, height, H) + size
        for ln in lines:
            page.insert_text((x0, y), ln, fontsize=size, fontname="Kalam", color=RED)
            y += size * 1.35

    # ----- build -----------------------------------------------------------
    def build(self, out_path: str) -> None:
        out = fitz.open()
        W0 = self.raw[0].rect.width
        strip_w = STRIP_W * self.k
        total_w = W0 + strip_w
        self.header_h = pr._report_strip_height(self.report, total_w)
        pages = []
        for pno in range(len(self.raw)):
            src = self.raw[pno]
            W, H = src.rect.width, src.rect.height
            header = self.header_h if pno == 0 else 0.0
            op = out.new_page(width=W + strip_w, height=H + header)
            if header:
                pr._draw_report_strip(op, 0, 0, W + strip_w, self.report)
                op.draw_line((0, header), (W + strip_w, header), color=RED, width=1)
            op.show_pdf_page(fitz.Rect(0, header, W, header + H), self.raw, pno)
            op.draw_line((W, header), (W, header + H), color=RED, width=1)
            pages.append((W, H + header))
        for q in self.plan["questions"]:
            marks = q.get("marks") or []
            for i, m in enumerate(marks):
                kind, anchor = m[0], m[1]
                value = m[2] if len(m) > 2 else {"tick": 1, "half": 0.5}.get(kind)
                glyph = "cross" if kind == "cross" else "tick"
                pno, r = self.rect_of(anchor)
                W, H = pages[pno - 1]
                op = out[pno - 1]          # re-fetch: handles go stale after new_page
                self.draw_mark(op, r, W, glyph, value, seed=f"{q['qid']}:{i}")
            entry_anchor = q.get("at") or (marks[0][1] if marks else None)
            if entry_anchor is not None:
                pno, r = self.rect_of(entry_anchor)
                W, H = pages[pno - 1]
                self.draw_entry(out[pno - 1], pno, r, W, H, q)
        # SME review (2026-09-23): a page with writing and no mark reads as "not
        # checked". A page that earns nothing (an uncounted attempt, a second photo of
        # a page) gets a plain seen tick and a note saying why.
        for i, note in enumerate(self.plan.get("page_notes") or []):
            pno, r = self.rect_of(note["at"])
            W, H = pages[pno - 1]
            self.draw_mark(out[pno - 1], r, W, "tick", None, seed=f"note:{i}")
            self.draw_note(out[pno - 1], pno, r, W, H, note["note"])
        out.save(out_path, garbage=4, deflate=True)
        out.close()


def render_previews(pdf_path: str, out_dir: str, stem: str, width_px: int = 1100):
    doc = fitz.open(pdf_path)
    for i, p in enumerate(doc):
        z = width_px / p.rect.width
        p.get_pixmap(matrix=fitz.Matrix(z, z)).save(f"{out_dir}/{stem}_fixed_p{i+1}.png")
    doc.close()
