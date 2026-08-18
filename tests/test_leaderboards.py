"""The all-time boards.

Every test here builds through aggregate.build rather than calling
leaderboards.build directly, because the property most worth protecting is
the one that spans the two: these boards are global, and no range selection
may narrow them.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import aggregate, leaderboards
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 7, 30, 12, 0, 0)


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
        output_tokens=1_000_000,  # $25 on opus-4-8
        cache_read_tokens=0,
        cache_write_5m=0,
        cache_write_1h=0,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


def board(records, title: str, titles=None):
    data = aggregate.build(records, titles or {}, now=NOW)
    return next(b for b in data.leaderboards if b.title == title)


def placings(records, title: str, titles=None):
    return [(l.label, l.value) for l in board(records, title, titles).leaders]


# --- scope ---------------------------------------------------------------


def test_the_boards_do_not_follow_the_selected_range():
    """The property the whole section rests on.

    A day outside the selected range still places, because "top 3 all time"
    means all time. If this ever fails the heading on the page becomes a
    lie, which is worse than the panel being wrong.
    """
    records = [
        rec("m1", "2026-07-30"),  # today, inside every range
        rec("m2", "2026-01-05", output_tokens=4_000_000),  # far outside 7d
    ]
    inside = placings(records, "DATE · SPEND")
    data = aggregate.build(records, {}, now=NOW, range_key="7d")
    scoped = [
        (l.label, l.value)
        for b in data.leaderboards
        if b.title == "DATE · SPEND"
        for l in b.leaders
    ]
    assert inside == scoped
    assert scoped[0][0] == "05 Jan 2026"


def test_every_board_is_built_even_with_no_history():
    """Twelve boards, so the section is never a grid with holes in it."""
    data = aggregate.build([], {}, now=NOW)
    assert len(data.leaderboards) == 12
    assert all(b.leaders == [] for b in data.leaderboards)


def test_a_board_keeps_at_most_three_placings():
    records = [rec(f"m{i}", f"2026-07-{10 + i:02d}") for i in range(6)]
    assert len(placings(records, "DATE · SPEND")) == leaderboards.TOP == 3


# --- weekdays ------------------------------------------------------------


def test_weekday_spend_sums_every_occurrence_of_that_weekday():
    """Two Thursdays outrank one bigger Monday: the board ranks weekdays,
    not days."""
    records = [
        rec("m1", "2026-07-30"),  # Thursday, $25
        rec("m2", "2026-07-23"),  # Thursday, $25
        rec("m3", "2026-07-27", output_tokens=1_600_000),  # Monday, $40
    ]
    assert placings(records, "WEEKDAY · SPEND") == [
        ("Thursday", pytest.approx(50.0)),
        ("Monday", pytest.approx(40.0)),
    ]


def test_weekday_sessions_counts_distinct_sessions_not_messages():
    records = [
        rec("m1", "2026-07-30", session_id="s1"),
        rec("m2", "2026-07-30", session_id="s1"),
        rec("m3", "2026-07-30", session_id="s2"),
    ]
    assert placings(records, "WEEKDAY · SESSIONS") == [("Thursday", 2)]


def test_a_session_running_past_midnight_counts_on_both_weekdays():
    """Documented behaviour, not an accident: crediting the whole session to
    the day it opened on would hand every late night to the day before."""
    records = [
        rec("m1", "2026-07-30", session_id="s1"),  # Thursday
        rec("m2", "2026-07-31", session_id="s1"),  # Friday, same session
    ]
    counts = dict(placings(records, "WEEKDAY · SESSIONS"))
    assert counts == {"Thursday": 1, "Friday": 1}


def test_weekday_messages_counts_records():
    records = [rec("m1", "2026-07-30"), rec("m2", "2026-07-30")]
    assert placings(records, "WEEKDAY · MESSAGES") == [("Thursday", 2)]


# --- calendar labels -----------------------------------------------------


def test_month_is_labelled_the_way_it_was_asked_for():
    records = [rec("m1", "2026-07-30")]
    assert placings(records, "MONTH · SPEND") == [("07 2026", pytest.approx(25.0))]


def test_a_date_is_labelled_in_full():
    records = [rec("m1", "2026-07-30")]
    assert placings(records, "DATE · SPEND")[0][0] == "30 Jul 2026"


# --- prompts -------------------------------------------------------------


def test_a_prompt_is_the_sum_of_its_messages_machine_time():
    """One human turn and everything the machine did answering it — which is
    why prompt_run is carried across records rather than read off one."""
    records = [
        rec("m1", "2026-07-30", prompt_run="p1", work_seconds=60.0),
        rec("m2", "2026-07-30", prompt_run="p1", work_seconds=90.0),
        rec("m3", "2026-07-30", prompt_run="p2", work_seconds=100.0),
    ]
    assert placings(records, "LONGEST PROMPT") == [
        ("30 Jul 2026", pytest.approx(150.0)),
        ("30 Jul 2026", pytest.approx(100.0)),
    ]


def test_a_prompt_carries_how_many_messages_it_took():
    records = [
        rec("m1", "2026-07-30", prompt_run="p1", work_seconds=60.0),
        rec("m2", "2026-07-30", prompt_run="p1", work_seconds=90.0),
    ]
    assert board(records, "LONGEST PROMPT").leaders[0].note == "2 msgs"


def test_a_prompt_is_dated_from_its_earliest_message_not_the_first_seen():
    """Records do not have to arrive in order, and a prompt dated from
    whichever message happened to come first would drift by a day."""
    records = [
        rec("m2", "2026-07-31", prompt_run="p1", work_seconds=10.0),
        rec("m1", "2026-07-30", prompt_run="p1", work_seconds=10.0),
    ]
    assert placings(records, "LONGEST PROMPT")[0][0] == "30 Jul 2026"


def test_messages_with_no_prompt_recorded_do_not_form_a_prompt():
    """Rows written before the column existed must not all collapse into one
    giant phantom prompt under the empty-string id."""
    records = [
        rec("m1", "2026-07-30", work_seconds=60.0),
        rec("m2", "2026-07-29", work_seconds=90.0),
    ]
    assert placings(records, "LONGEST PROMPT") == []


def test_the_priciest_prompt_is_ranked_by_cost_not_by_time():
    records = [
        rec("m1", "2026-07-30", prompt_run="slow", work_seconds=999.0,
            output_tokens=100),
        rec("m2", "2026-07-29", prompt_run="dear", work_seconds=1.0,
            output_tokens=4_000_000),
    ]
    assert placings(records, "LONGEST PROMPT")[0][0] == "30 Jul 2026"
    assert placings(records, "PRICIEST PROMPT")[0] == (
        "29 Jul 2026",
        pytest.approx(100.0),
    )


# --- streaks and breaks --------------------------------------------------


def test_a_streak_is_a_run_of_consecutive_active_days():
    records = [
        rec("m1", "2026-07-20"),
        rec("m2", "2026-07-21"),
        rec("m3", "2026-07-22"),
        rec("m4", "2026-07-30"),  # separate run
    ]
    assert placings(records, "LONGEST STREAK") == [
        ("20–22 Jul 2026", 3),
        ("30 Jul 2026", 1),
    ]


def test_a_streak_spanning_two_months_names_both():
    records = [rec("m1", "2026-06-30"), rec("m2", "2026-07-01")]
    assert placings(records, "LONGEST STREAK")[0][0] == "30 Jun–01 Jul 2026"


def test_a_break_counts_the_days_off_not_the_days_either_side():
    """29 to 31 July is one day off, not two and not three."""
    records = [rec("m1", "2026-07-29"), rec("m2", "2026-07-31")]
    assert placings(records, "LONGEST BREAK") == [("29 Jul → 31 Jul 2026", 1)]


def test_consecutive_days_produce_no_break():
    records = [rec("m1", "2026-07-29"), rec("m2", "2026-07-30")]
    assert placings(records, "LONGEST BREAK") == []


# --- clock ---------------------------------------------------------------


def test_two_in_the_morning_is_a_later_night_than_eleven_at_night():
    """The whole reason the hour is re-based. On the raw 0-23 clock 23 beats
    02 and the board would crown the earlier night."""
    records = [
        rec("m1", "2026-07-29", hour=23),
        rec("m2", "2026-07-30", hour=2),
    ]
    assert placings(records, "LATEST NIGHT") == [("30 Jul 2026", 2), ("29 Jul 2026", 23)]


def test_one_night_places_once_however_many_messages_it_had():
    records = [
        rec("m1", "2026-07-30", hour=2),
        rec("m2", "2026-07-30", hour=2),
        rec("m3", "2026-07-29", hour=1),
    ]
    # 02:00 on the 30th outranks 01:00 on the 29th, and the 30th appears
    # once rather than twice despite carrying two messages at that hour.
    assert [label for label, _ in placings(records, "LATEST NIGHT")] == [
        "30 Jul 2026",
        "29 Jul 2026",
    ]


def test_the_earliest_start_rides_along_as_a_note():
    records = [rec("m1", "2026-07-30", hour=7), rec("m2", "2026-07-29", hour=23)]
    assert board(records, "LATEST NIGHT").note == "earliest start 07:00"


def test_records_with_no_hour_recorded_are_left_out():
    assert placings([rec("m1", "2026-07-30", hour=-1)], "LATEST NIGHT") == []


# --- cache misses and subagents ------------------------------------------


def test_the_biggest_cache_miss_carries_its_reason():
    records = [
        rec("m1", "2026-07-30", cache_missed_tokens=500, cache_miss_reason="system_changed"),
        rec("m2", "2026-07-29", cache_missed_tokens=900, cache_miss_reason="expired"),
    ]
    top = board(records, "BIGGEST CACHE MISS").leaders[0]
    assert (top.label, top.value, top.note) == ("29 Jul 2026", 900, "expired")


def test_only_subagent_messages_count_towards_the_subagent_board():
    records = [
        rec("m1", "2026-07-30", session_id="s1", is_subagent=True),
        rec("m2", "2026-07-30", session_id="s1", is_subagent=True),
        rec("m3", "2026-07-30", session_id="s1", is_subagent=False),
    ]
    assert placings(records, "MOST SUBAGENTS", {"s1": "refactor billing"}) == [
        ("refactor billing", 2)
    ]


def test_an_untitled_session_falls_back_to_its_id():
    records = [rec("m1", "2026-07-30", session_id="abcdef123456", is_subagent=True)]
    assert placings(records, "MOST SUBAGENTS")[0][0] == "abcdef12"


# --- stability -----------------------------------------------------------


def test_ties_are_broken_by_label_so_the_order_never_flickers():
    """The page reloads every 30 seconds. Two weekdays a cent apart swapping
    places between refreshes reads as data changing when nothing has."""
    records = [
        rec("m1", "2026-07-30"),  # Thursday
        rec("m2", "2026-07-27"),  # Monday, same cost
    ]
    first = placings(records, "WEEKDAY · SPEND")
    assert first == placings(list(reversed(records)), "WEEKDAY · SPEND")
    assert [label for label, _ in first] == ["Monday", "Thursday"]


def test_a_malformed_day_is_skipped_rather_than_taking_the_page_down():
    records = [rec("m1", "not-a-day"), rec("m2", "2026-07-30")]
    assert placings(records, "DATE · SPEND") == [("30 Jul 2026", pytest.approx(25.0))]
