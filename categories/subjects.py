# -*- coding: utf-8 -*-
"""The subject axis, kept apart from the category axis.

A category is the exam family (CA, commerce stream, school board ...). The subject
decides which subject conventions a paper is marked with: a commerce stream English
paper is still marked as English.

The app grew three different "is this a commerce subject" lists (the grader's, the
costing table's and the exam package's). They are kept here as named variants so
moving every caller onto this module changes no mark, no prompt byte and no rupee.
Unifying them is a deliberate, announced change, not part of the move.
"""
from __future__ import annotations

import re

# Subjects whose answers hinge on reasoning or derivation (grader REASONING_RIGOR).
REASONING_SUBJECT_KEYWORDS = ("physics", "chemistry", "math", "science", "phy ", "chem")
# Descriptive, language or humanities subjects, unless the name also names
# physics, chemistry or maths.
NON_REASONING_HINTS = (
    "social", "english", "hindi", "sanskrit", "urdu", "language", "literature",
    "history", "geograph", "civic", "political", "economic", "moral", "art",
)

# The grader's list: decides the commerce conventions prompt block, the mark size
# and the second examiner.
GRADER_COMMERCE_KEYWORDS = (
    "account", "tax", "gst", "cost", "audit", "law", "financ", "business",
    "commerce", "econom", "strategic", "management",
)
# The costing table's list: decides the Rs.18 budget tier.
COST_COMMERCE_HINTS = (
    "account", "audit", "banking", "business", "commerce", "corporate",
    "cost", "econom", "entrepreneur", "finance", "financial", "gst",
    "insurance", "law", "management", "marketing", "secretarial", "tax",
)
# The exam package's list: decides ExamPackage.department (part of the package hash
# and printed into the grading prompt).
PACKAGE_COMMERCE_WORDS = (
    "account", "commerce", "business", "econom", "cost", "audit", "tax", "law",
)

LANGUAGE_SUBJECTS = (
    "english", "hindi", "sanskrit", "urdu", "marathi", "gujarati", "bengali", "bangla",
    "tamil", "telugu", "kannada", "malayalam", "punjabi", "odia", "oriya", "assamese",
    "nepali", "french", "german", "spanish", "literature", "grammar", "language",
)

_COMMERCE_THEORY_ALIASES = ("bst", "ocm", "sp")


def is_commerce_theory_subject(subject: str | None) -> bool:
    """Unambiguous names/abbreviations for the commerce point-answer subjects."""
    text = " ".join(str(subject or "").lower().split())
    return (any(word in text for word in ("business studies", "econom", "secretarial practice",
                                          "organisation of commerce", "organization of commerce"))
            or any(re.search(rf"(?<![a-z]){word}(?![a-z])", text)
                   for word in _COMMERCE_THEORY_ALIASES))


def is_maths_subject(subject: str | None) -> bool:
    return bool(re.search(r"\b(?:maths?|mathematics)\b", str(subject or "").lower()))


def is_reasoning_subject(subject: str | None) -> bool | None:
    """True for Physics/Chemistry/Maths/Science, False for clearly language or
    humanities subjects, None when blank or unknown (the prompt text self gates)."""
    if not subject or not subject.strip():
        return None
    s = subject.strip().lower()
    has_hard = any(k in s for k in ("physics", "chemistry", "math"))
    if any(h in s for h in NON_REASONING_HINTS) and not has_hard:
        return False
    if any(k in s for k in REASONING_SUBJECT_KEYWORDS):
        return True
    return None


def is_commerce_subject(subject: str | None) -> bool | None:
    """The grader's rule: True for clearly commerce subjects, None when blank."""
    if not subject or not subject.strip():
        return None
    s = subject.strip().lower()
    return any(k in s for k in GRADER_COMMERCE_KEYWORDS) or is_commerce_theory_subject(s)


def is_cost_commerce_subject(subject: str | None) -> bool:
    """The costing table's rule (the Rs.18 tier)."""
    s = " ".join(str(subject or "").strip().lower().split())
    return any(hint in s for hint in COST_COMMERCE_HINTS) or is_commerce_theory_subject(s)


def package_department(exam_type: str, student_class: str, subject: str) -> str:
    """ExamPackage.department: 'ca', 'commerce' or 'school' (the exam package's rule)."""
    text = f"{student_class} {subject}".lower()
    if (exam_type or "").strip().lower() == "professional" or re.search(
            r"\b(?:ca|icai|chartered accountant)\b", text):
        return "ca"
    if any(word in text for word in PACKAGE_COMMERCE_WORDS) or is_commerce_theory_subject(subject):
        return "commerce"
    return "school"


def is_language_paper(subject: str | None) -> bool:
    """English, Hindi, Sanskrit and other language or literature papers only."""
    text = " ".join(str(subject or "").lower().split())
    if not text:
        return False
    return any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text)
               for word in LANGUAGE_SUBJECTS)
