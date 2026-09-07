"""Per-run cost accounting for one grading call (LLM tokens + Mathpix pages).

Pricing lives in PRICING / MATHPIX_PER_PAGE_USD below — edit those when rates change.
All token rates are USD per 1,000,000 tokens. Sources + last-verified date are in the
comments; treat them as the single place to update when a provider changes prices.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# PRICING — USD per 1,000,000 tokens. Update here when rates change.
#   Gemini:  https://ai.google.dev/gemini-api/docs/pricing
#   Claude:  https://platform.claude.com/docs/en/about-claude/models/overview
#   Last verified: 2026-08-31
# Keys are matched against the model id by substring, MOST SPECIFIC FIRST — see
# _match_rates. Order matters: "claude-sonnet-5" must be tried before any looser
# key that could also match it.
#
# Claude cache multipliers on the base input rate:
#   read           0.10x   (either TTL)
#   write, 5-min   1.25x
#   write, 1-hour  2.00x   <- the doubled write is why 1h only pays off from the
#                             third request onward that shares the prefix.
# ---------------------------------------------------------------------------
_CACHE_READ = 0.10
_CACHE_WRITE_5M = 1.25
_CACHE_WRITE_1H = 2.00


def _claude(inp: float, out: float) -> dict[str, float]:
    """A Claude pricing row, with the cache rates derived from the input rate."""
    return {
        "input": inp,
        "cached_input": round(inp * _CACHE_READ, 4),
        "cache_write": round(inp * _CACHE_WRITE_5M, 4),
        "cache_write_1h": round(inp * _CACHE_WRITE_1H, 4),
        "output": out,
    }


PRICING: dict[str, dict[str, float]] = {
    # --- Gemini (Google) --- (cached_input = context-cache read rate)
    "gemini-2.5-pro":   {"input": 1.25, "cached_input": 0.125, "output": 10.00},  # ≤200k-token prompts
    "gemini-2.5-flash": {"input": 0.30, "cached_input": 0.075, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
    # --- Claude (Anthropic) --- most specific ids first.
    # The -5 generation does NOT match the -4 keys by substring, so every one of
    # them needs its own row: without it _match_rates returned None and the whole
    # LLM cost silently computed as 0.0, which would have taken the per-copy
    # budget check offline the moment the grader was upgraded.
    "claude-fable-5":    _claude(10.00, 50.00),
    "claude-mythos-5":   _claude(10.00, 50.00),
    "claude-opus-5":     _claude(5.00, 25.00),
    "claude-sonnet-5":   _claude(2.00, 10.00),
    "claude-haiku-5":    _claude(1.00, 5.00),
    "claude-opus-4":     _claude(5.00, 25.00),
    "claude-sonnet-4":   _claude(3.00, 15.00),
    "claude-haiku-4":    _claude(1.00, 5.00),
}

# Mathpix Convert API (incl. /v3/text): USD per page/image processed (PAYG, 0–1M pages).
#   https://mathpix.com/pricing/api  ·  Last verified: 2026-06-08
# (The one-time $19.99 setup fee is NOT per-copy and is not counted here.)
MATHPIX_PER_PAGE_USD = 0.002

DEFAULT_USD_TO_INR = 94.0


def _match_rates(model: str) -> dict[str, float] | None:
    """Find the pricing row for a model id (e.g. 'claude-opus-4-7' → claude-opus-4)."""
    m = (model or "").lower()
    for key, rates in PRICING.items():
        if key in m:
            return rates
    # Family fallback for versioned ids not caught above. Every generation needs
    # its own pair here: "sonnet-4" does not appear anywhere in "claude-sonnet-5",
    # so a -5 id used to fall through to None and price as zero.
    for family, tiers in (("5", ("fable", "mythos", "opus", "sonnet", "haiku")),
                          ("4", ("opus", "sonnet", "haiku"))):
        for tier in tiers:
            if f"{tier}-{family}" in m:
                key = f"claude-{tier}-{family}"
                if key in PRICING:
                    return PRICING[key]
    return None


@dataclass
class CostBreakdown:
    provider: str
    model: str
    input_tokens: int             # full-price input tokens
    cached_input_tokens: int      # served from cache (cheaper)
    cache_write_tokens: int       # written to cache at the 5-min rate (1.25x)
    cache_write_1h_tokens: int    # written to cache at the 1-hour rate (2x)
    output_tokens: int
    llm_cost_usd: float
    mathpix_pages: int
    mathpix_cost_usd: float
    total_usd: float
    usd_to_inr: float
    total_inr: float
    rates_found: bool
    usage_attested: bool
    # Immutable per-call events.  Older callers can ignore this field; strict v2 uses
    # it to prove that a Haiku primary and an Opus verifier were priced separately.
    events: list[dict] = field(default_factory=list)

    @property
    def billed_input_tokens(self) -> int:
        return (self.input_tokens + self.cached_input_tokens
                + self.cache_write_tokens + self.cache_write_1h_tokens)


def compute_cost(usage: dict | None, mathpix_pages: int = 0,
                 usd_to_inr: float = DEFAULT_USD_TO_INR) -> CostBreakdown:
    """Turn raw token usage + Mathpix page count into a costed breakdown.

    `usage` is the dict populated by the grader (provider, model, input_tokens,
    cached_input_tokens, cache_write_tokens, cache_write_1h_tokens,
    output_tokens). Missing keys → 0.
    """
    usage = usage or {}
    raw_events = [event for event in list(usage.get("_events") or [])
                  if isinstance(event, dict)]
    event_rows = raw_events or [usage]
    inp = cin = cw = cw1h = out = 0
    llm = 0.0
    all_priced = True
    all_attested = True
    models: list[str] = []
    providers: list[str] = []
    costed_events: list[dict] = []
    for event in event_rows:
        event_model = str(event.get("model", "") or "")
        event_provider = str(event.get("provider", "") or "")
        ein = int(event.get("input_tokens", 0) or 0)
        ecin = int(event.get("cached_input_tokens", 0) or 0)
        ecw = int(event.get("cache_write_tokens", 0) or 0)
        ecw1h = int(event.get("cache_write_1h_tokens", 0) or 0)
        eout = int(event.get("output_tokens", 0) or 0)
        if event_model and (ein + ecin + ecw + ecw1h + eout) <= 0:
            all_attested = False
        rates = _match_rates(event_model)
        event_llm = 0.0
        if rates:
            event_llm = (
                ein * rates["input"]
                + ecin * rates.get("cached_input", rates["input"])
                + ecw * rates.get("cache_write", 0.0)
                # A 1-hour write costs 2x base, not 1.25x. Falling back to the 5-min
                # rate here would under-report every batch copy.
                + ecw1h * rates.get("cache_write_1h", rates.get("cache_write", 0.0))
                + eout * rates["output"]
            ) / 1_000_000
        elif event_model:
            all_priced = False
        inp += ein
        cin += ecin
        cw += ecw
        cw1h += ecw1h
        out += eout
        llm += event_llm
        if event_model and event_model not in models:
            models.append(event_model)
        if event_provider and event_provider not in providers:
            providers.append(event_provider)
        if raw_events:
            costed = dict(event)
            costed["llm_cost_usd"] = round(event_llm, 6)
            costed["cost_inr"] = round(event_llm * float(usd_to_inr), 4)
            costed_events.append(costed)
    model = models[0] if len(models) == 1 else ("mixed:" + ",".join(models) if models else "")
    provider = (providers[0] if len(providers) == 1
                else ("mixed:" + ",".join(providers) if providers else ""))

    pages = max(0, int(mathpix_pages or 0))
    mpx = pages * MATHPIX_PER_PAGE_USD
    total = llm + mpx
    return CostBreakdown(
        provider=provider, model=model,
        input_tokens=inp, cached_input_tokens=cin,
        cache_write_tokens=cw, cache_write_1h_tokens=cw1h, output_tokens=out,
        llm_cost_usd=round(llm, 6),
        mathpix_pages=pages, mathpix_cost_usd=round(mpx, 6),
        total_usd=round(total, 6),
        usd_to_inr=float(usd_to_inr),
        total_inr=round(total * float(usd_to_inr), 4),
        rates_found=all_priced and bool(models),
        usage_attested=all_attested and bool(models),
        events=costed_events,
    )


_USAGE_TOKEN_KEYS = (
    "input_tokens", "cached_input_tokens", "cache_write_tokens",
    "cache_write_1h_tokens", "output_tokens",
)


def record_usage_event(target: dict | None, event: dict | None, *, stage: str) -> None:
    """Append one provider response without losing its model-specific price.

    The numeric aggregate is retained for old logs/tools.  ``compute_cost`` prefers the
    event list, so mixed-model follow-ups are no longer billed at whichever model last
    happened to occupy the top-level fields.
    """
    if target is None or not event:
        return
    row = {
        "stage": str(stage or "provider"),
        "provider": str(event.get("provider") or ""),
        "model": str(event.get("model") or ""),
    }
    for key in _USAGE_TOKEN_KEYS:
        row[key] = int(event.get(key, 0) or 0)
    target.setdefault("_events", []).append(row)
    for key in _USAGE_TOKEN_KEYS:
        target[key] = int(target.get(key, 0) or 0) + row[key]
    providers = {str(item.get("provider") or "") for item in target["_events"]}
    models = {str(item.get("model") or "") for item in target["_events"]}
    providers.discard("")
    models.discard("")
    target["provider"] = (next(iter(providers)) if len(providers) == 1 else "mixed")
    target["model"] = (next(iter(models)) if len(models) == 1 else "mixed")


# --- Per-copy budget -------------------------------------------------------
# The department-aware commercial contract, in rupees:
#   school, non-commerce (any total)       -> Rs.15
#   CA/professional or commerce, <=50      -> Rs.15
#   CA/professional or commerce, >50       -> Rs.20
#
# Keep classification and the cap calculation here. BudgetPolicy, the server,
# pipeline release checks, and measurement tools all call budget_for() instead of
# carrying local copies of this table.
BUDGET_SPLIT_MARKS = 50.0
BUDGET_INR_SMALL = 15.0
BUDGET_INR_LARGE = 20.0


_PROFESSIONAL_EXAM_TYPES = frozenset({
    "professional", "ca", "cma", "cs", "chartered accountancy",
})
_COMMERCE_SUBJECT_HINTS = (
    "account", "audit", "banking", "business", "commerce", "corporate",
    "cost", "econom", "entrepreneur", "finance", "financial", "gst",
    "insurance", "law", "management", "marketing", "secretarial", "tax",
)


def is_professional_or_commerce(*, exam_type: str = "", subject: str = "") -> bool:
    """Whether a paper belongs to the department that gets the Rs.20 large-paper tier."""
    normalized_exam = " ".join(str(exam_type or "").strip().lower().split())
    if normalized_exam in _PROFESSIONAL_EXAM_TYPES:
        return True
    normalized_subject = " ".join(str(subject or "").strip().lower().split())
    return any(hint in normalized_subject for hint in _COMMERCE_SUBJECT_HINTS)


def budget_for(total_marks: float | None, *, exam_type: str = "",
               subject: str = "") -> float:
    """Return the single authoritative per-copy provider-cost ceiling.

    AICHECKER_BUDGET_INR overrides it for the whole process. The ceiling is a
    commercial decision, not an engineering one, and it is currently what sets the
    accuracy ceiling: it picks the cheap primary, forbids window fan-out, and
    bounds verification to whatever money is left — which is how a live Core Maths
    copy came back UNGRADABLE with "the budget could not cover: Q15, Q12". Raise it
    for a run where a correct copy matters more than its unit cost. Unset, nothing
    changes.
    """
    override = (os.environ.get("AICHECKER_BUDGET_INR") or "").strip()
    if override:
        try:
            value = float(override)
            if math.isfinite(value) and value > 0:
                return value
        except ValueError:
            pass
    try:
        marks = float(total_marks) if total_marks is not None else None
    except (TypeError, ValueError):
        marks = None
    large_department = is_professional_or_commerce(
        exam_type=exam_type, subject=subject)
    if (large_department and marks is not None and math.isfinite(marks)
            and marks > BUDGET_SPLIT_MARKS):
        return BUDGET_INR_LARGE
    return BUDGET_INR_SMALL
