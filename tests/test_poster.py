"""The live/derived figures added by the "poster" change set.

These are the numbers that can be wrong rather than merely stale — a
projection, a rate over a window, a counterfactual — so most of what is here
is about the boundaries where each one stops being meaningful.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import aggregate, ranges, render_html
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 8, 11, 18, 0, 0)


def rec(message_id: str, day: str, hour: int = 10, minute: int = 0, **overrides) -> UsageRecord:
    """One record. Timestamps are UTC in transcripts; the helper writes them
    as such and lets aggregate convert, exactly as scan.py does."""
    base = dict(
        message_id=message_id,
        ts=f"{day}T{hour:02d}:{minute:02d}:00.000Z",
        day=day,
        model="claude-opus-4-8",       # $5/MTok in, $25/MTok out
        project="alpha",
        skill="(none)",
        session_id="sess-a",
        input_tokens=0,
        output_tokens=1_000_000,       # $25 exactly, so totals are readable
        cache_read_tokens=0,
        cache_write_5m=0,
        cache_write_1h=0,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


# --- cache economics ----------------------------------------------------

def test_cache_saved_is_the_counterfactual_not_the_amount_paid():
    """1M cache-read tokens on opus-4-8: billed at 0.1x $5/MTok = $0.50.
    Uncached they would have cost $5.00, so the saving is $4.50 — the 0.9x
    that was never spent, not the $0.50 that was."""
    data = aggregate.build(
        [rec("m1", "2026-08-11", cache_read_tokens=1_000_000, output_tokens=0)],
        {},
        now=NOW,
    )
    assert data.cache_saved == pytest.approx(4.50)


def test_an_unpriced_model_contributes_no_saving():
    """We cannot know what an unknown model would have cost uncached, so it
    must contribute nothing rather than a zero-rate guess."""
    data = aggregate.build(
        [rec("m1", "2026-08-11", model="claude-future-9", cache_read_tokens=1_000_000)],
        {},
        now=NOW,
    )
    assert data.cache_saved == 0.0


def test_the_page_qualifies_the_saving_as_a_counterfactual():
    """Without the qualifier the number reads as money that was once at
    stake. There is no tooltip on iOS 5.1.1 to explain it after the fact."""
    data = aggregate.build(
        [rec("m1", "2026-08-11", cache_read_tokens=1_000_000)], {}, now=NOW
    )
    out = render_html.render(data)
    assert "caching saved" in out
    assert "same tokens at uncached rates" in out


def test_no_cache_reads_says_so_rather_than_showing_zero():
    data = aggregate.build([rec("m1", "2026-08-11")], {}, now=NOW)
    assert "no cache reads yet" in render_html.render(data)


# --- on pace ------------------------------------------------------------

def test_on_pace_is_suppressed_early_in_a_month():
    """A trailing-7-day window on the 2nd is six parts last month. The
    projection would describe a month that has already ended."""
    early = dt.datetime(2026, 8, 2, 12, 0, 0)
    data = aggregate.build([rec("m1", "2026-08-01")], {}, now=early)
    assert data.on_pace is None
    assert "on pace" not in render_html.render(data)


def test_on_pace_projects_month_to_date_plus_the_trailing_rate():
    """11 Aug: MTD is one $25 day. The trailing 7 days (4th-10th) hold one
    other $25 day, so the rate is 25/7 and 20 days remain."""
    records = [rec("m1", "2026-08-11"), rec("m2", "2026-08-05")]
    data = aggregate.build(records, {}, now=NOW)
    assert data.on_pace == pytest.approx(50.0 + (25.0 / 7) * 20)


def test_quiet_days_count_as_zero_not_as_missing():
    """A weekend with no usage is part of the rate. Averaging only over the
    days that had activity would overstate every projection — here the
    trailing window holds one $25 day and six empty ones, so the rate is
    25/7 a day and not $25 a day.

    The $25 also sits inside month-to-date, which the projection adds on top.
    """
    data = aggregate.build([rec("m1", "2026-08-10")], {}, now=NOW)
    assert data.on_pace == pytest.approx(25.0 + (25.0 / 7) * 20)


def test_on_pace_on_the_last_day_of_a_month_is_just_month_to_date():
    last = dt.datetime(2026, 8, 31, 23, 0, 0)
    data = aggregate.build([rec("m1", "2026-08-31")], {}, now=last)
    assert data.on_pace == pytest.approx(25.0)


def test_the_page_states_which_projection_method_it_used():
    """Two defensible methods disagree. The label is what stops the number
    being unfalsifiable."""
    data = aggregate.build([rec("m1", "2026-08-11")], {}, now=NOW)
    out = render_html.render(data)
    assert "on pace for" in out
    assert "last 7 days" in out


# --- burn rate ----------------------------------------------------------

def test_burn_rate_is_cost_over_active_time():
    """Two messages 3 minutes apart — inside the idle cap, so the whole gap
    counts. $50 over 3 minutes is $1000/hr."""
    records = [
        rec("m1", "2026-08-11", hour=9, minute=0),
        rec("m2", "2026-08-11", hour=9, minute=3),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly == pytest.approx(1000.0)


def test_a_long_gap_contributes_only_the_idle_cap():
    """The load-bearing case: a session left open across a lunch break. Wall
    clock would say 4 hours and dilute the rate to nothing; capped, the gap
    contributes 5 minutes."""
    records = [
        rec("m1", "2026-08-11", hour=9, minute=0),
        rec("m2", "2026-08-11", hour=13, minute=0),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly == pytest.approx(50.0 / (aggregate.IDLE_CAP_SECONDS / 3600))


def test_a_single_message_today_does_not_divide_by_zero():
    data = aggregate.build([rec("m1", "2026-08-11")], {}, now=NOW)
    assert data.burn_rate_hourly is None
    assert data.idle_minutes is None


def test_no_messages_today_suppresses_the_segment_entirely():
    """Rendering $0.00/hr on a quiet day is worse than rendering nothing: it
    reads as a measurement rather than an absence."""
    data = aggregate.build([rec("m1", "2026-08-04")], {}, now=NOW)
    assert data.burn_rate_hourly is None
    out = render_html.render(data)
    assert "/hr" not in out


def test_idle_minutes_counts_from_the_last_message():
    records = [
        rec("m1", "2026-08-11", hour=9, minute=0),
        rec("m2", "2026-08-11", hour=9, minute=30),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.idle_minutes is not None
    assert data.idle_minutes > 0
    assert "idle" in render_html.render(data)


# --- BY SKILL -----------------------------------------------------------

def test_the_unattributed_bucket_is_excluded_from_by_skill():
    records = [
        rec("m1", "2026-08-11", skill="(none)"),
        rec("m2", "2026-08-11", skill="tdd"),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert [bar.label for bar in data.by_skill] == ["tdd"]


def test_by_skill_shares_renormalise_within_the_panel():
    """Shares must add to 1.0 across what is shown. Leaving them relative to
    the grand total would print a panel whose percentages sum to 10%."""
    records = [
        rec("m1", "2026-08-11", skill="(none)"),
        rec("m2", "2026-08-11", skill="(none)"),
        rec("m3", "2026-08-11", skill="tdd"),
        rec("m4", "2026-08-11", skill="review"),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert sum(bar.share for bar in data.by_skill) == pytest.approx(1.0)


def test_an_entirely_unattributed_history_yields_an_empty_panel():
    data = aggregate.build([rec("m1", "2026-08-11")], {}, now=NOW)
    assert data.by_skill == []
    assert "no data yet" in render_html.render(data)


def test_the_heading_declares_the_exclusion():
    """Silently dropping 90% of the spend would change what the percentages
    mean without saying so."""
    records = [rec("m1", "2026-08-11", skill="tdd")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert "BY SKILL" in out
    assert "ATTRIBUTED" in out


# --- the daily chart ----------------------------------------------------

def test_todays_column_is_marked():
    records = [rec("m1", "2026-08-11"), rec("m2", "2026-08-10")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert out.count('class="col today"') == 1


def test_no_column_is_marked_when_today_has_no_usage():
    records = [rec("m1", "2026-08-10"), rec("m2", "2026-08-09")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert 'class="col today"' not in out


# --- the default range --------------------------------------------------

def test_the_default_range_is_not_all_time():
    """An all-time default is why every panel below the selector was the most
    static content on the page."""
    assert ranges.DEFAULT.key != "all"


def test_the_hero_row_is_unaffected_by_the_default_range():
    """Changing the default must not move the summary — it is global by
    construction and several deltas depend on that."""
    records = [rec("m1", "2026-08-11"), rec("m2", "2026-01-02")]
    default = aggregate.build(records, {}, now=NOW)
    everything = aggregate.build(records, {}, now=NOW, range_key="all")
    assert default.all_time == everything.all_time
    assert default.today == everything.today
    assert default.active_days == everything.active_days


# --- iOS 5 lint on the new elements -------------------------------------

def test_the_new_elements_survive_the_compatibility_lint():
    records = [
        rec("m1", "2026-08-11", hour=9, minute=0, cache_read_tokens=1_000_000),
        rec("m2", "2026-08-11", hour=9, minute=30, skill="tdd"),
    ]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert "<script" not in out.lower()
    assert "display:flex" not in out.replace(" ", "")
    assert "display:grid" not in out.replace(" ", "")
    assert "var(--" not in out
