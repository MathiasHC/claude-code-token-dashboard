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
    assert data.scoped.cache_saved == pytest.approx(4.50)


def test_an_unpriced_model_contributes_no_saving():
    """We cannot know what an unknown model would have cost uncached, so it
    must contribute nothing rather than a zero-rate guess."""
    data = aggregate.build(
        [rec("m1", "2026-08-11", model="claude-future-9", cache_read_tokens=1_000_000)],
        {},
        now=NOW,
    )
    assert data.scoped.cache_saved == 0.0


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
    assert "on pace" in out
    assert "7-day rate" in out


# --- burn rate ----------------------------------------------------------
# Timestamps are derived from NOW rather than written as fixed UTC hours: a
# fixed hour lands in tomorrow (or the future) for machines far enough east,
# which made an earlier version of these tests fail at UTC+9 and beyond.


def _ago(minutes: int) -> dt.datetime:
    return NOW - dt.timedelta(minutes=minutes)


def at(moment: dt.datetime, message_id: str, **overrides) -> UsageRecord:
    """A record at a given *local* moment, stored as the UTC string a
    transcript would actually contain."""
    utc = moment.astimezone(dt.timezone.utc)
    return rec(
        message_id,
        moment.date().isoformat(),
        **{"ts": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), **overrides},
    )


def test_burn_rate_is_cost_over_active_time():
    """Five messages five minutes apart: four gaps at exactly the idle cap is
    20 minutes of active time, and $125 over 20 minutes is $375/hr."""
    records = [at(_ago(120 - 5 * i), f"m{i}") for i in range(5)]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly == pytest.approx(375.0)


def test_a_long_gap_contributes_only_the_idle_cap():
    """A session left open across the afternoon. Four messages spanning nine
    hours give three capped gaps — 15 minutes of active time, not nine hours,
    so the rate describes the work rather than the wall clock."""
    records = [at(_ago(600 - 180 * i), f"m{i}") for i in range(4)]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly == pytest.approx(100.0 / (900 / 3600))


def test_too_little_active_time_shows_no_rate_at_all():
    """The defect this floor exists for. On real history the first three
    messages of a day are seconds apart, and dividing by that produced
    $2,219/hr against a true $20.59/hr — a 108x overstatement that stayed on
    screen for a quarter of an hour."""
    records = [at(_ago(120), "m1"), at(_ago(119), "m2"), at(_ago(118), "m3")]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly is None
    assert "/hr" not in render_html.render(data)


def test_the_floor_is_active_time_not_message_count():
    """Many messages inside a couple of minutes still must not produce a
    rate — it is the denominator that is too small, not the sample."""
    records = [at(_ago(120) + dt.timedelta(seconds=5 * i), f"m{i}") for i in range(40)]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly is None


def test_a_single_message_today_does_not_divide_by_zero():
    data = aggregate.build([at(_ago(60), "m1")], {}, now=NOW)
    assert data.burn_rate_hourly is None
    assert data.idle_minutes is None


def test_no_messages_today_suppresses_the_segment_entirely():
    """Rendering $0.00/hr on a quiet day is worse than rendering nothing: it
    reads as a measurement rather than an absence."""
    data = aggregate.build([rec("m1", "2026-08-04")], {}, now=NOW)
    assert data.burn_rate_hourly is None
    assert "/hr" not in render_html.render(data)


def test_an_entirely_unpriced_day_shows_no_rate():
    """Cost over active time is 0/x. The docstring promises never to print
    $0.00/hr, and a zero numerator is as misleading as a tiny denominator."""
    records = [
        at(_ago(120 - 5 * i), f"m{i}", model="claude-future-9") for i in range(5)
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.burn_rate_hourly is None
    assert "/hr" not in render_html.render(data)


def test_idle_minutes_counts_from_the_last_message():
    records = [at(_ago(120 - 5 * i), f"m{i}") for i in range(5)]
    data = aggregate.build(records, {}, now=NOW)
    assert data.idle_minutes == pytest.approx(100, abs=1)
    assert "idle" in render_html.render(data)


# --- BY SKILL -----------------------------------------------------------

def test_the_unattributed_bucket_is_excluded_from_by_skill():
    records = [
        rec("m1", "2026-08-11", skill="(none)"),
        rec("m2", "2026-08-11", skill="tdd"),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert [bar.label for bar in data.scoped.by_skill] == ["tdd"]


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
    assert sum(bar.share for bar in data.scoped.by_skill) == pytest.approx(1.0)


def test_an_entirely_unattributed_history_yields_an_empty_panel():
    data = aggregate.build([rec("m1", "2026-08-11")], {}, now=NOW)
    assert data.scoped.by_skill == []
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


def test_todays_column_is_present_and_marked_even_when_today_is_idle():
    """Today used to drop out of the chart until it had cost something, so a
    day that had not started yet looked exactly like a day that was not in
    the window. It is now a marked column sitting on the baseline — the
    existing 1px height floor is what keeps the marker findable, which is
    the only job the colour has."""
    records = [rec("m1", "2026-08-10"), rec("m2", "2026-08-09")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert out.count('class="col today"') == 1
    assert 'class="col today" style="height:1px"' in out


# --- the default range --------------------------------------------------

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

