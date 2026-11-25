"""Explicit fail-closed state machine for zero-human automatic grading."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class WorkflowState(str, Enum):
    RECEIVED = "received"
    PACKAGE_VALIDATING = "package_validating"
    PACKAGE_VALIDATED = "package_validated"
    NORMALIZING = "normalizing"
    OCR = "ocr"
    ANSWER_MAPPING = "answer_mapping"
    PRIMARY_GRADING = "primary_grading"
    VERIFYING = "verifying"
    ADJUDICATING = "adjudicating"
    AGGREGATING = "aggregating"
    RENDERING = "rendering"
    # One close read of each MCQ row the release gate could not confirm
    # (objective_confirm.py), between the first render and the gate.
    CONFIRMING_MCQ = "confirming_mcq"
    RELEASE_CHECK = "release_check"
    # The release gate can re-render a copy once, in margin-only mode, when marks
    # collide with the student's writing. MEASURED 2026-09-16: the pipeline reported
    # this stage, the enum did not have it, and ValueError("'repairing_layout' is not
    # a valid WorkflowState") was caught by the gate and reported as a BLOCKING
    # release issue - so the step that exists to repair a copy destroyed it instead.
    REPAIRING_LAYOUT = "repairing_layout"
    RELEASED = "released"
    # A marked copy the release gate would not send to the student goes to a
    # faculty reviewer WITH its PDF (operator's rule, 2026-09-19: a copy is never
    # thrown away; it goes to the student or to faculty). UNGRADABLE remains for a
    # copy that could not be marked at all (wrong paper, not an answer sheet).
    FACULTY_REVIEW = "faculty_review"
    UNGRADABLE = "ungradable"
    FAILED = "failed"


TERMINAL_STATES = {
    WorkflowState.RELEASED,
    WorkflowState.FACULTY_REVIEW,
    WorkflowState.UNGRADABLE,
    WorkflowState.FAILED,
}


_ALLOWED: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.RECEIVED: {
        WorkflowState.PACKAGE_VALIDATING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.PACKAGE_VALIDATING: {
        WorkflowState.PACKAGE_VALIDATED, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.PACKAGE_VALIDATED: {
        WorkflowState.NORMALIZING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.NORMALIZING: {
        WorkflowState.OCR, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.OCR: {
        WorkflowState.ANSWER_MAPPING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.ANSWER_MAPPING: {
        WorkflowState.PRIMARY_GRADING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.PRIMARY_GRADING: {
        WorkflowState.VERIFYING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.VERIFYING: {
        WorkflowState.ADJUDICATING, WorkflowState.AGGREGATING,
        WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.ADJUDICATING: {
        WorkflowState.AGGREGATING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.AGGREGATING: {
        WorkflowState.RENDERING, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.RENDERING: {
        WorkflowState.CONFIRMING_MCQ, WorkflowState.RELEASE_CHECK,
        WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.CONFIRMING_MCQ: {
        WorkflowState.RELEASE_CHECK, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.RELEASE_CHECK: {
        WorkflowState.REPAIRING_LAYOUT,
        WorkflowState.RELEASED, WorkflowState.FACULTY_REVIEW,
        WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.REPAIRING_LAYOUT: {
        WorkflowState.RELEASE_CHECK, WorkflowState.RELEASED, WorkflowState.FACULTY_REVIEW,
        WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
}
# A copy can be sent to faculty from any working state: the gate is the usual
# place, but an earlier stage may also decide the copy needs a reviewer.
for _state, _targets in _ALLOWED.items():
    if _state not in (WorkflowState.RECEIVED, WorkflowState.PACKAGE_VALIDATING):
        _targets.add(WorkflowState.FACULTY_REVIEW)
del _state, _targets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkflowEvent:
    state: WorkflowState
    at: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state.value, "at": self.at, "detail": self.detail}


@dataclass
class AutomaticWorkflow:
    """In-memory state object with a serializable append-only event history.

    A repository can persist ``snapshot()`` without knowing any grading internals.
    Illegal transitions raise immediately; callers must explicitly choose UNGRADABLE
    or FAILED instead of accidentally treating an incomplete flow as released.
    """
    state: WorkflowState = WorkflowState.RECEIVED
    events: list[WorkflowEvent] = field(default_factory=list)
    failure_code: str = ""
    failure_message: str = ""
    on_change: Callable[[dict], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.events:
            self.events.append(WorkflowEvent(self.state, _now(), "Workflow created"))

    def transition(self, new_state: WorkflowState | str, detail: str = "") -> None:
        target = WorkflowState(new_state)
        if self.state in TERMINAL_STATES:
            raise RuntimeError(f"Cannot leave terminal state {self.state.value}.")
        allowed = _ALLOWED.get(self.state, set())
        if target not in allowed:
            raise RuntimeError(
                f"Illegal automatic-grading transition {self.state.value} -> {target.value}."
            )
        self.state = target
        self.events.append(WorkflowEvent(target, _now(), detail))
        if self.on_change:
            self.on_change(self.snapshot())

    def ungradable(self, code: str, message: str) -> None:
        self.failure_code = (code or "UNGRADABLE").strip().upper()
        self.failure_message = (message or "The copy could not be verified automatically.").strip()
        self.transition(WorkflowState.UNGRADABLE, self.failure_message)

    def fail(self, code: str, message: str) -> None:
        self.failure_code = (code or "FAILED").strip().upper()
        self.failure_message = (message or "Automatic grading failed.").strip()
        self.transition(WorkflowState.FAILED, self.failure_message)

    def faculty_review(self, code: str, message: str) -> None:
        """The copy is marked but goes to a faculty reviewer instead of the student."""
        self.failure_code = (code or "FACULTY_REVIEW").strip().upper()
        self.failure_message = (message or "This copy needs a faculty reviewer.").strip()
        self.transition(WorkflowState.FACULTY_REVIEW, self.failure_message)

    @property
    def releasable(self) -> bool:
        return self.state == WorkflowState.RELEASED

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "events": [event.as_dict() for event in self.events],
            "failure": ({
                "code": self.failure_code,
                "message": self.failure_message,
            } if self.failure_code or self.failure_message else None),
        }

    @classmethod
    def from_snapshot(cls, data: dict | None) -> "AutomaticWorkflow":
        data = data or {}
        events = [
            WorkflowEvent(
                WorkflowState(item["state"]),
                str(item.get("at") or _now()),
                str(item.get("detail") or ""),
            )
            for item in list(data.get("events") or [])
            if isinstance(item, dict) and item.get("state")
        ]
        return cls(
            state=WorkflowState(data.get("state") or WorkflowState.RECEIVED.value),
            events=events,
            failure_code=str((data.get("failure") or {}).get("code") or ""),
            failure_message=str((data.get("failure") or {}).get("message") or ""),
        )


def stage_label(state: WorkflowState | str) -> str:
    labels = {
        WorkflowState.RECEIVED: "Received",
        WorkflowState.PACKAGE_VALIDATING: "Validating official exam package",
        WorkflowState.PACKAGE_VALIDATED: "Official exam package validated",
        WorkflowState.NORMALIZING: "Normalizing pages",
        WorkflowState.OCR: "Reading handwriting",
        WorkflowState.ANSWER_MAPPING: "Mapping answers to questions",
        WorkflowState.PRIMARY_GRADING: "Grading question by question",
        WorkflowState.VERIFYING: "Verifying evidence and marks",
        WorkflowState.ADJUDICATING: "Resolving grading disagreement",
        WorkflowState.AGGREGATING: "Calculating final marks",
        WorkflowState.RENDERING: "Producing evaluated PDF",
        WorkflowState.CONFIRMING_MCQ: "Reading the unconfirmed MCQ rows once more",
        WorkflowState.RELEASE_CHECK: "Running automatic release checks",
        WorkflowState.REPAIRING_LAYOUT: "Repairing annotation placement",
        WorkflowState.RELEASED: "Released to the student",
        WorkflowState.FACULTY_REVIEW: "Sent for faculty review",
        WorkflowState.UNGRADABLE: "Could not be graded automatically",
        WorkflowState.FAILED: "Failed",
    }
    # A missing label is a missing SENTENCE, never a reason to fail a marked copy:
    # the unlabelled REPAIRING_LAYOUT raised KeyError here, through the progress
    # callback, and the release gate reported it as a blocking defect of the copy.
    try:
        return labels[WorkflowState(state)]
    except (KeyError, ValueError):
        return str(getattr(state, "value", state)).replace("_", " ").capitalize()


def safe_stage_reporter(flow: "AutomaticWorkflow"):
    """A stage callback that reports progress and can never fail a copy.

    This exists as ONE function because the same bug has now destroyed two batches
    of marked copies: the pipeline reported a stage ("repairing_layout"), something
    behind the callback raised - first ValueError for an unlisted state, then
    KeyError for a state with no label - and the release gate caught it and reported
    it as a blocking defect OF THE COPY. Progress display is not grading authority,
    so nothing raised in here reaches the caller.
    """
    def report(state: str, detail: str = "") -> None:
        try:
            target = WorkflowState(state)
            if flow.state != target:
                flow.transition(target, detail)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            print(f"[stage] ignoring progress update {state!r}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    return report
