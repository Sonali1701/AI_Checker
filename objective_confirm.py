"""One close reading of an MCQ answer row the release gate could not confirm.

The two key-blind readers see whole pages with every row on them. When they
disagree, when the OCR put the option on a line with no question number, or when
the verdict is anchored to a line that does not start with the question's number,
the copy is held for a faculty reviewer. MEASURED 2026-10-12 on the Parishram
Humanities batch: 21 of the 70 held copies were held for an unconfirmed MCQ option
and 8 for a mark on the wrong line, while on the 110 MCQs with the faculty's own
marks the tool was right on 106. Most of these holds were reading shortfalls, not
wrong marks.

This pass cuts the one row in question out of the page image, with its question
number, and asks a third model what option is written there. Agreement with the
option already scored confirms the row; a confirmed row whose verdict sat on the
wrong line is re-anchored to the numbered row the reading was taken from. Anything
else leaves the hold in place. Nothing here raises, lowers or changes a mark: a
row is confirmed or it is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from exam_package import normalize_qid

# Neither of the key-blind readers (the Haiku reader and the Sonnet tie-break) and
# not the grader: a crop of one row costs a few hundred tokens, so the strongest
# model is affordable here (about Rs 0.8 a row).
CONFIRM_MODEL = "claude-opus-4-8"
# The second close reader, for a row the first close read could not confirm. Two
# close reads that agree are the tool's own standard for transcribing an MCQ
# choice (two independent readers, the key scores it in code). MEASURED 2026-10-12
# on CA: the close read disagreed with the record on 5 rows and was right on the
# two checked by eye (Auditing Q15 "(4) Notes reflecting preliminary thinking"
# scored A, so 0 for a right answer; CMA Q5 "2) 145500" credited as A).
SECOND_MODEL = "claude-sonnet-4-6"
# A copy with more unconfirmed rows than this has a reading problem the gate should
# keep, not a few rows to settle.
MAX_ROWS = 8
# A single-option MCQ carries 1 or 2 marks. Political Science Infinity Test 10 Q13
# (12 Oct 2026): a 4-mark source-based question with four sub-parts reached here as
# one "objective" row; two close reads of one strip cannot settle four answers, and
# a 0 of 4 released as confirmed would cost the student a faculty look.
MAX_MARKS = 2.0

_BARE_DETAIL_RE = re.compile(r'(\S+) \("')
_ANCHOR_DETAIL_RE = re.compile(r"^(\S+) is anchored to ")
_NUMBER_RE = re.compile(r"^\s*q?\s*(\d{1,3})(?!\d)", re.IGNORECASE)
_LABEL_RE = re.compile(r"^\s*q?\s*(\d{1,3})(?:[.\s(]+([a-z]{1,4}))?", re.IGNORECASE)


class RowReading(BaseModel):
    """What one model saw on the strip around a single answer row."""
    question_number_visible: bool = False
    chosen_option: Literal["", "A", "B", "C", "D"] = ""
    written_answer: str = ""
    selection_state: Literal["single_active", "replacement", "multiple_active", "unclear"] = "unclear"
    clear: bool = False


@dataclass
class ConfirmResult:
    notes: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    reanchored: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    cost_rows: int = 0


def _number(qid: str) -> int | None:
    match = _NUMBER_RE.match(str(qid or ""))
    return int(match.group(1)) if match else None


def row_label(qid: str) -> tuple[int | None, str]:
    """(question number, part letter) of an MCQ qid: "Q1.C" -> (1, "c"), "Q7" -> (7, "")."""
    match = _LABEL_RE.match(str(qid or ""))
    if not match:
        return None, ""
    return int(match.group(1)), (match.group(2) or "").lower()


def _label_of(text: str) -> int | None:
    """The question number an OCR line starts with (preflight's own rule)."""
    from preflight import _ANSWER_LINE_RE
    found = _ANSWER_LINE_RE.match(re.sub(r"\\[()\[\]]", " ", text or ""))
    return int(found.group(1)) if found else None


def _lines(ocr_pages) -> dict[str, object]:
    return {str(getattr(line, "line_id", "") or ""): line
            for page in (ocr_pages or []) for line in (getattr(page, "lines", None) or [])}


def _page_of(ocr_pages, number: int):
    for page in ocr_pages or []:
        if int(getattr(page, "page", 0) or 0) == number:
            return page
    return None


def rows_to_confirm(report, ocr_pages, *, answer_map=None, paper_text: str = "") -> dict[str, set[str]]:
    """The MCQ rows the release gate would hold, with why: unconfirmed, bare, anchor."""
    from preflight import _check_bare_objective, _check_objective_anchor_ownership
    by_qid = {normalize_qid(q.qid): q for q in (getattr(report, "questions", None) or [])
              if bool(getattr(q, "objective", False)) and getattr(q, "attempted", True) is not False}
    out: dict[str, set[str]] = {}

    def add(qid, why):
        key = normalize_qid(qid)
        if key in by_qid:
            out.setdefault(key, set()).add(why)

    for qid in getattr(report, "objective_unconfirmed_qids", None) or []:
        add(qid, "unconfirmed")
    for issue in _check_bare_objective(report, ocr_pages):
        for qid in _BARE_DETAIL_RE.findall(str(getattr(issue, "detail", "") or "")):
            add(qid, "bare")
    for issue in _check_objective_anchor_ownership(report, ocr_pages, answer_map=answer_map,
                                                   paper_text=paper_text):
        if getattr(issue, "code", "") != "objective-anchor-mismatch":
            continue
        match = _ANCHOR_DETAIL_RE.match(str(getattr(issue, "detail", "") or ""))
        if match:
            add(match.group(1), "anchor")
    return out


def row_strip(png: bytes, ocr_page, bbox, y_fraction: float) -> tuple[bytes, float]:
    """The strip of the page image around one answer row, full width so the question
    number in the margin is on it, and the strip's centre as a fraction of the page."""
    image = Image.open(BytesIO(png))
    image.load()
    width, height = image.size
    if bbox and ocr_page is not None and int(getattr(ocr_page, "image_height", 0) or 0) > 0:
        scale = height / float(ocr_page.image_height)
        y0, y1 = float(bbox[1]) * scale, float(bbox[3]) * scale
        line_h = max(y1 - y0, 0.02 * height)
        top, bottom = y0 - 1.5 * line_h, y1 + 1.5 * line_h
    else:
        centre = min(max(float(y_fraction or 0.5), 0.0), 1.0) * height
        top, bottom = centre - 0.07 * height, centre + 0.07 * height
    top, bottom = max(0, int(top)), min(height, int(bottom))
    if bottom - top < int(0.05 * height):
        middle = (top + bottom) // 2
        top, bottom = max(0, middle - int(0.025 * height)), min(height, middle + int(0.025 * height))
    strip = image.crop((0, top, width, bottom))
    if strip.width > 1200:
        strip = strip.resize((1200, max(1, int(strip.height * 1200 / strip.width))))
    out = BytesIO()
    strip.save(out, format="PNG")
    return out.getvalue(), (top + bottom) / 2.0 / height


_QUESTION_HEAD = r"(?:Q\.?|Que\.?|Question)\s*{n}\s*[.):]"


def question_block(paper_text: str, number: int | None, limit: int = 1200) -> str:
    """The printed question and its options, cut from the question paper's text.

    Statement-combination MCQs ("Which of the statements are correct? (A) 1, 2 and 4
    only") are answered in words, and the option parser misses some of them; the
    printed question lets the reader turn those words into the letter. This is the
    question paper, never the key."""
    if not paper_text or number is None:
        return ""
    start = re.search(_QUESTION_HEAD.format(n=number), paper_text)
    if not start:
        return ""
    end = re.search(_QUESTION_HEAD.format(n=number + 1), paper_text[start.start():])
    block = paper_text[start.start(): start.start() + (end.start() if end else limit)]
    block = re.sub(r"=+\s*PAGE\s*\d+\s*=+", " ", block)
    return re.sub(r"[ \t]+", " ", block).strip()[:limit]


def read_row(strip: bytes, qid: str, number: int | None, options: dict[str, str] | None, *,
             model: str, google_token: str, task_id: str, filename: str = "",
             usage_out: dict | None = None, question_text: str = "") -> RowReading:
    """One key-blind reading of the strip. Raises on any provider or parse failure."""
    from costs import record_usage_event
    from grader import (_claude_call, _claude_text, _claude_usage, _image_block,
                        _loads_model_json, _strict_json_schema, objective_options_block)
    _, part = row_label(qid)
    label = (f"part ({part}) of question {number}" if part and number is not None
             else f"question {number}" if number is not None else qid)
    # CMA 548a000c (12 Oct 2026): "c) 2) 50 units" is part (c) answered with option 2;
    # without this the reader took the part letter for the option.
    part_rule = (f" The row may start with the part label '({part})' or '{part})', which is "
                 "NOT the option; the chosen option follows it as a letter A to D or a "
                 "number 1 to 4." if part else "")
    # CA FM MT01 (21 Oct 2026): "A=3" (part A, option 3) was read as option A, and the
    # Part B heading "3. (B) (1) cost of equity" as option B of MCQ 3.
    part_rule += (" A letter written just before a number ('A=3', '(A)(3)', 'B) 1') is a part "
                  "label followed by the chosen option number: report that number as its letter. "
                  "A sub-part heading of a long answer such as '3(B)' or '3. (B)' is not an MCQ "
                  "answer; set clear=false when that is all the strip shows.")
    seen_rule = (f"the part label ({part}) or the number {number}" if part
                 else f"the number {number if number is not None else ''}")
    prompt = (
        f"This strip is cut from a handwritten answer sheet around the row where the "
        f"answer to {label} should be. Read the option the student finally selected for "
        f"{label}.{part_rule} chosen_option is the letter they wrote (A to D; report 1 to 4 "
        "as A to D), the option they circled or ticked, or, when they wrote an option's own "
        "words or statement numbers instead of a letter, the letter of the printed option "
        "those words match; leave it empty when no printed option matches what is written. "
        "Copy the live answer as written into written_answer. Do not guess and do not use "
        "subject knowledge: you are not told the right answer. Set clear=false when the "
        "row is not readable or shows no answer. question_number_visible is true only when "
        f"{seen_rule} itself (with or without 'Q', 'Ans', a bracket or a dot) is visible on "
        "this strip beside that answer. If an earlier answer is struck out and replaced, "
        "report only the live one and set selection_state=replacement. If two uncancelled "
        "answers compete, set selection_state=multiple_active and clear=false."
        + (objective_options_block({qid: options}) if options else "")
        + (f"\n\nThe printed question and its options, from the question paper:\n{question_text}"
           if question_text else "")
    )
    response = _claude_call(
        google_token, task_id=task_id, model=model,
        request={
            "max_tokens": 300,
            "system": "You are an independent visual transcription examiner.",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      _image_block(strip)]}],
            "output_config": {"format": {"type": "json_schema",
                                         "schema": _strict_json_schema(RowReading.model_json_schema())}},
        },
        filename=filename, count=1)
    record_usage_event(usage_out, _claude_usage(response, model), stage="objective_confirm")
    raw = _loads_model_json(_claude_text(response), ("result", "response"))
    return RowReading.model_validate(raw)


def _seen_letter(reading: RowReading, options: dict[str, str]) -> str:
    from strict_grading import _choice_letter, option_named_by_words
    if not reading.clear or reading.selection_state == "multiple_active":
        return ""
    letter = _choice_letter(reading.chosen_option or "")
    if not letter and reading.written_answer and options:
        letter = option_named_by_words(reading.written_answer, options)[0] or ""
    return letter


def label_read_as_option(letter: str, *texts: str) -> bool:
    """True when the letter read stands just before an option NUMBER: a part label.

    CA FM MT01 (21 Oct 2026): copy 8bc28fde wrote "A=3" for case study part A, option
    3 (right), and two close reads saw option A; copy 63c82ae8's Q3 was re-anchored to
    the Part B heading "3. (B) (1) cost of equity" and read as option B. Both
    corrections took 2 marks from a right answer. Such a reading never corrects a row.
    """
    if not letter:
        return False
    label = re.compile(rf"(?<![A-Za-z]){re.escape(letter)}\s*[)\].:=\-]*\s*\)?\s*\(?\s*[1-4]\s*\)?(?![\d.,%])",
                       re.IGNORECASE)
    return any(label.search(re.sub(r"\\[()\[\]]|&", " ", str(text or ""))) for text in texts)


def _score_row(question, letter: str, correct: str, options: dict | None = None,
               numbered: bool = False) -> None:
    """Score one MCQ row from the official option, the way strict_grading does. The
    remark is the one the rest of the copy uses (wording.mcq_remark): this pass runs
    after the last wording pass, and a CA FM copy (22 Oct 2026) read "Ans: D" beside
    "Correct answer is (3)." on a paper that numbers its options."""
    question.objective = True
    question.attempted = True
    question.chosen_option = letter
    question.correct_option = correct
    question.is_correct = letter == correct
    question.criteria = []
    question.mark_style = "single"
    question.score = float(question.max_score or 0) if letter == correct else 0.0
    from wording import mcq_remark
    question.remark = "" if letter == correct else mcq_remark(correct, options, numbered)


def confirm_objective_rows(report, ocr_pages, student_pngs, *, answer_map=None, paper_text: str = "",
                           google_token: str, task_id: str, filename: str = "",
                           usage_out: dict | None = None, model: str = CONFIRM_MODEL,
                           second_model: str = SECOND_MODEL, reader=None, second_reader=None,
                           max_rows: int = MAX_ROWS) -> ConfirmResult:
    """Confirm, correct, or leave held, every MCQ row the release gate cannot confirm.

    One close read that agrees with the option on record confirms the row. When it
    disagrees, or there is no option on record, a second close read on another model
    decides: two close reads that agree on a letter transcribe the choice, and the
    official option scores it in code; anything else keeps the hold. reader and
    second_reader (strip, qid, number, options) -> RowReading stand in for the models
    in tests. A read that fails leaves its row exactly as it was.
    """
    from strict_grading import _choice_letter, option_texts_from_paper
    result = ConfirmResult()
    rows = rows_to_confirm(report, ocr_pages, answer_map=answer_map, paper_text=paper_text)
    if not rows or not student_pngs:
        return result
    if len(rows) > max_rows:
        result.notes.append(f"{len(rows)} MCQ rows need confirming; more than {max_rows}, "
                            "so the copy keeps its hold")
        return result

    def real_reader(which: str):
        return lambda strip, qid, number, options: read_row(
            strip, qid, number, options, model=which, google_token=google_token,
            task_id=task_id, filename=filename, usage_out=usage_out,
            question_text=question_block(paper_text, number))

    read = reader or real_reader(model)
    read_again = second_reader or real_reader(second_model)
    paper_options = option_texts_from_paper(paper_text) if paper_text else {}
    lines = _lines(ocr_pages)
    by_qid = {normalize_qid(q.qid): q for q in (getattr(report, "questions", None) or [])}
    unconfirmed = {normalize_qid(x) for x in (getattr(report, "objective_unconfirmed_qids", None) or [])}
    verified = {str(x) for x in (getattr(report, "objective_visual_verified_qids", None) or [])}
    crop_confirmed = {str(x) for x in (getattr(report, "objective_crop_confirmed_qids", None) or [])}

    for key, reasons in sorted(rows.items()):
        question = by_qid[key]
        qid = str(question.qid)
        if float(getattr(question, "max_score", 0) or 0) > MAX_MARKS:
            result.notes.append(f"{qid}: carries {float(question.max_score):g} marks, not a single-option "
                                "MCQ; left for faculty")
            continue
        number = _number(qid)
        chosen = _choice_letter(str(getattr(question, "chosen_option", "") or ""))
        correct = _choice_letter(str(getattr(question, "correct_option", "") or ""))
        options = paper_options.get(number, {}) if number is not None else {}
        anchor = str(getattr(question, "anchor_line_id", "") or "").strip()
        anchor_line = lines.get(anchor)
        # Where to look: the question's own numbered rows when the verdict sits on
        # the wrong line (an anchor already on its own numbered row is kept, as the
        # renderer's row repair may have put it there), else the anchored row.
        candidates: list[tuple[object | None, int]] = []
        if "anchor" in reasons and number is not None and not (
                anchor_line is not None and _label_of(getattr(anchor_line, "text", "")) == number):
            own_rows = [line for line in lines.values()
                        if not getattr(line, "cancelled", False)
                        and _label_of(getattr(line, "text", "")) == number]
            own_rows.sort(key=lambda line: (int(getattr(line, "page", 0) or 0) != int(question.page or 0),
                                            int(getattr(line, "page", 0) or 0)))
            candidates = [(line, int(getattr(line, "page", 0) or 0)) for line in own_rows[:2]]
        if not candidates:
            if anchor_line is None and not chosen:
                result.notes.append(f"{qid}: no option on record and no row to read; left for faculty")
                continue
            candidates = [(anchor_line, int(getattr(anchor_line, "page", 0) or 0) if anchor_line is not None
                           else int(question.page or 1))]
        settled = False
        for line, page_number in candidates:
            if not 1 <= page_number <= len(student_pngs):
                continue
            try:
                strip, centre = row_strip(student_pngs[page_number - 1], _page_of(ocr_pages, page_number),
                                          getattr(line, "bbox", None) if line is not None else None,
                                          float(getattr(question, "y_fraction", 0.5) or 0.5))
                reading = read(strip, qid, number, options)
                result.cost_rows += 1
            except Exception as exc:  # noqa: BLE001 - a failed read keeps the hold
                result.notes.append(f"{qid}: close read failed ({type(exc).__name__}: {exc}); left for faculty")
                continue
            seen = _seen_letter(reading, options)
            moved = line is not None and str(getattr(line, "line_id", "")) != anchor
            placed = not moved or reading.question_number_visible
            written = " ".join(str(reading.written_answer or "").split())[:40]
            if chosen and seen == chosen and placed:
                verdict = "confirmed"
            elif seen and label_read_as_option(seen, reading.written_answer,
                                               getattr(line, "text", "") if line is not None else ""):
                result.notes.append(f"{qid}: close read saw {seen}, but on this row {seen} is a part label "
                                    "before an option number; left for faculty")
                continue
            elif seen and placed and correct:
                # The close read disagrees with the record, or there is none: a
                # second close read on another model has to see the same letter.
                try:
                    again = read_again(strip, qid, number, options)
                    result.cost_rows += 1
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"{qid}: second close read failed ({type(exc).__name__}: {exc}); "
                                        "left for faculty")
                    continue
                seen_again = _seen_letter(again, options)
                if seen_again != seen:
                    result.notes.append(f"{qid}: two close reads of its answer row disagree "
                                        f"({seen} and {seen_again or 'nothing clear'}); left for faculty")
                    continue
                verdict = "corrected"
            else:
                result.notes.append(
                    f"{qid}: close read of its answer row saw "
                    f"{seen or 'no clear option'}" + (f" (\"{written}\")" if written else "")
                    + (" with no question number" if moved else "")
                    + (f", not {chosen}" if chosen else "") + "; left for faculty")
                continue
            if moved:
                question.anchor_line_id = str(getattr(line, "line_id", ""))
                question.page = page_number
                question.y_fraction = round(centre, 4)
                result.reanchored.append(qid)
            if verdict == "corrected":
                was = chosen or "no option"
                from wording import paper_numbers_options
                _score_row(question, seen, correct, options, paper_numbers_options(paper_text))
                result.corrected.append(qid)
                result.notes.append(f"{qid}: {seen} read by two close reads of its answer row (was {was}); "
                                    f"scored {question.score:g} of {float(question.max_score or 0):g}"
                                    + (" and re-anchored" if qid in result.reanchored else ""))
            else:
                result.confirmed.append(qid)
                result.notes.append(f"{qid}: {chosen} confirmed by a close read of its answer row"
                                    + (" and re-anchored to it" if qid in result.reanchored else ""))
            settled = True
            break
        if not settled:
            continue
        unconfirmed.discard(key)
        verified.add(qid)
        crop_confirmed.add(qid)
    report.objective_unconfirmed_qids = sorted(
        x for x in (getattr(report, "objective_unconfirmed_qids", None) or []) if normalize_qid(x) in unconfirmed)
    report.objective_visual_verified_qids = sorted(verified)
    report.objective_crop_confirmed_qids = sorted(crop_confirmed)
    if result.corrected:
        from grader import _rebuild_section_totals
        report.total_score = round(sum(float(q.score or 0) for q in report.questions), 2)
        _rebuild_section_totals(report)
    return result
