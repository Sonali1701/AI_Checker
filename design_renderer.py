# -*- coding: utf-8 -*-
"""The evaluated copy of a design paper, in the format design faculty already use.

Page 1 is the OVERALL DRAWING FEEDBACK cover (name, marks, where you are good at,
areas of improvement). It is drawn on the institute's own template when
assets/design_feedback_cover.pdf is bundled, and on a plain equivalent when not.

Every student page follows at its original size, with
* a red hand drawn loop round each part that has a fault, numbered;
* the marks for each drawing ("Q2 10/20") written beside it, on the emptiest corner;
* a feedback panel BELOW the page: marks by criterion, the critique, and what each
  numbered loop points at.

Faculty write the critique across the drawing. The panel says the same thing
without covering the student's work, and it cannot run out of room.

Nothing here decides a mark. It draws a verified DesignReport and never raises for
a cosmetic reason: a loop or a tag that cannot be placed is skipped.
"""
from __future__ import annotations

import math
import os
import sys

import fitz
import pdf_safety

from pdf_renderer import RED, _sanitize_for_pdf, _wobble, _wrap_to_width

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLE_DIR = getattr(sys, "_MEIPASS", _PROJECT_DIR)
COVER_TEMPLATE_CANDIDATES = (
    os.path.join(_BUNDLE_DIR, "assets", "design_feedback_cover.pdf"),
    os.path.join(_PROJECT_DIR, "assets", "design_feedback_cover.pdf"),
)

INK = (0.13, 0.13, 0.16)
GREY = (0.45, 0.45, 0.48)
RULE = (0.80, 0.80, 0.83)
PANEL_FILL = (0.985, 0.975, 0.97)
BLUE = (0.10, 0.35, 0.80)
GREEN = (0.13, 0.52, 0.30)
BAR_TRACK = (0.86, 0.86, 0.88)

# The panel shows the short form of the feedback; the full text stays in the report
# for the faculty. Design team, 14 Oct 2026: the strips were long paragraphs that
# repeated the points, and asked to be shorter and easier to read. 26 Oct 2026: two
# sentences in 45 words kept the opening praise and dropped the sentence that named
# the faults (a gallery and a cafe whose overall feedback was praise only), and the
# faculty asked for the faults in detail. The marker writes at most 60 words and the
# faults by its second sentence; the panel keeps up to three sentences of that.
SUMMARY_SENTENCES = 3
SUMMARY_WORDS = 60
POINT_WORDS = 12
NOTE_WORDS = 14
STRENGTHS_SHOWN = 2
IMPROVEMENTS_SHOWN = 3

# The template's writing areas, in its own points (595.28 x 841.89): the PW Design &
# Arch "OVERALL FEEDBACK" page (2026-09-23), measured off its borders.
_COVER_SIZE = (595.28, 841.89)
_COVER_NAME_BOX = fitz.Rect(157.2, 204.3, 524.1, 233.9)
_COVER_MARKS_BOX = fitz.Rect(157.2, 253.8, 524.1, 283.5)
_COVER_PLUS_BOX = fitz.Rect(215.6, 310.0, 524.1, 498.8)
_COVER_IMPROVE_BOX = fitz.Rect(215.6, 524.5, 524.1, 733.2)
CREAM = (1.0, 0.973, 0.933)
CARD = (0.894, 0.851, 0.812)


def _fmt(value: float) -> str:
    return f"{float(value):g}"


def _measure(font: str, size: float):
    face = fitz.Font(font)
    return lambda text: face.text_length(text, fontsize=size)


def _clean(text: str) -> str:
    return _sanitize_for_pdf(" ".join(str(text or "").split()))


def _wrap(text: str, width: float, font: str, size: float) -> list[str]:
    return _wrap_to_width(_clean(text), width, _measure(font, size))


_SENTENCE_END = __import__("re").compile(r"(?<=[.!?])\s+")


_TRAILING = {"a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on", "at", "as", "so",
             "but", "by", "from", "into", "its", "their", "your", "is", "are", "was", "that", "which"}


def _cut_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    kept = words[:max_words]
    # Never end on a filler word: "too mature for a." read badly on a live strip.
    while len(kept) > 3 and kept[-1].lower().strip(",;:.!?") in _TRAILING:
        kept.pop()
    return " ".join(kept).rstrip(",;:.!? ") + "."


def short_text(text: str, max_sentences: int = SUMMARY_SENTENCES, max_words: int = SUMMARY_WORDS) -> str:
    """The first sentences of a critique, within a word budget: the summary a strip
    shows. The first sentence always stays, cut to the budget when it is long."""
    text = _clean(text)
    if not text:
        return ""
    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    kept: list[str] = []
    for sentence in sentences[:max_sentences]:
        if kept and len(" ".join(kept + [sentence]).split()) > max_words:
            break
        kept.append(sentence)
    return _cut_words(" ".join(kept), max_words)


def short_point(text: str, max_words: int = POINT_WORDS) -> str:
    """One point of a list: its first sentence, within a word budget."""
    text = _clean(text)
    if not text:
        return ""
    first = _SENTENCE_END.split(text)[0]
    return _cut_words(first, max_words)


def _fit_text(page, rect: fitz.Rect, text: str, *, font="helv", size=9.5, min_size=6.5,
              color=INK, leading=1.28) -> None:
    """Write `text` inside `rect`, shrinking the type until it fits. At the smallest
    size the text is cut at a line end rather than spilling out of the box."""
    text = _clean(text)
    if not text:
        return
    while True:
        lines = _wrap(text, rect.width, font, size)
        if len(lines) * size * leading <= rect.height or size <= min_size:
            break
        size -= 0.5
    room = max(1, int(rect.height // (size * leading)))
    y = rect.y0 + size
    for line in lines[:room]:
        page.insert_text((rect.x0, y), line, fontname=font, fontsize=size, color=color)
        y += size * leading


# Faculty edit the feedback before it goes to the student (PW Design & Arch, 23 Oct
# 2026: "can we make that feedback section editable so that faculties can edit them").
# The words sit in PDF form fields filled with the AI's text, with no border and the
# page's own font and size, so the copy looks the same until someone clicks into a
# box; Acrobat Reader, Edge and Chrome all edit and save them. Since 24 Oct 2026 the
# marks are fields too ("we also need to make those rating points editable. only
# heading can be non-editable"): each criterion's name and marks, the question's
# marks in the panel and on the drawing, the loop notes, and the cover's name, total
# and breakdown. Only the headings stay printed. The totals follow the marks: a
# question's marks add up its criteria, its tag on the drawing copies them, and the
# cover's total and breakdown add up the questions (PDF calculation scripts, run by
# Acrobat Reader, Chrome and Edge; a viewer without scripts leaves every field as it
# was typed). The bars keep the AI's shares.
EDITABLE_FEEDBACK = True
# Room under the AI's text for a faculty member's extra words, in lines. The words
# themselves keep the design team's compact panel (14 Oct 2026); the room adds at
# most two lines, as the summary and the two side-by-side lists each get one.
EDIT_ROOM_LINES = 1
BULLET = "\u2022 "


def _text_field(page, rect: fitz.Rect, name: str, value: str, *, size: float,
                color=INK, bold: bool = False, right: bool = False,
                baseline: float | None = None, calc: str = "") -> None:
    """A borderless form field holding `value`: multi-line in the page's Helvetica,
    or (`bold`) one line of Helvetica-Bold, right-aligned when `right`, with its
    baseline at `baseline` so it sits where the printed marks did. `calc` is the
    script that works its value out from other fields."""
    widget = fitz.Widget()
    if calc:
        widget.script_calc = calc
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_flags = 0 if bold else fitz.PDF_TX_FIELD_IS_MULTILINE
    widget.field_name = name
    widget.field_value = value
    widget.rect = rect
    widget.text_font = "Helv"
    widget.text_fontsize = size
    widget.text_color = color
    widget.fill_color = None
    widget.border_color = None
    widget.border_width = 0
    annot = page.add_widget(widget)
    if bold:
        _bold_appearance(page.parent, annot.xref, rect, value, size=size, color=color,
                         right=right, baseline=baseline)


def _bold_font_xref(doc) -> int:
    """Helvetica-Bold in the form's own fonts (/HeBo), added once per document."""
    catalog = doc.pdf_catalog()
    kind, value = doc.xref_get_key(catalog, "AcroForm/DR/Font/HeBo")
    if kind == "xref":
        return int(value.split()[0])
    xref = doc.get_new_xref()
    doc.update_object(xref, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold"
                            "/Encoding/WinAnsiEncoding>>")
    doc.xref_set_key(catalog, "AcroForm/DR/Font/HeBo", f"{xref} 0 R")
    return xref


def _bold_appearance(doc, xref: int, rect: fitz.Rect, value: str, *, size: float, color,
                     right: bool, baseline: float | None) -> None:
    """Draw a one-line field's value in Helvetica-Bold, as the marks were printed.

    PyMuPDF knows four field fonts, none of them bold, and MuPDF ignores a bold font
    in a field's /DA when it draws one. The field says /HeBo, which the form's fonts
    carry, so a viewer that redraws it after an edit keeps it bold; the first drawing
    is written here. Right-aligned fields get /Q 2 for the same reason."""
    font = _bold_font_xref(doc)
    rgb = " ".join(f"{c:g}" for c in color)
    doc.xref_set_key(xref, "DA", f"(/HeBo {size:g} Tf {rgb} rg)")
    if right:
        doc.xref_set_key(xref, "Q", "2")
    text = _clean(value)
    width = fitz.Font("hebo").text_length(text, fontsize=size)
    x = rect.width - 2.0 - width if right else 2.0
    y = rect.y1 - baseline if baseline is not None else (rect.height - size * 0.72) / 2
    literal = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = (f"/Tx BMC q BT /HeBo {size:g} Tf {rgb} rg {x:.2f} {y:.2f} Td "
              f"({literal}) Tj ET Q EMC")
    ap = doc.get_new_xref()
    doc.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[0 0 {rect.width:.2f} "
                          f"{rect.height:.2f}]/Resources<</Font<</HeBo {font} 0 R>>>>>>")
    doc.update_stream(ap, stream.encode("latin-1", "replace"))
    doc.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")


def _bold_field(page, name: str, value: str, x: float, baseline: float, *, size: float,
                color=RED, right_edge: float | None = None, room: float = 0.0,
                calc: str = "") -> None:
    """One line of bold marks where it used to be printed: starting at `x`, or ending
    at `right_edge` (right-aligned), with `room` points more for a longer edit."""
    width = fitz.Font("hebo").text_length(_clean(value), fontsize=size) + 4 + room
    x0 = right_edge - width if right_edge is not None else x - 2
    rect = fitz.Rect(x0, baseline - size * 1.05, x0 + width, baseline + size * 0.35)
    _text_field(page, rect, name, value, size=size, color=color, bold=True,
                right=right_edge is not None, baseline=baseline, calc=calc)


def _line_field(page, name: str, value: str, x: float, baseline: float, width: float, *,
                size: float, color=INK, lines: int = 1, calc: str = "") -> None:
    """Regular text, `lines` lines, whose first baseline sits at `baseline`."""
    top = baseline - size * 1.12
    # A field draws its text right at its left edge, so the field starts where the
    # printed text did (one 2 point step left put names off their bars).
    rect = fitz.Rect(x, top, x + width, top + lines * size * 1.16 + size * 0.45)
    _text_field(page, rect, name, value, size=size, color=color, calc=calc)


# --------------------------------------------------------------------------
# Totals that follow the marks
# --------------------------------------------------------------------------
#
# Calculation scripts, in the oldest JavaScript a PDF viewer runs (ES3: no let, no
# arrow functions, no trim). Each reads the first number in the fields it adds up
# ("2.5 / 7" gives 2.5) and writes the result in the copy's own format. When a field
# holds no number (a faculty member typed words), the total is left as it was.

def _js_text(value: str) -> str:
    import json
    return json.dumps(str(value))


def _js_numbers(names: list[str]) -> str:
    """Script that sets v to the list of first numbers in `names`, or v = null."""
    return ("var n=[" + ",".join(_js_text(n) for n in names) + "],v=[],i,f,m;"
            "for(i=0;i<n.length;i++){f=this.getField(n[i]);"
            "m=f?/^\\s*(\\d+(?:\\.\\d+)?)/.exec(String(f.value)):null;"
            "if(!m){v=null;break;}v.push(parseFloat(m[1]));}")


_JS_SUM = "var s=0;for(i=0;i<v.length;i++){s+=v[i];}s=Math.round(s*100)/100;"


def _calc_sum(names: list[str], suffix: str) -> str:
    """The sum of the first numbers in `names`, followed by `suffix` (" / 20")."""
    return _js_numbers(names) + "if(v!==null){" + _JS_SUM + "event.value=s+" + _js_text(suffix) + ";}"


def _calc_breakdown(parts: list[tuple[str, str]]) -> str:
    """"(Q1 8.5 + Q2 10)" from each question's marks field."""
    labels = "[" + ",".join(_js_text(label) for label, _ in parts) + "]"
    return (_js_numbers([name for _, name in parts]) + "if(v!==null){var l=" + labels + ",p=[];"
            "for(i=0;i<v.length;i++){p.push(l[i]+\" \"+Math.round(v[i]*100)/100);}"
            "event.value=\"(\"+p.join(\" + \")+\")\";}")


def _criterion_field(answer, index: int) -> str:
    return _field_name(answer.qid, f"c{index + 1}")


def _question_calc(answer) -> str:
    """A question's marks: its criteria added up. None to add for a question with no
    criteria (not attempted): its marks stay as typed."""
    if not answer.attempted or not answer.criteria:
        return ""
    names = [f"{_criterion_field(answer, i)}_marks" for i in range(len(answer.criteria))]
    return _calc_sum(names, f" / {_fmt(answer.max_marks)}")


def _order_calculations(doc, report) -> None:
    """Question marks are worked out before the tags and the cover that read them."""
    first = {_field_name(a.qid, "marks") for a in report.answers}
    calc = [(w.field_name, w.xref) for page in doc for w in page.widgets() if w.script_calc]
    if not calc:
        return
    ordered = [x for n, x in calc if n in first] + [x for n, x in calc if n not in first]
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/CO", "[" + " ".join(f"{x} 0 R" for x in ordered) + "]")


def _field_name(*parts) -> str:
    """A form field name: letters, digits and underscores only (a dot would nest it)."""
    import re as _re
    return "_".join(_re.sub(r"[^A-Za-z0-9_]+", "", str(part)) or "x" for part in parts)


# --------------------------------------------------------------------------
# Cover
# --------------------------------------------------------------------------

def _plain_cover(page) -> None:
    """The same layout as the institute's template, drawn from scratch."""
    page.draw_rect(fitz.Rect(32, 0, 563, page.rect.height), fill=CREAM, color=None)
    page.insert_text((50, 88), "OVERALL", fontname="hebo", fontsize=30, color=INK)
    page.insert_text((50, 122), "FEEDBACK", fontname="hebo", fontsize=30, color=INK)
    page.insert_text((400, 100), "Design & Arch", fontname="hebo", fontsize=18, color=INK)
    page.insert_text((50, 223), "Student Name:", fontname="hebo", fontsize=9, color=INK)
    page.insert_text((50, 273), "Total Marks", fontname="hebo", fontsize=9, color=INK)
    for box in (_COVER_NAME_BOX, _COVER_MARKS_BOX):
        page.draw_rect(box, color=INK, fill=(1, 1, 1), width=0.8, radius=0.45)
    for box, title, ask in ((_COVER_PLUS_BOX, "plus", "What were your strengths?"),
                            (_COVER_IMPROVE_BOX, "improve",
                             "What are some specific areas you can improve?")):
        card = fitz.Rect(50, box.y0, 202, box.y1)
        page.draw_rect(card, color=INK, fill=CARD, width=0.8, radius=0.08)
        page.insert_text((62, box.y0 + 26), title, fontname="hebo", fontsize=14, color=INK)
        _fit_text(page, fitz.Rect(62, box.y0 + 34, 194, box.y0 + 80), ask, size=8.5, color=INK)
        page.draw_rect(box, color=INK, fill=(1, 1, 1), width=0.8, radius=0.05)
    page.insert_text((440, 790), "TEAM PW DESIGN", fontname="hebo", fontsize=7, color=INK)
    page.insert_text((440, 800), "AND ARCHITECTURE", fontname="hebo", fontsize=7, color=INK)


def _bullets(page, rect: fitz.Rect, points: list[str], *, size=10.5, min_size=7.0,
             leading=1.32, gap=5.0) -> None:
    """Points as a list with a red dot each, shrinking the type until all fit."""
    points = [_clean(p) for p in points if _clean(p)]
    if not points:
        return
    indent = 11.0
    while True:
        wrapped = [_wrap(p, rect.width - indent, "helv", size) for p in points]
        needed = sum(len(w) for w in wrapped) * size * leading + gap * (len(points) - 1)
        if needed <= rect.height or size <= min_size:
            break
        size -= 0.5
    y = rect.y0
    for lines in wrapped:
        if y + size > rect.y1:
            break
        page.draw_circle(fitz.Point(rect.x0 + 3, y + size * 0.62), 2.1, color=None, fill=RED)
        for line in lines:
            if y + size > rect.y1:
                break
            page.insert_text((rect.x0 + indent, y + size), line, fontname="helv", fontsize=size, color=INK)
            y += size * leading
        y += gap


def _draw_cover(out, report) -> None:
    template = next((p for p in COVER_TEMPLATE_CANDIDATES if os.path.exists(p)), None)
    page = None
    if template:
        try:
            with fitz.open(template) as source:
                out.insert_pdf(source, from_page=0, to_page=0)
            page = out[-1]
        except Exception as exc:  # noqa: BLE001 - a damaged template only costs the branding
            print(f"[design] cover template not used ({type(exc).__name__}: {exc})", flush=True)
            page = None
    if page is None:
        page = out.new_page(width=_COVER_SIZE[0], height=_COVER_SIZE[1])
        _plain_cover(page)

    box = _COVER_NAME_BOX
    name = _clean(report.student_name)
    if EDITABLE_FEEDBACK:
        # Editable even when blank: the faculty can write the name in.
        size = 12.0
        while name and size > 8 and fitz.Font("helv").text_length(name, fontsize=size) > box.width - 32:
            size -= 0.5
        _line_field(page, "cover_name", name, box.x0 + 14, box.y0 + 8 + size, box.width - 28, size=size)
    elif name:
        _fit_text(page, fitz.Rect(box.x0 + 14, box.y0 + 8, box.x1 - 14, box.y1 - 4), name,
                  font="helv", size=12, min_size=8, color=INK)
    box = _COVER_MARKS_BOX
    total = f"{_fmt(report.total_score)} out of {_fmt(report.max_total)}"
    size = 13.0
    questions = [_field_name(a.qid, "marks") for a in report.answers]
    if EDITABLE_FEEDBACK:
        _bold_field(page, "cover_total", total, box.x0 + 14, box.y1 - 10, size=size, room=10,
                    calc=_calc_sum(questions, f" out of {_fmt(report.max_total)}"))
    else:
        page.insert_text((box.x0 + 14, box.y1 - 10), total, fontname="hebo", fontsize=size, color=RED)
    if len(report.answers) > 1:
        after = box.x0 + 14 + fitz.Font("hebo").text_length(total, fontsize=size) + 12
        parts = "(" + " + ".join(f"{a.qid} {_fmt(a.score)}" for a in report.answers) + ")"
        rect = fitz.Rect(after, box.y0 + 10, box.x1 - 12, box.y1 - 4)
        if EDITABLE_FEEDBACK:
            psize = 10.0
            while psize > 6.5 and fitz.Font("helv").text_length(parts, fontsize=psize) > rect.width - 4:
                psize -= 0.5
            _line_field(page, "cover_breakdown", parts, rect.x0, box.y1 - 10, rect.width,
                        size=psize, color=RED,
                        calc=_calc_breakdown([(a.qid, q) for a, q in zip(report.answers, questions)]))
        else:
            _fit_text(page, rect, parts, font="helv", size=10, min_size=6.5, color=RED)

    for points, box, name in ((report.good_at, _COVER_PLUS_BOX, "cover_plus"),
                              (report.to_improve, _COVER_IMPROVE_BOX, "cover_improve")):
        inner = fitz.Rect(box.x0 + 14, box.y0 + 14, box.x1 - 14, box.y1 - 12)
        if EDITABLE_FEEDBACK:
            _bullet_field(page, inner, name, list(points))
        else:
            _bullets(page, inner, list(points))


def _bullet_field(page, rect: fitz.Rect, name: str, points: list[str], *, size=10.5,
                  min_size=7.0, leading=1.32) -> None:
    """The cover's list as one editable field, at the largest size that fits the box."""
    points = [_clean(p) for p in points if _clean(p)]
    value = "\n".join(BULLET + p for p in points)
    while size > min_size:
        lines = sum(len(_wrap(BULLET + p, rect.width - 4, "helv", size)) for p in points)
        if (lines + EDIT_ROOM_LINES) * size * leading <= rect.height:
            break
        size -= 0.5
    _text_field(page, rect, name, value, size=size)


# --------------------------------------------------------------------------
# Marks on the drawing
# --------------------------------------------------------------------------

def _loop(page, rect: fitz.Rect, seed: str) -> fitz.Point:
    """A teacher's loop: one pen stroke round the part, not quite closed, slightly
    uneven. Returns the point where its number goes."""
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    rx, ry = rect.width / 2 * 1.10 + 3, rect.height / 2 * 1.10 + 3
    start = math.radians(215 + 20 * _wobble(seed, 1))
    sweep = 2 * math.pi + math.radians(28)
    steps = 72
    points = []
    for i in range(steps + 1):
        t = i / steps
        angle = start + sweep * t
        drift = 1.0 + 0.045 * t + 0.012 * math.sin(3 * angle + 6 * _wobble(seed, 2))
        points.append(fitz.Point(cx + rx * drift * math.cos(angle),
                                 cy + ry * drift * math.sin(angle)))
    bounds = page.rect
    points = [fitz.Point(min(max(p.x, 2), bounds.width - 2), min(max(p.y, 2), bounds.height - 2))
              for p in points]
    width = max(1.3, min(2.4, min(bounds.width, bounds.height) / 380))
    page.draw_polyline(points, color=RED, width=width, closePath=False, lineCap=1, lineJoin=1)
    return points[0]


def _number_tag(page, at: fitz.Point, number: int, radius: float = 7.5) -> None:
    bounds = page.rect
    centre = fitz.Point(min(max(at.x, radius + 1), bounds.width - radius - 1),
                        min(max(at.y, radius + 1), bounds.height - radius - 1))
    page.draw_circle(centre, radius, color=RED, fill=RED, width=0.5)
    label = str(number)
    size = radius * 1.35
    length = fitz.Font("hebo").text_length(label, fontsize=size)
    page.insert_text((centre.x - length / 2, centre.y + size * 0.36), label,
                     fontname="hebo", fontsize=size, color=(1, 1, 1))


# A tag may sit on paper this inky at most. On the third live copy the Q1 tag was
# printed over the student's Q4 notes because the emptiest corner was still writing.
TAG_MAX_INK = 0.03


def _marks_tag(page, answer, box_rect: fitz.Rect, ink, taken: list, loops: list | None = None) -> None:
    """'Q2 10/20' on the emptiest corner of the drawing that no other tag has used,
    keeping off the loops. Never over the student's work: when every corner, inside
    and just outside the drawing, has writing under it, there is no tag (the marks
    are in the feedback panel under the page)."""
    label = f"{answer.qid}  {_fmt(answer.score)}/{_fmt(answer.max_marks)}"
    size = max(11.0, min(16.0, page.rect.width / 42))
    length = fitz.Font("hebo").text_length(label, fontsize=size)
    w, h = length + 12, size + 9
    bounds = page.rect
    corners = [
        (box_rect.x1 - w - 4, box_rect.y0 + 4), (box_rect.x0 + 4, box_rect.y0 + 4),
        (box_rect.x1 - w - 4, box_rect.y1 - h - 4), (box_rect.x0 + 4, box_rect.y1 - h - 4),
        (box_rect.x1 - w - 4, box_rect.y0 - h - 2), (box_rect.x0 + 4, box_rect.y0 - h - 2),
        (box_rect.x1 - w - 4, box_rect.y1 + 2), (box_rect.x0 + 4, box_rect.y1 + 2),
        (box_rect.x1 + 4, box_rect.y0 + 4), (box_rect.x0 - w - 4, box_rect.y0 + 4),
    ]
    best, best_ink = None, None
    for x, y in corners:
        rect = fitz.Rect(x, y, x + w, y + h)
        if rect.x0 < 2 or rect.y0 < 2 or rect.x1 > bounds.width - 2 or rect.y1 > bounds.height - 2:
            continue
        if any(rect.intersects(other) for other in taken):
            continue
        covered = ink(rect) if ink else 0.0
        if any(rect.intersects(loop) for loop in (loops or [])):
            covered += 0.5
        if best is None or covered < best_ink - 1e-6:
            best, best_ink = rect, covered
    if best is None or best_ink > TAG_MAX_INK:
        return
    taken.append(best)
    page.draw_rect(best, color=RED, fill=(1, 1, 1), width=1.0, fill_opacity=0.86, radius=0.25)
    if not EDITABLE_FEEDBACK:
        page.insert_text((best.x0 + 6, best.y1 - 6.5), label, fontname="hebo", fontsize=size, color=RED)
        return
    # The question number is the tag's heading; its marks can be edited.
    head = f"{answer.qid}  "
    page.insert_text((best.x0 + 6, best.y1 - 6.5), head, fontname="hebo", fontsize=size, color=RED)
    marks = f"{_fmt(answer.score)}/{_fmt(answer.max_marks)}"
    after = best.x0 + 6 + fitz.Font("hebo").text_length(head, fontsize=size)
    _bold_field(page, _field_name(answer.qid, "marks_tag"), marks, after, best.y1 - 6.5, size=size,
                room=8, calc=_calc_sum([_field_name(answer.qid, "marks")], f"/{_fmt(answer.max_marks)}"))


def _ink_lookup(png: bytes | None, width: float, height: float):
    if not png:
        return None
    try:
        from design_grading import ink_mask
        mask = ink_mask(png)
    except Exception:  # noqa: BLE001 - placement falls back to the first free corner
        return None
    rows, cols = mask.shape[:2]

    def covered(rect: fitz.Rect) -> float:
        left, right = int(rect.x0 / width * cols), int(math.ceil(rect.x1 / width * cols))
        top, bottom = int(rect.y0 / height * rows), int(math.ceil(rect.y1 / height * rows))
        patch = mask[max(0, top):max(top + 1, bottom), max(0, left):max(left + 1, right)]
        return float(patch.mean()) if patch.size else 0.0

    return covered


def _to_rect(box: list[float], width: float, height: float) -> fitz.Rect:
    return fitz.Rect(box[0] / 1000 * width, box[1] / 1000 * height,
                     box[2] / 1000 * width, box[3] / 1000 * height)


# --------------------------------------------------------------------------
# Feedback panel
# --------------------------------------------------------------------------

class _Column:
    """Text flowing down one column. With page=None it only measures, so the panel's
    height is computed by the very code that later draws it."""

    def __init__(self, page, x: float, y: float, width: float):
        self.page, self.x, self.y, self.width = page, x, y, width

    def text(self, value: str, *, font="helv", size=9.5, color=INK, gap=3.0, indent=0.0) -> None:
        for line in _wrap(value, self.width - indent, font, size):
            self.y += size * 1.27
            if self.page is not None:
                self.page.insert_text((self.x + indent, self.y), line, fontname=font,
                                      fontsize=size, color=color)
        self.y += gap

    def row(self, left: str, right: str, *, size=9.0, color=INK, field: str = "") -> None:
        """A criterion and its marks. With `field`, both are form fields named
        `field`_name and `field`_marks, in the same place and the same height."""
        amount = fitz.Font("hebo").text_length(right, fontsize=size)
        lines = _wrap(left, self.width - amount - 10, "helv", size)
        if field and EDITABLE_FEEDBACK:
            first = self.y + size * 1.3
            if self.page is not None:
                _line_field(self.page, f"{field}_name", left, self.x, first,
                            self.width - amount - 8, size=size, color=color, lines=len(lines) or 1)
                _bold_field(self.page, f"{field}_marks", right, 0, first, size=size,
                            right_edge=self.x + self.width + 2, room=6)
            self.y += (len(lines) or 1) * size * 1.3 + 1.5
            return
        for index, line in enumerate(lines):
            self.y += size * 1.3
            if self.page is not None:
                self.page.insert_text((self.x, self.y), line, fontname="helv", fontsize=size, color=color)
                if index == 0:
                    self.page.insert_text((self.x + self.width - amount, self.y), right,
                                          fontname="hebo", fontsize=size, color=RED)
        self.y += 1.5

    def editable(self, name: str, value: str, *, size=9.3, gap=3.0, color=INK) -> None:
        """Text a faculty member can change: a form field over the room the text takes,
        plus EDIT_ROOM_LINES more. Measured with the page's own wrapping, so the panel
        is the same height whether it is measured or drawn."""
        if not EDITABLE_FEEDBACK:
            for paragraph in value.split("\n"):
                self.text(paragraph, size=size, color=color, gap=0)
            self.y += gap
            return
        lines = sum(len(_wrap(paragraph, self.width - 4, "helv", size)) or 1
                    for paragraph in value.split("\n"))
        height = (lines + EDIT_ROOM_LINES) * size * 1.27 + 3
        if self.page is not None:
            # From self.x, not 2 points left of it: the text then lines up under its
            # heading (24 Oct 2026, it sat 2 points left of "Overall feedback").
            _text_field(self.page, fitz.Rect(self.x, self.y + 1, self.x + self.width,
                                             self.y + 1 + height), name, value, size=size,
                        color=color)
        self.y += height + gap

    def rule(self) -> None:
        self.y += 4
        if self.page is not None:
            self.page.draw_line(fitz.Point(self.x, self.y), fitz.Point(self.x + self.width, self.y),
                                color=RULE, width=0.6)
        self.y += 3

    def bar(self, share: float, *, height: float = 3.2, gap: float = 5.0) -> None:
        """A thin bar showing a criterion's share of its marks, under its row."""
        share = min(max(float(share or 0), 0.0), 1.0)
        if self.page is not None:
            track = fitz.Rect(self.x, self.y + 1.5, self.x + self.width, self.y + 1.5 + height)
            self.page.draw_rect(track, color=None, fill=BAR_TRACK, radius=0.5)
            if share > 0:
                fill = fitz.Rect(track.x0, track.y0, track.x0 + track.width * share, track.y1)
                self.page.draw_rect(fill, color=None, fill=RED, radius=0.5)
        self.y += 1.5 + height + gap

    def point(self, value: str, *, color=RED, size=8.8, gap=2.0) -> None:
        """One short point with a coloured dot in front of it."""
        indent = 9.0
        lines = _wrap(value, self.width - indent, "helv", size)
        for index, line in enumerate(lines):
            self.y += size * 1.25
            if self.page is not None:
                if index == 0:
                    self.page.draw_circle(fitz.Point(self.x + 2.6, self.y - size * 0.32), 1.9,
                                          color=None, fill=color)
                self.page.insert_text((self.x + indent, self.y), line, fontname="helv",
                                      fontsize=size, color=INK)
        self.y += gap


def _marks_block(column: _Column, answer) -> None:
    marks = f"{_fmt(answer.score)} / {_fmt(answer.max_marks)}"
    if EDITABLE_FEEDBACK:
        # "Q2" is the heading; the marks after it can be edited.
        size = 13.5
        column.y += size * 1.27
        if column.page is not None:
            head = f"{answer.qid} "
            column.page.insert_text((column.x, column.y), head, fontname="hebo", fontsize=size, color=RED)
            after = column.x + fitz.Font("hebo").text_length(head, fontsize=size)
            _bold_field(column.page, _field_name(answer.qid, "marks"), marks, after, column.y,
                        size=size, room=12, calc=_question_calc(answer))
        column.y += 3
    else:
        column.text(f"{answer.qid}   {marks}", font="hebo", size=13.5, color=RED, gap=3)
    if not answer.attempted:
        if EDITABLE_FEEDBACK:
            column.editable(_field_name(answer.qid, "not_attempted"), "Not attempted.", size=9.5, gap=3)
        else:
            column.text("Not attempted.", size=9.5)
        return
    for index, criterion in enumerate(answer.criteria):
        column.row(criterion.name, f"{_fmt(criterion.awarded)} / {_fmt(criterion.max)}", size=8.6,
                   field=_criterion_field(answer, index))
        column.y -= 1.5
        column.bar(criterion.awarded / criterion.max if criterion.max else 0.0)


# The panel's three headings, in the PW Design & Arch faculty's words (review comments
# on the Diya copy, 22 Oct 2026: "under the heading OVERALL FEEDBACK", "GOOD JOB",
# "NEEDS IMPROVEMENT").
SUMMARY_HEADING = "Overall feedback"
STRENGTHS_HEADING = "Good Job"
IMPROVEMENTS_HEADING = "Needs improvement"


def _feedback_block(column: _Column, answer) -> None:
    """The short form: a two sentence overall feedback, what was done well and what
    needs improvement as two short lists side by side, then what each numbered loop
    points at."""
    if not answer.attempted:
        return
    summary = short_text(answer.feedback)
    if summary:
        column.text(SUMMARY_HEADING, font="hebo", size=8.0, color=INK, gap=1)
        column.editable(_field_name(answer.qid, "overall_feedback"), summary, size=9.3, gap=5)
    works = [short_point(p) for p in answer.strengths[:STRENGTHS_SHOWN]]
    work_on = [short_point(p) for p in answer.improvements[:IMPROVEMENTS_SHOWN]]
    works, work_on = [p for p in works if p], [p for p in work_on if p]
    if works or work_on:
        gutter = 10.0
        half = (column.width - gutter) / 2
        left = _Column(column.page, column.x, column.y, half)
        right = _Column(column.page, column.x + half + gutter, column.y, half)
        if works:
            left.text(STRENGTHS_HEADING, font="hebo", size=8.0, color=GREEN, gap=1)
            if EDITABLE_FEEDBACK:
                left.editable(_field_name(answer.qid, "good_job"),
                              "\n".join(BULLET + p for p in works), size=8.8, gap=2)
            else:
                for point in works:
                    left.point(point, color=GREEN)
        if work_on:
            right.text(IMPROVEMENTS_HEADING, font="hebo", size=8.0, color=RED, gap=1)
            if EDITABLE_FEEDBACK:
                right.editable(_field_name(answer.qid, "needs_improvement"),
                               "\n".join(BULLET + p for p in work_on), size=8.8, gap=2)
            else:
                for point in work_on:
                    right.point(point, color=RED)
        column.y = max(left.y, right.y) + 2
    for region in answer.regions:
        note = short_point(region.note, NOTE_WORDS)
        if not EDITABLE_FEEDBACK:
            column.text(f"{region.number}   {note}", size=8.6, color=RED, gap=1.5)
            continue
        # The loop's number is printed; what it points at can be edited.
        size = 8.6
        head = f"{region.number} "
        indent = fitz.Font("helv").text_length(head, fontsize=size)
        lines = _wrap(note, column.width - indent - 4, "helv", size) or [""]
        first = column.y + size * 1.27
        if column.page is not None:
            column.page.insert_text((column.x, first), head, fontname="helv", fontsize=size, color=RED)
            _line_field(column.page, _field_name(answer.qid, f"note{region.number}"), note,
                        column.x + indent, first, column.width - indent, size=size, color=RED,
                        lines=len(lines))
        column.y += len(lines) * size * 1.27 + 1.5


def _panel(page, answers: list, x0: float, y0: float, width: float) -> float:
    """Draw (or, with page=None, measure) the panel. Returns its bottom y."""
    pad, gutter = 16.0, 22.0
    inner = width - 2 * pad
    columns = 2 if inner >= 380 else 1
    col_w = (inner - gutter * (columns - 1)) / columns
    y = y0 + pad
    head = _Column(page, x0 + pad, y - 4, inner)
    head.text("FEEDBACK ON THIS PAGE", font="hebo", size=8.0, color=GREY, gap=2)
    y = head.y + 2
    if len(answers) == 1 and columns == 2:
        # Marks and their bars take the narrower left column, the words the wider right.
        marks_w = inner * 0.36
        left = _Column(page, x0 + pad, y, marks_w)
        _marks_block(left, answers[0])
        right = _Column(page, x0 + pad + marks_w + gutter, y, inner - marks_w - gutter)
        _feedback_block(right, answers[0])
        return max(left.y, right.y) + pad
    for start in range(0, len(answers), columns):
        bottoms = []
        for offset, answer in enumerate(answers[start:start + columns]):
            column = _Column(page, x0 + pad + offset * (col_w + gutter), y, col_w)
            _marks_block(column, answer)
            column.y += 3
            _feedback_block(column, answer)
            bottoms.append(column.y)
        y = max(bottoms) + 6
        if start + columns < len(answers):
            rule = _Column(page, x0 + pad, y, inner)
            rule.rule()
            y = rule.y + 4
    return y + pad - 6


def _unlocated_page(out, answers: list, width: float) -> None:
    """Answers with no drawing on any page (not attempted, or not located)."""
    height = _panel(None, answers, 0, 0, width) + 8
    page = out.new_page(width=width, height=max(height, 120))
    page.draw_rect(page.rect, fill=PANEL_FILL, color=None)
    _panel(page, answers, 0, 0, width)


def build_design_pdf(upright_doc, report, pngs: list[bytes] | None = None) -> bytes:
    """The evaluated copy: cover, then every student page with its loops, marks and
    feedback panel. `upright_doc` is the page coordinate system the report's boxes
    were given in (design_grading.upright_pdf)."""
    out = fitz.open()
    _draw_cover(out, report)
    pngs = list(pngs or [])
    by_page: dict[int, list] = {}
    for answer in report.answers:
        by_page.setdefault(answer.page if answer.page else 0, []).append(answer)
    # "Q3 0 out of 15. Not attempted." is said once, under the last page, not on a
    # page of its own and not beside another question's drawing.
    skipped = [a for a in by_page.get(0, []) if not a.attempted]
    if skipped and len(upright_doc):
        by_page[0] = [a for a in by_page[0] if a.attempted]
        by_page.setdefault(len(upright_doc), []).extend(skipped)

    for index in range(len(upright_doc)):
        number = index + 1
        source = upright_doc[index].rect
        width, height = source.width, source.height
        answers = by_page.get(number, [])
        panel_h = (_panel(None, answers, 0, height, width) - height) if answers else 0.0
        page = out.new_page(width=width, height=height + panel_h)
        pdf_safety.show_source_page(page, fitz.Rect(0, 0, width, height), upright_doc, index)
        if not answers:
            continue
        ink = _ink_lookup(pngs[index] if index < len(pngs) else None, width, height)
        taken: list = []
        loops: list = []
        for answer in answers:
            for region in answer.regions:
                if region.page != number:
                    continue
                try:
                    around = _to_rect(region.box, width, height)
                    loops.append(around + (-8, -8, 8, 8))
                    at = _loop(page, around, f"{answer.qid}:{region.number}")
                    _number_tag(page, at, region.number)
                except Exception as exc:  # noqa: BLE001 - a loop is never worth a copy
                    print(f"[design] loop {region.number} not drawn ({exc})", flush=True)
        for answer in answers:
            if answer.box is None:
                continue
            try:
                _marks_tag(page, answer, _to_rect(answer.box, width, height), ink, taken, loops)
            except Exception as exc:  # noqa: BLE001
                print(f"[design] marks tag for {answer.qid} not drawn ({exc})", flush=True)
        page.draw_rect(fitz.Rect(0, height, width, height + panel_h), fill=PANEL_FILL, color=None)
        page.draw_line(fitz.Point(0, height), fitz.Point(width, height), color=RED, width=1.2)
        _panel(page, answers, 0, height, width)

    # Loops whose region sits on another page than the answer's own page.
    for answer in report.answers:
        for region in answer.regions:
            if region.page != answer.page and 1 <= region.page <= len(upright_doc):
                page = out[region.page]          # cover is page 0
                rect = upright_doc[region.page - 1].rect
                try:
                    at = _loop(page, _to_rect(region.box, rect.width, rect.height),
                               f"{answer.qid}:{region.number}")
                    _number_tag(page, at, region.number)
                except Exception:  # noqa: BLE001
                    pass

    unlocated = by_page.get(0, [])
    if unlocated:
        _unlocated_page(out, unlocated, _COVER_SIZE[0])
    if EDITABLE_FEEDBACK:
        _order_calculations(out, report)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
