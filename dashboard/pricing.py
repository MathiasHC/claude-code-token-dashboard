"""Model rate table and cost arithmetic. Pure — no I/O, no clock."""

from __future__ import annotations

import re

from .models import CostBreakdown, UsageRecord

# Some transcripts carry a dated model id, e.g. claude-haiku-4-5-20251001.
DATED_MODEL_SUFFIX = re.compile(r"-\d{8}$")

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.00

# model -> (input $/MTok, output $/MTok), Claude API first-party list rates.
STANDARD_RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Fast mode is a research preview on Opus 5 / 4.8 only, priced at $10/$50.
FAST_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (10.0, 50.0),
    "claude-opus-4-8": (10.0, 50.0),
}


def normalise_model(model: str) -> str:
    """Strip a trailing -YYYYMMDD so dated ids hit the rate table."""
    return DATED_MODEL_SUFFIX.sub("", model or "")


def rates_for(model: str, speed: str | None = None) -> tuple[float, float] | None:
    key = normalise_model(model)
    if speed == "fast" and key in FAST_RATES:
        return FAST_RATES[key]
    return STANDARD_RATES.get(key)


def is_priced(model: str) -> bool:
    return rates_for(model) is not None


def cost(record: UsageRecord) -> CostBreakdown:
    """Cost of one message. An unpriced model yields an all-zero breakdown;
    its tokens are still counted elsewhere."""
    found = rates_for(record.model, record.speed)
    if found is None:
        return CostBreakdown()
    rate_in, rate_out = found
    per_token_in = rate_in / 1_000_000
    return CostBreakdown(
        fresh_input=record.input_tokens * per_token_in,
        cache_read=record.cache_read_tokens * per_token_in * CACHE_READ_MULTIPLIER,
        cache_write=(
            record.cache_write_5m * per_token_in * CACHE_WRITE_5M_MULTIPLIER
            + record.cache_write_1h * per_token_in * CACHE_WRITE_1H_MULTIPLIER
        ),
        output=record.output_tokens * (rate_out / 1_000_000),
    )
