"""Bind independently read MCQ locations to the actual written answer cells.

Coordinates come from the key-blind page reader. No table subdivisions or evenly
spaced cells are inferred. A location is usable only after the existing choice
consensus succeeds, and only when the original page contains ink in that box.
This module changes evidence locations, never scores or selected options.
"""
from dataclasses import replace
from io import BytesIO
import math
import re

from exam_package import normalize_qid
from mathpix import LineBox


def _location_box(observation):
    if not (getattr(observation, "clear", False)
            and getattr(observation, "location_clear", False)):
        return None
    raw = getattr(observation, "answer_bbox", None)
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0 <= v <= 1 for v in raw):
        return None
    left, top, right, bottom = raw
    if not (0 < right - left <= .65 and 0 < bottom - top <= .085):
        return None
    return tuple(raw)


def _contains_writing(png, box, page):
    from PIL import Image
    try:
        with Image.open(BytesIO(png)) as image:
            w, h = image.size
            if not (w and h and page.image_width > 0 and page.image_height > 0):
                return False
            expected = page.image_width / page.image_height
            if abs(w / h - expected) > .04 * expected:
                return False
            crop = image.convert("L").crop((int(box[0] * w), int(box[1] * h),
                                            math.ceil(box[2] * w), math.ceil(box[3] * h)))
            if not crop.width or not crop.height:
                return False
            histogram = crop.histogram()
            return sum(histogram[:145]) / (crop.width * crop.height) >= .01
    except (OSError, ValueError, TypeError):
        return False


def _owned_written_row(question, page, answer_map, observation, options):
    """Prefer an already located, readable answer over a guessed vision box."""
    from grader import _student_label_num
    qid = normalize_qid(question.qid)
    location = next((item for item in getattr(answer_map, "locations", ())
                     if normalize_qid(item.qid) == qid), None)
    if location is None or location.status != "located":
        return None
    from strict_grading import _objective_choice_from_line, option_named_by_words
    # "q1.4" is the MCQ the student numbers 4 (Q1 (i) to (x) of a paper).
    expected = re.fullmatch(r"q(\d+)", qid) or re.fullmatch(r"q\d+\.(\d+)", qid)
    number = int(expected.group(1)) if expected else None
    copied = set(re.findall(r"\w{2,}", str(getattr(observation, "written_answer", "")).lower()))
    candidates = []
    for line in page.lines:
        if (line.line_id not in location.line_ids or getattr(line, "cancelled", False)
                or getattr(line, "kind", "text") != "text"):
            continue
        label = _student_label_num(str(line.text or ""))
        if label is not None and label != number:
            continue
        # A row that opens with this question's own number is its answer, however
        # unsure the OCR is of the words after it. Uday Bihar Board (10 Oct 2026):
        # "1 Ans - (A) A. Masshadl" read at 0.33 was passed over for the reader's
        # estimated box, and the ticks of nine MCQs fell between rows.
        own_label = number is not None and label == number
        if not own_label and (getattr(line, "confidence", None) is None or line.confidence < .8):
            continue
        content = re.sub(r"^\s*(?:(?:q|ans(?:wer)?)[\s.-]*)?\(?\d+\)?[).:\s-]*",
                         "", str(line.text or ""), flags=re.I)
        if not content.strip() or re.match(r"^(?:section|part|answer)\b", content, flags=re.I):
            continue
        left, top, right, bottom = line.bbox
        if not (0 <= left < right <= page.image_width and 0 <= top < bottom <= page.image_height
                and (bottom - top) / page.image_height <= .18):
            continue
        letter = _objective_choice_from_line(line.text, number)
        named = option_named_by_words(line.text, options)[0] if options else ""
        # Shared words such as "the campaign against" cannot make a conflicting
        # active option the location of the independently confirmed selection.
        if ((letter and letter != question.chosen_option)
                or (named and named != question.chosen_option)):
            continue
        copied_words = copied & set(re.findall(r"\w{2,}", content.lower()))
        if (letter == question.chosen_option or named == question.chosen_option
                or len(copied_words) >= 2 or own_label):
            candidates.append((int(line.line_id == question.anchor_line_id), len(copied_words), line))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:2], reverse=True)
    return candidates[0][2]


def bind_objective_locations(report, ocr_pages, answer_map, visual_choices, *, student_pngs,
                             question_paper_text=""):
    """Return an updated immutable AnswerMap and concise location notes."""
    verified = {normalize_qid(qid) for qid in report.objective_visual_verified_qids}
    unconfirmed = {normalize_qid(qid) for qid in report.objective_unconfirmed_qids}
    observations = {normalize_qid(qid): observation
                    for qid, observation in (visual_choices or {}).items()}
    from strict_grading import option_texts_from_paper
    paper_options = option_texts_from_paper(question_paper_text)
    pages = {page.page: page for page in ocr_pages or []}
    updated = {}
    notes = []
    for question in report.questions:
        qid = normalize_qid(question.qid)
        if (not question.objective or not question.attempted
                or qid not in verified or qid in unconfirmed):
            continue
        observation = observations.get(qid)
        if observation is None:
            continue
        box = _location_box(observation)
        number = getattr(observation, "page", 0)
        page = pages.get(number)
        if page is None or not 1 <= number <= len(student_pngs):
            continue
        # The scoring path has already normalized written answer values to the
        # printed options. Reuse that key-blind normalization, never the key.
        letter = str(getattr(observation, "chosen_option", "") or "").upper()
        final_letter = str(question.chosen_option or "").upper()
        if letter and letter != final_letter:
            continue
        if final_letter not in {"A", "B", "C", "D"}:
            continue
        qnumber = re.fullmatch(r"q(\d+)", qid)
        options = paper_options.get(int(qnumber.group(1)), {}) if qnumber else {}
        owned = _owned_written_row(question, page, answer_map, observation, options)
        if owned is not None:
            # Key-blind choice consensus confirms what is written. Existing OCR
            # geometry locates it; an approximate model box must not move it.
            lid = owned.line_id
            question.anchor_line_id = lid
            question.page = number
            question.y_fraction = (owned.bbox[1] + owned.bbox[3]) / (2 * page.image_height)
            region = {"qid": question.qid, "status": "located", "pages": [number],
                      "label_line_ids": [], "first_position": [number, page.lines.index(owned) + 1],
                      "confidence": 1.0, "line_ids": [lid],
                      "regions": [{"page": number, "line_ids": [lid], "bbox": list(owned.bbox)}],
                      "uncertain_line_ids": [], "evidence_line_ids": [lid],
                      "source": "confirmed_owned_ocr_location", "repaired": True}
            report.answer_regions[qid] = region
            updated[qid] = region
            notes.append(f"{question.qid}: kept confirmed written OCR row on page {number}")
            continue
        if box is None:
            continue
        if not _contains_writing(student_pngs[number - 1], box, page):
            continue
        text = str(getattr(observation, "written_answer", "") or "").strip() or letter
        if not text:
            continue
        pixels = tuple(v * (page.image_width if i % 2 == 0 else page.image_height)
                       for i, v in enumerate(box))
        old = report.answer_regions.get(qid, {})
        existing = next((line for line in page.lines
                         if line.line_id in old.get("line_ids", [])
                         and old.get("source") == "independent_visual_location"
                         and line.text == text and tuple(line.bbox) == pixels), None)
        if existing is None:
            indices = [int(match.group(1)) for line in page.lines
                       if (match := re.fullmatch(rf"P{number}L(\d+)", line.line_id))]
            lid = f"P{number}L{max(indices, default=0) + 1}"
            existing = LineBox(lid, text, pixels, number, confidence=1.0)
            page.lines.append(existing)
        lid = existing.line_id
        question.anchor_line_id = lid
        question.page = number
        question.y_fraction = (box[1] + box[3]) / 2
        region = {"qid": question.qid, "status": "located", "pages": [number],
                  "label_line_ids": [], "first_position": [number, len(page.lines)],
                  "confidence": 1.0, "line_ids": [lid],
                  "regions": [{"page": number, "line_ids": [lid], "bbox": list(pixels)}],
                  "uncertain_line_ids": [], "evidence_line_ids": [lid],
                  "source": "independent_visual_location", "repaired": True}
        report.answer_regions[qid] = region
        updated[qid] = region
        notes.append(f"{question.qid}: independently located written answer on page {number}")
    if answer_map is not None and updated:
        locations = []
        for location in answer_map.locations:
            region = updated.get(normalize_qid(location.qid))
            if region is not None:
                location = replace(location, status="located", pages=tuple(region["pages"]),
                    label_line_ids=(), first_position=tuple(region["first_position"]),
                    confidence=1.0, line_ids=tuple(region["line_ids"]),
                    regions=tuple(region["regions"]), uncertain_line_ids=())
            locations.append(location)
        answer_map = replace(answer_map, locations=tuple(locations))
    return answer_map, notes
