"""Strict package-bound compilation and automatic release checks.

Legacy reconciliation intentionally contains recovery heuristics.  This module never
re-homes, drops, invents, or proportionally rescales a model decision.  Structural
ambiguity is an UNGRADABLE outcome for the zero-human path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from choice_graph import build_choice_forest
from exam_package import ExamPackage, PackageIssue, normalize_qid, resolve_qid
from grader import QuestionMark, _STUDENT_QLABEL_RE
from mathpix import readable_ocr_text


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
            "anchor": str(getattr(question, "anchor_line_id", "") or ""),
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
                    "quote": str(getattr(item, "anchor_quote", "") or ""),
                    "rubric_id": str(getattr(item, "rubric_criterion_id", "") or ""),
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
# "Q1 -> B] Capital introduced in Kind", "Q5 -> C) Income & Expenditure": the option's
# letter first after the label (and an arrow), with only its closing bracket. MEASURED
# 2026-10-22 (Maharashtra HSC Book-keeping): every MCQ written so read as no answer.
# Letters only: "1)" after a label is far more often a sub-part than option A, and so
# is a small "a)" ("1) a) 3" is part a's answer 3); a small letter counts with "]".
# The label may lack its punctuation ("Q7 D] 71000"); its number must still match.
_HALF_BRACKET_OPTION_RE = re.compile(
    r"^\s*(?:[Qq](?:uestion)?\s*(\d{1,3})\s*)?(?:[-=]*>|→|:-|[:\-])?\s*"
    r"(?:([A-D])\s*[\])]|([a-d])\s*\])(?=\s|$)")
_RAW_LEADING_OPTION_RE = re.compile(r"^\s*[\[(]\s*([A-Za-z0-9])\s*[\])]", re.IGNORECASE)
# "b) 2) 520 units" / "a) i) 21575500": the part's own letter, then the option number
# with a half bracket, then the value. MEASURED 2026-10-09 (CA Inter CMA Master Test 04,
# live): every MCQ row of a readable page came back "could not be read", because the
# option sat between the part label and the value with no opening bracket, and a
# correct 15-mark Part A scored 2. A bare "2) 217.99" stays ambiguous (question 2 or
# option 2) and is left to the written-value match.
_SUBLABEL_OPTION_RE = re.compile(
    r"^\s*\(?\s*(?:[a-eA-E]|i{1,3}|iv|v)\s*[).]{1,2}\s*\(?\s*([1-4]|i{1,3}|iv)\s*[).](?:\s+\S|\s*$)",
    re.IGNORECASE)
_ROMAN_OPTION = {"i": "A", "ii": "B", "iii": "C", "iv": "D"}


# One option token as a model spells it: "B", "b", "(b)", "[B]", "B)", "option b",
# "Option (B)", "2". Commentary may follow in brackets or after a stop, colon or
# dash. A comma or slash does not count: "B, C" and "B/C" name two options.
_MODEL_CHOICE_RE = re.compile(
    r"^\s*(?:opt(?:ion)?\.?\s*)?[\[(]?\s*([A-Da-d1-4])\s*[\])]?"
    r"\s*(?:$|[(\[.:\-–—])",
    re.IGNORECASE,
)


def _choice_letter(value: str) -> str:
    """The single A-D option a model or reader named, or "".

    MEASURED on the ICSE Class 9 SST batch of 2026-09-14: the key-blind reader
    returned "b" for one booklet and the option's WORDS ("Indica", "25 years") for
    another, and the grading pass returned "B (STUDENT WROTE SAMUDRAGUPTA)". None of
    those equalled a bare capital, so every one either crashed the sheet or broke the
    two-reader agreement. The letter is recovered here; an option's words are never
    passed through as if they were a letter (``option_named_by_words`` reads those).
    """
    match = _MODEL_CHOICE_RE.match(str(value or ""))
    if not match:
        return ""
    return _norm_letter(match.group(1))


# A bracketed "R" is the Reason of an assertion-reason item. No A-D paper offers
# an option R, so this token never competes with a choice - it identifies one.
_ASSERTION_REF_RE = re.compile(r"\(\s*R\s*\)", re.IGNORECASE)
# Assertion-reason prose, with or without brackets: "Both (A) and (R) are true",
# "the correct explanation of A".
_ASSERTION_PROSE_RE = re.compile(
    r"\(\s*R\s*\)|\bboth\s+\(?\s*A\s*\)?\s*(?:and|&)\s*\(?\s*R\b"
    r"|\bexplanation\s+of\s+\(?\s*A\b", re.IGNORECASE)
# ...in which an "A" that a statement follows names the Assertion, not option A:
# "(A) is true but (R) is false" is option (C)'s words. An "(A)" that opens the
# answer and is followed by the option's words ("(A) Both (A) and ...") is a choice.
_ASSERTION_A_REF_RE = re.compile(
    r"(?:\(\s*[Aa]\s*\)|(?<![\w(])A(?![\w)]))"
    r"(?=\s*(?:(?:is|are|was|were|and|but)\b|[&,.]|$))")


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
    if re.fullmatch(
            r"\s*(?:ans(?:wer)?|q(?:uestion)?)\s*[.:–—\-]?\s*\d{1,3}\s*[.):]?\s*",
            value, re.IGNORECASE):
        # A standalone 'Answer - 2' owns the following answer, but its question
        # number is not option 2. Never turn these labels into A/B/C/D choices,
        # with or without the question number: the answer map's label lines are
        # read with none. MEASURED on a Parishram Political Science copy (8 Oct
        # 2026): "Answer. 1" over "(B) Jawaharlal Nehru" was scored as option A,
        # and Q1 to Q3 lost three correct answers.
        return ""
    part_option = _SUBLABEL_OPTION_RE.match(value)
    if part_option:
        token = part_option.group(1).lower()
        return _ROMAN_OPTION.get(token) or "ABCD"[int(token) - 1]
    label = _ANSWER_NUMBER_RE.match(value)
    if label:
        if question_number is not None and int(label.group(1)) != question_number:
            return ""
        value = value[label.end():]
    # MEASURED 2026-10-22 (Maharashtra HSC Economics): "Both (A) and (R) are true,
    # and (R) is the correct explanation" - option (D)'s words - was read as option A
    # and crossed. The Assertion's (A) is taken out first; with no letter left the
    # line names no letter, and the option's words decide (option_named_by_words).
    assertion_reason = bool(_ASSERTION_PROSE_RE.search(value))
    if assertion_reason:
        value = _ASSERTION_A_REF_RE.sub(" ", value)
    tokens = [_choice_letter(match.group(1)) for match in _PAREN_OBJECTIVE_RE.finditer(value)]
    tokens = [token for token in tokens if token in {"A", "B", "C", "D"}]
    if len(set(tokens)) == 1:
        return tokens[0]
    half = _HALF_BRACKET_OPTION_RE.match(value) if not tokens else None
    if half and not (half.group(1) and question_number is not None
                     and int(half.group(1)) != question_number):
        return (half.group(2) or half.group(3)).upper()
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
    if len(set(tokens)) > 1:
        # A statement combination is a whole written value, not the last token.
        # Its printed option must be matched separately; conflicting guesses
        # without a matching combination remain unresolved.
        return ""
    if assertion_reason:
        return ""      # a letter closing the prose ("...explanation of A") is the Assertion
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
    paper_options: dict[int, dict[str, str]] | None = None,
) -> list[str]:
    """Bind each package-declared MCQ to its own numbered OCR line.

    This is the invisible answer-box boundary for dense objective sections.  The
    grader may suggest an anchor, but Q5 is allowed to use only a line beginning
    with ``5)``; it can never borrow Q6's line and make that answer look both right
    and wrong.  When several candidates exist, the grader's current location is
    used only to choose the nearest candidate with the correct number.

    A line whose WORDS name one of that question's printed options ("2) Tamil")
    is an answer line too, even with no option letter on it.
    """
    paper_options = paper_options or {}
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
        options_here = paper_options.get(number, {})

        def answers_here(text: str) -> bool:
            return bool(_objective_choice_from_line(text, number)
                        or (options_here and option_named_by_words(text, options_here)[0]))

        candidates = []
        for page_number, index, line in rows:
            text = str(getattr(line, "text", "") or "")
            label = _ANSWER_NUMBER_RE.match(text)
            if label and int(label.group(1)) == number and answers_here(text):
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


def effective_selection_policy(group, package) -> str:
    """How extra attempts of an "answer any N" group are resolved for this paper.

    A school or commerce paper keeps the answers that earn the most: PW's own
    papers say "If a student has attempted an extra question, answer of the
    question deserving more marks should be retained". MEASURED 2026-09-29: under
    the first-attempted rule a Radiant Physics copy counted Q7 (1/2) and Q8 over a
    fully correct Q9 and Q10, and a Biology copy dropped a correct Q9. A CA paper
    keeps ICAI's order rule: only the first answers written are evaluated.
    """
    # The paper's own printed rule wins: "If more than 15 questions are answered,
    # then only the first 15 will be evaluated" (Uday Bihar Board English, 10 Oct
    # 2026). Under best score a student who answered all twenty had Q20 counted and
    # Q15 dropped, where the faculty marked the first fifteen.
    printed = str(getattr(group, "printed_rule", "") or "")
    if printed:
        return printed
    policy = str(getattr(group, "selection_policy", "") or "first_attempted")
    if policy == "first_attempted" and str(getattr(package, "department", "") or "") != "ca":
        return "best_score"
    return policy


def option_texts_from_paper(question_paper_text: str) -> dict[int, dict[str, str]]:
    """{question number: {"A": option text, ...}} read off the official paper."""
    text = str(question_paper_text or "")
    if not text.strip():
        return {}
    # Split into per-question blocks on a line that is (or starts with) a number.
    # A block ends where the NEXT question's label begins. Ending it after that label
    # instead glued every paper's last option to the next number ("Pali\nQ3"), which
    # then matched a student's "Pali" worse than any other option.
    found: list[tuple[int, int, int, bool]] = []
    for match in _PAPER_QUESTION_RE.finditer(text):
        number = match.group(1) or match.group(2)
        if number:
            labelled = match.group(0).lstrip().upper().startswith("Q")
            found.append((int(number), match.start(), match.end(), labelled))
    # A bare number on its own line is often part of a formula ("3+√7" is typeset
    # with the 7 alone on a line). MEASURED 2026-09-30 on the Radiant Class 9 Maths
    # paper: that 7 was read as question 7, Q3's options were filed under Q7, and a
    # wrong MCQ's remark named another question's option. A paper that labels its
    # questions "Q1" is read by those labels only, and a question number must come
    # after the one before it.
    if sum(1 for *_x, labelled in found if labelled) >= 3:
        found = [f for f in found if f[3]]
    else:
        # Bare numbers alone on a line are also each question's printed mark ("1",
        # "2") and pieces of typeset formulas ("16", "49", "90"). A paper whose
        # questions read "1." / "2)" is read by those labels only.
        punctuated = [f for f in found if re.search(r"\d\s*[.)]", text[f[1]:f[2]])]
        if len(punctuated) >= 5:
            found = punctuated
    # MEASURED 2026-10-01 (CBSE Class 9 SST Test-03): the General Instructions are
    # numbered 1 to 8 above Section A, so the real "1." to "8." were skipped as not
    # ascending, instruction 8's block swallowed Q1 to Q8, Q1's options were filed
    # under 8 and the Q8 remark read "Correct answer is (A) Troposphere". A list
    # that starts again at 1 followed by 2 is a new numbering; the run before it
    # ends there.
    starts: list[tuple[int, int, int]] = []
    for position, (number, begin, end, _labelled) in enumerate(found):
        restart = (number == 1 and len(starts) >= 3
                   and position + 1 < len(found) and found[position + 1][0] == 2)
        if restart:
            starts.append((0, begin, begin))      # closes the previous run's last block
        elif starts and number <= starts[-1][0]:
            continue
        starts.append((number, begin, end))
    out: dict[int, dict[str, str]] = {}
    for index, (number, _label_start, begin) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        block = text[begin:end]
        combination_instruction = re.search(
            r"choose\s+the\s+correct\s+combination\s*:?", block, re.IGNORECASE)
        if combination_instruction:
            # Statement labels (A)-(D) are not the later option labels. This
            # explicit instruction separates the two lists on statement MCQs.
            block = block[combination_instruction.end():]
        # "Assertion (A): ..." and "Reason (R): ..." name the statements, not options,
        # and "(A) Both (A) and (R) are true" mentions (A) inside option A: options
        # come in order A, B, C, D, so a marker out of that order is option text.
        runs: list[list] = [[]]
        for m in _PAPER_OPTION_RE.finditer(block):
            if combination_instruction and block[block.rfind("\n", 0, m.start()) + 1:m.start()].strip():
                # Inner (B),(C),(D) symbols are the option VALUE, not new options.
                # Only the printed label at the beginning of a line opens one.
                continue
            if block[m.end():m.end() + 4].lstrip().startswith(":"):
                continue
            run = runs[-1]
            if len(run) < 4 and _norm_letter(m.group(1)) == "ABCD"[len(run)]:
                run.append(m)
            elif len(run) == 4 and _norm_letter(m.group(1)) == "A":
                runs.append([m])          # the same four options again, in a second language
        marks = runs[0]
        # A bilingual paper prints each question's options in Hindi, then English. Its
        # text layer garbles Devanagari, and a wrong MCQ's remark read "Correct answer
        # is (B) लुका िैससयोली" (Uday Bihar Board Accountancy, 10 Oct 2026). The run in
        # Latin script is used when the first one is not.
        def latin_share(run) -> float:
            line_end = block.find("\n", run[-1].end())
            span = block[run[0].start():line_end if line_end >= 0 else len(block)]
            latin = len(re.findall(r"[A-Za-z]", span))
            other = len(re.findall(r"[\u0900-\u097F]", span))
            return latin / max(1, latin + other)
        later = [run for run in runs[1:] if len(run) >= 2 and latin_share(run) >= 0.8]
        if later and len(marks) >= 2 and latin_share(marks) < 0.5:
            marks = later[0]
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
            # The question's printed mark on the line after its last option
            # ("Thermosphere\n1"); a stacked fraction ("25\n4") in an earlier option stays.
            if position == len(marks) - 1:
                body = re.sub(r"\s*\n\s*[1-5]\s*$", "", body).strip()
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


# --- an answer written in WORDS instead of a letter ----------------------------
#
# MEASURED on the ICSE Class 9 SST Subjective Test-06 batch of 2026-09-14. Students
# answer an MCQ in words at least as often as by letter, and put the letter
# anywhere or nowhere:
#
#     1) Indica (b)        Q3. [3B] Fold Mountain        Q.5 [C] 25yrs      2) Tamil
#
# The strict gate read only the letter, so a booklet with five correct answers could
# not be scored at all. The words ARE the answer: when they name exactly one printed
# option, that option is what the student chose.

# An option marker anywhere on the line: "(b)", "[B]", "(2)", "(option b)",
# "option (A)", "opt. B". "[3B]" is a digit the student overwrote with B.
_OPTION_MARKER_RE = re.compile(
    r"\(\s*option\s*[A-Da-d1-4]\s*\)"
    r"|\bopt(?:ion)?\.?\s*[\[(]?\s*[A-Da-d1-4]\s*[\])]?(?![A-Za-z0-9])"
    r"|[\[(]\s*[A-Da-d1-4]{1,2}\s*[\])]",
    re.IGNORECASE,
)
_WORDS_MATCH_MIN = 0.80      # strict: the words may choose the option
_WORDS_MATCH_MARGIN = 0.15   # ...and must stand clear of every other option


def _answer_words(line_text: str) -> str:
    """What the student wrote on an answer line, without its label or option marker."""
    value = readable_ocr_text(str(line_text or ""))
    label = _STUDENT_QLABEL_RE.match(value)
    if label:
        value = value[label.end():]
    value = _OPTION_MARKER_RE.sub(" ", value)
    return " ".join(value.split()).strip(" .:;,-–—=")


def _partial_ratio(target: str, written: str) -> float:
    """Best similarity of ``target`` to any same-length stretch of ``written``."""
    from difflib import SequenceMatcher

    if len(written) <= len(target) + 1:
        return SequenceMatcher(None, written, target).ratio()
    best = 0.0
    for size in (len(target) - 1, len(target), len(target) + 1):
        if size < 3:
            continue
        for start in range(0, len(written) - size + 1):
            best = max(best, SequenceMatcher(
                None, written[start:start + size], target).ratio())
    return best


def _option_scores(words: str, options: dict[str, str]) -> list[tuple[float, str]]:
    """How closely the written words match each printed option, best first."""
    written = _compact(words)
    if len(written) < 3:
        return []
    written_digits = set(re.findall(r"\d+", words))
    scored: list[tuple[float, str]] = []
    for letter, body in (options or {}).items():
        target = _compact(body)
        if len(target) < 2:
            continue
        # "25 years" and "21 years" differ only in digits, which fuzzy matching
        # barely weighs. A number printed in the option must be the student's.
        option_digits = set(re.findall(r"\d+", body))
        if option_digits and not option_digits <= written_digits:
            scored.append((0.0, letter))
            continue
        if len(target) >= 4 and target in written:
            score = 1.0
        elif len(target) >= 5:
            score = _partial_ratio(target, written)
        else:
            from difflib import SequenceMatcher
            score = SequenceMatcher(None, written, target).ratio()
        scored.append((score, letter))
    return sorted(scored, reverse=True)


def _statement_combination_tokens(value: str) -> tuple[str, ...]:
    """A whole symbolic statement combination, never a first/last option letter."""
    text = readable_ocr_text(str(value or ""))
    label = _STUDENT_QLABEL_RE.match(text)
    if label:
        text = text[label.end():]
    else:
        # Number + answer label is a common handwritten form ("4 Ans ...").
        text = re.sub(r"^\s*\d{1,3}\s+(?:ans(?:wer)?|otns)\b\s*", "", text,
                      flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:ans(?:wer)?|otns)\b\s*[:.\-]?\s*", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\band\b", ",", text, flags=re.IGNORECASE)
    tokens = tuple(match.group(0).upper() for match in re.finditer(
        r"(?<![A-Za-z0-9])[A-D](?![A-Za-z0-9])", text, re.IGNORECASE))
    residue = re.sub(r"(?<![A-Za-z0-9])[A-D](?![A-Za-z0-9])", "", text,
                     flags=re.IGNORECASE)
    if (len(tokens) < 2 or re.sub(r"[\s()\[\],;.&:\-]", "", residue)
            or len(set(tokens)) != len(tokens)):
        return ()
    return tokens


def _statement_combination_choice(value: str, options: dict[str, str]) -> tuple[str, float]:
    tokens = _statement_combination_tokens(value)
    if not tokens:
        return "", 0.0
    matches = [letter for letter, body in options.items()
               if (other := _statement_combination_tokens(body))
               and set(tokens) == set(other)]
    return (matches[0], 1.0) if len(matches) == 1 else ("", 0.0)


def option_named_by_words(line_text: str, options: dict[str, str]) -> tuple[str, float]:
    """The printed option whose WORDS the student wrote, and how close the match is.

    ("", score) when the words name no option, or name two ("Graben (Rift Valley)"
    against a paper that prints both Graben and Rift Valley is not an answer).
    """
    # These bracketed symbols ARE the answer value, not removable option markers.
    # Match the complete set exactly; a subset or ambiguous duplicate never wins
    # a fuzzy comparison against the closest complete combination.
    if _statement_combination_tokens(line_text):
        return _statement_combination_choice(line_text, options)
    scored = _option_scores(_answer_words(line_text), options)
    if not scored:
        return "", 0.0
    best, letter = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best >= _WORDS_MATCH_MIN and best - runner_up >= _WORDS_MATCH_MARGIN:
        return letter, best
    return "", best


def _observed_letter(observation, options: dict[str, str]) -> str:
    """A key-blind active answer; a cancelled label cannot outrank its replacement."""
    raw = str(getattr(observation, "chosen_option", "") or "")
    written = str(getattr(observation, "written_answer", "") or "")
    kind = str(getattr(observation, "answer_kind", "") or "")
    state = str(getattr(observation, "selection_state", "") or "")
    cancelled = str(getattr(observation, "cancelled_answer", "") or "")
    if state == "multiple_active":
        return ""
    written_combination = bool(_statement_combination_tokens(written))
    raw_combination = bool(_statement_combination_tokens(raw))
    if kind == "statement_combination" or written_combination or raw_combination:
        mapped, _ = _statement_combination_choice(written if written_combination else raw, options)
        # An explicit option label beside a combination must agree with its value.
        # A legacy single-letter guess never outranks a copied combination.
        if kind == "option_label" and not raw_combination:
            letter = _choice_letter(raw)
            if letter and letter != mapped:
                return ""
        return mapped
    letter = _choice_letter(raw)
    # A copied '(b.)' is a common handwriting convention, not another choice.
    # Parse only the separately transcribed ACTIVE answer; never choose the last
    # symbol from a mixed row that still contains both cancelled and live text.
    active_text = re.sub(r"([\[(]\s*[A-Da-d1-4])\s*[.:]\s*([\])])", r"\1\2",
                         readable_ocr_text(written))
    copied_label = _objective_choice_from_line(active_text)
    labels = {_choice_letter(match.group(1))
              for match in _PAREN_OBJECTIVE_RE.finditer(active_text)}
    if len(labels) > 1 and not copied_label:
        return ""
    exact_words = _compact(_answer_words(active_text))
    exact_values = [key for key, body in options.items()
                    if exact_words and exact_words == _compact(body)]
    words_choice = (exact_values[0] if len(exact_values) == 1 else
                    option_named_by_words(active_text, options)[0] if written and options else "")
    if copied_label and words_choice and copied_label != words_choice:
        return ""  # conflicting uncancelled label/value, not a correction proof
    active = copied_label or words_choice
    if letter and active and letter != active:
        cancelled_text = re.sub(
            r"([\[(]\s*[A-Da-d1-4])\s*[.:]\s*([\])])", r"\1\2",
            readable_ocr_text(cancelled))
        cancelled_labels = {_choice_letter(match.group(1))
                            for match in _PAREN_OBJECTIVE_RE.finditer(cancelled_text)}
        cancelled_choice = _choice_letter(cancelled) or _objective_choice_from_line(cancelled_text)
        if (state == "replacement" and cancelled.strip()
                and (letter in cancelled_labels or letter == cancelled_choice)):
            return active
        return ""  # copied live answer contradicts the reader's guessed label
    if state == "replacement" and (not cancelled.strip() or not written.strip()):
        return ""  # cancellation needs separately transcribed surviving evidence
    return active or letter


def apply_official_objective_evidence(
    report, package: ExamPackage, ocr_pages, answer_map=None,
    visual_choices: dict[str, object] | None = None,
    question_paper_text: str = "",
    allow_unconfirmed: bool = False,
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
    uncertain_ocr = {line.line_id for page in ocr_pages or [] for line in page.lines
                     if getattr(line, "confidence", None) is not None and line.confidence < 0.8}
    notes: list[str] = []
    visual_choices = {
        normalize_qid(str(key or "")): value
        for key, value in (visual_choices or {}).items()
    }
    visual_verified = set(
        getattr(report, "objective_visual_verified_qids", None) or [])
    unconfirmed: set[str] = set(
        getattr(report, "objective_unconfirmed_qids", None) or [])
    # Option wording, so an answer written out in words can be recognised.
    paper_options = option_texts_from_paper(question_paper_text)
    notes.extend(bind_official_objective_anchors(
        report, package, ocr_pages, answer_map=answer_map,
        paper_options=paper_options))
    mapped_lines = _mapped_objective_lines(answer_map)
    for question in list(getattr(report, "questions", None) or []):
        spec = specs.get(normalize_qid(getattr(question, "qid", "")))
        if spec is None:
            continue
        correct = _objective_letter(spec.objective_answer or "")
        anchor = str(getattr(question, "anchor_line_id", "") or "").strip()
        number_match = re.fullmatch(r"q(\d{1,3})", normalize_qid(spec.qid))
        number = int(number_match.group(1)) if number_match else None
        options_here = paper_options.get(number, {}) if number is not None else {}
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
        letter_choice = chosen
        # The words on the line, read against this question's printed options.
        words_choice = (option_named_by_words(line_text, options_here)[0]
                        if line_text and options_here else "")
        ocr_value_conflict = bool(letter_choice and words_choice
                                  and letter_choice != words_choice)
        if not chosen and words_choice:
            chosen = words_choice
            notes.append(f"{spec.qid}: no option letter on the line; its words "
                         f"name option {words_choice}")
        # A letter and words naming the SAME option are two readings of one line: a
        # misread glyph does not also produce that option's printed words.
        ocr_corroborated = bool(letter_choice) and letter_choice == words_choice
        # A checkbox/table region may be classified by Mathpix as a diagram with
        # no text at all.  In that case require TWO independent vision readings:
        # the primary examiner's chosen_option and a separate provider's key-blind
        # observation.  Agreement transcribes the student's choice; the official
        # key still determines the score in code.  One model alone never suffices.
        used_visual_consensus = False
        words_settled = False
        observation = visual_choices.get(normalize_qid(spec.qid))
        if observation is not None and bool(
                getattr(observation, "clear", False)):
            primary_choice = _choice_letter(
                str(getattr(question, "chosen_option", "") or ""))
            independent_choice = _observed_letter(observation, options_here)
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
                if not allow_unconfirmed:
                    raise StrictGradeError(
                        "OBJECTIVE_VISUAL_DISAGREEMENT",
                        f"Two visual readers disagree on {spec.qid}; no score was released.",
                    )
                # Last resort, after a second key-blind read failed to settle it.
                # The student's own answer line outranks either reader: it is what
                # they actually wrote. MEASURED 2026-09-16 on 849bbe95, whose line
                # reads "1) Indica (b)" - the page said B, the grading pass said B,
                # one reader said A for every row, and the copy was refused.
                fallback = letter_choice or words_choice or primary_choice
                if (words_choice and letter_choice != words_choice
                        and words_choice in {primary_choice, independent_choice}):
                    # The option's own words on the line and one reader name the
                    # same option; the other reader and the OCR glyph are the two
                    # readings of one unclear letter. MEASURED on a Parishram
                    # Geography copy (8 Oct 2026): "(D) Roca - Western Europe" was
                    # OCR'd "(1)", one reader said A, and a correct answer got 0.
                    fallback = words_choice
                    words_settled = True
                notes.append(
                    f"{spec.qid}: readers disagree ({primary_choice} vs "
                    f"{independent_choice}); used {fallback} from "
                    + ("the option's words written on the line" if words_settled
                       else "the student's answer line" if letter_choice or words_choice
                       else "the grading pass")
                    + " — please check this one")
                chosen = fallback
                unconfirmed.add(spec.qid)
            elif (anchor and independent_choice in {"A", "B", "C", "D"}
                  and independent_choice in {letter_choice, words_choice}):
                # The grading pass named no usable option (live: "?" on 39b55d4d's
                # Q3, whose "[3B]" was overwritten), but the key-blind reader and the
                # OCR of the owned answer line read the same one independently.
                chosen = independent_choice
                used_visual_consensus = True
                visual_verified.add(spec.qid)
                notes.append(
                    f"{spec.qid}: {chosen} confirmed by a key-blind visual read and "
                    "the OCR of its answer line")
        if ocr_value_conflict and not used_visual_consensus and not words_settled:
            # An OCR glyph that parses is not enough when the same owned line's
            # unique printed option value names a different choice. Neither the
            # letter nor a correct-key word match may silently decide the answer.
            if not allow_unconfirmed:
                raise StrictGradeError(
                    "OBJECTIVE_ANSWER_UNRESOLVED",
                    f"{spec.qid}: anchored OCR label and option words disagree; "
                    "requires a key-blind active-answer reading.")
            unconfirmed.add(spec.qid)
            visual_verified.discard(spec.qid)
            notes.append(f"{spec.qid}: OCR label {letter_choice} and written value "
                         f"{words_choice} disagree — please check this one")
        if (getattr(report, "evidence_contract", False) and anchor in uncertain_ocr
                and not used_visual_consensus and spec.qid not in visual_verified
                and not ocr_corroborated):
            if not allow_unconfirmed:
                raise StrictGradeError("OBJECTIVE_ANSWER_UNRESOLVED",
                                       f"{spec.qid}: low-confidence OCR requires independent visual reading.")
            unconfirmed.add(spec.qid)
        if not correct:
            # With no official answer there is nothing to score against. This used
            # to stop the whole copy as a package fault; now the row keeps the
            # grader's own decision, is named to the teacher, and the copy goes on
            # (operator's rule, 2026-09-19: no copy is lost, it goes to the student
            # or to faculty review).
            unconfirmed.add(spec.qid)
            notes.append(f"{spec.qid}: the official key gives no option for this "
                         "question, so the grader's own marking stands — please check this one")
            continue
        if (not chosen
                or (not anchor and not used_visual_consensus
                    and spec.qid not in visual_verified)):
            if not allow_unconfirmed:
                raise StrictGradeError(
                    "OBJECTIVE_ANSWER_UNRESOLVED",
                    f"{spec.qid} has no unambiguous anchored A-D/1-4 answer on the student page.",
                )
            # Nothing on the page could be read for this row. Score what the readers
            # did see rather than losing every other mark on the copy, and say so.
            observed = visual_choices.get(normalize_qid(spec.qid))
            if getattr(package, "department", "") == "ca":
                # A CA copy takes only a KEY-BLIND reading here. The grading pass has
                # the key in front of it, and on an unreadable row it reports the
                # key's letter. MEASURED 2026-10-09 (CA live check): the key-blind
                # reader saw nothing on rows 1, 2, 3, 5, 6 of a Taxation copy and the
                # grading pass "read" the key's letter on every one, so two blank rows
                # got 2 marks each and ticks. Six blank rows on an Auditing copy were
                # credited the same way. The option's own words written on the line
                # can still earn the mark below.
                chosen = chosen or (
                    _observed_letter(observed, options_here)
                    if observed is not None and bool(getattr(observed, "clear", False)) else "")
            else:
                chosen = chosen or _choice_letter(
                    str(getattr(question, "chosen_option", "") or "")) or (
                    _observed_letter(observed, options_here) if observed is not None else "")
            unconfirmed.add(spec.qid)
            notes.append(f"{spec.qid}: the option could not be read from the page; "
                         + (f"used {chosen} from the examiners' reading"
                            if chosen else "scored 0")
                         + " — please check this one")
        # The letter disagrees with the key. Before scoring a zero, read what the
        # student actually WROTE: if they copied out the correct option's own words,
        # the mark is theirs and the letter is a slip or an OCR misread. Never the
        # other way round — a written value cannot turn a correct letter wrong.
        awarded_choice = chosen
        if (chosen != correct and number is not None
                and not (ocr_value_conflict and not used_visual_consensus
                         and not words_settled)):
            value_text = line_text
            if (used_visual_consensus and observation is not None
                    and getattr(observation, "selection_state", "") == "replacement"):
                # The raw OCR row can still contain a cancelled correct answer.
                # A verified replacement's separately copied LIVE value is the
                # only text eligible for the written-value fallback.
                value_text = str(getattr(observation, "written_answer", "") or "")
            written, ratio = written_value_choice(
                value_text, paper_options.get(number, {}), number)
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
    report.objective_unconfirmed_qids = sorted(unconfirmed)
    return notes


def _half_mark_steps(question, criteria: list, qid: str) -> list:
    """The grader's own step breakdown in half marks, each step keeping its share.

    MEASURED 2026-10-22 (MP Board Core Math, rows 71 and 72): Q8's 2 marks were split
    into three equal steps of 0.67/0.67/0.66, so a student read "0.67" circled beside
    a line and "1.34/2" as the question's mark. School marks come in half marks. The
    maxima are re-split in half marks (largest remainder, the same total), and each
    step's award keeps its share of its maximum, snapped to a half mark.
    """
    def half(value: float) -> bool:
        return abs(float(value) * 2 - round(float(value) * 2)) < 1e-6
    if not criteria or all(half(c.max) and half(c.awarded) for c in criteria):
        return criteria
    from grader import _scale_to_total, _snap_half
    total = sum(float(c.max) for c in criteria)
    maxima = _scale_to_total([float(c.max) for c in criteria], total)
    rebuilt = []
    for criterion, maximum in zip(criteria, maxima):
        share = float(criterion.awarded) / float(criterion.max) if float(criterion.max) > 0 else 0.0
        awarded = min(_snap_half(share * maximum), maximum)
        rebuilt.append(criterion.model_copy(update={
            "max": maximum, "awarded": awarded, "met": awarded >= maximum - 1e-9 and maximum > 0}))
    question.criteria = rebuilt
    print(f"[reconcile] {qid}: steps of " + "/".join(f"{float(c.max):g}" for c in criteria)
          + " re-split in half marks as " + "/".join(f"{m:g}" for m in maxima), flush=True)
    return rebuilt


def validate_question_marks(question, spec, *, evidence_contract=True):
    """Validate one provisional breakdown without mutating scores or step weights.

    Used before automatic regrading and by the final compiler, so recovery cannot
    accept a weaker contract than the one required for releasing the copy.
    """
    criteria = list(getattr(question, "criteria", None) or [])
    if evidence_contract:
        if not math.isfinite(float(question.score)) or not 0 <= question.score <= spec.max_marks:
            raise StrictGradeError("INVALID_AWARD", f"{spec.qid}: award is outside the official range.")
        for criterion in criteria:
            if (not math.isfinite(float(criterion.max)) or not math.isfinite(float(criterion.awarded))
                    or not 0 <= criterion.awarded <= criterion.max):
                raise StrictGradeError("INVALID_AWARD", f"{spec.qid}: invalid criterion award.")
        if (question.attempted and spec.question_type != "objective"
                and spec.max_marks > 1 and not criteria):
            raise StrictGradeError("MISSING_CRITERIA",
                f"{spec.qid}: the attempted answer needs a breakdown covering {spec.max_marks:g} marks.")
    # An objective question is scored from the key's own option, and the caller rewrites
    # its row (objective, correct_option) right after this. The grader answers it with a
    # chosen option and no criteria breakdown, so holding it to a fixed step list fails on
    # a paper whose MCQs happen to carry one. MEASURED 12 Nov 2026: both CA Corporate Law
    # papers number their MCQs Q1-Q8 and their descriptive parts Q1.a-Q4.b, and their Q1-Q7
    # carry a single step each. Every Corporate Law copy raised FIXED_RUBRIC_MISMATCH on an
    # MCQ and the whole paid run was discarded with no report, no PDF and no score. The
    # Taxation papers passed only because their MCQs carry no steps.
    if spec.criteria and bool(getattr(question, "attempted", True)) \
            and spec.question_type != "objective":
        fixed = {c.criterion_id.casefold(): c for c in spec.criteria}
        returned = [c.rubric_criterion_id.strip().casefold() for c in criteria]
        if len(returned) != len(set(returned)) or set(returned) != set(fixed):
            raise StrictGradeError("FIXED_RUBRIC_MISMATCH",
                f"{spec.qid}: every fixed rubric criterion must be graded exactly once.")
        for criterion in criteria:
            rule = fixed[criterion.rubric_criterion_id.strip().casefold()]
            if abs(float(criterion.max) - rule.max_marks) > 1e-6:
                raise StrictGradeError("FIXED_RUBRIC_MISMATCH",
                    f"{spec.qid}: {rule.criterion_id} maximum is immutable ({rule.max_marks:g}).")
            award = float(criterion.awarded)
            if not 0 <= award <= rule.max_marks:
                raise StrictGradeError("FIXED_RUBRIC_MISMATCH", f"{spec.qid}: invalid step award.")
            if rule.mark_increment and abs(award / rule.mark_increment - round(award / rule.mark_increment)) > 1e-6:
                raise StrictGradeError("FIXED_RUBRIC_MISMATCH", f"{spec.qid}: award violates the rubric increment.")
    if evidence_contract and criteria:
        maximum = sum(float(c.max) for c in criteria)
        if abs(maximum - float(spec.max_marks)) > 1e-6:
            raise StrictGradeError("CRITERION_MAXIMUM_MISMATCH",
                f"{spec.qid}: step maxima total {maximum:g}; the paper allocates {spec.max_marks:g}.")


def _graded_start(question) -> tuple[int, int] | None:
    """Where the grader says an answer starts: (page, y in ten-thousandths), or None."""
    try:
        page = int(getattr(question, "page", 0) or 0)
        y = float(getattr(question, "y_fraction", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if page < 1 or not (0.0 <= y <= 1.0):
        return None
    return (page, int(round(y * 10000)))


def reconcile_report_to_package(
    report,
    package: ExamPackage,
    answer_map=None,
    *,
    finalize_choices: bool = True,
    choices_already_finalized: bool = False,
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
        if qid and qid not in canonical:
            # The grader's spelling of a package question (exam_package.resolve_qid):
            # a name is not a mark and never fails the copy on its own.
            resolved = resolve_qid(getattr(question, "qid", ""), [q.qid for q in package.questions])
            if resolved is not None:
                question.qid = resolved
                qid = normalize_qid(resolved)
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
        validate_question_marks(question, spec, evidence_contract=getattr(report, "evidence_contract", False))
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
        if spec.criteria and bool(getattr(question, "attempted", True)):
            fixed = {c.criterion_id.casefold(): c for c in spec.criteria}
            for criterion in criteria:
                # validate_question_marks guarantees this set matches for a graded
                # question, but an objective row is exempt from that check, so a stray
                # id here must be skipped rather than raise KeyError and lose the copy.
                rule = fixed.get(criterion.rubric_criterion_id.strip().casefold())
                if rule is None:
                    continue
                award = float(criterion.awarded)
                criterion.description = rule.description
                criterion.met = abs(award - rule.max_marks) < 1e-6
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
            if not spec.criteria:
                criteria = _half_mark_steps(question, criteria, spec.qid)
            criterion_score = round(sum(float(c.awarded) for c in criteria), 4)
            question.score = min(max(0.0, criterion_score), float(spec.max_marks))

    counted: set[str] = set(canonical)
    choice_candidate_qids: set[str] = set()
    forest = build_choice_forest(
        {qid: float(spec.max_marks) for qid, spec in canonical.items()},
        package.choice_groups, normalize_qid)
    resolved_choices = {}

    def select_group(index):
        if index in resolved_choices:
            return resolved_choices[index]
        group = package.choice_groups[index]
        policy = effective_selection_policy(group, package)
        options: list[tuple[object, list[str], tuple[int, int], bool]] = []
        for option in group.options:
            option_qids = [normalize_qid(qid) for qid in option.question_ids]
            present = []
            positions: list[tuple[int, int]] = []
            for qid in option_qids:
                location = locations.get(qid)
                if qid not in rows_by_qid:
                    # A located answer with no row is the grader's omission, unless an
                    # earlier finalizing pass already pruned the rows of the
                    # alternative the choice rule did not count. MEASURED on Piyush's
                    # SST copy (2026-09-18): Q9.a was attempted and located, Q10 was
                    # the counted alternative, the post-challenger pass dropped Q9.a's
                    # row, and the exit pass then refused the whole graded copy.
                    if (answer_map is not None and location is not None
                            and getattr(location, "status", "") == "located"
                            and not choices_already_finalized):
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
        # and the fixed first-attempted policy must discard a surplus option. When
        # the map could not locate a label for every attempted option (an OR
        # alternative the paper leaves unlabelled has none of its own), the order
        # comes from the grader's own reading of where each answer starts, taken
        # uniformly for the whole group so the two kinds of position never mix.
        # It used to raise CHOICE_ORDER_UNRESOLVED and hold the copy; a checked
        # paper matters more (operator's rule, 2026-09-17).
        if (policy == "first_attempted"
                and len(options) > group.select_count
                and any(not has_position for _option, _present, _order, has_position
                        in options)):
            reordered = []
            for option, present, _order, _has in options:
                starts = [pos for pos in (_graded_start(rows_by_qid[qid]) for qid in present)
                          if pos is not None]
                order = min(starts) if starts else (10**9, min(report_order[qid] for qid in present))
                reordered.append((option, present, order, bool(starts)))
            options = reordered
            print(f"[choice] {group.group_id}: surplus attempts ordered by the grader's "
                  f"answer positions", flush=True)
        if policy == "best_score":
            def option_score(pair):
                eligible = set(pair[1])
                for child in forest.children[index]:
                    if forest.members[child].intersection(eligible):
                        selected_child, _, _ = select_group(child)
                        eligible.difference_update(forest.members[child] - selected_child)
                return sum(float(rows_by_qid[qid].score) for qid in eligible)

            options.sort(
                key=lambda pair: (
                    -option_score(pair),
                    pair[2],
                )
            )
        else:
            # 'first_attempted' is the normal examiner rule. 'explicit' is accepted
            # only when the answer map exposes exactly the required number of options;
            # otherwise no unambiguous explicit selection exists in this schema.
            if policy == "explicit" and len(options) != group.select_count:
                raise StrictGradeError(
                    "EXPLICIT_CHOICE_AMBIGUOUS",
                    f"{group.group_id} needs an explicit selection, but {len(options)} "
                    f"options contain mapped answers.",
                )
            options.sort(key=lambda pair: pair[2])
        if not options:
            # Nothing in the group attempted: its first alternatives stay on the copy
            # as "not attempted", so the paper is still out of its official total.
            # MEASURED 2026-09-30: with every alternative dropped, the copy was
            # re-totalled from its rows and read "out of 28" on a 40-mark paper.
            options = [(option, [normalize_qid(qid) for qid in option.question_ids], (10**9, 0), False)
                       for option in group.options
                       if all(normalize_qid(qid) in rows_by_qid for qid in option.question_ids)
                       ][:group.select_count]
        selected = options[:group.select_count]
        selected_qids = {
            normalize_qid(qid)
            for option, _, _, _ in selected
            for qid in option.question_ids
        }
        candidate_options = options if policy == "best_score" else selected
        candidates = {
            normalize_qid(qid)
            for option, _, _, _ in candidate_options
            for qid in option.question_ids
            if normalize_qid(qid) in rows_by_qid
        }
        retained = (set(selected_qids) if finalize_choices else {
            normalize_qid(qid) for option, _, _, _ in options for qid in option.question_ids
        })
        for child in forest.children[index]:
            child_members = forest.members[child]
            # A child of an excluded parent cannot add marks or require a choice
            # decision. Before verification its extra rows may remain as evidence.
            if not child_members.intersection(selected_qids | candidates):
                continue
            child_selected, child_retained, child_candidates = select_group(child)
            selected_qids.difference_update(child_members - child_selected)
            retained.difference_update(child_members - child_retained)
            candidates.difference_update(child_members - child_candidates)
        result = selected_qids, retained, candidates
        resolved_choices[index] = result
        return result

    for root in forest.roots:
        _, retained, candidates = select_group(root)
        counted.difference_update(forest.members[root])
        counted.update(retained)
        choice_candidate_qids.update(candidates)

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
            if _top(spec.qid) in attempted_tops and not getattr(report, "evidence_contract", False):
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
        # zero-max questions on the student copy. They are named, though: a page of
        # answer left with no mark and no word read as a page nobody checked (Radiant
        # Physics and Biology, 2026-09-29), so the copy says "not counted" beside it.
        # Choices are finalised more than once per copy (after grading, again after
        # moderation), and the second pass sees only the counted rows. Keep what an
        # earlier pass set aside, or the note is wiped (Sonakshi's Biology copy,
        # 2026-09-30: Q7, Q10 and Q11 were answered, not counted, and not mentioned).
        earlier = {str(x.get("qid")): x for x in (getattr(report, "uncounted_choice", None) or [])}
        # The note says which rule left the answer out. A CA copy told a student "the
        # best answers in that section were counted" beside a better Q4(b) that the
        # first attempted rule had left out (Business Laws, live check 6 Oct 2026).
        rule_of: dict[str, str] = {}
        for group in package.choice_groups:
            policy = effective_selection_policy(group, package)
            for option in group.options:
                for member in option.question_ids:
                    rule_of[normalize_qid(member)] = policy
        for q in report.questions:
            if (normalize_qid(q.qid) not in counted and bool(getattr(q, "attempted", True))
                    and not bool(getattr(q, "objective", False))):
                key = normalize_qid(q.qid)
                while key not in rule_of and "." in key:
                    key = key.rsplit(".", 1)[0]
                earlier[q.qid] = {"qid": q.qid, "page": int(getattr(q, "page", 0) or 0),
                                  "anchor_line_id": str(getattr(q, "anchor_line_id", "") or ""),
                                  "rule": rule_of.get(key, "best_score")}
        report.uncounted_choice = [x for qid, x in earlier.items()
                                   if normalize_qid(qid) not in counted]
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
    # Everything the teacher should see on a copy that IS released.
    warnings: tuple[PackageIssue, ...] = ()

    def as_dict(self) -> dict:
        return {
            "release": self.release,
            "blockers": [issue.model_dump(mode="json") for issue in self.blockers],
            "warnings": [issue.model_dump(mode="json") for issue in self.warnings],
        }


def automatic_release_decision(
    *,
    package: ExamPackage,
    report,
    preflight_issues,
    cost,
    budget_cap_inr: float | None,
    mandatory_verification_complete: bool,
    category: str = "",
) -> ReleaseDecision:
    """One decision after every automatic stage has completed.

    A copy is held when its SCORE is wrong or unprovable (preflight.SCORE_INTEGRITY_
    CODES), and released with warnings otherwise. Holding a correctly marked copy
    over a mark that grazes a letter, or over the second examiner citing the wrong
    line id, helps nobody: the student gets nothing back and the teacher never sees
    the note. MEASURED 2026-09-16: six of six copies were refused and none of them
    was refused over a wrong mark.
    """
    from preflight import advisory, release_blocking

    blockers = list(package.blockers())
    issues = list(preflight_issues or [])
    blockers.extend(PackageIssue(
        severity="block",
        code=str(getattr(issue, "code", "preflight")),
        detail=str(getattr(issue, "detail", issue)),
    ) for issue in release_blocking(issues, category))
    warnings = [PackageIssue(
        severity="warn",
        code=str(getattr(issue, "code", "preflight")),
        detail=str(getattr(issue, "detail", issue)),
    ) for issue in advisory(issues, category)
      if str(getattr(issue, "severity", "")).lower() in {"warn", "block"}]
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
    if budget_cap_inr is not None and not getattr(cost, "rates_found", False):
        blockers.append(PackageIssue(
            severity="block", code="unpriced-provider-call",
            detail="At least one provider call has no configured price, so the budget is unknown.",
        ))
    if budget_cap_inr is not None and not getattr(cost, "usage_attested", False):
        blockers.append(PackageIssue(
            severity="block", code="provider-usage-missing",
            detail=("At least one dispatched provider call returned no token usage; its "
                    "actual bill cannot be proven against the copy budget."),
        ))
    if budget_cap_inr is not None and float(getattr(cost, "total_inr", 0.0)) > float(budget_cap_inr) + 1e-6:
        blockers.append(PackageIssue(
            severity="block", code="budget-exceeded",
            detail=(f"Provider cost Rs.{float(cost.total_inr):.2f} exceeded the hard "
                    f"Rs.{float(budget_cap_inr):g} cap."),
        ))
    if not mandatory_verification_complete:
        # The second examiner's pass is a check ON marks already decided and
        # evidenced; when it cannot finish (its own reply quoted the wrong line id,
        # or it was cut off), the marks are the primary examiner's, unreviewed.
        # That is worth telling the teacher, not worth destroying the copy over.
        warnings.append(PackageIssue(
            severity="warn", code="verification-incomplete",
            detail=("The second examiner did not certify every mark on this copy; "
                    "the marks shown are the first examiner's. Worth a read."),
        ))
    # Deduplicate identical codes/details so the UI gets a concise machine verdict.
    unique: dict[tuple[str, str], PackageIssue] = {
        (issue.code, issue.detail): issue for issue in blockers
    }
    result = tuple(unique.values())
    unique_warnings: dict[tuple[str, str], PackageIssue] = {
        (issue.code, issue.detail): issue for issue in warnings
        if (issue.code, issue.detail) not in unique
    }
    return ReleaseDecision(release=not result, blockers=result,
                           warnings=tuple(unique_warnings.values()))
