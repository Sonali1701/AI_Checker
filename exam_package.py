"""Immutable, authoritative exam-package domain model for automatic grading.

This module deliberately has no dependency on ``grader.py``.  The legacy grader is
large and imports OCR/rendering code; keeping the package model independent gives the
strict grading path one stable source of truth and avoids a circular dependency.

An ExamPackage describes what may be graded.  Models may decide whether a student met
its criteria, but they may not create question ids, maxima, choice rules, or reference
material.  A package that cannot prove those things is not eligible for automatic
release.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from typing import Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = "2.0"
QUESTION_GRAPH_PROOF_VERSION = "dual-provider-question-graph-v2"
_QUESTION_GRAPH_PROOF_KEY = secrets.token_bytes(32)


def normalize_qid(value: str) -> str:
    """Canonical comparison form without changing the qid printed to a student.

    Also splits a digit/letter boundary, so "Q1a" and "Q1.a" reach the same form.
    They did not, and two providers spell the same sub-part both ways: on the
    Corporate Laws master test Claude returned Q1a/Q1b/Q1c where Gemini returned
    Q1.a/Q1.b/Q1.c, every maximum identical, and the paper was refused as a
    disagreement over punctuation alone. A CA paper cannot fall back on the regex
    reader, so that refusal meant it could not be graded at all.

    "Q11" still differs from "Q1.1": the split only adds a boundary where the
    spelling changes character class, it never removes one that was written.
    """
    text = (value or "").strip().lower()
    text = re.sub(r"^question\s*", "q", text)
    text = re.sub(r"^ans(?:wer)?\s*", "q", text)
    text = re.sub(r"[^a-z0-9]+", ".", text).strip(".")
    had_q = text.startswith("q")
    rest = text[1:] if had_q else text
    parts = [piece for segment in rest.split(".") if segment
             for piece in re.findall(r"\d+|[a-z]+", segment)]
    if not parts:
        return "q" if had_q else text
    body = ".".join(parts)
    return "q" + body if (had_q or body[0].isdigit()) else body


def _question_graph_proof_message(
    paper_sha256: str,
    official_total: float,
    rows,
    choice_groups=(),
) -> bytes:
    def field(value, name: str, default=None):
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    canonical_rows = sorted(
        (normalize_qid(str(getattr(row, "qid", "") or "")),
         round(float(getattr(row, "max_marks", getattr(row, "max", 0.0)) or 0.0), 4))
        for row in rows
    )
    canonical_choices = []
    for group in choice_groups or []:
        options = sorted(
            tuple(sorted(
                normalize_qid(str(qid or ""))
                for qid in (field(option, "question_ids", []) or [])
            ))
            for option in (field(group, "options", []) or [])
        )
        canonical_choices.append({
            "select_count": int(field(group, "select_count", 0) or 0),
            "selection_policy": str(
                field(group, "selection_policy", "first_attempted")
                or "first_attempted"
            ).strip().lower(),
            "contribution_marks": round(float(
                field(group, "contribution_marks", 0.0) or 0.0), 4),
            "options": options,
        })
    canonical_choices.sort(key=lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    payload = {
        "version": QUESTION_GRAPH_PROOF_VERSION,
        "paper_sha256": str(paper_sha256 or ""),
        "official_total": round(float(official_total), 4),
        "rows": canonical_rows,
        "choice_groups": canonical_choices,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def issue_question_graph_proof(
    paper_sha256: str,
    official_total: float,
    rows,
    choice_groups=(),
) -> str:
    """Mint a process-local proof only after the server verifies both providers."""
    digest = hmac.new(
        _QUESTION_GRAPH_PROOF_KEY,
        _question_graph_proof_message(
            paper_sha256, official_total, rows, choice_groups),
        hashlib.sha256,
    ).hexdigest()
    return f"{QUESTION_GRAPH_PROOF_VERSION}.{digest}"


def verify_question_graph_proof(
    proof: str,
    paper_sha256: str,
    official_total: float,
    rows,
    choice_groups=(),
) -> bool:
    if not str(proof or "").startswith(f"{QUESTION_GRAPH_PROOF_VERSION}."):
        return False
    expected = issue_question_graph_proof(
        paper_sha256, official_total, rows, choice_groups)
    return hmac.compare_digest(str(proof), expected)


class PackageIssue(BaseModel):
    severity: Literal["block", "warn"]
    code: str
    detail: str


class RubricCriterionSpec(BaseModel):
    criterion_id: str
    description: str
    max_marks: float = Field(gt=0)
    reference_ids: list[str] = Field(default_factory=list)
    evidence_required: bool = True
    scoring_kind: Literal[
        "objective", "numeric", "method", "theory", "law", "format", "other"
    ] = "other"


class QuestionNode(BaseModel):
    qid: str
    parent_qid: str | None = None
    scorable: bool = True
    max_marks: float = Field(ge=0)
    question_type: Literal[
        "objective", "numeric", "method", "theory", "law", "mixed", "unknown"
    ] = "unknown"
    objective_answer: str | None = None
    prompt_text: str = ""
    criteria: list[RubricCriterionSpec] = Field(default_factory=list)
    choice_group_id: str | None = None


class ChoiceOption(BaseModel):
    option_id: str
    question_ids: list[str]


class ChoiceGroup(BaseModel):
    group_id: str
    options: list[ChoiceOption]
    select_count: int = Field(ge=1)
    selection_policy: Literal["first_attempted", "best_score", "explicit"]
    contribution_marks: float = Field(gt=0)


class OfficialReference(BaseModel):
    reference_id: str
    document_hash: str
    page: int | None = Field(default=None, ge=1)
    text: str = ""


class ExamPackage(BaseModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    package_id: str
    authority: Literal["official", "verified_template", "estimate"]
    release_eligible: bool
    department: Literal["school", "commerce", "ca"]
    level: str
    subject: str
    paper: str = ""
    attempt: str = ""
    applicable_law_date: str | None = None
    total_marks: float = Field(gt=0)
    question_paper_sha256: str
    answer_key_sha256: str
    questions: list[QuestionNode]
    choice_groups: list[ChoiceGroup] = Field(default_factory=list)
    references: list[OfficialReference] = Field(default_factory=list)
    source: Literal[
        "submitted", "text", "sections", "ai_verified", "ai_unverified", "unknown"
    ] = "unknown"
    # Opaque server-generated digest for a dual-provider graph.  This is part of the
    # canonical package hash, so the proof cannot be removed or swapped after setup.
    question_graph_proof: str = ""

    def canonical_payload(self) -> dict:
        data = self.model_dump(mode="json")
        data.pop("package_id", None)
        # Ordering is part of the contract: stable packages produce stable hashes and
        # stable prompt prefixes, which in turn preserves provider-cache hits.
        data["questions"] = sorted(data["questions"], key=lambda q: normalize_qid(q["qid"]))
        data["choice_groups"] = sorted(data["choice_groups"], key=lambda g: g["group_id"])
        data["references"] = sorted(data["references"], key=lambda r: r["reference_id"])
        return data

    def computed_id(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validation_issues(self) -> list[PackageIssue]:
        issues: list[PackageIssue] = []
        if self.authority == "estimate" or not self.release_eligible:
            issues.append(PackageIssue(
                severity="block", code="non-authoritative-material",
                detail="The answer key/rubric is an estimate, not an official release source.",
            ))
        if not self.question_paper_sha256:
            issues.append(PackageIssue(
                severity="block", code="missing-question-paper",
                detail="An official question paper is required for automatic release.",
            ))
        if not self.answer_key_sha256:
            issues.append(PackageIssue(
                severity="block", code="missing-answer-key",
                detail="An official answer key or marking scheme is required for automatic release.",
            ))
        if self.source in ("submitted", "ai_unverified", "unknown"):
            issues.append(PackageIssue(
                severity="block", code="unverified-question-graph",
                detail=("The question/maxima graph is browser-entered or single-source "
                        "and has not been independently matched to the official paper."),
            ))
        if self.source == "ai_verified" and not self.question_graph_proof:
            issues.append(PackageIssue(
                severity="block", code="missing-question-graph-proof",
                detail=("The independently verified question/maxima graph has no "
                        "server-issued proof bound to this package."),
            ))
        elif self.source == "ai_verified" and not verify_question_graph_proof(
                self.question_graph_proof,
                self.question_paper_sha256,
                self.total_marks,
                self.questions,
                self.choice_groups):
            issues.append(PackageIssue(
                severity="block", code="invalid-question-graph-proof",
                detail=("The question/maxima proof was not issued for this exact "
                        "paper, total, and graph."),
            ))
        if self.package_id != self.computed_id():
            issues.append(PackageIssue(
                severity="block", code="package-hash-mismatch",
                detail="The exam package no longer matches its immutable package id.",
            ))
        if not self.questions:
            issues.append(PackageIssue(
                severity="block", code="missing-question-graph",
                detail="No authoritative question/marks graph could be built from the paper.",
            ))

        by_qid: dict[str, QuestionNode] = {}
        for question in self.questions:
            qid = normalize_qid(question.qid)
            if not qid:
                issues.append(PackageIssue(
                    severity="block", code="empty-question-id",
                    detail="The question graph contains an empty question id.",
                ))
                continue
            if qid in by_qid:
                issues.append(PackageIssue(
                    severity="block", code="duplicate-question-id",
                    detail=f"Question id {question.qid!r} appears more than once.",
                ))
            by_qid[qid] = question
            objective_answer = str(question.objective_answer or "").strip()
            if question.question_type == "objective" and not re.fullmatch(
                    r"(?:option\s*)?[A-Da-d]", objective_answer):
                issues.append(PackageIssue(
                    severity="block", code="missing-objective-answer",
                    detail=(f"{question.qid} is objective but has no authoritative A-D "
                            "answer in the exam package."),
                ))
            if question.question_type != "objective" and objective_answer:
                issues.append(PackageIssue(
                    severity="block", code="objective-answer-on-nonobjective-question",
                    detail=(f"{question.qid} carries an objective answer but is typed "
                            f"{question.question_type}."),
                ))
            if question.scorable and (not math.isfinite(question.max_marks)
                                      or question.max_marks <= 0):
                issues.append(PackageIssue(
                    severity="block", code="invalid-question-maximum",
                    detail=f"{question.qid} has invalid maximum {question.max_marks!r}.",
                ))
            if question.criteria:
                criterion_ids: set[str] = set()
                criterion_total = 0.0
                for criterion in question.criteria:
                    cid = criterion.criterion_id.strip().lower()
                    if not cid or cid in criterion_ids:
                        issues.append(PackageIssue(
                            severity="block", code="duplicate-criterion-id",
                            detail=f"{question.qid} has an empty or duplicate criterion id.",
                        ))
                    criterion_ids.add(cid)
                    criterion_total += float(criterion.max_marks)
                if abs(criterion_total - float(question.max_marks)) > 0.01:
                    issues.append(PackageIssue(
                        severity="block", code="criterion-maximum-mismatch",
                        detail=(f"{question.qid} criteria total {criterion_total:g}, but the "
                                f"question is worth {question.max_marks:g}."),
                    ))

        # Parent references must exist when they are explicit.  A flat marks table may
        # omit non-scorable parent nodes, so inferred parents are not required.
        for question in self.questions:
            if question.parent_qid and normalize_qid(question.parent_qid) not in by_qid:
                issues.append(PackageIssue(
                    severity="block", code="missing-question-parent",
                    detail=f"{question.qid} refers to missing parent {question.parent_qid}.",
                ))

        reference_ids = {reference.reference_id for reference in self.references}
        for question in self.questions:
            for criterion in question.criteria:
                missing = [ref for ref in criterion.reference_ids if ref not in reference_ids]
                if missing:
                    issues.append(PackageIssue(
                        severity="block", code="unknown-official-reference",
                        detail=f"{criterion.criterion_id} cites unknown reference(s): {', '.join(missing)}.",
                    ))

        grouped_questions: set[str] = set()
        group_ids: set[str] = set()
        group_contribution = 0.0
        for group in self.choice_groups:
            group_id = group.group_id.strip().lower()
            if not group_id:
                issues.append(PackageIssue(
                    severity="block", code="empty-choice-group-id",
                    detail="Every choice group requires a non-empty stable id.",
                ))
            if group_id in group_ids:
                issues.append(PackageIssue(
                    severity="block", code="duplicate-choice-group",
                    detail=f"Choice group {group.group_id!r} appears more than once.",
                ))
            group_ids.add(group_id)
            if group.select_count >= len(group.options):
                issues.append(PackageIssue(
                    severity="block", code="invalid-choice-count",
                    detail=(f"{group.group_id} asks for {group.select_count} option(s) "
                            f"from only {len(group.options)} alternatives; a choice must "
                            "leave at least one alternative unselected."),
                ))
            option_totals: list[float] = []
            option_ids: set[str] = set()
            for option in group.options:
                option_id = option.option_id.strip().lower()
                if not option_id:
                    issues.append(PackageIssue(
                        severity="block", code="empty-choice-option-id",
                        detail=f"{group.group_id} contains an option with no id.",
                    ))
                elif option_id in option_ids:
                    issues.append(PackageIssue(
                        severity="block", code="duplicate-choice-option-id",
                        detail=f"{group.group_id} repeats option id {option.option_id!r}.",
                    ))
                option_ids.add(option_id)
                option_qids = [normalize_qid(qid) for qid in option.question_ids]
                if len(set(option_qids)) != len(option_qids):
                    issues.append(PackageIssue(
                        severity="block", code="duplicate-qid-in-choice-option",
                        detail=(f"{group.group_id}/{option.option_id} repeats a question id."),
                    ))
                missing = [qid for qid in option_qids if qid not in by_qid]
                if missing:
                    issues.append(PackageIssue(
                        severity="block", code="unknown-choice-question",
                        detail=f"{group.group_id}/{option.option_id} contains unknown {missing}.",
                    ))
                overlap = grouped_questions.intersection(option_qids)
                if overlap:
                    issues.append(PackageIssue(
                        severity="block", code="overlapping-choice-membership",
                        detail=f"Question(s) {sorted(overlap)} belong to incompatible choices.",
                    ))
                grouped_questions.update(option_qids)
                option_totals.append(sum(float(by_qid[q].max_marks) for q in option_qids
                                         if q in by_qid))
            if option_totals and any(abs(total - option_totals[0]) > 0.01
                                     for total in option_totals[1:]):
                issues.append(PackageIssue(
                    severity="block", code="choice-option-maximum-mismatch",
                    detail=f"{group.group_id} options do not contribute equal maximum marks.",
                ))
            if (option_totals and abs(float(group.contribution_marks)
                                      - option_totals[0] * group.select_count) > 0.01):
                issues.append(PackageIssue(
                    severity="block", code="choice-contribution-mismatch",
                    detail=(f"{group.group_id} contributes {group.contribution_marks:g} marks, "
                            f"but selecting {group.select_count} option(s) contributes "
                            f"{option_totals[0] * group.select_count:g}."),
                ))
            group_contribution += float(group.contribution_marks)

        fixed_total = sum(
            float(question.max_marks) for question in self.questions
            if question.scorable and normalize_qid(question.qid) not in grouped_questions
        )
        effective_total = fixed_total + group_contribution
        if self.choice_groups:
            if abs(effective_total - float(self.total_marks)) > 0.01:
                issues.append(PackageIssue(
                    severity="block", code="choice-effective-total-mismatch",
                    detail=(f"Fixed questions plus choice groups total {effective_total:g}, "
                            f"not the official {self.total_marks:g}."),
                ))
        else:
            flat_total = sum(float(q.max_marks) for q in self.questions if q.scorable)
            tolerance = 0.01 if self.source == "ai_verified" else 0.25
            if abs(flat_total - float(self.total_marks)) > tolerance:
                issues.append(PackageIssue(
                    severity="block", code="question-graph-total-mismatch",
                    detail=(f"Question maxima total {flat_total:g}, not the official "
                            f"{self.total_marks:g}."),
                ))
        return issues

    def blockers(self) -> list[PackageIssue]:
        return [issue for issue in self.validation_issues() if issue.severity == "block"]


def sha256_bytes(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data else ""


def department_for(exam_type: str, student_class: str, subject: str) -> str:
    text = f"{student_class} {subject}".lower()
    if (exam_type or "").strip().lower() == "professional" or re.search(
            r"\b(?:ca|icai|chartered accountant)\b", text):
        return "ca"
    if any(word in text for word in (
            "account", "commerce", "business", "econom", "cost", "audit", "tax", "law")):
        return "commerce"
    return "school"


def _declared_objective_numbers(question_paper_text: str) -> set[int]:
    """Question numbers the official paper explicitly declares as objective.

    The marks-attestation proof deliberately covers only qids and maxima.  That is
    the right security boundary, but it used to erase the MCQ type before grading,
    so one-mark MCQs were rendered like prose answers (large circled marks that
    could drift into the next row).  Recover the type only from the exact uploaded
    paper text; never infer it merely because a question is worth one mark.
    """
    text = re.sub(r"\s+", " ", str(question_paper_text or ""))
    out: set[int] = set()
    range_re = re.compile(
        r"questions?\s+(?:no\.?\s*)?(\d{1,3})\s+"
        r"(?:to|[-\u2013\u2014])\s+(\d{1,3})"
        r".{0,120}?\b(?:mcqs?|multiple\s+choice|objective)\b",
        re.IGNORECASE,
    )
    for match in range_re.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if 1 <= start <= end <= 500:
            out.update(range(start, end + 1))
    # Some school exports state only ``Section A contains 6 Multiple Choice
    # Questions``.  This is still deterministic when the paper visibly contains
    # the consecutive top-level labels Q1..Q6; do not require a synthetic range the
    # source never printed.
    count_re = re.compile(
        r"section\s+[A-Za-z0-9]+\s+contains\s+(\d{1,3})\s+"
        r"(?:multiple\s+choice\s+questions?|mcqs?|objective\s+questions?)",
        re.IGNORECASE,
    )
    for match in count_re.finditer(text):
        count = int(match.group(1))
        if 1 <= count <= 200 and all(
                re.search(rf"\bQ\s*{number}(?!\d)", text, re.IGNORECASE)
                for number in range(1, count + 1)):
            out.update(range(1, count + 1))
    return out


def _official_objective_answers(
    answer_key_text: str,
    question_paper_text: str,
) -> dict[int, str]:
    """Return official A-D answers for paper-declared objective questions.

    Both ``(3)`` and ``(C)`` answer-key styles are supported.  A number is mapped
    to its option letter so the existing deterministic objective scorer has one
    canonical representation.  Text outside the paper-declared objective range is
    ignored, preventing a short numerical answer such as ``Q14: 3`` from being
    mistaken for an MCQ key.
    """
    declared = _declared_objective_numbers(question_paper_text)
    key = str(answer_key_text or "")
    out: dict[int, str] = {}

    # The compact publisher key: a row labelled "Q1" and the option letter on the
    # next line, with no "." after the number and no "Answer:" label -
    #     Q1 \xa0\xa0
    #     D
    # MEASURED on a Class 10 PW Physics paper (Udaan Infinity, Subjective Test-09),
    # whose key prints exactly this for Q1-Q3 and "Check Solution" for the
    # descriptive Q4-Q6. Neither existing pattern matched, so the paper's own answers
    # (D, A, D) never reached the deterministic scorer and all three MCQs were left to
    # model judgement - which marked a correct (D) wrong and a wrong (B) correct.
    #
    # Only an explicit A-D LETTER is taken this way, and it does not need the paper to
    # have declared an objective range. A key that writes "D" is stating an option, not
    # inviting an inference. A DIGIT still requires that declared range, because "3" as
    # a whole answer is far more often arithmetic than option C - which is the very
    # confusion the range guard exists to prevent.
    compact_re = re.compile(
        r"(?m)^[^\S\n]*Q[^\S\n]*(\d{1,3})[^\S\n]*(?:\r?\n)?[^\S\n]*"
        r"\(?[^\S\n]*([A-Da-d])[^\S\n]*\)?[^\S\n]*$"
    )
    answer_re = re.compile(
        r"(?m)^\s*(\d{1,3})\s*[.)]\s*(?:\r?\n\s*)?"
        r"\(?\s*([1-4A-Da-d])\s*\)?\s*$"
    )
    number_to_letter = {"1": "A", "2": "B", "3": "C", "4": "D"}
    for match in answer_re.finditer(key):
        number = int(match.group(1))
        if number not in declared:
            continue
        raw = match.group(2).upper()
        # The compact ANSWER KEY appears before the detailed worked solutions in
        # publisher PDFs. Keep that first explicit entry: later prose can contain
        # question-number/option-shaped lines that are examples, not a replacement
        # key (a live Core Maths solution otherwise changed Q2 from D to A).
        out.setdefault(number, number_to_letter.get(raw, raw))
    for match in compact_re.finditer(key):
        out.setdefault(int(match.group(1)), match.group(2).upper())
    # Publisher solution PDFs commonly put explanation text between ``Qn`` and
    # ``Correct Answer``.  Parse within the individual question block so the
    # answer cannot leak from Q(n+1) into Qn when one solution is absent.
    question_header_re = re.compile(r"(?im)^\s*Q\s*(\d{1,3})\s*(?:[.)])?\s*$")
    headers = list(question_header_re.finditer(key))
    block_answer_re = re.compile(
        r"(?im)^\s*(?:Correct\s+)?Answer\s*:\s*"
        r"(?:\(\s*([1-4A-Da-d])\s*\)|([1-4A-Da-d])\s*[.)]?)"
    )
    for index, header in enumerate(headers):
        number = int(header.group(1))
        if number not in declared:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(key)
        answer_match = block_answer_re.search(key, header.end(), end)
        if answer_match:
            raw = (answer_match.group(1) or answer_match.group(2)).upper()
            out.setdefault(number, number_to_letter.get(raw, raw))
    return out


def _question_nodes(
    marks_scheme,
    *,
    question_paper_text: str = "",
    answer_key_text: str = "",
) -> list[QuestionNode]:
    nodes: list[QuestionNode] = []
    if marks_scheme is None:
        return nodes
    official_objectives = _official_objective_answers(
        answer_key_text, question_paper_text)
    for item in list(getattr(marks_scheme, "items", None) or []):
        qid = str(getattr(item, "qid", "") or "").strip()
        maximum = float(getattr(item, "max", 0.0) or 0.0)
        description = str(getattr(item, "description", "") or "").strip()
        question_type = str(getattr(item, "question_type", "unknown") or "unknown")
        objective_answer = str(getattr(item, "correct_answer", "") or "").strip()
        top_match = re.fullmatch(r"\s*[Qq]?\s*(\d{1,3})\s*", qid)
        if top_match:
            official_answer = official_objectives.get(int(top_match.group(1)))
            if official_answer:
                question_type = "objective"
                objective_answer = official_answer
        nodes.append(QuestionNode(
            qid=qid,
            # Flat source tables do not define non-scorable parent nodes.  Preserve the
            # inferred relationship in the id itself, but do not create a dangling
            # explicit parent reference that strict validation would rightly reject.
            parent_qid=None,
            max_marks=maximum,
            prompt_text=description,
            question_type=question_type,
            objective_answer=objective_answer or None,
        ))
    return nodes


def build_exam_package(
    *,
    exam_type: str,
    student_class: str,
    subject: str,
    level: str,
    paper: str,
    attempt: str,
    applicable_law_date: str | None,
    total_marks: float,
    question_paper: bytes | None,
    answer_key: bytes | None,
    key_source: str,
    marks_scheme,
    scheme_source: str = "unknown",
    question_graph_proof: str = "",
    choice_groups: list[ChoiceGroup] | None = None,
    question_paper_text: str = "",
    answer_key_text: str = "",
) -> ExamPackage:
    """Construct and hash a package from already-extracted authoritative inputs.

    Uploaded keys are treated as official because the application cannot independently
    identify an institution's document.  Generated rubric text is intentionally never
    release eligible.
    """
    authority = "official" if key_source == "upload" and answer_key else "estimate"
    source_aliases = {
        "manual": "submitted", "submitted": "submitted", "items": "submitted",
        "regex": "text", "regex_fallback": "text", "text": "text",
        "sections": "sections", "ai": "ai_unverified",
        "ai_unverified": "ai_unverified", "ai_verified": "ai_verified",
    }
    package = ExamPackage(
        package_id="pending",
        authority=authority,
        release_eligible=authority == "official",
        department=department_for(exam_type, student_class, subject),
        level=(level or student_class or "").strip(),
        subject=(subject or "").strip(),
        paper=(paper or subject or "").strip(),
        attempt=(attempt or "").strip(),
        applicable_law_date=(applicable_law_date or "").strip() or None,
        total_marks=float(total_marks),
        question_paper_sha256=sha256_bytes(question_paper),
        answer_key_sha256=sha256_bytes(answer_key),
        questions=_question_nodes(
            marks_scheme,
            question_paper_text=question_paper_text,
            answer_key_text=answer_key_text,
        ),
        choice_groups=list(choice_groups or []),
        references=[],
        source=source_aliases.get((scheme_source or "").strip().lower(), "unknown"),
        question_graph_proof=(question_graph_proof or "").strip(),
    )
    return package.model_copy(update={"package_id": package.computed_id()})


def package_prompt_block(package: ExamPackage) -> str:
    """Compact immutable block for provider prompts; contains no student data."""
    questions = "\n".join(
        f"- {q.qid}: {q.max_marks:g} marks"
        + (f" | {q.prompt_text}" if q.prompt_text else "")
        + (f" | official objective answer={q.objective_answer}"
           if q.question_type == "objective" and q.objective_answer else "")
        for q in package.questions
    )
    choices = "\n".join(
        f"- {group.group_id}: select {group.select_count}; policy={group.selection_policy}; "
        + " | ".join(
            f"{option.option_id}=[{', '.join(option.question_ids)}]"
            for option in group.options
        )
        for group in package.choice_groups
    )
    optional_metadata = ""
    if package.paper and package.paper.strip().casefold() != package.subject.strip().casefold():
        optional_metadata += f"Paper label: {package.paper}\n"
    if package.attempt:
        optional_metadata += f"Attempt: {package.attempt}\n"
    if package.applicable_law_date:
        optional_metadata += (
            f"Applicable law/standards date: {package.applicable_law_date}\n")
    return (
        "=== AUTHORITATIVE EXAM PACKAGE V2 ===\n"
        f"Package id: {package.package_id}\n"
        f"Department: {package.department}\n"
        f"Level: {package.level}\n"
        f"Subject: {package.subject}\n"
        + optional_metadata
        + f"Official total: {package.total_marks:g}\n"
        "The uploaded official paper and answer key identified by this package are "
        "controlling. Optional labels or dates must never be replaced with external "
        "syllabus assumptions.\n"
        "The question ids and maxima below are immutable. Never invent, rename, drop, "
        "or rescale them. For internal choice, return every part of each attempted "
        "option and omit wholly unattempted alternatives.\n" + questions
        + ("\nChoice groups:\n" + choices if choices else "")
    )
