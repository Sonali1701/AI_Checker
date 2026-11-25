# -*- coding: utf-8 -*-
"""Exam categories: one place that says which exam family a paper belongs to and
what that family's settings are.

    resolution = categories.resolve(category=form_category, exam_type=form_exam_type,
                                    student_class=..., subject=...)
    resolution.category.choice.selection_policy

Order of precedence (the operator always wins over a guess):
  1. an explicit category the operator picked (an unknown one is an error),
  2. the design paper type,
  3. the legacy exam type "professional" (old clients and job records),
  4. auto detection from the operator's class and subject fields only. Vidyapeeth
     and design are never guessed. The legacy exam type "school" rules out CA.

Detection here is mechanical parsing of what the operator typed. A future guess from
the paper's own content must be an AI suggestion that quotes its evidence and that
the operator confirms; it never overrides an explicit choice.

This package imports nothing from grader, pipeline or server.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .base import Category, CategoryId
from .ca import CA
from .commerce import COMMERCE
from .design import DESIGN
from .gurukul import GURUKUL
from .innovation import INNOVATION
from .school import SCHOOL
from . import subjects
from .teaching import TEACHING
from .vidyapeeth import VIDYAPEETH

REGISTRY: dict[str, Category] = {c.id: c for c in (SCHOOL, VIDYAPEETH, COMMERCE, CA, DESIGN,
                                                   TEACHING, INNOVATION, GURUKUL)}
# What the operator may type or old records may carry.
ALIASES = {
    "professional": "ca", "cma": "ca", "cs": "ca", "icai": "ca",
    "chartered accountancy": "ca", "ca/cma/cs": "ca",
    "board": "school", "school board": "school", "cbse": "school", "icse": "school",
    "isc": "school", "state board": "school",
    "pw vidyapeeth": "vidyapeeth", "vp": "vidyapeeth",
    "commerce 11-12": "commerce", "commerce (class 11 and 12)": "commerce",
    "drawing": "design",
    "teaching exams": "teaching", "teacher recruitment": "teaching", "kvs": "teaching",
    "nvs": "teaching", "emrs": "teaching", "tet": "teaching", "ctet": "teaching",
    "school of innovation": "innovation", "soi": "innovation", "pw skills": "innovation",
    "pwskills": "innovation", "skills": "innovation", "tech": "innovation",
    "non-tech": "innovation", "non tech": "innovation", "tech and non-tech": "innovation",
    "fsd": "innovation", "full stack": "innovation", "full stack development": "innovation",
    "data science": "innovation", "data analytics": "innovation",
    "gurukulam": "gurukul", "the gurukulam school": "gurukul", "gurukulam school": "gurukul",
    "pw gurukul": "gurukul", "pw gurukulam": "gurukul",
    "the gurukulam school (pw gurukul)": "gurukul",
}
SOURCES = ("explicit", "paper_type", "exam_type", "auto", "job")


class UnknownCategory(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    category: Category
    source: str               # one of SOURCES

    @property
    def id(self) -> CategoryId:
        return self.category.id

    @property
    def explicit(self) -> bool:
        return self.source in ("explicit", "paper_type")


def get(category_id: str) -> Category:
    key = " ".join(str(category_id or "").strip().lower().split())
    key = ALIASES.get(key, key)
    if key not in REGISTRY:
        raise UnknownCategory(
            f"Unknown exam category {category_id!r}. Choose one of: "
            + ", ".join(REGISTRY) + ".")
    return REGISTRY[key]




def _class_level(student_class: str | None) -> str:
    """'11', '12', ... or '' when the class field names no level."""
    text = " ".join(str(student_class or "").lower().replace("-", " ").split())
    m = re.search(r"(?<![0-9])(1[0-2]|[1-9])(?:st|nd|rd|th)?(?![0-9])", text)
    if m:
        return m.group(1)
    roman = {"xii": "12", "xi": "11", "x": "10", "ix": "9", "viii": "8", "vii": "7",
             "vi": "6", "v": "5", "iv": "4", "iii": "3", "ii": "2", "i": "1"}
    for token in text.split():
        if token in roman and token != "i":
            return roman[token]
    return ""


def is_ca_text(student_class: str | None, subject: str | None) -> bool:
    """The grader's professional exam rule on the operator's class and subject."""
    text = f"{student_class or ''} {subject or ''}".strip().lower()
    if not text:
        return False
    if any(h in text for h in CA.detection.class_hints):
        return True
    tokens = (student_class or "").lower().replace("-", " ").split()
    return any(t in tokens for t in CA.detection.class_tokens)


def _is_commerce_text(student_class: str | None, subject: str | None) -> bool:
    level = _class_level(student_class)
    if (level in COMMERCE.detection.levels
            and re.search(r"\bcommerce\b", str(student_class or ""), re.I)
            and subjects.is_maths_subject(subject)):
        return True
    # Both older commerce lists must agree, so an auto detected commerce paper gets
    # exactly the mark size, budget and examiner it got before categories existed.
    if not (subjects.is_commerce_subject(subject) and subjects.is_cost_commerce_subject(subject)):
        return False
    return level == "" or level in COMMERCE.detection.levels


def resolve(*, category: str = "", exam_type: str = "", paper_type: str = "",
            student_class: str = "", subject: str = "") -> Resolution:
    if str(category or "").strip():
        return Resolution(get(category), "explicit")
    if str(paper_type or "").strip().lower() == "design":
        return Resolution(DESIGN, "paper_type")
    legacy = str(exam_type or "").strip().lower()
    if legacy == "professional":
        return Resolution(CA, "exam_type")
    if legacy != "school" and is_ca_text(student_class, subject):
        return Resolution(CA, "auto")
    if _is_commerce_text(student_class, subject):
        return Resolution(COMMERCE, "auto")
    return Resolution(SCHOOL, "exam_type" if legacy == "school" else "auto")


def for_job(job: dict | None) -> Resolution:
    """The category of a stored job, including jobs saved before categories existed."""
    job = job or {}
    stored = str(job.get("category") or "").strip()
    if stored:
        try:
            return Resolution(get(stored), "job")
        except UnknownCategory:
            pass
    if str(job.get("mode") or "").strip().lower() == "design":
        return Resolution(DESIGN, "job")
    package = job.get("exam_package") or {}
    department = str((package.get("department") if isinstance(package, dict) else "") or "")
    if department in ("ca", "commerce", "school"):
        return Resolution(REGISTRY[department], "job")
    if str(job.get("exam_type") or "").strip().lower() == "professional":
        return Resolution(CA, "job")
    return Resolution(SCHOOL, "job")


def ui_choices() -> list[dict]:
    return [{"id": c.id, "name": c.display_name, "hint": c.ui_hint,
             "pipeline": c.pipeline, "level_label": c.level_label,
             "linked_work": bool(c.intake.linked_work),
             "generates_key": bool(c.answer_key.may_generate)} for c in REGISTRY.values()]


# --- Settings that still follow the subject inside a category -----------------
# Before categories, a school paper in a commerce subject (Class 9 Economics, Class
# 10 Commercial Studies) got the commerce mark size, budget and second examiner.
# These keep that until it is changed on purpose.

def mark_scale(category: Category, subject: str | None) -> float:
    if category.is_professional or category.id == "commerce":
        return category.render.mark_scale
    return 0.8 if subjects.is_commerce_subject(subject) else category.render.mark_scale


def budget_inr(category: Category, subject: str | None) -> float:
    if category.is_professional or category.id == "commerce":
        return category.cost.budget_inr
    return COMMERCE.cost.budget_inr if subjects.is_cost_commerce_subject(subject) else category.cost.budget_inr


def always_moderate(category: Category, subject: str | None) -> bool:
    return bool(category.examiner.always_moderate or subjects.is_commerce_subject(subject))


def selection_policy(category: Category | None, printed_policy: str, package_department: str = "") -> str:
    """How extra attempts of an OR / "answer any N" group are resolved.

    The question graph keeps the printed policy (its proof hashes it), and the
    category's rule is applied here, where it is used. Without a category the
    package department decides, as before."""
    policy = str(printed_policy or "first_attempted")
    if policy != "first_attempted":
        return policy
    if category is not None:
        return category.choice.selection_policy
    return "first_attempted" if package_department == "ca" else "best_score"


__all__ = ["Category", "CategoryId", "Resolution", "UnknownCategory", "REGISTRY", "ALIASES",
           "get", "resolve", "for_job", "ui_choices", "is_ca_text", "mark_scale",
           "budget_inr", "always_moderate", "selection_policy", "subjects"]
