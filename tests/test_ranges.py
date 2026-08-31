"""Selecting which slice of history the breakdown panels cover."""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import aggregate, ranges, render_html
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 8, 7, 12, 0, 0)


def rec(message_id: str, day: str, **overrides) -> UsageRecord:
    base = dict(
        message_id=message_id,
        ts=f"{day}T10:00:00.000Z",
        day=day,
        model="claude-opus-4-8",
        project="alpha",
        skill="(none)",
        session_id="sess-a",
        input_tokens=0,
        output_tokens=1_000_000,   # $25 on opus-4-8, so totals are easy to read
        cache_read_tokens=0,
        cache_write_5m=0,
        cache_write_1h=0,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


# One record on each of several days, spread to straddle every boundary.
SPREAD = [
    rec("today", "2026-08-07"),        # today
    rec("d3", "2026-08-04"),           # within 7d, this month
    rec("d10", "2026-07-28"),          # within 30d, last month
    rec("d60", "2026-06-08"),          # all time only
]


# --- the catalogue ------------------------------------------------------

def test_unknown_range_falls_back_to_the_default():
    """A stale bookmark or a hand-typed URL must not error the page."""
    for value in (None, "", "  ", "last-tuesday", "7D "):
        assert ranges.resolve(value).key in {r.key for r in ranges.CATALOGUE}
    assert ranges.resolve("nonsense") == ranges.DEFAULT


def test_known_keys_resolve_exactly():
    assert ranges.resolve("7d").key == "7d"
    assert ranges.resolve("  MONTH ").key == "month"


@pytest.mark.parametrize(
    "key,expected",
    [
        ("today", {"2026-08-07"}),
        ("7d", {"2026-08-07", "2026-08-04"}),
        ("30d", {"2026-08-07", "2026-08-04", "2026-07-28"}),
        ("month", {"2026-08-07", "2026-08-04"}),
        ("all", {"2026-08-07", "2026-08-04", "2026-07-28", "2026-06-08"}),
    ],
)
def test_each_range_selects_the_right_days(key, expected):
    inside = ranges.contains(ranges.resolve(key), NOW)
    assert {r.day for r in SPREAD if inside(r.day)} == expected


def test_an_unparseable_day_is_never_in_a_bounded_range():
    """scan.local_day returns '' for a timestamp it cannot read. Such a record
    has no place on a timeline and must only reach all-time totals."""
    for key in ("today", "7d", "30d", "month"):
        assert not ranges.contains(ranges.resolve(key), NOW)("")
        assert not ranges.contains(ranges.resolve(key), NOW)("not-a-date")


# --- what the range does and does not move ------------------------------

def test_panels_follow_the_selected_range():
    week = aggregate.build(SPREAD, {}, now=NOW, range_key="7d")
    assert sum(bar.cost for bar in week.scoped.money) == pytest.approx(50.0)  # 2 records

    everything = aggregate.build(SPREAD, {}, now=NOW, range_key="all")
    assert sum(bar.cost for bar in everything.scoped.money) == pytest.approx(100.0)


def test_the_hero_row_never_follows_the_range():
    """The top row is the fixed summary — it must read the same whatever is
    selected, or the page contradicts itself."""
    a = aggregate.build(SPREAD, {}, now=NOW, range_key="today")
    b = aggregate.build(SPREAD, {}, now=NOW, range_key="all")
    for field in ("today", "last_7_days", "month_to_date", "all_time"):
        assert getattr(a, field) == getattr(b, field)
    assert a.active_days == b.active_days == 4


def test_shares_are_relative_to_the_selected_range():
    """A panel's percentages must add up to what that panel is showing, not
    to a total the reader cannot see."""
    week = aggregate.build(SPREAD, {}, now=NOW, range_key="7d")
    assert sum(bar.share for bar in week.scoped.by_model) == pytest.approx(1.0)
    assert sum(bar.share for bar in week.scoped.money) == pytest.approx(1.0)


def test_delegation_and_source_bands_follow_the_range():
    records = [
        rec("m1", "2026-08-07"),
        rec("m2", "2026-06-08", is_subagent=True),  # outside 7d
    ]
    week = aggregate.build(records, {}, now=NOW, range_key="7d")
    assert week.scoped.subagent_cost == 0.0
    everything = aggregate.build(records, {}, now=NOW, range_key="all")
    assert everything.scoped.subagent_cost == pytest.approx(25.0)


def test_an_empty_range_renders_rather_than_dividing_by_zero():
    """Pick 'today' on a day with no usage: every panel is empty and every
    share would be 0/0."""
    data = aggregate.build([rec("old", "2026-06-08")], {}, now=NOW, range_key="today")
    assert data.scoped.money == []
    assert data.scoped.avg_cost_per_message == 0.0
    out = render_html.render(data)
    assert out.startswith("<!DOCTYPE html>")
    assert "no data yet" in out


# --- the selector ----------------------------------------------------------

def test_every_range_gets_a_link_and_only_one_is_current():
    out = render_html.render(aggregate.build(SPREAD, {}, now=NOW, range_key="7d"))
    for entry in ranges.CATALOGUE:
        assert f">{entry.label}<" in out
    assert out.count('class="range on"') == 1


def test_the_default_range_links_without_a_query_string():
    """The default range and the bare URL must not be two different cached
    pages. Written against ranges.DEFAULT rather than a hard-coded key, so
    changing the default cannot silently create that split."""
    out = render_html.render(
        aggregate.build(SPREAD, {}, now=NOW, range_key=ranges.DEFAULT.key),
        base_path="/d/tok",
    )
    assert 'href="/d/tok"' in out
    assert f"range={ranges.DEFAULT.key}" not in out


def test_every_non_default_range_carries_its_query_string():
    out = render_html.render(aggregate.build(SPREAD, {}, now=NOW), base_path="/d/tok")
    for entry in ranges.CATALOGUE:
        if entry.key != ranges.DEFAULT.key:
            assert f"?range={entry.key}" in out


def test_panel_headings_name_the_selected_range():
    out = render_html.render(aggregate.build(SPREAD, {}, now=NOW, range_key="30d"))
    assert "LAST 30 DAYS" in out
    assert "ALL TIME" not in out.split('class="grid"')[1]


# --- the refresh ------------------------------------------------------------


    """The refresh must reload the current URL, query string and all.

    Sending it to a fixed URL instead resets the selection roughly every 30
    seconds, which is what the first version did and what made the feature
    unusable in practice: you cannot read a range that keeps vanishing.
    A bare content="N" reloads whatever is in the address bar.
    """

    assert 'content="30"' in out
    assert "url=" not in out.split("</head>")[0], "the refresh must not navigate away"


# --- refresh interval -------------------------------------------------------

def test_returning_to_the_default_is_always_one_click_away():
    """With the selection now sticky, the ALL TIME link is the only way back —
    it must be present and must point at the bare URL."""
    out = render_html.render(
        aggregate.build(SPREAD, {}, now=NOW, range_key="today"), base_path="/d/tok"
    )
    assert 'href="/d/tok"' in out


# --- the daily chart's axis -------------------------------------------------

def test_day_span_is_every_calendar_day_up_to_today():
    assert ranges.day_span("2026-08-04", NOW) == [
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
    ]


def test_day_span_keeps_the_days_nothing_happened_on():
    """A weekend is two days whatever was spent on it. The span is built
    from the calendar, so it cannot skip them."""
    span = ranges.day_span("2026-07-31", NOW)
    assert "2026-08-01" in span and "2026-08-02" in span  # a Saturday and a Sunday
    assert span == sorted(span)
    assert len(span) == 8


def test_day_span_of_a_single_day_is_that_day():
    assert ranges.day_span("2026-08-07", NOW) == ["2026-08-07"]


def test_day_span_of_a_day_that_will_not_parse_is_empty():
    """Same rule as every other window: a day that cannot be placed on a
    timeline is not on this one either."""
    assert ranges.day_span("not-a-day", NOW) == []


def test_day_span_does_not_run_backwards_from_a_future_day():
    """A skewed clock on the writing machine can stamp a record ahead of the
    reading one. That is one bar, not a negative range."""
    assert ranges.day_span("2026-08-09", NOW) == ["2026-08-09"]
