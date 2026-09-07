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
    RELEASE_CHECK = "release_check"
    RELEASED = "released"
    UNGRADABLE = "ungradable"
    FAILED = "failed"


TERMINAL_STATES = {
    WorkflowState.RELEASED,
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
        WorkflowState.RELEASE_CHECK, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
    WorkflowState.RELEASE_CHECK: {
        WorkflowState.RELEASED, WorkflowState.UNGRADABLE, WorkflowState.FAILED,
    },
}


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
        WorkflowState.RELEASE_CHECK: "Running automatic release checks",
        WorkflowState.RELEASED: "Released",
        WorkflowState.UNGRADABLE: "Could not be graded automatically",
        WorkflowState.FAILED: "Failed",
    }
    return labels[WorkflowState(state)]
