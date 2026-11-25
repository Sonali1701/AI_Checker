"""Deterministic quality checks for feedback shown to a student."""
from __future__ import annotations

import re


# Prompts target 12 words. A complete 13-word correction is not a grading defect;
# allow a small layout tolerance instead of buying another evidence-verification
# call over one extra word. Long or generic explanations still require repair.
MARGIN_REMARK_MAX_WORDS = 26

# These phrases report that something is wrong without telling the student what
# was wrong or what the correct result should be.  They are never releaseable.
_GENERIC_REMARK_PATTERNS = (
    re.compile(r"\bneeds?\s+(?:correction|improvement|revision)\b", re.IGNORECASE),
    re.compile(
        r"^(?:the\s+)?(?:answer|calculation|method|explanation|response|solution|"
        r"working|concept)?\s*(?:is\s+)?(?:incorrect|wrong|incomplete|vague)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:incorrect|wrong|incomplete|vague)\s*"
        r"(?:answer|calculation|method|explanation|response|solution|working)?\.?$",
        re.IGNORECASE,
    ),
)


# A sentence with no subject ("Is vague.") or no object ("Misses.") names nothing
# the student can act on.  Compression produces these by removing the quoted phrase
# at the end of a sentence, or the "the student" at the front of one; a released copy
# carried "Is vague. Misses." as the whole margin note for a three-mark answer.
_COPULA_OPENERS = frozenset(("is", "are", "was", "were", "isn't", "aren't"))
_ACTIVE_VERBS = frozenset((
    "misses", "missed", "omits", "omitted", "lacks", "lacked", "ignores", "ignored",
    "skips", "skipped", "states", "mentions", "explains", "shows", "gives", "uses",
    "includes", "defines", "writes", "wrote", "calculates", "applies", "identifies",
))


_TRANSITIVE_PRESENT = frozenset((
    "misses", "omits", "lacks", "ignores", "skips", "states", "mentions", "explains",
    "shows", "gives", "uses", "includes", "defines", "writes", "calculates",
    "applies", "identifies", "names", "lists",
))


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]


def fragment_problem(text: str | None) -> str:
    """Return ``"fragment"`` when a remark has lost its subject or its object.

    Checks each sentence, because the fault is per-sentence: "Is vague. Misses."
    is two fragments, while "Interest omitted" and "No Venn diagram drawn" are
    complete notes that happen to end on a verb.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return ""
    for sentence in _sentences(value):
        words = re.findall(r"[\w'’-]+", sentence)
        if not words:
            continue
        head = words[0].lower().strip("'’")
        if head in _COPULA_OPENERS:
            return "fragment"
        if len(words) <= 2 and head in _ACTIVE_VERBS:
            return "fragment"
        # Only the active present tense, never a past participle: "Answer omits" has
        # lost the thing omitted, while "Interest omitted" and "No Venn diagram drawn"
        # put the object first and are complete notes.
        if words[-1].lower() in _TRANSITIVE_PRESENT:
            return "fragment"
    return ""


def remark_quality_problem(text: str | None, *, require: bool = True) -> str:
    """Return why a margin remark is unsafe, or ``""`` when it is actionable.

    This deliberately checks form rather than academic truth.  Truth is decided by
    the independent examiner using the question, key and student page; this gate
    prevents that examiner from returning another empty/template explanation.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return "missing" if require else ""
    if len(value.split()) > MARGIN_REMARK_MAX_WORDS:
        return f"overlong-{len(value.split())}-words"
    if any(pattern.search(value) for pattern in _GENERIC_REMARK_PATTERNS):
        return "generic"
    # Deliberately NOT fragment_problem(): that one is a repair signal, not a release
    # gate. Nobody reviews a held copy, so a fragment is rewritten upstream by
    # finalise_student_remarks and only warned about here if one still gets through.
    return ""


def report_remark_problems(report) -> list[str]:
    """List release-blocking feedback defects on attempted deducted answers."""
    problems: list[str] = []
    for question in list(getattr(report, "questions", None) or []):
        if not bool(getattr(question, "attempted", True)):
            continue
        score = float(getattr(question, "score", 0.0) or 0.0)
        maximum = float(getattr(question, "max_score", 0.0) or 0.0)
        if score >= maximum - 1e-9:
            continue
        problem = remark_quality_problem(getattr(question, "remark", ""))
        if problem:
            problems.append(f"{getattr(question, 'qid', '?')}: {problem}")
    return problems
