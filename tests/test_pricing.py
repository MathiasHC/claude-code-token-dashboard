from __future__ import annotations

import pytest

from dashboard import pricing
from dashboard.models import UsageRecord


def rec(model: str, speed: str | None = None, **tokens) -> UsageRecord:
    return UsageRecord(
        message_id="m",
        ts="2026-07-30T10:00:00.000Z",
        day="2026-07-30",
        model=model,
        project="p",
        skill="(none)",
        session_id="s",
        speed=speed,
        **tokens,
    )


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-fable-5", 10.0),
        ("claude-mythos-5", 10.0),
        ("claude-opus-5", 5.0),
        ("claude-opus-4-8", 5.0),
        ("claude-opus-4-7", 5.0),
        ("claude-opus-4-6", 5.0),
        ("claude-opus-4-5", 5.0),
        ("claude-sonnet-5", 3.0),
        ("claude-sonnet-4-6", 3.0),
        ("claude-sonnet-4-5", 3.0),
        ("claude-haiku-4-5", 1.0),
    ],
)
def test_one_million_fresh_input_tokens_costs_the_input_rate(model, expected):
    assert pricing.cost(rec(model, input_tokens=1_000_000)).fresh_input == pytest.approx(expected)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-fable-5", 50.0),
        ("claude-mythos-5", 50.0),
        ("claude-opus-5", 25.0),
        ("claude-opus-4-8", 25.0),
        ("claude-opus-4-7", 25.0),
        ("claude-opus-4-6", 25.0),
        ("claude-opus-4-5", 25.0),
        ("claude-sonnet-5", 15.0),
        ("claude-sonnet-4-6", 15.0),
        ("claude-sonnet-4-5", 15.0),
        ("claude-haiku-4-5", 5.0),
    ],
)
def test_one_million_output_tokens_costs_the_output_rate(model, expected):
    assert pricing.cost(rec(model, output_tokens=1_000_000)).output == pytest.approx(expected)


def test_cache_read_is_one_tenth_of_input_rate():
    # opus-4-8 input is $5/MTok, so a cache read of 1M tokens costs $0.50
    assert pricing.cost(rec("claude-opus-4-8", cache_read_tokens=1_000_000)).cache_read == pytest.approx(0.50)


def test_five_minute_cache_write_is_1_25x_input_rate():
    assert pricing.cost(rec("claude-opus-4-8", cache_write_5m=1_000_000)).cache_write == pytest.approx(6.25)


def test_one_hour_cache_write_is_2x_input_rate():
    """The expensive one. 1h writes dominate real usage; billing them at
    1.25x understates cost badly."""
    assert pricing.cost(rec("claude-opus-4-8", cache_write_1h=1_000_000)).cache_write == pytest.approx(10.00)


def test_both_cache_write_ttls_sum_into_one_figure():
    got = pricing.cost(rec("claude-opus-4-8", cache_write_5m=1_000_000, cache_write_1h=1_000_000))
    assert got.cache_write == pytest.approx(16.25)


def test_total_sums_all_four_components():
    got = pricing.cost(
        rec(
            "claude-opus-4-8",
            input_tokens=1_000,
            output_tokens=2_000,
            cache_read_tokens=100_000,
            cache_write_5m=4_000,
            cache_write_1h=6_000,
        )
    )
    assert got.total == pytest.approx(0.19)


def test_fast_mode_doubles_opus_rates():
    got = pricing.cost(rec("claude-opus-5", speed="fast", input_tokens=1_000_000, output_tokens=1_000_000))
    assert got.fresh_input == pytest.approx(10.0)
    assert got.output == pytest.approx(50.0)


def test_fast_mode_on_a_model_without_fast_rates_falls_back_to_standard():
    got = pricing.cost(rec("claude-haiku-4-5", speed="fast", input_tokens=1_000_000))
    assert got.fresh_input == pytest.approx(1.0)


def test_unknown_model_costs_nothing_but_does_not_raise():
    got = pricing.cost(rec("claude-future-9", input_tokens=1_000_000, output_tokens=1_000_000))
    assert got.total == 0.0


def test_is_priced_distinguishes_known_from_unknown_models():
    assert pricing.is_priced("claude-opus-4-8") is True
    assert pricing.is_priced("claude-future-9") is False
    assert pricing.is_priced("<synthetic>") is False


def test_dated_model_id_is_normalised_to_its_rate_table_entry():
    """claude-haiku-4-5-20251001 appears in real subagent transcripts; without
    normalisation it misses the rate table and shows as unpriced."""
    got = pricing.cost(rec("claude-haiku-4-5-20251001", output_tokens=1_000_000))
    assert got.output == pytest.approx(5.0)


def test_dated_model_id_is_reported_as_priced():
    assert pricing.is_priced("claude-haiku-4-5-20251001") is True


def test_normalise_model_strips_only_an_eight_digit_suffix():
    assert pricing.normalise_model("claude-opus-4-8") == "claude-opus-4-8"
    assert pricing.normalise_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"


