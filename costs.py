"""Per-run cost accounting for one grading call (LLM tokens + Mathpix pages).

Pricing lives in PRICING / MATHPIX_PER_PAGE_USD below — edit those when rates change.
All token rates are USD per 1,000,000 tokens. Sources + last-verified date are in the
comments; treat them as the single place to update when a provider changes prices.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# PRICING — USD per 1,000,000 tokens. Update here when rates change.
#   Gemini:  https://ai.google.dev/gemini-api/docs/pricing
#   Claude:  https://platform.claude.com/docs/en/about-claude/models/overview
#   Last verified: 2026-06-08
# Keys are matched against the model id by substring (longest/most-specific wins
# via the explicit family fallback in _match_rates).
# ---------------------------------------------------------------------------
PRICING: dict[str, dict[str, float]] = {
    # --- Gemini (Google) --- (cached_input = context-cache read rate)
    "gemini-2.5-pro":   {"input": 1.25, "cached_input": 0.125, "output": 10.00},  # ≤200k-token prompts
    "gemini-2.5-flash": {"input": 0.30, "cached_input": 0.075, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
    # --- Claude (Anthropic) --- cache_read ≈ 0.1x input, cache_write ≈ 1.25x input (5-min TTL)
    "claude-opus-4":   {"input": 5.00, "cached_input": 0.50, "cache_write": 6.25,  "output": 25.00},
    "claude-sonnet-4": {"input": 3.00, "cached_input": 0.30, "cache_write": 3.75,  "output": 15.00},
    "claude-haiku-4":  {"input": 1.00, "cached_input": 0.10, "cache_write": 1.25,  "output": 5.00},
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
    # Family fallback for versioned ids not caught above.
    if "opus-4" in m:
        return PRICING["claude-opus-4"]
    if "sonnet-4" in m:
        return PRICING["claude-sonnet-4"]
    if "haiku-4" in m:
        return PRICING["claude-haiku-4"]
    return None


@dataclass
class CostBreakdown:
    provider: str
    model: str
    input_tokens: int          # full-price input tokens
    cached_input_tokens: int   # served from cache (cheaper)
    cache_write_tokens: int    # written to cache (Claude only; premium)
    output_tokens: int
    llm_cost_usd: float
    mathpix_pages: int
    mathpix_cost_usd: float
    total_usd: float
    usd_to_inr: float
    total_inr: float
    rates_found: bool

    @property
    def billed_input_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.cache_write_tokens


def compute_cost(usage: dict | None, mathpix_pages: int = 0,
                 usd_to_inr: float = DEFAULT_USD_TO_INR) -> CostBreakdown:
    """Turn raw token usage + Mathpix page count into a costed breakdown.

    `usage` is the dict populated by the grader (provider, model, input_tokens,
    cached_input_tokens, cache_write_tokens, output_tokens). Missing keys → 0.
    """
    usage = usage or {}
    model = str(usage.get("model", "") or "")
    provider = str(usage.get("provider", "") or "")
    inp = int(usage.get("input_tokens", 0) or 0)
    cin = int(usage.get("cached_input_tokens", 0) or 0)
    cw = int(usage.get("cache_write_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)

    rates = _match_rates(model)
    if rates:
        llm = (
            inp * rates["input"]
            + cin * rates.get("cached_input", rates["input"])
            + cw * rates.get("cache_write", 0.0)
            + out * rates["output"]
        ) / 1_000_000
    else:
        llm = 0.0

    pages = max(0, int(mathpix_pages or 0))
    mpx = pages * MATHPIX_PER_PAGE_USD
    total = llm + mpx
    return CostBreakdown(
        provider=provider, model=model,
        input_tokens=inp, cached_input_tokens=cin, cache_write_tokens=cw, output_tokens=out,
        llm_cost_usd=round(llm, 6),
        mathpix_pages=pages, mathpix_cost_usd=round(mpx, 6),
        total_usd=round(total, 6),
        usd_to_inr=float(usd_to_inr),
        total_inr=round(total * float(usd_to_inr), 4),
        rates_found=rates is not None,
    )
