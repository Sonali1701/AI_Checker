# -*- coding: utf-8 -*-
"""Repair a report's evidence bookkeeping from the grader's own citations, before drawing.

The answer map assigns OCR lines to questions before grading and, when it guesses,
guesses big; the grader then cites the lines it actually read. Two renderers draw
from the report (the teacher layout and the legacy layout) and both used to trust
the map's regions and the raw anchors, so on the SST review batch of 2026-09-16:

* a one-mark fill-in answer, "c. Intermountain", was mapped to the last line of
  page 1 plus ten lines of page 2, and its mark and margin remark came out a page
  away beside "Depositional plains" while the grader had cited the right line;
* a numbered MCQ list came back from Mathpix as ONE line holding a LaTeX array,
  five questions anchored to that block, and four of them drew nothing;
* a step whose quoted words ran across an OCR line break, or whose line OCR had
  garbled ("Servitured Plains" for "Structural Plains"), was drawn nowhere.

This pass rewrites the report so that whichever layout draws it sees:

* `answer_regions[qid]` trimmed to the rows the grader cited plus the mapped rows
  that continue the answer (pdf_renderer._lines_the_grader_read), never a row that
  another question's grader cited;
* `anchor_line_id` on a line of that region (the answer's last body line when the
  map's anchor was elsewhere), and for an MCQ row inside a merged block a synthetic
  OCR line for just that row, appended to the page's OCR (`P1L5#r2`);
* `criteria[i].step_line_id` resolved on the trimmed region (anchors.resolve_steps
  with evidence required), so a grader-cited line whose quote OCR cannot confirm is
  still honoured and a firm unique match elsewhere re-homes the step.

Scores, remarks and maxima are never touched. The pass is idempotent.
"""
from __future__ import annotations

import re

from exam_package import normalize_qid
from mathpix import LineBox


def _independently_located_objective(report, question):
    qid = normalize_qid(question.qid)
    verified = {normalize_qid(value) for value in getattr(report, "objective_visual_verified_qids", [])}
    unconfirmed = {normalize_qid(value) for value in getattr(report, "objective_unconfirmed_qids", [])}
    region = (getattr(report, "answer_regions", {}) or {}).get(qid, {})
    return (qid in verified and qid not in unconfirmed
            and region.get("source") in {"independent_visual_location", "confirmed_owned_ocr_location"}
            and question.anchor_line_id in region.get("line_ids", []))


# "1 B Indika", "Q4 C", "A1. Indika (B)", "Ans 2 (C) Tamil": the number after an
# optional q/ans/a prefix, and the option letter either right after it or in
# brackets anywhere on the line. Aditya's SST copy (2026-09-18) wrote "A1. Indika
# (B)" and its Q2 and Q3 ticks went to other lines, one on another page.
_LABEL_NUMBER_RE = re.compile(
    r"^\(?\s*(?:q|que|ques|qs|ans|a)?[._\s{]*([0-9Il])(?![0-9])", re.I)
_OPTION_LINE_RE = re.compile(
    r"^\(?\s*(?:q|que|ques|qs|ans|a)?[._\s{]*[0-9Il]?[0-9]?[).:\-}\s]*\(?\s*[A-Da-d]\b"
    r"|\(\s*[A-Da-d]\s*\)", re.I)


def _label_number(text: str) -> int | None:
    """The question number a short option line starts with; I and l read as 1."""
    from mathpix import readable_ocr_text
    text = readable_ocr_text(text)
    match = _LABEL_NUMBER_RE.match(text)
    if not match or not _OPTION_LINE_RE.search(text):
        return None
    digit = match.group(1)
    return 1 if digit in "Il" else int(digit)


# The number a line OPENS with, whatever follows it: "Q1) (A) Fortified citadel and
# lower town", "Q1 a) Fortified ...", "1. a) Fortified ...", "6. a> Kalibanga", "4.".
# A bracketed "(1) Silappadikaran" is a numbered point inside an answer, not a label.
_OPENING_NUMBER_RE = re.compile(
    r"^[^\w(\[]{0,2}(?:q(?:ue?s?)?\s*[._]?\s*)?(\d{1,3})(?![0-9])(?=\s*[).:\-}>\]]|\s|$)",
    re.I)
# A lettered sub-part label opening a line: "a) Kalibangan", "(c) third", "d). Arch...".
_SUBPART_OPEN_RE = re.compile(r"^\W{0,2}\(?\s*([a-h])\s*[).>]", re.I)


def _opening_number(text: str) -> int | None:
    from mathpix import readable_ocr_text
    match = _OPENING_NUMBER_RE.match(readable_ocr_text(text or ""))
    return int(match.group(1)) if match else None


# A short answer row's number read leniently: "Ques: -2-(D)", "- No -1:-(A)".
_ROW_OPENING_RE = re.compile(r"^\W*(?:(?:q(?:ue(?:s(?:tion)?)?)?|[a-z]ues?|no|ans)\W{0,4}){1,2}(\d{1,3})(?!\d)", re.I)


def _row_question_number(text: str) -> int | None:
    number = _opening_number(text)
    if number is not None:
        return number
    from mathpix import readable_ocr_text
    plain = readable_ocr_text(text or "")
    match = _ROW_OPENING_RE.match(plain) or _NUMBER_THEN_ANS_RE.match(plain)
    return int(match.group(1)) if match else None


# "2Ans - (D) Both B and C": the number run into the word after it.
_NUMBER_THEN_ANS_RE = re.compile(r"^\W*(\d{1,3})\s*(?:ans|q)", re.I)


def _numbered_rows_over_visual_boxes(report, by_lid: dict, by_page: dict, student_doc,
                                     changed: dict) -> None:
    """An MCQ placed by the visual reader's box moves onto its own numbered row.

    The key-blind reader confirms WHAT was written; the box it returns for WHERE is a
    model's estimate. MEASURED on the Uday Bihar Board copies (12 Oct 2026): English
    a5f0f595's Q1 box ran from the answer to the far right of the page and its tick
    floated there, Q5, Q6, Q9 and Q13 sat a row off; Economics e782e43c's ten rows
    ("1 Ans - (A) A. Masshadl", OCR confidence 0.33) were passed over for such boxes
    and the ticks fell between rows; on f5d29451 Q1's box was the heading "Choose
    the correct option", a row above the answer. The OCR row that opens with this
    question's number, alone on its page, and names no other option is the answer
    itself, however unsure the OCR is of its words; inside a merged block it is that
    block's numbered row (pdf_renderer._array_rows). Nothing moves when there is no
    such row, or more than one.
    """
    import pdf_renderer as pr
    from mathpix import LineBox
    from strict_grading import _objective_choice_from_line
    objective = [q for q in report.questions
                 if pr._is_objective_mark(q) and getattr(q, "attempted", True)]
    visual = [q for q in objective
              if ((getattr(report, "answer_regions", None) or {}).get(normalize_qid(q.qid)) or {})
              .get("source") == "independent_visual_location"
              and _case_part(str(q.qid)) is None]      # parts: _anchor_case_parts_in_order
    if not visual:
        return
    numbers = {q.qid: pr._objective_item_number(str(q.qid)) for q in objective}
    foreign = _lines_of_other_questions(report, by_lid, set(numbers.values()))
    visual_lids = {(getattr(q, "anchor_line_id", "") or "").strip() for q in visual}
    taken = {(getattr(q, "anchor_line_id", "") or "").strip() for q in objective} - visual_lids
    for q in visual:
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        current = by_lid.get(anchor)
        number = numbers[q.qid]
        if current is None or number >= 9999:
            continue
        chosen = str(getattr(q, "chosen_option", "") or "").strip().upper()[:1]
        page_h = student_doc[current["page"] - 1].rect.height
        candidates = []
        for line in list(by_lid.values()):
            if (line["page"] != current["page"] or line["lid"] in visual_lids or "#r" in line["lid"]
                    or line["lid"] in foreign or line["lid"] in taken):
                continue
            px = line.get("_px")
            if getattr(px, "cancelled", False) or getattr(px, "kind", "text") not in ("text", "math"):
                continue
            tall = line["y1"] - line["y0"] > 0.085 * page_h
            rows = (pr._array_rows(student_doc, line, by_lid) or []) if tall else [line]
            for row in rows:
                text = row.get("text") or ""
                label = row.get("_row_label") if row is not line else None
                if label is None:
                    label = _row_question_number(text)
                if label != number:
                    continue
                letter = _objective_choice_from_line(text, number)
                if letter and chosen and letter != chosen:
                    continue
                # Only a row the reader roughly pointed at: across the same part of the
                # page, and within a tenth of the page of its box (the Bihar boxes drift
                # up to three rows down a long list). A box away from every numbered
                # row, such as an inked answer cell in a grid to their right, is the
                # answer itself and stays.
                if row["x0"] > current["x1"] or row["x1"] < current["x0"]:
                    continue
                gap = max(0.0, max(row["y0"], current["y0"]) - min(row["y1"], current["y1"]))
                if gap > 0.10 * page_h:
                    continue
                candidates.append((gap, row))
        if not candidates:
            candidates = _row_after_neighbour(q, current, number, objective, numbers, by_lid,
                                              visual_lids, foreign, taken, page_h)
        page_w = student_doc[current["page"] - 1].rect.width
        if not candidates and current["x1"] - current["x0"] > 0.4 * page_w:
            # A box across the row with no numbered row to move to: OCR missed the
            # faint "4)". MEASURED 2026-10-10 (CA Inter Corporate Law, sheet row F40):
            # two ticks floated past the photo's right edge, level with rows 1 and 4.
            # The one unnumbered option line on that same row is the answer's writing.
            same_row = [line for line in by_lid.values()
                        if line["page"] == current["page"] and line["lid"] not in visual_lids
                        and "#r" not in line["lid"] and line["lid"] not in foreign
                        and line["lid"] not in taken and _is_option_line(line, page_w)
                        and _row_question_number(line.get("text") or "") is None
                        and current["x0"] - 4 <= line["x0"] and line["x1"] <= current["x1"] + 4
                        and min(line["y1"], current["y1"]) - max(line["y0"], current["y0"])
                        > 0.5 * min(line["y1"] - line["y0"], current["y1"] - current["y0"])
                        and _objective_choice_from_line(line.get("text") or "", number)
                        in ("", None, chosen)]
            if len(same_row) == 1 and chosen:
                candidates = [(0.0, same_row[0])]
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0])
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 1.0:
            continue                      # two rows equally near: nothing to choose by
        target = candidates[0][1]
        if target.get("_array_row") is not None:
            new_lid = f"{target['lid']}#r{target['_array_row']}"
            if new_lid not in by_lid:
                pg = by_page.get(target["page"])
                parent = by_lid.get(target["lid"])
                if pg is None or parent is None:
                    continue
                page = student_doc[target["page"] - 1]
                sx = float(pg.image_width or 1) / max(1.0, page.rect.width)
                sy = float(pg.image_height or 1) / max(1.0, page.rect.height)
                box = (target["x0"] * sx, target["y0"] * sy, target["x1"] * sx, target["y1"] * sy)
                row_line = LineBox(new_lid, target["text"], box, target["page"],
                                   confidence=getattr(parent["_px"], "confidence", None))
                pg.lines.append(row_line)
                by_lid[new_lid] = {**target, "lid": new_lid, "_px": row_line}
            target = by_lid[new_lid]
        q.anchor_line_id = target["lid"]
        q.page = target["page"]
        taken.add(target["lid"])
        _set_region(report, q, [target])
        report.answer_regions[normalize_qid(q.qid)]["source"] = "confirmed_owned_ocr_location"
        changed.setdefault(q.qid, []).append(f"own numbered row {anchor} -> {target['lid']}")


_ROMAN_INDEX = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
                "ix": 9, "x": 10}
# A case-study part row: "(ii) (C) 37", "ii) c) 37", OCR's "11) c) 37", "III) D) 100",
# "iiy (C) 37" (")" read as "y"), and the question's own first row "18.1) a) 48 and
# 50" / "19.(i) (B) 7". A number before the label needs a "." or a space after it, so
# "11)" is part (ii), not question 1 part (i).
_PART_ROW_RE = re.compile(
    r"^\W{0,2}(?:q?\s*(\d{1,3})(?:\s*[.\-]\s*|\s+|(?=\()))?\(?\s*([ivxlI1|]{1,4})\s*(?:[).>]|y(?=\s))",
    re.I)


def _case_part(qid: str) -> tuple[int, int] | None:
    """(18, 2) for "Q18.ii" or "Q18(ii)"; None for a question with no roman part."""
    match = re.fullmatch(r"\s*q?\s*(\d{1,3})\s*[.(]?\s*([ivx]{1,4})\s*\)?\s*", str(qid or ""), re.I)
    if not match or match.group(2).lower() not in _ROMAN_INDEX:
        return None
    return int(match.group(1)), _ROMAN_INDEX[match.group(2).lower()]


def _part_row(text: str) -> tuple[int | None, int] | None:
    """(number or None, part index) for a part row that also names an option."""
    from mathpix import readable_ocr_text
    plain = readable_ocr_text(text or "")
    match = _PART_ROW_RE.match(plain)
    if not match or not _OPTION_LINE_RE.search(plain[match.end():]):
        return None
    token = re.sub(r"[lI1|]", "i", match.group(2)).lower()
    if token not in _ROMAN_INDEX:
        return None
    return (int(match.group(1)) if match.group(1) else None), _ROMAN_INDEX[token]


def _anchor_case_parts_in_order(report, by_lid: dict, changed: dict) -> None:
    """The MCQ parts of a case-study question take that question's rows in order.

    MEASURED 2026-10-14 (Vidyapeeth Class 8 Maths, Q18 (i)-(iii), Q19 (i)-(iii)): the
    student wrote "18.(i) a) 48 and 50 / ii) c) 37 / iii) d) (15,20,26)". OCR read
    "ii)" as "11)", so that row counted as Q11's and Q18.ii was moved by its number
    alone onto Q18.i's row ("18.1)"); Q18.iii sat on the visual reader's estimate, on
    blank paper below Q19. Both marks were drawn on the wrong rows of a copy sent to
    the student. The row opening with the number is part (i) and each following
    labelled row the next part, up to the next question. A part moves only when it
    sits on another part's row or outside its question's rows altogether."""
    from collections import defaultdict
    from mathpix import readable_ocr_text
    import pdf_renderer as pr
    parts: dict[int, dict[int, object]] = defaultdict(dict)
    for q in report.questions:
        place = _case_part(str(getattr(q, "qid", "") or ""))
        if place is not None:
            parts[place[0]][place[1]] = q
    for number, by_index in parts.items():
        movable = [q for q in by_index.values()
                   if pr._is_objective_mark(q) and getattr(q, "attempted", True)]
        if not movable or sorted(by_index) != list(range(1, len(by_index) + 1)):
            continue
        pages = {by_lid[a]["page"] for q in by_index.values()
                 for a in [(getattr(q, "anchor_line_id", "") or "").strip()] if a in by_lid}
        pages |= {int(getattr(q, "page", 0) or 0) for q in by_index.values()} - {0}
        assigned = None
        for page in sorted(pages):
            lines = sorted((l for l in by_lid.values() if l["page"] == page and "#r" not in l["lid"]),
                           key=lambda l: (l["y0"], l["x0"]))
            openers = [i for i, l in enumerate(lines)
                       if (_row_question_number(l.get("text") or "") == number
                           or (_part_row(l.get("text") or "") or (None, 0))[0] == number)]
            for start in openers:
                opener = lines[start]
                # "18.(i) a) 48 and 50" is part (i) itself; "19:- Case study" is a
                # heading, and part (i) is the next labelled row.
                body = _OPENING_NUMBER_RE.sub("", readable_ocr_text(opener.get("text") or ""), count=1)
                rows = ([opener] if (_part_row(opener.get("text") or "")
                                     or _OPTION_LINE_RE.search(body)) else [])
                for line in lines[start + 1:]:
                    if len(rows) == len(by_index):
                        break
                    if min(line["x1"], opener["x1"]) - max(line["x0"], opener["x0"]) <= 0:
                        continue             # a rough-work column beside the answers
                    read = _part_row(line.get("text") or "")
                    if read is not None and read[0] in (None, number):
                        rows.append(line)
                        continue
                    opening = _row_question_number(line.get("text") or "")
                    if opening is not None and opening != number and read is None:
                        break                # the next question begins
                if len(rows) != len(by_index):
                    continue
                read_parts = [(_part_row(r.get("text") or "") or (None, 0))[1] for r in rows]
                if any(p and p != k for k, p in enumerate(read_parts, 1)):
                    continue                 # labels out of order: nothing to trust
                assigned = rows
                break
            if assigned is not None:
                break
        if assigned is None:
            continue
        top, bottom = assigned[0]["y0"], assigned[-1]["y1"]
        own_rows = {r["lid"]: k for k, r in enumerate(assigned, 1)}
        movable_ids = {id(q) for q in movable}
        for index, q in by_index.items():
            if id(q) not in movable_ids:
                continue
            target = assigned[index - 1]
            anchor = (getattr(q, "anchor_line_id", "") or "").strip()
            current = by_lid.get(anchor.split("#r")[0]) if anchor else None
            if current is not None and current["lid"] == target["lid"]:
                continue
            on_other_part = current is not None and own_rows.get(current["lid"]) not in (None, index)
            outside = (current is None or current["page"] != target["page"]
                       or current["y1"] < top - 2 or current["y0"] > bottom + 2)
            if not (on_other_part or outside):
                continue
            q.anchor_line_id = target["lid"]
            q.page = target["page"]
            _set_region(report, q, [target])
            report.answer_regions[normalize_qid(q.qid)]["source"] = "confirmed_owned_ocr_location"
            changed.setdefault(q.qid, []).append(
                f"case part row {anchor or '-'} -> {target['lid']} "
                f"({readable_ocr_text(target.get('text') or '')[:30]!r})")


# "Q1.A", "Q1(c)", "q1.e": the question number and a lettered part.
_LETTER_PART_QID_RE = re.compile(r"\s*q?\s*(\d{1,3})\s*[.(]?\s*([a-h])\s*\)?\s*", re.I)
# A row that opens with a part letter: "A=3", "(A) (3) 336000", "B - 1", "(c) 3 (4)",
# "1) & A & (1)" (the question's first row in a table), or "C" alone (a blank part).
# Groups: number, opening bracket, letter, the mark after the letter.
_LETTER_ROW_RE = re.compile(
    r"^\W{0,3}(?:q?\s*(\d{1,3})\s*[).:\-]\s*)?(\()?\s*([a-h])\s*([)=:.\-–—]|(?=\s)|$)", re.I)
_OPTION_START_RE = re.compile(r"^\W{0,3}\(?\s*(?:[1-4]|[A-Da-d])\s*\)?(?:\W|$)")


def _letter_part(qid: str) -> tuple[int, str] | None:
    """(1, "a") for "Q1.A"; None for anything else (roman parts are _case_part's)."""
    match = _LETTER_PART_QID_RE.fullmatch(str(qid or ""))
    return (int(match.group(1)), match.group(2).lower()) if match else None


def _letter_row(text: str) -> tuple[int | None, str] | None:
    """(number or None, letter) for a row that opens with a part letter and then holds
    nothing but an answer: an option, a short value, or nothing (a blank part)."""
    from mathpix import readable_ocr_text
    plain = re.sub(r"\\text\s*\{([^}]*)\}", r"\1", str(text or ""))
    plain = readable_ocr_text(plain).replace("&", " ")
    plain = re.sub(r"\\[()\[\]]|\\hline", " ", plain)
    plain = " ".join(plain.split())
    match = _LETTER_ROW_RE.match(plain)
    if not match:
        return None
    rest = plain[match.end():].strip()
    if rest and not (_OPTION_START_RE.match(rest)
                     or (len(rest) <= 12 and re.search(r"[1-4]|\b[A-Da-d]\b", rest))):
        return None                       # "A company ..." is prose, not a part row
    # A bare letter ("a (1) Patters for raring additions", OCR's "Q2 (1) pattern for
    # raising additional finance") needs a short answer after it; a marked label
    # ("(E)(2) increase the financial leverage", "A=3", "B - 1") may carry words.
    marked = bool(match.group(2)) or bool((match.group(4) or "").strip())
    if not marked and len(re.findall(r"[A-Za-z]{2,}", rest)) > 1:
        return None
    return (int(match.group(1)) if match.group(1) else None), match.group(3).lower()


def _inked_band(student_doc, page: int, x0: float, y0: float, x1: float, y1: float) -> bool:
    import pdf_renderer as pr
    mask, scale = pr._page_ink(student_doc, page)
    if mask is None:
        return False
    h, w = mask.shape
    a, b = max(0, int(y0 * scale)), min(h, int(y1 * scale))
    c, d = max(0, int(x0 * scale)), min(w, int(x1 * scale))
    if b - a < 3 or d - c < 3:
        return False
    return float(mask[a:b, c:d].mean()) > 0.005


def _letter_rows_from_ink(student_doc, line: dict, rows: list[dict], parts: dict, page_w: float) -> list[dict]:
    """The lettered rows of one OCR block, placed on the block's own rows of writing.

    The block's box often leaves out the column of part letters, and OCR keeps fewer
    rows than were written (CA FM 8bc28fde: "A=3 / B=1 / C=4 / D=3" for five written
    rows A to E). An even split of the box then drifts a row by part D. The rows of ink,
    read across the letter column too, give each lettered text row its own band, in
    order; one more band right after the last letter is the next part's row when that
    part exists and OCR dropped it. Unlettered text rows (a value OCR split off its
    row) are left out. When the ink does not stack regularly the rows are kept as
    they were."""
    import pdf_renderer as pr
    lettered = [(row, _letter_row(row.get("text") or "")) for row in rows]
    lettered = [(row, read) for row, read in lettered if read is not None]
    if len(lettered) < 2:
        return rows
    letters = [read[1] for _, read in lettered]
    if any(ord(b) - ord(a) != 1 for a, b in zip(letters, letters[1:])):
        return rows
    wide = dict(line, x0=max(0.0, line["x0"] - 0.12 * page_w))
    nxt = chr(ord(letters[-1]) + 1)
    bands = pr._ink_row_bands(student_doc, wide, len(lettered) + 1) if nxt in parts else None
    extra = bands is not None
    if bands is None:
        bands = pr._ink_row_bands(student_doc, wide, len(lettered))
    if bands is None:
        return rows
    out = [dict(row, y0=bands[k][0], y1=bands[k][1], x0=wide["x0"]) for k, (row, _) in enumerate(lettered)]
    if extra:
        last = lettered[-1][0]
        out.append(dict(last, text=f"({nxt.upper()})", y0=bands[-1][0], y1=bands[-1][1], x0=wide["x0"],
                        _array_row=max(int(r.get("_array_row") or 0) for r in rows) + 1))
    return out


def _anchor_letter_parts(report, by_lid: dict, by_page: dict, student_doc, changed: dict) -> dict[int, float]:
    """The lettered MCQ parts of a case study take the rows labelled with their letters.

    CA FM MT01 (21 Oct 2026, five copies): case study Q1 has parts (A) to (E) answered
    with option NUMBERS, written as "A=3", "(A) (3) 336000", "B - 1", "(c) 3 (4)", often
    inside one OCR block. No part had a row of its own, every part sat on the visual
    reader's estimated box, and the boxes ran a row or two off: Q1(B)'s tick on row A,
    the crosses of blank rows C and D on row A, an extra tick under E. Each part moves to
    the one row on its page that opens with its own letter; a part with no such row, or
    with two, stays where it is, except the row OCR dropped right after the last lettered
    row ("E - 2" under "D - 3"), which is taken one row pitch further down when there is
    writing there and no other question's row. CA papers only (report.whole_total)."""
    below: dict[int, float] = {}
    if not bool(getattr(report, "whole_total", False)) or student_doc is None:
        return below
    import pdf_renderer as pr
    from collections import defaultdict
    from mathpix import LineBox
    groups: dict[int, dict[str, object]] = defaultdict(dict)
    for q in report.questions:
        place = _letter_part(str(getattr(q, "qid", "") or ""))
        if place is not None and pr._is_objective_mark(q):
            groups[place[0]][place[1]] = q

    def add_line(page: int, lid: str, text: str, x0, y0, x1, y1, parent=None):
        pg = by_page.get(page)
        if pg is None:
            return None
        doc_page = student_doc[page - 1]
        sx = float(pg.image_width or 1) / max(1.0, doc_page.rect.width)
        sy = float(pg.image_height or 1) / max(1.0, doc_page.rect.height)
        line = LineBox(lid, text, (x0 * sx, y0 * sy, x1 * sx, y1 * sy), page,
                       confidence=getattr(getattr(parent, "get", lambda k: None)("_px"), "confidence", None)
                       if parent else None)
        pg.lines.append(line)
        by_lid[lid] = {"lid": lid, "page": page, "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                       "_px": line}
        return by_lid[lid]

    def move(q, row: dict, how: str) -> None:
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        q.anchor_line_id = row["lid"]
        q.page = row["page"]
        _set_region(report, q, [row])
        report.answer_regions[normalize_qid(q.qid)]["source"] = "confirmed_owned_ocr_location"
        changed.setdefault(q.qid, []).append(f"lettered part {how} {anchor or '-'} -> {row['lid']}")

    for number, parts in groups.items():
        if len(parts) < 2:
            continue
        pages = set()
        for q in parts.values():
            anchor = (getattr(q, "anchor_line_id", "") or "").strip()
            if anchor in by_lid:
                pages.add(by_lid[anchor]["page"])
            elif int(getattr(q, "page", 0) or 0):
                pages.add(int(q.page))
        if len(pages) != 1:
            continue
        page = pages.pop()
        if not 1 <= page <= len(student_doc):
            continue
        page_h = student_doc[page - 1].rect.height
        page_w = student_doc[page - 1].rect.width
        # The visual reader's boxes, taken before any part moves off its box.
        visual = {lid for region in (getattr(report, "answer_regions", None) or {}).values()
                  if isinstance(region, dict) and region.get("source") == "independent_visual_location"
                  for lid in region.get("line_ids") or []}
        rows_by_letter: dict[str, list[dict]] = defaultdict(list)
        for line in list(by_lid.values()):
            if line["page"] != page or "#r" in line["lid"] or line["lid"] in visual:
                continue
            px = line.get("_px")
            if getattr(px, "cancelled", False) or getattr(px, "kind", "text") not in ("text", "math", "table"):
                continue
            text = line.get("text") or ""
            # A block a third of a page tall is a working-notes table, not an answer
            # list (CA FM b9cfb6d3: its split rows were read as parts C to E).
            split = ("\\\\" in text or "\n" in text.strip()) and line["y1"] - line["y0"] <= 0.33 * page_h
            rows = (pr._array_rows(student_doc, line, by_lid) or [line]) if split else [line]
            if split and len(rows) >= 2:
                rows = _letter_rows_from_ink(student_doc, line, rows, parts, page_w)
            for row in rows:
                read = _letter_row(row.get("text") or "")
                if read is None or read[0] not in (None, number):
                    continue
                if row["y1"] - row["y0"] > 0.085 * page_h:
                    continue
                rows_by_letter[read[1]].append(row)
        placed: dict[str, dict] = {}
        for letter, q in sorted(parts.items()):
            found = rows_by_letter.get(letter) or []
            if len(found) != 1:
                continue
            target = found[0]
            if target.get("_array_row") is not None:
                lid = f"{target['lid']}#r{target['_array_row']}"
                if lid not in by_lid:
                    parent = by_lid.get(target["lid"])
                    if parent is None or add_line(page, lid, target["text"], target["x0"], target["y0"],
                                                  target["x1"], target["y1"], parent) is None:
                        continue
                target = by_lid[lid]
            if any(row["lid"] == target["lid"] for row in placed.values()):
                continue
            placed[letter] = target
            if (getattr(q, "anchor_line_id", "") or "").strip() != target["lid"]:
                move(q, target, "row")
        if placed:
            below[page] = max(below.get(page, 0.0), max(row["y1"] for row in placed.values()))
        # The row OCR dropped after the last lettered row: one pitch further down.
        order = sorted(parts)
        found = [letter for letter in order if letter in placed]
        if len(found) < 2:
            continue
        centres = [(placed[a]["y0"] + placed[a]["y1"]) / 2 for a in found]
        steps = sorted(b - a for a, b in zip(centres, centres[1:]) if b > a)
        if not steps:
            continue
        pitch = steps[len(steps) // 2]
        last = found[-1]
        for letter in order[order.index(last) + 1:]:
            if letter in placed:
                break
            prev = placed[order[order.index(letter) - 1]] if order[order.index(letter) - 1] in placed else None
            if prev is None:
                break
            y0, y1 = prev["y0"] + pitch, prev["y1"] + pitch
            x0 = min(placed[k]["x0"] for k in found)
            x1 = max(placed[k]["x1"] for k in found)
            # Another question's own row in that slot stops it. The visual reader's
            # boxes (taken above, before any part moved) and the multi-row block the
            # rows came from are not rows.
            clash = any(l["page"] == page and min(l["y1"], y1) - max(l["y0"], y0) > 0.4 * (y1 - y0)
                        and l["lid"] not in visual and "#r" not in l["lid"]
                        and "\\\\" not in (l.get("text") or "")
                        and (_row_question_number(l.get("text") or "") not in (None, number)
                             or _letter_row(l.get("text") or "") is not None)
                        for l in by_lid.values())
            if clash or not _inked_band(student_doc, page, x0, y0, x1, y1):
                break
            indices = [int(m.group(1)) for l in by_lid if (m := re.fullmatch(rf"P{page}L(\d+)", l))]
            row = add_line(page, f"P{page}L{max(indices, default=0) + 1}", f"({letter.upper()})",
                           x0, y0, x1, y1)
            if row is None:
                break
            placed[letter] = row
            below[page] = max(below.get(page, 0.0), row["y1"])
            move(parts[letter], row, "row one pitch below")
    return below


_LEADING_NUMBER_RE = re.compile(r"^\W{0,3}(?:q\s*)?\(?\s*(\d{1,2})\s*\)?(?=[\s(\-&.:)]|$)", re.I)


def _leading_number(text: str) -> int | None:
    """The question number an MCQ row opens with: "3 (1) 26.25", "3(2)", "(2)-1",
    "4 & 2 & 10.53%", "Q3 (1)". None for a lettered part row."""
    from mathpix import readable_ocr_text
    plain = re.sub(r"\\text\s*\{([^}]*)\}", r"\1", str(text or ""))
    plain = readable_ocr_text(plain).replace("&", " ")
    plain = " ".join(re.sub(r"\\[()\[\]]|\\hline", " ", plain).split())
    if _letter_row(plain) is not None:
        return None
    match = _LEADING_NUMBER_RE.match(plain)
    return int(match.group(1)) if match else None


def _anchor_numbered_rows_below(report, by_lid: dict, by_page: dict, student_doc, changed: dict,
                         below: dict[int, float]) -> None:
    """The numbered MCQs after a lettered case study take their own rows below it.

    CA FM MT01 (21 Oct 2026): after case study Q1 (A)-(E) the students wrote "2 (1)",
    "3 (1) 26.25 (ii) 14%", "4 2 10.53%", or "(2)-1 / (3)-1 / (4)-2" in one block.
    Q4's tick sat on row 3, Q3 and Q4 shared the row "4 (2)", and one copy's Q3 was
    anchored to the Part B heading "3. (B) (1) cost of equity" on another page. On the
    case study's page, below its last lettered row, the one row opening with an MCQ's
    own number is that MCQ's row. Nothing moves when there is no such row or two.
    `below` gives, by page, the bottom of the lettered rows placed there."""
    if not bool(getattr(report, "whole_total", False)) or student_doc is None or not below:
        return
    import pdf_renderer as pr
    from collections import defaultdict
    page = max(below, key=lambda p: below[p])
    floor = below[page]
    page_h = student_doc[page - 1].rect.height
    # The visual reader's boxes hold the written option alone ("1", "3"), not a row.
    visual = {lid for region in (getattr(report, "answer_regions", None) or {}).values()
              if isinstance(region, dict) and region.get("source") == "independent_visual_location"
              for lid in region.get("line_ids") or []}
    # A line that is one option token and nothing else ("2", "(3)") is such a box, or
    # an option without its number: a numbered row carries its number AND an answer.
    visual |= {lid for lid, line in by_lid.items()
               if re.fullmatch(r"\s*\(?\s*[1-4A-Da-d]\s*\)?\s*", line.get("text") or "")}
    rows_by_number: dict[int, list[dict]] = defaultdict(list)
    for line in list(by_lid.values()):
        if line["page"] != page or "#r" in line["lid"] or line["y1"] <= floor or line["lid"] in visual:
            continue
        px = line.get("_px")
        if getattr(px, "cancelled", False) or getattr(px, "kind", "text") not in ("text", "math", "table"):
            continue
        text = line.get("text") or ""
        split = ("\\\\" in text or "\n" in text.strip()) and line["y1"] - line["y0"] <= 0.33 * page_h
        rows = (pr._array_rows(student_doc, line, by_lid) or [line]) if split else [line]
        for row in rows:
            number = _leading_number(row.get("text") or "")
            if number is None or row["y0"] < floor - 2 or row["y1"] - row["y0"] > 0.085 * page_h:
                continue
            rows_by_number[number].append(row)
    for q in report.questions:
        qid = str(getattr(q, "qid", "") or "")
        if (not pr._is_objective_mark(q) or _letter_part(qid) is not None or _case_part(qid) is not None
                or not re.fullmatch(r"\s*q?\s*(\d{1,2})\s*", qid, re.I)):
            continue
        number = int(re.search(r"\d+", qid).group())
        found = rows_by_number.get(number) or []
        if len(found) != 1:
            continue
        target = found[0]
        lid = (f"{target['lid']}#r{target['_array_row']}" if target.get("_array_row") is not None
               else target["lid"])
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        if anchor == lid:
            continue
        current = by_lid.get(anchor)
        if current is not None and current["page"] == page and _leading_number(current.get("text") or "") == number:
            continue                      # already on a row of its own number
        if lid not in by_lid:
            pg = by_page.get(page)
            parent = by_lid.get(target["lid"])
            if pg is None or parent is None:
                continue
            from mathpix import LineBox
            doc_page = student_doc[page - 1]
            sx = float(pg.image_width or 1) / max(1.0, doc_page.rect.width)
            sy = float(pg.image_height or 1) / max(1.0, doc_page.rect.height)
            line = LineBox(lid, target["text"], (target["x0"] * sx, target["y0"] * sy,
                                                 target["x1"] * sx, target["y1"] * sy), page,
                           confidence=getattr(parent["_px"], "confidence", None))
            pg.lines.append(line)
            by_lid[lid] = {**target, "lid": lid, "_px": line}
        q.anchor_line_id = lid
        q.page = page
        _set_region(report, q, [by_lid[lid]])
        report.answer_regions[normalize_qid(q.qid)]["source"] = "confirmed_owned_ocr_location"
        changed.setdefault(q.qid, []).append(f"numbered MCQ row {anchor or '-'} -> {lid}")


def _row_after_neighbour(q, current: dict, number: int, objective, numbers: dict, by_lid: dict,
                         visual_lids: set, foreign: set, taken: set, page_h: float) -> list:
    """The row next to the question numbered one before (or after) this one, when the
    visual reader's box for this question sits on that question's own row.

    MEASURED 2026-10-14 (Vidyapeeth Class 8 Maths, TEJASWINI): Q10's box lay on Q9's
    row "g9) (a)", and Q10's own row read "g104. (a)" ("Q10>" as "g104."), so no
    number found it. Both verdicts were drawn on Q9's row, the wrong one with a tick.
    The next row below Q9 that names an option and belongs to nobody is Q10's."""
    from mathpix import readable_ocr_text
    out = []
    for other in objective:
        if other is q or numbers.get(other.qid) not in (number - 1, number + 1):
            continue
        row = by_lid.get((getattr(other, "anchor_line_id", "") or "").strip())
        if row is None or row["lid"] in visual_lids or row["page"] != current["page"]:
            continue
        overlap = min(row["y1"], current["y1"]) - max(row["y0"], current["y0"])
        if overlap < .5 * min(row["y1"] - row["y0"], current["y1"] - current["y0"]):
            continue
        below = numbers[other.qid] == number - 1
        near = []
        for line in by_lid.values():
            if (line["page"] != row["page"] or line["lid"] in (row["lid"], current["lid"])
                    or "#r" in line["lid"] or line["lid"] in visual_lids
                    or line["lid"] in foreign or line["lid"] in taken
                    or min(line["x1"], row["x1"]) - max(line["x0"], row["x0"]) <= 0
                    or not _OPTION_LINE_RE.search(readable_ocr_text(line.get("text") or ""))):
                continue
            gap = line["y0"] - row["y1"] if below else row["y0"] - line["y1"]
            if -3 <= gap <= .10 * page_h:
                near.append((gap, line))
        near.sort(key=lambda item: item[0])
        if near and (len(near) == 1 or near[1][0] - near[0][0] >= 1.0):
            out.append(near[0])
    return out if len(out) == 1 else []


def _lines_of_other_questions(report, by_lid: dict, objective_numbers: set[int]) -> set[str]:
    """Lines that belong to a NON-objective question, which an MCQ may never take.

    MEASURED 2026-10-01 (Radiant 2.0 Class 9 SST Test 02, Section A = MCQs Q1-Q5 then
    the fill-in Q6 (a)-(e)): Q6's five short answer lines ("a) Kalibangan", "b) Great
    Bath", "c) third", ...) were the longest run of short option-looking lines on the
    page, so the MCQ block below took them, and Aradhya's Q1, Q3 and Q4 were moved off
    "Q1) (A) Fortified citadel...", "Q3) (B) It is flattened..." onto "a) Kalibangan",
    "c) third", "a) 26 Nov 1949". Preflight then held the copy. Same on Agrata, Avika,
    Aryan, Isha ("6. a> Kalibanga" took Q1) and Digeshwar ("(c) third" took Q3).
    A line is another question's when that question cites it (anchor, step or label),
    when it opens with that question's number, or when it is a lettered sub-part
    under a heading that opened with that question's number ("Q 6" then "a) ...").
    """
    foreign: set[str] = set()
    other_numbers: set[int] = set()
    subpart_letters: set[str] = set()
    import pdf_renderer as pr
    for q in report.questions:
        if pr._is_objective_mark(q):
            continue
        number = pr._objective_lane_key(str(q.qid))[0]
        if number < 9999 and number not in objective_numbers:
            other_numbers.add(number)
        tail = re.search(r"[.(]\s*([a-h])\s*\)?$", str(q.qid or ""), re.I)
        if tail:
            subpart_letters.add(tail.group(1).lower())
        if not getattr(q, "attempted", True):
            continue                     # an unattempted row's anchor is only nominal
        foreign.add((getattr(q, "anchor_line_id", "") or "").strip())
        for criterion in (getattr(q, "criteria", None) or []):
            foreign.add((getattr(criterion, "step_line_id", "") or "").strip())
        region = (getattr(report, "answer_regions", None) or {}).get(normalize_qid(q.qid), {})
        foreign.update(str(lid) for lid in (region.get("label_line_ids") or []))
    for pno in sorted({line["page"] for line in by_lid.values()}):
        heading: int | None = None
        for line in sorted((l for l in by_lid.values() if l["page"] == pno and "#r" not in l["lid"]),
                           key=lambda l: (l["y0"], l["x0"])):
            text = line.get("text") or ""
            number = _opening_number(text)
            if number is not None:
                heading = number
                if number in other_numbers:
                    foreign.add(line["lid"])
                continue
            sub = _SUBPART_OPEN_RE.match(text.strip())
            if (sub and heading in other_numbers and sub.group(1).lower() in subpart_letters
                    and re.search(r"[A-Za-z]{3,}|\d{2,}", text[sub.end():])):
                foreign.add(line["lid"])
    foreign.discard("")
    return foreign


def _is_option_line(line: dict, page_w: float) -> bool:
    """A short line carrying an option letter: 'Q1 B', '2 c Tamil', '5.c 25 years'."""
    from mathpix import readable_ocr_text
    text = readable_ocr_text(line.get("text") or "")
    if not text or len(text.split()) > 6:
        return False
    if line["x1"] - line["x0"] > 0.6 * page_w:
        return False
    return bool(_OPTION_LINE_RE.search(text))


def _objective_home_page(objective, by_lid: dict, exclude) -> int | None:
    """The page holding most of the OTHER objective anchors (two or more, a clear lead)."""
    counts: dict[int, int] = {}
    for q in objective:
        line = by_lid.get((getattr(q, "anchor_line_id", "") or "").strip())
        if q is not exclude and line is not None:
            counts[line["page"]] = counts.get(line["page"], 0) + 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[1][1] == ranked[0][1]):
        return None
    return ranked[0][0]


def _anchor_own_numbered_lines(report, objective, numbers: dict, foreign: set,
                               by_lid: dict, changed: dict) -> None:
    """An MCQ anchored on another question's line moves to its OWN numbered line.

    Only when the current anchor is missing or is another question's line (foreign),
    and only onto the one line of that page that opens with this question's number,
    carries an answer after it, and is nobody else's. A line opening with a different
    number is left alone: OCR misreads the student's number ("Q 7) (A) Suitable
    distance from the sun" is Aradhya's Q4), so a number alone never moves a mark.

    An anchor that carries no option letter, on a page away from where the other
    MCQs are anchored, is stray too, and moves onto that page's one line opening with
    this number AND an option letter. MEASURED 2026-09-20 (Chemistry, Mohammed
    Hamza): Q2 sat on "2. Formula of (1)", a point of Q21 on page 8, while "2.) C)
    Both A and R are true" stood among Q1, Q3, Q4, Q5 on page 1.
    """
    from mathpix import readable_ocr_text
    taken = {(getattr(q, "anchor_line_id", "") or "").strip() for q in objective}
    for q in objective:
        number = numbers[q.qid]
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        current = by_lid.get(anchor)
        home = _objective_home_page(objective, by_lid, q)
        stray = (current is not None and home is not None and current["page"] != home
                 and not _OPTION_LINE_RE.search(readable_ocr_text(current.get("text") or "")))
        if current is not None and anchor not in foreign and not stray:
            continue
        page = home if stray else (current["page"] if current is not None
                                   else int(getattr(q, "page", 0) or 0))
        own = [line for line in by_lid.values()
               if "#r" not in line["lid"] and line["lid"] not in foreign
               and (not page or line["page"] == page)
               and _opening_number(line.get("text") or "") == number
               and (not stray or _OPTION_LINE_RE.search(readable_ocr_text(line.get("text") or "")))]
        if len(own) != 1 or own[0]["lid"] in taken:
            continue
        target = own[0]
        body = _OPENING_NUMBER_RE.sub("", readable_ocr_text(target.get("text") or ""), count=1)
        if not re.search(r"[A-Za-z]", body):
            continue                     # "4." alone: the number, no answer after it
        q.anchor_line_id = target["lid"]
        q.page = target["page"]
        taken.add(target["lid"])
        _set_region(report, q, [target])
        changed.setdefault(q.qid, []).append(f"own numbered line {anchor or '-'} -> {target['lid']}")


def _mcq_block(by_lid: dict, student_doc, exclude: frozenset | set = frozenset()) -> list[dict]:
    """The longest run of consecutive short option lines on any page (3 or more).

    A line in ``exclude`` (another question's, _lines_of_other_questions) ends a run.
    """
    best: list[dict] = []
    pages = sorted({line["page"] for line in by_lid.values()})
    for pno in pages:
        page_w = student_doc[pno - 1].rect.width if 1 <= pno <= len(student_doc) else 595.0
        ordered = sorted((line for line in by_lid.values() if line["page"] == pno),
                         key=lambda line: (line["y0"], line["x0"]))
        run: list[dict] = []
        for line in ordered:
            if "#r" in line["lid"]:
                continue
            if line["lid"] not in exclude and _is_option_line(line, page_w):
                run.append(line)
            else:
                if len(run) > len(best):
                    best = run
                run = []
        if len(run) > len(best):
            best = run
    return best if len(best) >= 3 else []


def _anchor_mcq_block(report, by_lid, student_doc, changed) -> None:
    """Objective questions anchored off the MCQ block are re-anchored onto it.

    The map matched numbered points of descriptive answers to MCQ numbers on two
    live copies ("(1) Silappadikaran" for Q1, "(4) Obedience to parents" for Q4),
    and the real answers "1 B Indika", "Q4 C" drew nothing. The block's lines take
    the questions by their leading number; a line whose number OCR garbled takes
    the next unassigned question in order. A question keeps its anchor when that
    anchor is already a line of the block; it is moved when the anchor is on
    another page, is a long line, is cited by a descriptive answer, or is missing.

    MEASURED 2026-10-01 (Radiant 2.0 SST Test 02): the block must never be built
    from another question's lines (Q6's "a) Kalibangan" fill-ins), and an anchor on
    the question's own numbered line ("Q1) (A) Fortified citadel and lower town")
    stays, however long; see _lines_of_other_questions.
    """
    import pdf_renderer as pr
    objective = [q for q in report.questions
                 if pr._is_objective_mark(q) and getattr(q, "attempted", True)
                 and not _independently_located_objective(report, q)
                 and _case_part(str(q.qid)) is None]    # parts share their number
    if len(objective) < 3:
        return
    numbers = {q.qid: pr._objective_item_number(str(q.qid)) for q in objective}
    foreign = _lines_of_other_questions(report, by_lid, set(numbers.values()))
    _anchor_own_numbered_lines(report, objective, numbers, foreign, by_lid, changed)
    block = _mcq_block(by_lid, student_doc, exclude=foreign)
    if len(block) < len(objective) - 1:
        return
    by_number: dict[int, dict] = {}
    unnumbered: list[dict] = []
    for line in block:
        number = _label_number(line.get("text") or "")
        if number is not None and number in numbers.values() and number not in by_number:
            by_number[number] = line
        elif number is not None and number not in numbers.values():
            continue                     # "6. a> Kalibanga": another question's number
        else:
            unnumbered.append(line)
    # Lines OCR could not number take the questions still unassigned, in order.
    for q in sorted(objective, key=lambda q: numbers[q.qid]):
        if numbers[q.qid] not in by_number and unnumbered:
            candidate = unnumbered[0]
            # Only a line that sits where this question's neighbours are.
            neighbours = [by_number[n] for n in by_number if abs(n - numbers[q.qid]) == 1]
            if neighbours and all(abs(candidate["y0"] - n["y0"]) > 3 * abs(n["y1"] - n["y0"]) for n in neighbours):
                continue
            by_number[numbers[q.qid]] = unnumbered.pop(0)
    cited_by_descriptive: set[str] = set()
    for q in report.questions:
        if pr._is_objective_mark(q):
            continue
        for criterion in (getattr(q, "criteria", None) or []):
            cited_by_descriptive.add((getattr(criterion, "step_line_id", "") or "").strip())
    block_lids = {line["lid"] for line in block}
    block_page = block[0]["page"]
    for q in objective:
        target = by_number.get(numbers[q.qid])
        if target is None:
            continue
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        if anchor in block_lids:
            continue
        current = by_lid.get(anchor)
        if (current is not None and anchor not in foreign
                and _opening_number(current.get("text") or "") == numbers[q.qid]):
            # The question's own numbered line ("Q1) (A) Fortified citadel and lower
            # town", seven words) is its answer, however long the student wrote it.
            continue
        stale = (current is None or current["page"] != block_page
                 or len((current.get("text") or "").split()) > 6
                 or anchor in cited_by_descriptive)
        if not stale:
            continue
        q.anchor_line_id = target["lid"]
        q.page = target["page"]
        _set_region(report, q, [target])
        changed.setdefault(q.qid, []).append(f"mcq block {anchor or '-'} -> {target['lid']}")


def _untangle_shared_rows(report, by_lid: dict, student_doc, changed: dict) -> None:
    """Two MCQs anchored on one row: each that is not that row's own moves to its own.

    MEASURED 2026-10-10 (CA Inter Corporate Law MTS 02, sheet rows F40 and F41, "MCQs
    tick marking is misplaced"): rows 2, 4 and 7 of the MCQ page carried no mark, row 5
    carried two ticks, and the margin listed Q7 between Q4 and Q5. OCR had not read the
    faint "4)" and "7)", so nothing moved them off Q5's row: the block pass keeps any
    anchor already in the block, and the shared-row check only reports it.

    The question whose number the row opens with keeps it. Another moves only to the
    one free row opening with its own number, else to the one free option row below
    the row its previous number is anchored on and above the next anchored row of a
    higher number. A row is free when no question anchors on it and no descriptive
    answer cites it. When that is not one row, nothing moves.
    """
    import pdf_renderer as pr
    objective = [q for q in report.questions
                 if pr._is_objective_mark(q) and getattr(q, "attempted", True)
                 and _case_part(str(q.qid)) is None]
    if len(objective) < 2:
        return

    def anchor_of(q) -> str:
        return (getattr(q, "anchor_line_id", "") or "").strip()

    def centre(line: dict) -> float:
        return (line["y0"] + line["y1"]) / 2

    numbers = {q.qid: pr._objective_item_number(str(q.qid)) for q in objective}
    sharing: dict[str, list] = {}
    for q in objective:
        if anchor_of(q) in by_lid:
            sharing.setdefault(anchor_of(q), []).append(q)
    if all(len(qs) < 2 for qs in sharing.values()):
        return
    foreign = _lines_of_other_questions(report, by_lid, set(numbers.values()))
    taken = {anchor_of(q) for q in report.questions} | foreign
    for lid, qs in sharing.items():
        if len(qs) < 2:
            continue
        row = by_lid[lid]
        page = row["page"]
        page_w = student_doc[page - 1].rect.width if 1 <= page <= len(student_doc) else 595.0
        row_number = _opening_number(row.get("text") or "")
        located = [q for q in qs if _independently_located_objective(report, q)]
        keeper = next((q for q in qs if numbers[q.qid] == row_number), None) or (
            located[0] if len(located) == 1 else None)
        staying = len(qs)
        for q in sorted(qs, key=lambda q: numbers[q.qid]):
            if q is keeper or any(q is l for l in located) or (keeper is None and staying == 1):
                continue
            number = numbers[q.qid]
            free = [line for line in by_lid.values()
                    if line["page"] == page and "#r" not in line["lid"] and line["lid"] not in taken]
            own = [line for line in free if _opening_number(line.get("text") or "") == number]
            target = own[0] if len(own) == 1 else None
            if target is None and not own:
                anchored = {numbers[o.qid]: by_lid[anchor_of(o)] for o in objective
                            if o is not q and anchor_of(o) in by_lid
                            and by_lid[anchor_of(o)]["page"] == page}
                before = anchored.get(number - 1)
                if before is not None and (before["lid"] != lid or keeper is not None):
                    centres = sorted({centre(line) for line in anchored.values()})
                    gaps = sorted(b - a for a, b in zip(centres, centres[1:]) if b - a > 4)
                    pitch = gaps[len(gaps) // 2] if gaps else 40.0
                    later = [centre(line) for n, line in anchored.items()
                             if n > number and centre(line) > centre(before)]
                    upper = min(later, default=centre(before) + 2.5 * pitch)
                    between = [line for line in free
                               if centre(before) < centre(line) < upper
                               and _is_option_line(line, page_w)
                               and _opening_number(line.get("text") or "") is None]
                    target = between[0] if len(between) == 1 else None
            if target is None:
                continue
            q.anchor_line_id = target["lid"]
            q.page = target["page"]
            taken.add(target["lid"])
            staying -= 1
            _set_region(report, q, [target])
            changed.setdefault(q.qid, []).append(f"shared row {lid} -> {target['lid']}")


def _lines_in_points(report, ocr_pages, student_doc) -> dict[str, dict]:
    by_lid: dict[str, dict] = {}
    for pg in ocr_pages:
        pno = int(getattr(pg, "page", 0) or 0)
        if not 1 <= pno <= len(student_doc):
            continue
        page = student_doc[pno - 1]
        sx = page.rect.width / max(1.0, float(pg.image_width or 1))
        sy = page.rect.height / max(1.0, float(pg.image_height or 1))
        for ln in pg.lines:
            x0, y0, x1, y1 = ln.bbox
            by_lid[ln.line_id] = {
                "lid": ln.line_id, "page": pno, "text": ln.text or "",
                "x0": x0 * sx, "y0": y0 * sy, "x1": x1 * sx, "y1": y1 * sy, "_px": ln,
            }
    return by_lid


def _set_region(report, q, lines: list[dict]) -> None:
    key = normalize_qid(q.qid)
    region = report.answer_regions.setdefault(key, {"qid": q.qid, "status": "inferred", "confidence": 0.0})
    lids = [line["lid"] for line in lines]
    region["line_ids"] = list(lids)
    region["evidence_line_ids"] = list(lids)
    region["label_line_ids"] = [lid for lid in region.get("label_line_ids", []) if lid in lids]
    region["pages"] = sorted({line["page"] for line in lines})
    region["regions"] = []
    for pno in region["pages"]:
        rows = [line for line in lines if line["page"] == pno]
        boxes = [getattr(line["_px"], "bbox", None) for line in rows]
        boxes = [b for b in boxes if b]
        region["regions"].append({
            "page": pno, "line_ids": [line["lid"] for line in rows],
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else [],
        })
    region["repaired"] = True


def repair_report_evidence(report, ocr_pages, student_doc) -> dict[str, list[str]]:
    """Rewrite regions, anchors and step lines in place. Returns what changed, by qid."""
    import pdf_renderer as pr
    from anchors import resolve_steps
    from grading_quality import answer_line_ids, answer_mark_pages

    changed: dict[str, list[str]] = {}
    if not getattr(report, "evidence_contract", False):
        return changed
    pages = list(ocr_pages or [])
    if not pages or student_doc is None or len(student_doc) == 0:
        return changed
    if not isinstance(getattr(report, "answer_regions", None), dict):
        report.answer_regions = {}
    by_lid = _lines_in_points(report, ocr_pages, student_doc)
    by_page = {pg.page: pg for pg in pages}

    # 0. An MCQ placed by the visual reader's estimated box goes to its own row.
    _numbered_rows_over_visual_boxes(report, by_lid, by_page, student_doc, changed)
    # 0b. The MCQ parts of a case study take their question's rows in order.
    _anchor_case_parts_in_order(report, by_lid, changed)
    # 0c. CA case studies lettered (A) to (E), and the numbered MCQs after them.
    below = _anchor_letter_parts(report, by_lid, by_page, student_doc, changed)
    _anchor_numbered_rows_below(report, by_lid, by_page, student_doc, changed, below)

    # 1. A merged MCQ block: one synthetic OCR line per numbered row.
    for q in report.questions:
        if not getattr(q, "attempted", True) or not pr._is_objective_mark(q):
            continue
        if _case_part(str(q.qid)) is not None:
            continue                                   # a part is not its number's row
        if _independently_located_objective(report, q):
            continue
        own = by_lid.get((getattr(q, "anchor_line_id", "") or "").strip())
        if own is not None and "#r" in own["lid"]:
            continue                                   # already repaired
        try:
            row = pr._objective_row(student_doc, q, own, by_lid)
        except Exception:  # noqa: BLE001 - a raster that cannot be built
            row = None
        if row is None or row.get("_array_row") is None:
            continue
        parent = by_lid.get(row["lid"])
        if parent is None:
            continue
        new_lid = f"{row['lid']}#r{row['_array_row']}"
        if new_lid not in by_lid:
            pg = by_page.get(row["page"])
            page = student_doc[row["page"] - 1]
            if pg is None:
                continue
            sx = float(pg.image_width or 1) / max(1.0, page.rect.width)
            sy = float(pg.image_height or 1) / max(1.0, page.rect.height)
            box = (row["x0"] * sx, row["y0"] * sy, row["x1"] * sx, row["y1"] * sy)
            line = LineBox(new_lid, row["text"], box, row["page"],
                           confidence=getattr(parent["_px"], "confidence", None))
            pg.lines.append(line)
            by_lid[new_lid] = {**row, "lid": new_lid, "_px": line}
        q.anchor_line_id = new_lid
        q.page = row["page"]
        _set_region(report, q, [by_lid[new_lid]])
        changed.setdefault(q.qid, []).append(f"mcq row {new_lid}")

    # 1b. Objective questions the map anchored off the MCQ block.
    _anchor_mcq_block(report, by_lid, student_doc, changed)
    # 1c. Two objective questions on one row: the one that is not its own moves.
    _untangle_shared_rows(report, by_lid, student_doc, changed)

    # 2. Regions and anchors from the grader's citations.
    for q in report.questions:
        if not getattr(q, "attempted", True):
            continue
        key = normalize_qid(q.qid)
        region = report.answer_regions.get(key, {})
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        if not (getattr(q, "criteria", None) or []):
            # An MCQ or a one-line answer with no step evidence: the grader's own
            # anchor line IS the answer, whatever rows the map gathered around it.
            if anchor in by_lid and set(region.get("line_ids", [])) != {anchor}:
                _set_region(report, q, [by_lid[anchor]])
                q.page = by_lid[anchor]["page"]
                changed.setdefault(q.qid, []).append(f"region -> anchor {anchor}")
            elif anchor in by_lid and int(getattr(q, "page", 0) or 0) != by_lid[anchor]["page"]:
                # The row's page is its anchor's page: Shweta's Q3 kept the grader's
                # "page 1" while its anchor, P4L11, is on page 4.
                q.page = by_lid[anchor]["page"]
                changed.setdefault(q.qid, []).append(f"page -> {q.page}")
            continue
        pages_ok = answer_mark_pages(report, q)
        lines = [by_lid[lid] for lid in answer_line_ids(report, q)
                 if lid in by_lid and by_lid[lid]["page"] in pages_ok]
        trimmed = pr._lines_the_grader_read(report, q, lines, by_lid)
        if not trimmed:
            continue
        trimmed_lids = [line["lid"] for line in trimmed]
        if set(trimmed_lids) != set(region.get("line_ids", [])):
            _set_region(report, q, trimmed)
            changed.setdefault(q.qid, []).append(
                f"region {len(region.get('line_ids', []))} -> {len(trimmed_lids)} lines")
        anchor = (getattr(q, "anchor_line_id", "") or "").strip()
        if anchor not in trimmed_lids:
            body = [line for line in trimmed if not pr._is_header(line.get("text") or "")] or trimmed
            q.anchor_line_id = body[-1]["lid"]
            q.page = body[-1]["page"]
            changed.setdefault(q.qid, []).append(f"anchor {anchor or '-'} -> {q.anchor_line_id}")
        elif int(getattr(q, "page", 0) or 0) != by_lid[anchor]["page"]:
            q.page = by_lid[anchor]["page"]
            changed.setdefault(q.qid, []).append(f"page -> {q.page}")

        # 3. Step lines resolved on the trimmed region.
        criteria = list(getattr(q, "criteria", None) or [])
        if not criteria:
            continue
        band = set(trimmed_lids)

        def anchor_ok(lid, criterion, band=band):
            pl = by_lid.get(lid)
            return (pl is not None and lid in band
                    and not pr._is_header(pl.get("text") or "")
                    and not pr._is_cancelled_line(pl))

        verified_anchors = getattr(report, "verified_step_anchors", None) or {}
        verified = [i for i, c in enumerate(criteria)
                    if verified_anchors.get(f"{key}:{i}") == [c.step_line_id, c.anchor_quote]]
        resolutions = resolve_steps(criteria, trimmed, model_anchor_ok=anchor_ok,
                                    require_evidence=True, verified_indices=verified)
        for index, (criterion, res) in enumerate(zip(criteria, resolutions)):
            if not res.placed or not res.line_id:
                continue
            if index in verified:
                continue
            if res.line_id != (criterion.step_line_id or "").strip():
                changed.setdefault(q.qid, []).append(
                    f"step {index} {criterion.step_line_id or '-'} -> {res.line_id}")
                criterion.step_line_id = res.line_id

    # The objective-row raster went through pdf_renderer's per-document ink cache,
    # keyed by id(); drop it so a later document reusing the id cannot inherit it.
    pr._MARK_INK.pop(id(student_doc), None)
    return changed
