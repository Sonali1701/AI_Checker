"""Strict package-bound compilation and automatic release checks.

Legacy reconciliation intentionally contains recovery heuristics.  This module never
re-homes, drops, invents, or proportionally rescales a model decision.  Structural
ambiguity is an UNGRADABLE outcome for the zero-human path.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from exam_package import ExamPackage, PackageIssue, normalize_qid
from grader import QuestionMark


class StrictGradeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def report_decision_digest(report) -> str:
    """Stable digest of every score-bearing decision in a report.

    A verifier certificate is valid only for the decisions it actually saw.  Layout
    cleanup may run afterward, but any later score/criterion/attempted mutation must
    invalidate automatic release instead of silently inheriting an earlier boolean.
    """
    rows = []
    for question in list(getattr(report, "questions", None) or []):
        rows.append({
            "qid": normalize_qid(str(getattr(question, "qid", "") or "")),
            "attempted": bool(getattr(question, "attempted", True)),
            "score": float(getattr(question, "score", 0.0) or 0.0),
            "max": float(getattr(question, "max_score", 0.0) or 0.0),
            "objective": bool(getattr(question, "objective", False)),
            "chosen": str(getattr(question, "chosen_option", "") or ""),
            "correct": str(getattr(question, "correct_option", "") or ""),
            "criteria": [
                {
                    "description": str(getattr(item, "description", "") or ""),
                    "awarded": float(getattr(item, "awarded", 0.0) or 0.0),
                    "max": float(getattr(item, "max", 0.0) or 0.0),
                    "met": bool(getattr(item, "met", False)),
                    "anchor": str(getattr(item, "step_line_id", "") or ""),
                }
                for item in list(getattr(question, "criteria", None) or [])
            ],
        })
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _top(qid: str) -> str:
    normalized = normalize_qid(qid)
    return normalized.split(".", 1)[0] if normalized else qid


def _mapped_attempt(question) -> bool:
    """Conservative signal that a choice option contains an actual mapped answer."""
    if not bool(getattr(question, "attempted", True)):
        return False
    remark = str(getattr(question, "remark", "") or "").strip().lower()
    blank_labels = {"not attempted", "unattempted", "blank", "not answered"}
    if (float(getattr(question, "score", 0.0) or 0.0) == 0.0
            and not list(getattr(question, "criteria", None) or [])
            and remark in blank_labels):
        return False
    return True


def _objective_letter(value: str) -> str:
    match = re.fullmatch(r"\s*(?:option\s*)?([A-Da-d])\s*", str(value or ""))
    return match.group(1).upper() if match else ""


_TRAILING_OBJECTIVE_RE = re.compile(
    r"(?:\(|\[|\b)([1-4A-Da-d])(?:\)|\]|[.])?\s*$", re.IGNORECASE)
_ANSWER_NUMBER_RE = re.compile(
    # Mathpix emits ordinary handwritten ``7.)`` labels with both punctuation
    # marks.  Accept the short punctuation run as one label, but never consume the
    # following parenthesised option token.
    r"^\s*(?:q(?:uestion)?\s*)?(\d{1,3})\s*[).:\-]{1,3}\s*",
    re.IGNORECASE,
)
_PAREN_OBJECTIVE_RE = re.compile(r"[\[(]\s*([1-4A-Da-d])\s*[\])]", re.IGNORECASE)
_RAW_LEADING_OPTION_RE = re.compile(r"^\s*[\[(]\s*([A-Za-z0-9])\s*[\])]", re.IGNORECASE)


def _choice_letter(value: str) -> str:
    raw = str(value or "").strip().upper()
    return {"1": "A", "2": "B", "3": "C", "4": "D"}.get(raw, raw)


# A bracketed "R" is the Reason of an assertion-reason item. No A-D paper offers
# an option R, so this token never competes with a choice - it identifies one.
_ASSERTION_REF_RE = re.compile(r"\(\s*R\s*\)", re.IGNORECASE)


def _objective_choice_from_line(text: str, question_number: int | None = None) -> str:
    """Read one A-D / 1-4 choice from a student's numbered answer line.

    Students commonly write ``6) (3) is negative...`` or ``2) (A) A and R...``;
    requiring a letter at the *end* of the OCR line rejected both forms.  The
    parenthesised token immediately following the optional question label is the
    answer.  Ambiguous lines still fail closed.
    """
    # Mathpix may wrap a handwritten option twice: ``( \( A \) )``.  The
    # backslash delimiters are presentation markup, not extra answer tokens.
    value = str(text or "").replace(r"\(", "").replace(r"\)", "").strip()
    label = _ANSWER_NUMBER_RE.match(value)
    if label:
        if question_number is not None and int(label.group(1)) != question_number:
            return ""
        value = value[label.end():]
    tokens = [_choice_letter(match.group(1)) for match in _PAREN_OBJECTIVE_RE.finditer(value)]
    tokens = [token for token in tokens if token in {"A", "B", "C", "D"}]
    if len(set(tokens)) == 1:
        return tokens[0]
    # An ASSERTION-REASON answer restates the claim it is judging, so the line
    # carries a second bracketed capital that is not a choice:
    #     Q3 (D) (A) is false, but (R) is true
    # There is no option "(R)" on any A-D paper, so a literal (R) marks the line as
    # assertion-reason prose and the later (A) as a reference to the Assertion. The
    # option is then the first token after the label, which is what this function's
    # contract says it is. MEASURED on a Class 10 PW Physics copy: the student chose
    # (D), correctly, and this returned "" - so the strict objective gate could not
    # anchor the row and HELD the entire paper.
    #
    # Deliberately narrow. Only a genuine (R) reference enables it, and only when
    # every later token is the Assertion's (A). A student who writes two real
    # options - a correction like "(A) ... (C)" - still fails closed, which is the
    # behaviour this all-agree rule exists to provide.
    if (tokens and _ASSERTION_REF_RE.search(value)
            and set(tokens[1:]) <= {"A"}):
        return tokens[0]
    match = _TRAILING_OBJECTIVE_RE.search(value)
    return _choice_letter(match.group(1)) if match else ""


def _raw_leading_option(text: str) -> str:
    """Return the single option glyph immediately after a numbered answer label."""
    value = str(text or "").strip()
    label = _ANSWER_NUMBER_RE.match(value)
    if not label:
        return ""
    raw = _RAW_LEADING_OPTION_RE.match(value[label.end():])
    return raw.group(1).upper() if raw else ""


def _mapped_objective_lines(answer_map) -> dict[str, tuple[str, ...]]:
    return {
        normalize_qid(str(getattr(item, "qid", "") or "")): tuple(
            str(line_id or "")
            for line_id in (getattr(item, "label_line_ids", None) or ())
            if str(line_id or "")
        )
        for item in list(getattr(answer_map, "locations", None) or [])
        if getattr(item, "status", "") == "located"
    }


def bind_official_objective_anchors(
    report, package: ExamPackage, ocr_pages, answer_map=None,
) -> list[str]:
    """Bind each package-declared MCQ to its own numbered OCR line.

    This is the invisible answer-box boundary for dense objective sections.  The
    grader may suggest an anchor, but Q5 is allowed to use only a line beginning
    with ``5)``; it can never borrow Q6's line and make that answer look both right
    and wrong.  When several candidates exist, the grader's current location is
    used only to choose the nearest candidate with the correct number.
    """
    specs = {
        normalize_qid(item.qid): item
        for item in package.questions
        if item.question_type == "objective"
    }
    if not specs:
        return []
    rows: list[tuple[int, int, object]] = []
    by_id: dict[str, tuple[int, int, object]] = {}
    mapped_lines = _mapped_objective_lines(answer_map)
    for page in list(ocr_pages or []):
        page_number = int(getattr(page, "page", 0) or 0)
        for index, line in enumerate(list(getattr(page, "lines", None) or [])):
            row = (page_number, index, line)
            rows.append(row)
            by_id[str(getattr(line, "line_id", "") or "")] = row
    rebound: list[str] = []
    for question in list(getattr(report, "questions", None) or []):
        spec = specs.get(normalize_qid(getattr(question, "qid", "")))
        if spec is None:
            continue
        number_match = re.fullmatch(r"q(\d{1,3})", normalize_qid(spec.qid))
        if not number_match:
            continue
        number = int(number_match.group(1))
        candidates = []
        for page_number, index, line in rows:
            text = str(getattr(line, "text", "") or "")
            label = _ANSWER_NUMBER_RE.match(text)
            if (label and int(label.group(1)) == number
                    and _objective_choice_from_line(text, number)):
                candidates.append((page_number, index, line))
        # The deterministic answer map repairs one narrowly proved OCR error: a
        # duplicated digit bracketed by its two sequential neighbours (the live
        # Economics Q7 was read as a second Q1 between Q6 and Q8).  Reuse that exact
        # ownership proof here instead of asking a provider to guess or holding a
        # visibly answerable copy.  It must identify exactly one line and that line
        # must itself contain one option token.
        if not candidates:
            mapped = [by_id[line_id] for line_id in mapped_lines.get(
                normalize_qid(spec.qid), ()) if line_id in by_id]
            mapped = [
                row for row in mapped
                if (_objective_choice_from_line(
                    str(getattr(row[2], "text", "") or ""), None)
                    or _raw_leading_option(
                        str(getattr(row[2], "text", "") or "")) == "H")
            ]
            if len(mapped) == 1:
                candidates = mapped
        if not candidates:
            continue
        current_id = str(getattr(question, "anchor_line_id", "") or "")
        current = by_id.get(current_id)
        if current in candidates:
            chosen = current
        elif current is not None:
            chosen = min(
                candidates,
                key=lambda row: (abs(row[0] - current[0]), abs(row[1] - current[1])),
            )
        else:
            chosen = candidates[0]
        line = chosen[2]
        new_id = str(getattr(line, "line_id", "") or "")
        if new_id and new_id != current_id:
            question.anchor_line_id = new_id
            question.page = chosen[0]
            rebound.append(f"{spec.qid}: {current_id or '(estimated)'} -> {new_id}")
    return rebound


# --- reading an objective answer by its VALUE, not only its letter -------------
#
# MEASURED on the live Economics copy of 2026-09-04: the student wrote
#
#     1.) (A) Measured in numerical units called util.
#
# The letter says A. The words they wrote ARE option C, which is the official
# answer. Scoring compared letters only, so a correct answer was marked wrong and
# the student lost a mark they had earned. The grading prompt has always said the
# opposite -- "Award FULL marks if EITHER the chosen letter OR the written value
# matches the correct option in the key... give the legible correct value
# precedence over a garbled letter" -- but this code path overrides the model, so
# the rule never reached the score.
#
# Only ever RESTORES a mark. A written value can rescue an answer the letter got
# wrong; it can never take one away.

# "1." / "1)" / "Q1." at the start of a question block on the paper.
_PAPER_QUESTION_RE = re.compile(
    r"^[ \t]*Q?\.?\s*(\d{1,3})\s*[.)]?\s*$|"
    r"^[ \t]*Q?\.?\s*(\d{1,3})\s*[.)]\s+",
    re.MULTILINE,
)
# "(A)" / "(1)" / "(a)" introducing an option, on the paper or on the answer line.
_PAPER_OPTION_RE = re.compile(r"[(\[]\s*([A-Da-d1-4])\s*[)\]]")
_DIGIT_TO_LETTER = {"1": "A", "2": "B", "3": "C", "4": "D"}


def _norm_letter(token: str) -> str:
    token = str(token or "").strip().upper()
    return _DIGIT_TO_LETTER.get(token, token if token in {"A", "B", "C", "D"} else "")


def _compact(text: str) -> str:
    """Letters and digits only, lowercased — for fuzzy comparison of two phrasings.

    Mathpix mangles spacing and punctuation far more than it mangles the letters
    themselves, and a student writes "util" where the paper prints "utils", so the
    comparison has to survive both.
    """
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def option_texts_from_paper(question_paper_text: str) -> dict[int, dict[str, str]]:
    """{question number: {"A": option text, ...}} read off the official paper."""
    text = str(question_paper_text or "")
    if not text.strip():
        return {}
    # Split into per-question blocks on a line that is (or starts with) a number.
    starts: list[tuple[int, int]] = []
    for match in _PAPER_QUESTION_RE.finditer(text):
        number = match.group(1) or match.group(2)
        if number:
            starts.append((int(number), match.end()))
    out: dict[int, dict[str, str]] = {}
    for index, (number, begin) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        block = text[begin:end]
        marks = list(_PAPER_OPTION_RE.finditer(block))
        if len(marks) < 2:
            continue
        options: dict[str, str] = {}
        for position, mark in enumerate(marks):
            letter = _norm_letter(mark.group(1))
            stop = marks[position + 1].start() if position + 1 < len(marks) else len(block)
            body = block[mark.end():stop].strip()
            # An option runs to the end of its line; anything after a blank line
            # belongs to the next question's stem, not to this option.
            body = re.split(r"\n\s*\n", body)[0].strip()
            if letter and body and letter not in options:
                options[letter] = body
        if len(options) >= 2:
            # A later restatement of the same question (a solution block appended to
            # the paper) must not overwrite the real one.
            out.setdefault(number, options)
    return out


def _student_written_value(line_text: str, number: int | None) -> str:
    """The student's answer text with its question label and option marker removed."""
    value = str(line_text or "").replace(r"\(", "").replace(r"\)", "").strip()
    label = _ANSWER_NUMBER_RE.match(value)
    if label and (number is None or int(label.group(1)) == number):
        value = value[label.end():]
    mark = _PAPER_OPTION_RE.match(value.strip())
    if mark:
        value = value.strip()[mark.end():]
    return value.strip()


# How close the student's words must be to an option before it counts as having
# been written out, and how far ahead of the runner-up. Deliberately strict: this
# runs only when the letter already disagrees with the key, so a loose match would
# hand marks to a wrong answer that happens to share wording with the right one.
_VALUE_MATCH_MIN = 0.80
_VALUE_MATCH_MARGIN = 0.15
_VALUE_MIN_CHARS = 8


def written_value_choice(line_text: str, options: dict[str, str],
                         number: int | None = None) -> tuple[str, float]:
    """The option the student WROTE OUT on this line, and how close the match is.

    Returns ("", 0.0) when they wrote only a letter, or when no option stands
    clearly ahead of the others.
    """
    from difflib import SequenceMatcher

    written = _compact(_student_written_value(line_text, number))
    if len(written) < _VALUE_MIN_CHARS or not options:
        return "", 0.0
    scored = sorted(
        ((SequenceMatcher(None, written, _compact(body)).ratio(), letter)
         for letter, body in options.items() if _compact(body)),
        reverse=True,
    )
    if not scored:
        return "", 0.0
    best_ratio, best_letter = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_ratio >= _VALUE_MATCH_MIN and best_ratio - runner_up >= _VALUE_MATCH_MARGIN:
        return best_letter, best_ratio
    return "", best_ratio


def apply_official_objective_evidence(
    report, package: ExamPackage, ocr_pages, answer_map=None,
    visual_choices: dict[str, object] | None = None,
    question_paper_text: str = "",
) -> list[str]:
    """Score package-declared objective rows only from official + page evidence.

    The primary model may navigate the page, but it does not get to provide its own
    answer key or self-certify ``is_correct``.  A release-eligible objective answer
    therefore needs an official package answer and one clean A-D choice at the end of
    the anchored OCR line. Ambiguous/circled-as-digit OCR is held, never guessed.
    """
    specs = {normalize_qid(item.qid): item for item in package.questions
             if item.question_type == "objective"}
    if not specs:
        return []
    lines = {
        str(getattr(line, "line_id", "") or ""): str(getattr(line, "text", "") or "")
        for page in list(ocr_pages or [])
        for line in list(getattr(page, "lines", None) or [])
    }
    notes: list[str] = []
    visual_choices = {
        normalize_qid(str(key or "")): value
        for key, value in (visual_choices or {}).items()
    }
    visual_verified = set(
        getattr(report, "objective_visual_verified_qids", None) or [])
    notes.extend(bind_official_objective_anchors(
        report, package, ocr_pages, answer_map=answer_map))
    mapped_lines = _mapped_objective_lines(answer_map)
    # Option wording, so an answer written out in words can be recognised.
    paper_options = option_texts_from_paper(question_paper_text)
    for question in list(getattr(report, "questions", None) or []):
        spec = specs.get(normalize_qid(getattr(question, "qid", "")))
        if spec is None:
            continue
        correct = _objective_letter(spec.objective_answer or "")
        anchor = str(getattr(question, "anchor_line_id", "") or "").strip()
        number_match = re.fullmatch(r"q(\d{1,3})", normalize_qid(spec.qid))
        number = int(number_match.group(1)) if number_match else None
        line_text = lines.get(anchor, "")
        chosen = _objective_choice_from_line(line_text, number)
        owned_by_map = anchor in mapped_lines.get(normalize_qid(spec.qid), ())
        if not chosen and owned_by_map:
            # A repaired label deliberately disagrees with the raw OCR digit, so
            # parse its option only after the independent sequence map owns it.
            chosen = _objective_choice_from_line(line_text, None)
        if not chosen and owned_by_map:
            # Handwritten ``4`` is occasionally returned as ``H``.  Resolve that
            # confusable only when all three signals agree: the answer map owns the
            # row, the token immediately after its label is H, and the visual grader
            # independently read option D/4.  An arbitrary H never earns a mark.
            raw = _raw_leading_option(line_text)
            provider_choice = _choice_letter(
                str(getattr(question, "chosen_option", "") or ""))
            if raw == "H" and provider_choice == "D":
                chosen = "D"
                notes.append(f"{spec.qid}: OCR H resolved as handwritten 4 by visual agreement")
            elif raw == "H" and number is not None:
                # The visual grader does not always emit ``chosen_option``.  The
                # alternative independent proof on the live sheet is notation
                # continuity: Q8 and Q10 are both sequence-owned rows whose first
                # tokens are numeric choices.  Require both neighbours; a lone or
                # arbitrary H remains unresolved.
                neighbour_tokens: list[str] = []
                for neighbour in (number - 1, number + 1):
                    neighbour_ids = mapped_lines.get(f"q{neighbour}", ())
                    if len(neighbour_ids) != 1:
                        neighbour_tokens.append("")
                        continue
                    neighbour_tokens.append(_raw_leading_option(
                        lines.get(neighbour_ids[0], "")))
                if all(token in {"1", "2", "3", "4"}
                       for token in neighbour_tokens):
                    chosen = "D"
                    notes.append(
                        f"{spec.qid}: OCR H resolved as handwritten 4 by owned numeric neighbours")
        # A checkbox/table region may be classified by Mathpix as a diagram with
        # no text at all.  In that case require TWO independent vision readings:
        # the primary examiner's chosen_option and a separate provider's key-blind
        # observation.  Agreement transcribes the student's choice; the official
        # key still determines the score in code.  One model alone never suffices.
        used_visual_consensus = False
        observation = visual_choices.get(normalize_qid(spec.qid))
        if observation is not None and bool(
                getattr(observation, "clear", False)):
            primary_choice = _choice_letter(
                str(getattr(question, "chosen_option", "") or ""))
            independent_choice = _choice_letter(
                str(getattr(observation, "chosen_option", "") or ""))
            if (primary_choice in {"A", "B", "C", "D"}
                    and primary_choice == independent_choice):
                if chosen and chosen != primary_choice:
                    notes.append(
                        f"{spec.qid}: noisy OCR {chosen} replaced by two-reader "
                        f"visual consensus {primary_choice}")
                chosen = primary_choice
                used_visual_consensus = True
                try:
                    question.page = max(1, int(getattr(observation, "page", 1) or 1))
                    question.y_fraction = min(1.0, max(
                        0.0, float(getattr(observation, "y_fraction", 0.5) or 0.5)))
                except (TypeError, ValueError):
                    pass
                visual_verified.add(spec.qid)
                notes.append(
                    f"{spec.qid}: {chosen} confirmed by two independent visual reads")
            elif (primary_choice in {"A", "B", "C", "D"}
                  and independent_choice in {"A", "B", "C", "D"}):
                raise StrictGradeError(
                    "OBJECTIVE_VISUAL_DISAGREEMENT",
                    f"Two visual readers disagree on {spec.qid}; no score was released.",
                )
        if (not correct or not chosen
                or (not anchor and not used_visual_consensus
                    and spec.qid not in visual_verified)):
            raise StrictGradeError(
                "OBJECTIVE_ANSWER_UNRESOLVED",
                f"{spec.qid} has no unambiguous anchored A-D/1-4 answer on the student page.",
            )
        # The letter disagrees with the key. Before scoring a zero, read what the
        # student actually WROTE: if they copied out the correct option's own words,
        # the mark is theirs and the letter is a slip or an OCR misread. Never the
        # other way round — a written value cannot turn a correct letter wrong.
        awarded_choice = chosen
        if chosen != correct and number is not None:
            written, ratio = written_value_choice(
                line_text, paper_options.get(number, {}), number)
            if written and written == correct:
                awarded_choice = correct
                notes.append(
                    f"{spec.qid}: letter reads {chosen} but the written answer is "
                    f"option {correct} verbatim ({ratio:.0%} match) — scored correct")
        question.objective = True
        question.correct_option = correct
        question.chosen_option = awarded_choice
        question.is_correct = awarded_choice == correct
        question.attempted = True
        question.criteria = []
        question.mark_style = "single"
        question.score = float(spec.max_marks) if awarded_choice == correct else 0.0
        question.remark = "" if awarded_choice == correct else f"Ans: {correct}"
        notes.append(f"{spec.qid}: page {chosen}, official {correct}")
    report.total_score = round(sum(float(q.score) for q in report.questions), 2)
    report.objective_visual_verified_qids = sorted(visual_verified)
    return notes


def reconcile_report_to_package(
    report,
    package: ExamPackage,
    answer_map=None,
    *,
    finalize_choices: bool = True,
):
    """Bind a legacy GradeReport's decisions to an immutable ExamPackage.

    This is the compatibility compiler for the first strict migration phase.  The
    provider still emits the old report schema, but none of its identifiers or maxima
    survive unless they exactly match the package.
    """
    blockers = package.blockers()
    if blockers:
        raise StrictGradeError(
            "INVALID_EXAM_PACKAGE",
            "; ".join(issue.detail for issue in blockers[:4]),
        )
    canonical = {normalize_qid(question.qid): question for question in package.questions}
    seen: set[str] = set()
    report_order: dict[str, int] = {}
    rows_by_qid: dict[str, object] = {}
    unverified_objective_qids: list[str] = list(
        getattr(report, "unverified_objective_qids", None) or [])
    locations = {
        normalize_qid(getattr(item, "qid", "")): item
        for item in list(getattr(answer_map, "locations", None) or [])
    }
    for position, question in enumerate(list(getattr(report, "questions", None) or [])):
        qid = normalize_qid(getattr(question, "qid", ""))
        if not qid or qid not in canonical:
            raise StrictGradeError(
                "UNKNOWN_QUESTION",
                f"The grader returned question {getattr(question, 'qid', '')!r}, which is not in "
                f"exam package {package.package_id[:12]}.",
            )
        if qid in seen:
            raise StrictGradeError(
                "DUPLICATE_QUESTION",
                f"The grader returned {canonical[qid].qid} more than once.",
            )
        seen.add(qid)
        report_order[qid] = position
        rows_by_qid[qid] = question
        spec = canonical[qid]
        provider_claimed_objective = bool(
            getattr(question, "objective", False)
            or str(getattr(question, "correct_option", "") or "").strip()
        )
        if spec.question_type == "objective":
            question.objective = True
            question.correct_option = _objective_letter(spec.objective_answer or "")
        else:
            if provider_claimed_objective:
                unverified_objective_qids.append(spec.qid)
            # A provider cannot create a deterministic-scoring bypass. Preserve its
            # provisional score for the challenger, but treat the row as subjective
            # until the complete evidence audit rules on it.
            question.objective = False
            question.correct_option = ""
            question.is_correct = False
        question.qid = spec.qid
        question.max_score = float(spec.max_marks)
        question.score = min(max(0.0, float(question.score)), float(spec.max_marks))
        criteria = list(getattr(question, "criteria", None) or [])
        if criteria:
            criterion_max = round(sum(float(c.max) for c in criteria), 4)
            official_max = float(spec.max_marks)
            if criterion_max < official_max - 0.25:
                # The provider sometimes accounts for the student's answer but
                # omits one official rubric point from its breakdown. Preserve the
                # denominator without pretending that point was earned: append an
                # explicit placeholder deduction which the mandatory challenger
                # must examine before automatic release. Any score the provider
                # included beyond its listed criteria is carried into this row;
                # otherwise it starts at zero. This is not proportional rescaling.
                criterion_score = round(sum(float(c.awarded) for c in criteria), 4)
                missing_max = round(official_max - criterion_max, 4)
                missing_awarded = min(
                    missing_max,
                    max(0.0, round(float(question.score) - criterion_score, 4)),
                )
                placeholder = criteria[0].model_copy(update={
                    "description": "Unallocated official rubric point",
                    "awarded": missing_awarded,
                    "max": missing_max,
                    "met": missing_awarded >= missing_max - 1e-9,
                    "evidence": (
                        "Primary breakdown omitted this official rubric point"
                    ),
                    "step_line_id": str(
                        getattr(question, "anchor_line_id", "") or
                        getattr(criteria[-1], "step_line_id", "") or ""
                    ),
                })
                question.criteria = criteria = [*criteria, placeholder]
                criterion_max = round(sum(float(c.max) for c in criteria), 4)
            if criterion_max > official_max + 0.25:
                # The primary examiner's split over-sums the official maximum.
                # Proportional rescaling is still refused: it invents a distribution
                # nobody awarded. But refusing the whole copy is worse, and it is not
                # even the score that is wrong - question.score is clamped to the
                # official maximum a few lines below, so a student can never be paid
                # more than the paper allows. What is wrong is a breakdown that does
                # not add up when it is shown to them.
                #
                # So take the surplus out of the maxima the student earned least on,
                # never cutting below what they were actually awarded. Every earned
                # mark survives, the steps sum to the official total, and no mark is
                # moved between criteria. Only if the awarded marks themselves exceed
                # the official maximum is there a real over-award to hold, and that is
                # a different fault with a different message.
                surplus = round(criterion_max - official_max, 4)
                trim_order = sorted(
                    range(len(criteria)),
                    key=lambda i: (float(criteria[i].awarded), -float(criteria[i].max)),
                )
                for index in trim_order:
                    if surplus <= 1e-9:
                        break
                    criterion = criteria[index]
                    headroom = round(
                        float(criterion.max) - float(criterion.awarded), 4)
                    if headroom <= 1e-9:
                        continue
                    cut = min(headroom, surplus)
                    trimmed_max = round(float(criterion.max) - cut, 4)
                    criteria[index] = criterion.model_copy(update={
                        "max": trimmed_max,
                        "met": float(criterion.awarded) >= trimmed_max - 1e-9,
                    })
                    surplus = round(surplus - cut, 4)
                question.criteria = criteria
                criterion_max = round(sum(float(c.max) for c in criteria), 4)
                if surplus > 1e-9 or criterion_max > official_max + 0.25:
                    raise StrictGradeError(
                        "CRITERION_MAXIMUM_MISMATCH",
                        f"{spec.qid} criteria total {criterion_max:g} against an official "
                        f"maximum of {spec.max_marks:g}, and the marks already awarded "
                        f"leave nothing to trim. Strict mode will not rescale the steps.",
                    )
                print(f"[reconcile] {spec.qid}: criteria over-summed the official "
                      f"{spec.max_marks:g} and were trimmed to fit without touching "
                      f"any awarded mark", flush=True)
            criterion_score = round(sum(float(c.awarded) for c in criteria), 4)
            question.score = min(max(0.0, criterion_score), float(spec.max_marks))

    grouped: set[str] = set()
    counted: set[str] = set(canonical)
    choice_candidate_qids: set[str] = set()
    for group in package.choice_groups:
        options: list[tuple[object, list[str], tuple[int, int], bool]] = []
        for option in group.options:
            option_qids = [normalize_qid(qid) for qid in option.question_ids]
            grouped.update(option_qids)
            present = []
            positions: list[tuple[int, int]] = []
            for qid in option_qids:
                location = locations.get(qid)
                if qid not in rows_by_qid:
                    if (answer_map is not None and location is not None
                            and getattr(location, "status", "") == "located"):
                        raise StrictGradeError(
                            "QUESTION_NOT_GRADED",
                            f"The answer map located {canonical[qid].qid}, but the grader omitted it.",
                        )
                    continue
                if not _mapped_attempt(rows_by_qid[qid]):
                    continue
                present.append(qid)
                position = (getattr(location, "first_position", None)
                            if location is not None and
                            getattr(location, "status", "") == "located" else None)
                if position is not None:
                    positions.append((int(position[0]), int(position[1])))
            if present:
                fallback = min(report_order[qid] for qid in present)
                options.append((option, present,
                                min(positions) if positions else (10**9, fallback),
                                bool(positions) or answer_map is None))
        # Fewer attempted options is a legitimate blank worth zero, not a structural
        # grading failure. Physical order matters only when the student over-attempted
        # and the fixed first-attempted policy must discard a surplus option.
        if (group.selection_policy == "first_attempted"
                and len(options) > group.select_count
                and any(not has_position for _option, _present, _order, has_position
                        in options)):
            raise StrictGradeError(
                "CHOICE_ORDER_UNRESOLVED",
                f"{group.group_id} has surplus attempts whose physical order is unresolved.",
            )
        if group.selection_policy == "best_score":
            options.sort(
                key=lambda pair: (
                    -sum(float(rows_by_qid[qid].score) for qid in pair[1]),
                    pair[2],
                )
            )
        else:
            # 'first_attempted' is the normal examiner rule. 'explicit' is accepted
            # only when the answer map exposes exactly the required number of options;
            # otherwise no unambiguous explicit selection exists in this schema.
            if group.selection_policy == "explicit" and len(options) != group.select_count:
                raise StrictGradeError(
                    "EXPLICIT_CHOICE_AMBIGUOUS",
                    f"{group.group_id} needs an explicit selection, but {len(options)} "
                    f"options contain mapped answers.",
                )
            options.sort(key=lambda pair: pair[2])
        selected = options[:group.select_count]
        selected_qids = {
            normalize_qid(qid)
            for option, _, _, _ in selected
            for qid in option.question_ids
        }
        candidate_options = options if group.selection_policy == "best_score" else selected
        choice_candidate_qids.update(
            normalize_qid(qid)
            for option, _, _, _ in candidate_options
            for qid in option.question_ids
            if normalize_qid(qid) in rows_by_qid
        )
        counted.difference_update(
            normalize_qid(qid) for option in group.options for qid in option.question_ids
        )
        if finalize_choices:
            counted.update(selected_qids)
        else:
            counted.update(
                normalize_qid(qid)
                for option, _, _, _ in options
                for qid in option.question_ids
            )

    missing = [key for key, question in canonical.items()
               if key in counted and key not in seen]
    if missing:
        # A missing model row is not proof that the student left it blank, so this
        # used to hold the whole copy. That threw away good copies: a CA Corporate
        # Laws script came back ungradable because the grader wrote no row for Q1.c
        # and Q3.b - the two sub-parts the student left blank - while Q1.a, Q1.b and
        # Q3.a were all marked correctly. Nobody reviews a held copy, so the student
        # got nothing for a paper that was fully and correctly marked. Three other
        # runs of that same script released, which is the tell: whether the grader
        # bothers to emit a zero row for a blank sub-part is not stable.
        #
        # `find_unmarked_answers` already settled what such a row means, on this very
        # pair of questions: "a sub-part whose SIBLING was attempted is already
        # explained by that sibling's writing... Leaving some sub-parts blank is
        # ordinary, and on a choice paper it is the norm." Apply that here. A blank
        # part of a question the student DID answer is theirs to lose, at the mark
        # the paper prints for it.
        #
        # What still holds the copy: a question with no attempted sibling anywhere.
        # Nothing explains those, and an entire answer the grader never read must not
        # be silently zeroed - that is the Chemistry copy this gate exists for. An
        # answer the map LOCATED is refused earlier still, before we ever get here.
        attempted_tops = {
            _top(question.qid) for question in report.questions
            if bool(getattr(question, "attempted", True))
        }
        pages = {}
        for question in report.questions:
            top = _top(question.qid)
            pages[top] = max(pages.get(top, 1), int(getattr(question, "page", 1) or 1))
        booked, unexplained = [], []
        for key in missing:
            spec = canonical[key]
            if _top(spec.qid) in attempted_tops:
                booked.append(spec)
            else:
                unexplained.append(spec.qid)
        if unexplained:
            raise StrictGradeError(
                "QUESTION_NOT_GRADED",
                "The grader omitted package question(s): " + ", ".join(unexplained[:12]),
            )
        for spec in booked:
            report.questions.append(QuestionMark(
                qid=spec.qid, score=0.0, max_score=float(spec.max_marks),
                page=pages.get(_top(spec.qid), 1), y_fraction=0.5,
                remark="Not attempted", attempted=False,
            ))
        # Said out loud, not silently: this is a claim about the student's script,
        # and the copy carries it as "Not attempted" for them to dispute.
        print(f"[reconcile] booked blank sub-part(s) of answered questions as 0: "
              f"{', '.join(spec.qid for spec in booked)}", flush=True)

    if package.choice_groups and finalize_choices:
        # Rows from extra attempted alternatives remain useful evidence internally but
        # are not part of the official counted script. Do not turn them into phantom
        # zero-max questions on the student copy.
        report.questions = [q for q in report.questions
                            if normalize_qid(q.qid) in counted]
    report.max_total = (
        float(package.total_marks) if package.choice_groups
        else round(sum(float(q.max_score) for q in report.questions), 2)
    )
    # A choice paper's denominator is the OFFICIAL total; skipping an alternative
    # costs the student those marks, it does not shrink the paper. Log what decided
    # it, because a copy that came out "of 40 but the paper is 50" gave no way to
    # tell whether the package lost its choice rules or the counting did.
    print(f"[choice] groups={len(package.choice_groups)} "
          f"package_total={float(package.total_marks):g} "
          f"counted={len(report.questions)} rows_sum="
          f"{sum(float(q.max_score) for q in report.questions):g} "
          f"-> max_total={float(report.max_total):g}", flush=True)
    report.total_score = round(sum(float(q.score) for q in report.questions), 2)
    report.unverified_objective_qids = unverified_objective_qids
    report.choice_candidate_qids = [canonical[qid].qid for qid in sorted(choice_candidate_qids)]
    if (finalize_choices
            and abs(float(report.max_total) - float(package.total_marks)) > 0.25):
        raise StrictGradeError(
            "OFFICIAL_TOTAL_MISMATCH",
            f"Counted questions total {report.max_total:g}; official package total is "
            f"{package.total_marks:g}. Strict mode will not force or rescale it.",
        )

    section_totals: dict[str, dict] = {}
    for question in report.questions:
        top = _top(question.qid)
        row = section_totals.setdefault(
            top, {"qid": top.upper(), "score": 0.0, "max_score": 0.0})
        row["score"] += float(question.score)
        row["max_score"] += float(question.max_score)
    report.section_totals = [
        {"qid": row["qid"], "score": round(row["score"], 2),
         "max_score": round(row["max_score"], 2)}
        for _, row in sorted(section_totals.items())
    ]
    report.maxima_rescaled = False
    return report


@dataclass(frozen=True)
class ReleaseDecision:
    release: bool
    blockers: tuple[PackageIssue, ...]

    def as_dict(self) -> dict:
        return {
            "release": self.release,
            "blockers": [issue.model_dump(mode="json") for issue in self.blockers],
        }


def automatic_release_decision(
    *,
    package: ExamPackage,
    report,
    preflight_issues,
    cost,
    budget_cap_inr: float,
    mandatory_verification_complete: bool,
) -> ReleaseDecision:
    """One fail-closed decision after every automatic stage has completed."""
    blockers = list(package.blockers())
    # With no reviewer, an actionable warning has nowhere to go. Strict mode upgrades
    # both warning and block severities to a hold; truly observational telemetry must
    # use a separate non-actionable severity.
    blockers.extend(PackageIssue(
        severity="block",
        code=str(getattr(issue, "code", "preflight")),
        detail=str(getattr(issue, "detail", issue)),
    ) for issue in (preflight_issues or [])
      if str(getattr(issue, "severity", "")).lower() in {"warn", "block"})
    if getattr(report, "maxima_rescaled", False):
        blockers.append(PackageIssue(
            severity="block", code="maxima-rescaled",
            detail="Strict automatic grading cannot release proportionally rescaled maxima.",
        ))
    inconsistent_attempts = [
        str(getattr(question, "qid", "") or "?")
        for question in list(getattr(report, "questions", None) or [])
        if (not bool(getattr(question, "attempted", True))
            and (float(getattr(question, "score", 0.0) or 0.0) > 1e-9
                 or any(float(getattr(item, "awarded", 0.0) or 0.0) > 1e-9
                        for item in list(getattr(question, "criteria", None) or []))))
    ]
    if inconsistent_attempts:
        blockers.append(PackageIssue(
            severity="block", code="unattempted-answer-has-credit",
            detail=("Credit was recorded for answer(s) still classified as unattempted: "
                    + ", ".join(inconsistent_attempts[:12]) + "."),
        ))
    if not getattr(cost, "rates_found", False):
        blockers.append(PackageIssue(
            severity="block", code="unpriced-provider-call",
            detail="At least one provider call has no configured price, so the budget is unknown.",
        ))
    if not getattr(cost, "usage_attested", False):
        blockers.append(PackageIssue(
            severity="block", code="provider-usage-missing",
            detail=("At least one dispatched provider call returned no token usage; its "
                    "actual bill cannot be proven against the copy budget."),
        ))
    if float(getattr(cost, "total_inr", 0.0)) > float(budget_cap_inr) + 1e-6:
        blockers.append(PackageIssue(
            severity="block", code="budget-exceeded",
            detail=(f"Provider cost Rs.{float(cost.total_inr):.2f} exceeded the hard "
                    f"Rs.{float(budget_cap_inr):g} cap."),
        ))
    if not mandatory_verification_complete:
        blockers.append(PackageIssue(
            severity="block", code="verification-incomplete",
            detail="Mandatory automatic evidence verification did not complete.",
        ))
    # Deduplicate identical codes/details so the UI gets a concise machine verdict.
    unique: dict[tuple[str, str], PackageIssue] = {
        (issue.code, issue.detail): issue for issue in blockers
    }
    result = tuple(unique.values())
    return ReleaseDecision(release=not result, blockers=result)
