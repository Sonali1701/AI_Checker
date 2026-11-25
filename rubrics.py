"""Import fixed, paper-specific marking rules without inventing official weights."""
from __future__ import annotations

import json
import math
import re


ANY_N_MARKS_CONTRACT = (
    "For a within-question 'any N points' rule, only N scoring slots carry marks. "
    "Choose the N best valid attempted points. The scoring maxima must still sum "
    "to the official question maximum. If fewer than N points are valid, retain "
    "the remaining required slots with the appropriate partial/zero awards. "
    "An extra incorrect attempt outside those scoring slots may have a separate "
    "criterion for its cross, but it MUST have max=0 and awarded=0. Omit correct "
    "surplus points from scoring criteria and explain the count limit if needed. "
    "Example: any 4 points at 1 mark each, with four correct and two wrong attempts: "
    "the four scoring rows have max=1, awarded=1; the two surplus wrong rows have "
    "max=0, awarded=0; max_score=4 and score=4, never max_score=6. "
    "Do not let a zero-scored surplus attempt reduce marks already earned."
)

COMMERCE_FIRST_N_MARKS_CONTRACT = (
    "For Commerce Business Studies / Economics (including OCM and SP), a within-answer "
    "'any N points' rule means check only the FIRST N live points in physical writing "
    "order, including wrong points. Never choose the best N or substitute a later "
    "correct point for an earlier wrong one. Later points are not checked and earn "
    "nothing. Keep the N official scoring slots and their full maxima, with partial "
    "or zero awards where required. The scoring maxima still sum to the official "
    "question maximum. Correct brief points earn full marks. Establish the count "
    "from the question and the order from the original page evidence; mark uncertainty "
    "for verification instead of guessing. This rule does not change OR-question choice."
)


def any_n_marks_contract(category: str = "", subject: str = "") -> str:
    """The point-attempt rule follows the exam category and its theory subject."""
    from categories.subjects import is_commerce_theory_subject
    if category == "commerce" and is_commerce_theory_subject(subject):
        return COMMERCE_FIRST_N_MARKS_CONTRACT
    return ANY_N_MARKS_CONTRACT


KEY_LAYOUT_NOTE = (
    "=== ANSWER KEY PAGE LAYOUT ===\n"
    "Read the original answer-key images to associate printed or annotated step marks "
    "with their solution paragraphs. Extracted text can detach margin labels, place "
    "them before the wrong paragraph, or split 'each point' / 'all points' across lines. "
    "A label for 'each point' applies to every point in that group; 'all points' is "
    "one combined allocation. Preserve the actual printed step weights, including "
    "case applications and conclusions, even when the student omitted those steps. "
    "Missing work earns zero against its full step maximum. Confirm that the complete "
    "breakdown sums to the official question maximum; never invent balancing marks."
)


def key_requires_visual_layout(text: str) -> bool:
    """A dense text layer does not prove that step-mark associations survived."""
    return bool(re.search(
        r"(?im)^\s*[\[(]?\d+(?:\.\d+)?\s*marks?\b[^\n]*$"
        r"|\b(?:each|all)\s+(?:the\s+)?points?\b", text or ""))


def rubric_rows(text: str, questions, *, supplied=None, key_hash: str = "") -> dict:
    # Imported lazily: exam_package calls this while building its question graph.
    from exam_package import RubricCriterionSpec, normalize_qid

    maxima = {normalize_qid(q.qid): float(q.max_marks) for q in questions}
    if supplied is not None:
        if not isinstance(supplied, dict) or set(supplied) - {
                "version", "answer_key_sha256", "questions"}:
            raise ValueError("Step rubric must contain version and questions only (optional answer_key_sha256).")
        if supplied.get("version") != 1 or not isinstance(supplied.get("questions"), list):
            raise ValueError("Step rubric must use version 1 and a questions array.")
        if supplied.get("answer_key_sha256") and supplied["answer_key_sha256"] != key_hash:
            raise ValueError("Step rubric belongs to a different answer key.")
        rows = supplied["questions"]
    else:
        # Only explicit 'mark(s)' allocations qualify. Bare figures, final answers,
        # question maxima and a solution's numbered lists are not step weights.
        rows = []
        active = None
        sections: dict[str, list] = {}
        header = re.compile(
            r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,3}(?:(?:\.[a-zivx]+)|(?:\([a-zivx]+\)))*)"
            r"[.):]?\s*(?:Answer|Solution)?\s*$", re.I)
        allocation = re.compile(
            r"^\s*(.+?)\s*(?:\[|\(|[:–—-])\s*(\d+(?:\.\d+)?)\s*marks?\s*[\])]?[.]?\s*$", re.I)
        for line in str(text or "").splitlines():
            match = header.fullmatch(line)
            if match:
                candidate = normalize_qid(match.group(1))
                active = candidate if candidate in maxima else None
                continue
            match = allocation.fullmatch(line)
            if active and match:
                description, value = match.groups()
                if re.search(r"\b(total|maximum|max\.?|question)\b", description, re.I):
                    continue
                criteria = sections.setdefault(active, [])
                criteria.append({"criterion_id": f"{active}.c{len(criteria) + 1}",
                                 "description": description.strip(),
                                 "max_marks": float(value), "source_quote": line.strip()})
        for qid, criteria in sections.items():
            # Partial or ambiguous extraction is not a complete official rubric.
            if abs(sum(c["max_marks"] for c in criteria) - maxima[qid]) < 1e-6:
                rows.append({"qid": qid, "criteria": criteria})

    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"qid", "criteria"}:
            raise ValueError("Each rubric question must contain qid and criteria.")
        qid = normalize_qid(str(row.get("qid", "")))
        if qid not in maxima or qid in result:
            raise ValueError(f"Unknown or duplicate rubric question: {qid}.")
        raw = row.get("criteria")
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{qid}: provide at least one rubric criterion.")
        criteria = [RubricCriterionSpec.model_validate(c) for c in raw]
        ids = [c.criterion_id.strip().casefold() for c in criteria]
        if any(not cid for cid in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"{qid}: criterion IDs must be unique and non-empty.")
        if any(not math.isfinite(c.max_marks) or not c.description.strip() for c in criteria):
            raise ValueError(f"{qid}: invalid criterion marks or description.")
        for criterion in criteria:
            increment = criterion.mark_increment
            if increment and (not math.isfinite(increment)
                              or abs(criterion.max_marks / increment - round(criterion.max_marks / increment)) > 1e-6):
                raise ValueError(f"{qid}: the mark increment must divide its criterion maximum.")
        if abs(sum(c.max_marks for c in criteria) - maxima[qid]) > 1e-6:
            raise ValueError(f"{qid}: step marks must sum exactly to {maxima[qid]:g}.")
        result[qid] = criteria
    return result


def parse_rubric_upload(value: str):
    if not value or not value.strip():
        return None
    if len(value.encode("utf-8")) > 1_000_000:
        raise ValueError("Step rubric exceeds the 1 MB limit.")
    return json.loads(value)
