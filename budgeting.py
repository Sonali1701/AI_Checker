"""Conservative per-copy cost reservations for strict automatic grading.

The provider proxy performs the actual billing.  This ledger prevents optional calls
whose quoted worst-case cost does not fit and keeps a protected verification reserve.
Every completed call must be committed as its own UsageEvent so mixed-model work is
never priced at the primary model's cheaper rate.
"""
from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
from enum import Enum

from costs import (DEFAULT_USD_TO_INR, budget_for, compute_cost,
                   is_professional_or_commerce)


MICROS = Decimal("1000000")


def _micros(value: float | Decimal) -> int:
    return int((Decimal(str(value)) * MICROS).to_integral_value(rounding=ROUND_CEILING))


def _inr(value: int) -> float:
    return float(Decimal(value) / MICROS)


class Stage(str, Enum):
    PACKAGE = "package"
    ORIENTATION = "orientation"
    OCR = "ocr"
    PRIMARY = "primary"
    VERIFY = "verify"
    ADJUDICATE = "adjudicate"
    RETRY = "retry"


class BudgetError(RuntimeError):
    pass


class UnpricedModel(BudgetError):
    pass


class BudgetDenied(BudgetError):
    pass


@dataclass(frozen=True)
class UsageEvent:
    call_id: str
    stage: Stage
    provider: str
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    output_tokens: int = 0
    mathpix_pages: int = 0
    question_ids: tuple[str, ...] = ()
    charge_state: str = "billed"

    def usage(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": max(0, int(self.input_tokens)),
            "cached_input_tokens": max(0, int(self.cached_input_tokens)),
            "cache_write_tokens": max(0, int(self.cache_write_tokens)),
            "cache_write_1h_tokens": max(0, int(self.cache_write_1h_tokens)),
            "output_tokens": max(0, int(self.output_tokens)),
        }

    def cost_inr(self, usd_to_inr: float = DEFAULT_USD_TO_INR) -> float:
        cost = compute_cost(self.usage(), self.mathpix_pages, usd_to_inr)
        if self.model and not cost.rates_found:
            raise UnpricedModel(f"No pricing is configured for model {self.model!r}.")
        return cost.total_inr

    def as_dict(self, usd_to_inr: float = DEFAULT_USD_TO_INR) -> dict:
        return {
            "call_id": self.call_id,
            "stage": self.stage.value,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_write_1h_tokens": self.cache_write_1h_tokens,
            "output_tokens": self.output_tokens,
            "mathpix_pages": self.mathpix_pages,
            "question_ids": list(self.question_ids),
            "charge_state": self.charge_state,
            "cost_inr": self.cost_inr(usd_to_inr),
        }


@dataclass(frozen=True)
class CallPlan:
    stage: Stage
    provider: str
    model: str
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    mathpix_pages: int = 0
    question_ids: tuple[str, ...] = ()
    mandatory: bool = False

    def quote_inr(self, usd_to_inr: float = DEFAULT_USD_TO_INR) -> float:
        # Quote with no cache discount.  Cache hits reduce the committed amount; they
        # are never assumed when deciding whether a call fits.
        event = UsageEvent(
            call_id="quote",
            stage=self.stage,
            provider=self.provider,
            model=self.model,
            input_tokens=self.max_input_tokens,
            output_tokens=self.max_output_tokens,
            mathpix_pages=self.mathpix_pages,
            question_ids=self.question_ids,
            charge_state="quote",
        )
        return event.cost_inr(usd_to_inr)


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    quoted_micros: int
    plan: CallPlan


@dataclass(frozen=True)
class BudgetPolicy:
    cap_inr: float
    target_inr: float
    verification_reserve_inr: float
    recovery_reserve_inr: float
    safety_inr: float

    @classmethod
    def for_copy(cls, *, total_marks: float | None, exam_type: str,
                 subject: str = "", category: str = "") -> "BudgetPolicy":
        try:
            marks = float(total_marks) if total_marks is not None else 0.0
        except (TypeError, ValueError):
            marks = 0.0
        if not math.isfinite(marks) or not (marks > 0):
            raise ValueError("A finite positive paper total is required for a hard budget.")
        cap = budget_for(marks, exam_type=exam_type, subject=subject, category=category)
        commercial = category in ("ca", "commerce") or is_professional_or_commerce(
            exam_type=exam_type, subject=subject)
        target = cap - (1.0 if commercial else 2.0)
        return cls(
            cap_inr=cap,
            target_inr=target,
            verification_reserve_inr=max(1.5, cap * 0.10),
            recovery_reserve_inr=max(1.2, cap * 0.08),
            safety_inr=max(0.5, cap * 0.05),
        )


@dataclass
class BudgetLedger:
    policy: BudgetPolicy
    usd_to_inr: float = DEFAULT_USD_TO_INR
    committed_micros: int = 0
    reservations: dict[str, Reservation] = field(default_factory=dict)
    events: list[UsageEvent] = field(default_factory=list)
    breached: bool = False
    mandatory_verification_complete: bool = False
    unresolved_disagreement: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cap_micros(self) -> int:
        return _micros(self.policy.cap_inr)

    @property
    def reserved_micros(self) -> int:
        return sum(item.quoted_micros for item in self.reservations.values())

    @property
    def protected_micros(self) -> int:
        if self.mandatory_verification_complete:
            return _micros(self.policy.safety_inr)
        return _micros(self.policy.verification_reserve_inr + self.policy.safety_inr)

    @property
    def remaining_inr(self) -> float:
        return _inr(max(0, self.cap_micros - self.committed_micros - self.reserved_micros))

    def reserve(self, plan: CallPlan) -> Reservation:
        quoted = _micros(plan.quote_inr(self.usd_to_inr))
        with self._lock:
            if self.breached:
                raise BudgetDenied("The copy budget is already breached.")
            protected = 0 if plan.stage in (Stage.VERIFY, Stage.ADJUDICATE) else self.protected_micros
            available = self.cap_micros - self.committed_micros - self.reserved_micros - protected
            if quoted > available:
                raise BudgetDenied(
                    f"{plan.stage.value} call needs Rs.{_inr(quoted):.4f}, but only "
                    f"Rs.{_inr(max(0, available)):.4f} is available after protected reserves."
                )
            ticket = Reservation(uuid.uuid4().hex, quoted, plan)
            self.reservations[ticket.reservation_id] = ticket
            return ticket

    def commit(self, reservation: Reservation, event: UsageEvent) -> int:
        actual = _micros(event.cost_inr(self.usd_to_inr))
        with self._lock:
            held = self.reservations.pop(reservation.reservation_id, None)
            if held is None:
                raise BudgetError("Unknown or already-finalized reservation.")
            self.committed_micros += actual
            self.events.append(event)
            if actual > held.quoted_micros or self.committed_micros > self.cap_micros:
                self.breached = True
            if event.stage == Stage.VERIFY and event.charge_state == "billed":
                self.mandatory_verification_complete = True
            return actual

    def charge_ambiguous_failure(self, reservation: Reservation) -> None:
        """Timeout/unknown outcome: conservatively consume the full quote."""
        event = UsageEvent(
            call_id=reservation.reservation_id,
            stage=reservation.plan.stage,
            provider=reservation.plan.provider,
            model=reservation.plan.model,
            # Store the quote's token envelope as the possible billed usage.
            input_tokens=reservation.plan.max_input_tokens,
            output_tokens=reservation.plan.max_output_tokens,
            mathpix_pages=reservation.plan.mathpix_pages,
            question_ids=reservation.plan.question_ids,
            charge_state="ambiguous_failure",
        )
        self.commit(reservation, event)

    def cancel_unbilled(self, reservation: Reservation) -> None:
        """Release a quote only when the request provably never reached a provider."""
        with self._lock:
            if self.reservations.pop(reservation.reservation_id, None) is None:
                raise BudgetError("Unknown or already-finalized reservation.")

    def can_release(self) -> tuple[bool, str]:
        if self.breached or self.committed_micros > self.cap_micros:
            return False, "The copy exceeded its hard provider budget."
        if self.reservations:
            return False, "One or more provider-cost reservations are unresolved."
        if not self.mandatory_verification_complete:
            return False, "Mandatory automatic verification did not complete."
        if self.unresolved_disagreement:
            return False, "A grading disagreement remains unresolved."
        return True, ""

    def summary(self) -> dict:
        ok, reason = self.can_release()
        return {
            "cap_inr": self.policy.cap_inr,
            "target_inr": self.policy.target_inr,
            "committed_inr": _inr(self.committed_micros),
            "reserved_inr": _inr(self.reserved_micros),
            "remaining_inr": self.remaining_inr,
            "breached": self.breached,
            "mandatory_verification_complete": self.mandatory_verification_complete,
            "unresolved_disagreement": self.unresolved_disagreement,
            "release_eligible": ok,
            "release_blocker": reason,
            "events": [event.as_dict(self.usd_to_inr) for event in self.events],
        }
