"""Deterministic first-pass map from OCR labels to the official question graph.

This map is deliberately conservative: absence of a readable label means unresolved,
never blank. The visual grader may still locate the answer, and selective verification
must adjudicate any unresolved item that the primary grader calls unattempted.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from exam_package import ExamPackage, normalize_qid
from grader import _student_label_num


def _student_label_qid(text: str) -> str | None:
    """Return the canonical qid actually written in a conservative label.

    ``_student_label_num`` remains the chemistry-safe gate.  Once it confirms that
    the line really is an answer label, capture only immediately attached sub-parts;
    a bare ``Q1`` is deliberately not evidence that Q1(a), Q1(b), ... all exist.
    """
    number = _student_label_num(str(text or ""))
    if number is None:
        return None
    raw = str(text or "").strip()
    match = re.match(
        r"^(?:q(?:uestion)?|ans(?:wer)?(?:\s*no\.?)?|उत्तर)?\s*[.\- ]*"
        rf"{number}(?!\d)(.*)$",
        raw,
        re.IGNORECASE,
    )
    if not match:
        return f"q{number}"
    suffix = match.group(1)
    parts: list[str] = []
    cursor = 0
    while cursor < len(suffix):
        part = re.match(
            r"\s*(?:[.\-:→]\s*)?(?:\(([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)\)|"
            r"([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)[.)])",
            suffix[cursor:],
        )
        if not part:
            break
        value = (part.group(1) or part.group(2) or "").lower()
        parts.append(value)
        cursor += part.end()
    return normalize_qid(".".join([f"q{number}", *parts]))


def _student_subpart_label(text: str) -> str | None:
    """A standalone ``(i)``/``(a)`` label, without inventing its parent.

    The caller may use this only while an official multi-part parent is active.
    That context is what keeps an option letter or chemistry expression from being
    promoted into a question id by itself.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # Mathpix commonly surrounds handwriting with inline-LaTeX delimiters.
    readable = raw.replace(r"\(", "").replace(r"\)", "").strip()
    match = re.match(
        r"^\W{0,3}\(?\s*([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)\s*\)"
        r"(?=\s|[.\-:])",
        readable,
    )
    return (match.group(1).lower() if match else None)


@dataclass(frozen=True)
class AnswerLocation:
    qid: str
    status: str                 # "located" | "unresolved"
    pages: tuple[int, ...] = ()
    label_line_ids: tuple[str, ...] = ()
    first_position: tuple[int, int] | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["pages"] = list(self.pages)
        data["label_line_ids"] = list(self.label_line_ids)
        data["first_position"] = (list(self.first_position)
                                  if self.first_position is not None else None)
        return data


@dataclass(frozen=True)
class AnswerMap:
    package_id: str
    locations: tuple[AnswerLocation, ...]

    @property
    def unresolved_qids(self) -> list[str]:
        return [item.qid for item in self.locations if item.status == "unresolved"]

    def as_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "locations": [item.as_dict() for item in self.locations],
            "unresolved_qids": self.unresolved_qids,
        }


def build_answer_map(package: ExamPackage, ocr_pages) -> AnswerMap:
    pages = list(ocr_pages or [])
    official_qids = {normalize_qid(question.qid) for question in package.questions}
    children_by_top: dict[str, set[str]] = {}
    for qid in official_qids:
        top_match = re.match(r"q\d+", qid)
        if top_match:
            children_by_top.setdefault(top_match.group(0), set()).add(qid)
    observations: list[tuple[int, int, str, str]] = []
    for page in pages:
        for line_index, line in enumerate(list(getattr(page, "lines", None) or []), start=1):
            parsed = _student_label_qid(str(getattr(line, "text", "") or ""))
            if not parsed:
                continue
            observations.append((
                int(getattr(page, "page", 0) or 0),
                line_index,
                str(getattr(line, "line_id", "") or ""),
                parsed,
            ))

    # Handwritten digits are occasionally confused even when the surrounding label
    # is clear (the live Economics copy reads 1,2,...,6,1,8 because Mathpix mistook
    # the student's 7 for 1).  Repair only that tightly proved case: three adjacent
    # top-level labels on one page, outer numbers differ by two, the middle reading
    # duplicates an earlier label, and the missing number is official.  This cannot
    # turn ordinary answer text into a label because every observation already passed
    # the conservative chemistry-safe label parser.
    seen_before: set[str] = set()
    repaired = list(observations)
    present = {parsed for _, _, _, parsed in observations}
    for index, observation in enumerate(observations):
        page, line_index, line_id, parsed = observation
        if 0 < index < len(observations) - 1:
            prev = observations[index - 1]
            nxt = observations[index + 1]
            prev_match = re.fullmatch(r"q(\d+)", prev[3])
            cur_match = re.fullmatch(r"q(\d+)", parsed)
            next_match = re.fullmatch(r"q(\d+)", nxt[3])
            if (prev_match and cur_match and next_match
                    and prev[0] == page == nxt[0]
                    and parsed in seen_before):
                prev_num = int(prev_match.group(1))
                next_num = int(next_match.group(1))
                expected = f"q{prev_num + 1}"
                if (next_num == prev_num + 2
                        and expected in official_qids
                        and expected not in present):
                    repaired[index] = (page, line_index, line_id, expected)
                    present.add(expected)
        # A leading 1 is the digit most often lost beside the notebook margin:
        # this sheet's visible ``15.)`` was OCR'd as a second ``5.)`` immediately
        # after Q14.  Repair only a repeated one-digit label following an official
        # teen predecessor, with the exact next official qid still absent.
        if index > 0 and parsed in seen_before:
            prev_match = re.fullmatch(r"q(\d+)", observations[index - 1][3])
            cur_match = re.fullmatch(r"q(\d)", parsed)
            if prev_match and cur_match:
                expected_num = int(prev_match.group(1)) + 1
                expected = f"q{expected_num}"
                if (10 <= expected_num <= 19
                        and int(cur_match.group(1)) == expected_num % 10
                        and expected in official_qids
                        and expected not in present):
                    repaired[index] = (page, line_index, line_id, expected)
                    present.add(expected)
            # The same lost-leading-1 error occurs when a new photographed page
            # starts with Q16: the live Economics sheet's visible ``16)`` came back
            # from Mathpix as ``(6.)``.  It cannot use the Q14->Q15 adjacency proof
            # above because the student answered Q16 before returning to Q14/Q15.
            # Repair only at the FIRST observed label on a later page, after a
            # contiguous run of teen questions has already proved that the script is
            # in the teens, and only when the corresponding teen qid is official and
            # otherwise absent.  A repeated Q6 on the same page stays Q6.
            if cur_match and observations[index - 1][0] != page:
                digit = int(cur_match.group(1))
                teen_num = 10 + digit
                teen = f"q{teen_num}"
                seen_numbers = {
                    int(match.group(1))
                    for value in seen_before
                    if (match := re.fullmatch(r"q(\d+)", value))
                }
                teen_seen = sorted(
                    number for number in seen_numbers if 10 <= number < teen_num
                )
                contiguous_teens = bool(teen_seen) and teen_seen == list(
                    range(teen_seen[0], teen_seen[-1] + 1)
                )
                if (teen in official_qids and teen not in present
                        and contiguous_teens and teen_seen[-1] >= teen_num - 3):
                    repaired[index] = (page, line_index, line_id, teen)
                    present.add(teen)
        seen_before.add(parsed)

    repaired_by_position = {
        (page, line_index, line_id): parsed
        for page, line_index, line_id, parsed in repaired
    }
    found: dict[str, list[tuple[int, int, str]]] = {}
    active_parent: str | None = None
    for page in pages:
        page_number = int(getattr(page, "page", 0) or 0)
        for line_index, line in enumerate(
                list(getattr(page, "lines", None) or []), start=1):
            line_id = str(getattr(line, "line_id", "") or "")
            parsed = repaired_by_position.get((page_number, line_index, line_id))
            if parsed:
                top_match = re.match(r"q\d+", parsed)
                top = top_match.group(0) if top_match else None
                active_parent = top
                target = parsed if parsed in official_qids else None
                if (target is None and top in official_qids
                        and len(children_by_top.get(top, set())) == 1):
                    target = top
                if target:
                    found.setdefault(target, []).append(
                        (page_number, line_index, line_id))
                continue
            subpart = _student_subpart_label(
                str(getattr(line, "text", "") or ""))
            if active_parent and subpart:
                target = normalize_qid(f"{active_parent}.{subpart}")
                if target in official_qids:
                    found.setdefault(target, []).append(
                        (page_number, line_index, line_id))

    locations: list[AnswerLocation] = []
    for question in package.questions:
        hits = found.get(normalize_qid(question.qid), [])
        if hits:
            ordered = sorted(hits, key=lambda item: (item[0], item[1]))
            locations.append(AnswerLocation(
                qid=question.qid,
                status="located",
                pages=tuple(sorted({page for page, _, _ in ordered if page > 0})),
                label_line_ids=tuple(line_id for _, _, line_id in ordered if line_id),
                first_position=(ordered[0][0], ordered[0][1]),
                confidence=0.95,
            ))
        else:
            locations.append(AnswerLocation(qid=question.qid, status="unresolved"))
    return AnswerMap(package_id=package.package_id, locations=tuple(locations))


def answer_map_prompt_block(answer_map: AnswerMap) -> str:
    located = [item for item in answer_map.locations if item.status == "located"]
    unresolved = answer_map.unresolved_qids
    lines = [
        "=== DETERMINISTIC OCR ANSWER MAP ===",
        "This is a navigation aid, not a grading decision. 'Unresolved' never means blank; "
        "search the page images yourself before setting attempted=false.",
    ]
    lines.extend(
        f"- {item.qid}: page(s) {', '.join(map(str, item.pages))}; "
        f"label(s) {', '.join(item.label_line_ids) or '(page label only)'}"
        for item in located
    )
    if unresolved:
        lines.append("- Unresolved labels: " + ", ".join(unresolved))
    return "\n".join(lines)
