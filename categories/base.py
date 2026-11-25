# -*- coding: utf-8 -*-
"""What one exam category is: the settings that differ between CA, Commerce 11-12,
school boards, PW Vidyapeeth and design papers.

Pure data. Nothing here imports grader, pipeline or server, so any module can read a
category without an import cycle. Shared machinery (OCR, answer maps, anchors,
drawing, attestation) does not belong here; only the decisions that really differ by
exam family do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CategoryId = Literal["ca", "commerce", "school", "vidyapeeth", "design", "teaching",
                     "innovation", "gurukul"]


@dataclass(frozen=True)
class DetectionRules:
    """How a category is recognised from the operator's own class and subject fields.

    Only mechanical parsing of what the operator typed. A category that cannot be told
    apart from the fields (Vidyapeeth, design) is never auto detected; the operator
    picks it."""
    class_hints: tuple[str, ...] = ()       # substrings of "class subject", lowercased
    class_tokens: tuple[str, ...] = ()      # whole words of the class field
    subject_hints: tuple[str, ...] = ()     # substrings of the subject
    levels: tuple[str, ...] = ()            # class levels the subject rule needs
    auto_detectable: bool = True


@dataclass(frozen=True)
class PromptPolicy:
    """Which exam family marking policy the grading prompt carries.

    The subject blocks (reasoning rigour, commerce conventions, language and
    humanities) follow the SUBJECT, not the category: a commerce stream English paper
    is still marked as English. The category picks the exam family's policy."""
    exam_family: Literal["school", "professional"] = "school"
    force_commerce_conventions: bool = False
    # Marking rules for this category only, appended to the grading prompt (for
    # example a Vidyapeeth convention that must not reach CA papers). Keep them
    # general, numbered and short: the prompt is cached and shared by the batch.
    extra_rules: str = ""


@dataclass(frozen=True)
class MarksReadingPolicy:
    try_regex: bool = True                  # free regex reading of printed tags
    try_section_headers: bool = True        # "Section A (5 x 1 = 5)" headers
    # The exam family's rule for a paper that prints no marks at all, told to the AI
    # reader as the allocation (PW Skills: equal shares of the entered total).
    unprinted_convention: str = ""


@dataclass(frozen=True)
class ChoicePolicy:
    """Which attempts of an "answer any N" / OR group count when a student answers more.

    best_score: PW school and commerce papers keep the answer deserving more marks.
    first_attempted: ICAI evaluates only the first answers written."""
    selection_policy: Literal["first_attempted", "best_score"] = "best_score"


@dataclass(frozen=True)
class ExaminerPolicy:
    """Whether the second examiner always reviews a copy of this category."""
    always_moderate: bool = False


@dataclass(frozen=True)
class IntakePolicy:
    """Upload and page gates."""
    min_photo_edge: int = 800               # smaller photos are refused as unreadable
    sparse_page_lines: int = 2              # a page with this few lines is read again
    blank_scan_detection: bool = True       # empty ruled pages are not "unread"
    language_marks: bool = True             # word level marks on language papers
    # Typed or printed PDFs are the submissions themselves (School of Innovation
    # assignments typed into a document), not a refused document: their text pages
    # are read from the PDF text layer (typed_sheets.py), a PDF that holds only a
    # link to the work goes to a reviewer, and the typed-document refusal is off.
    typed_submissions: bool = False
    # Pages above this go to a reviewer unread (a spreadsheet export of hundreds of
    # pages is not an answer document). 0 = no category limit.
    max_pages: int = 0
    # Linked work is opened and other formats are read (linked_work.py): GitHub,
    # Drive, Docs, Sheets, Colab and OneDrive links in the submission, and notebooks,
    # workbooks, Power BI, Tableau, Word and code files uploaded directly, all turned
    # into one PDF to grade (work_document.py).
    linked_work: bool = False


@dataclass(frozen=True)
class ReviewPolicy:
    """Release checks: which issues hold a copy for faculty review.

    The shared set (preflight.SCORE_INTEGRITY_CODES) holds every category; a category
    can add codes of its own, or let a code through as a warning only."""
    extra_hold_codes: frozenset[str] = frozenset()
    warn_only_codes: frozenset[str] = frozenset()
    auto_release: bool = True
    review_reason: str = ""
    # Subjects whose marking has been measured against faculty marks and met the
    # category's release rule: their copies go to students when the gate finds no
    # fault, even while auto_release is False for the category's other subjects.
    calibrated_subjects: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AnswerKeyPolicy:
    """Whether a paper of this category may be marked with no uploaded key.

    may_generate: the tool writes a model answer key from the question paper, once
    per paper, and marks every copy against it (teaching_key.py).
    examination / key_notes: the exam the key writer is told it writes for, and notes
    after its rules; empty for the teacher recruitment exams the writer was made for."""
    may_generate: bool = False
    examination: str = ""
    key_notes: str = ""


@dataclass(frozen=True)
class RenderPolicy:
    mark_scale: float = 1.0                 # size of ticks and circled marks
    whole_total: bool = False               # CA copies show one whole number total


@dataclass(frozen=True)
class CostPolicy:
    budget_inr: float = 12.0                # per copy ceiling


@dataclass(frozen=True)
class Category:
    id: CategoryId
    display_name: str
    ui_hint: str
    pipeline: Literal["written", "design"]
    department: Literal["school", "commerce", "ca"]
    legacy_exam_type: Literal["school", "professional"]
    detection: DetectionRules = field(default_factory=DetectionRules)
    prompt: PromptPolicy = field(default_factory=PromptPolicy)
    marks: MarksReadingPolicy = field(default_factory=MarksReadingPolicy)
    choice: ChoicePolicy = field(default_factory=ChoicePolicy)
    examiner: ExaminerPolicy = field(default_factory=ExaminerPolicy)
    intake: IntakePolicy = field(default_factory=IntakePolicy)
    review: ReviewPolicy = field(default_factory=ReviewPolicy)
    answer_key: AnswerKeyPolicy = field(default_factory=AnswerKeyPolicy)
    render: RenderPolicy = field(default_factory=RenderPolicy)
    cost: CostPolicy = field(default_factory=CostPolicy)
    # A category whose papers have no class or level (School of Innovation
    # assignments belong to a course, not a class): the form hides the class field
    # and this label stands in for it wherever a level is shown or required.
    level_label: str = ""

    @property
    def is_professional(self) -> bool:
        return self.legacy_exam_type == "professional"
