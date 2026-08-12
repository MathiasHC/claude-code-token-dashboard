from __future__ import annotations

import datetime as dt

import pytest

from dashboard import aggregate
from dashboard.models import Plan, UsageRecord

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


def test_today_window_counts_only_todays_records():
    data = aggregate.build([rec("m1", "2026-07-30"), rec("m2", "2026-07-29")], {}, now=NOW)
    assert data.today.cost == pytest.approx(25.0)
    assert data.today.messages == 1


def test_seven_day_window_includes_today_and_excludes_the_eighth_day_back():
    records = [
        rec("m1", "2026-07-30"),  # today, in
        rec("m2", "2026-07-24"),  # 6 days back, in
        rec("m3", "2026-07-23"),  # 7 days back, out
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.last_7_days.messages == 2


def test_month_to_date_covers_only_the_current_calendar_month():
    data = aggregate.build([rec("m1", "2026-07-01"), rec("m2", "2026-06-30")], {}, now=NOW)
    assert data.month_to_date.messages == 1


def test_previous_month_is_reported_separately():
    data = aggregate.build([rec("m1", "2026-07-05"), rec("m2", "2026-06-30")], {}, now=NOW)
    assert data.prev_month_label == "2026-06"
    assert data.prev_month_cost == pytest.approx(25.0)


def test_previous_month_crosses_a_year_boundary():
    data = aggregate.build([rec("m1", "2025-12-31")], {}, now=dt.datetime(2026, 1, 15, 9, 0))
    assert data.prev_month_label == "2025-12"
    assert data.prev_month_cost == pytest.approx(25.0)


def test_all_time_window_counts_everything():
    data = aggregate.build([rec("m1", "2026-07-30"), rec("m2", "2026-01-01")], {}, now=NOW)
    assert data.all_time.messages == 2
    assert data.all_time.cost == pytest.approx(50.0)


def test_active_days_counts_distinct_days():
    records = [rec("m1", "2026-07-30"), rec("m2", "2026-07-30"), rec("m3", "2026-07-29")]
    assert aggregate.build(records, {}, now=NOW).active_days == 2


def test_effective_multiple_divides_month_to_date_by_the_plan_price():
    plan = Plan("tiny", "Tiny", 5.0)
    data = aggregate.build([rec("m1", "2026-07-30")] * 1, {}, now=NOW, plan=plan)
    assert data.effective_multiple == pytest.approx(5.0)


def test_day_change_is_none_without_a_prior_day():
    assert aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW).day_change is None


def test_day_change_is_computed_from_yesterday():
    records = [
        rec("m1", "2026-07-30"),  # today: $25
        rec("m2", "2026-07-30"),  # today: $25 (total $50)
        rec("m3", "2026-07-29"),  # yesterday: $25
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.yesterday_cost == pytest.approx(25.0)
    assert data.day_change == pytest.approx(1.0)  # 50 vs 25 = +100%


def test_week_change_is_none_without_a_prior_week():
    assert aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW).week_change is None


def test_week_change_uses_the_adjacent_non_overlapping_prior_window():
    records = [
        rec("m1", "2026-07-30"),  # today, in current 7-day window
        rec("m2", "2026-07-23"),  # 7 days back: first day of prior window
        rec("m3", "2026-07-17"),  # 13 days back: last day of prior window
        rec("m4", "2026-07-16"),  # 14 days back: outside both windows
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.last_7_days.messages == 1  # only m1
    assert data.prior_7_days_cost == pytest.approx(50.0)  # m2 + m3, not m4
    assert data.week_change == pytest.approx((25.0 - 50.0) / 50.0)


def test_month_change_is_none_without_prior_month_to_date_data():
    assert aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW).month_change is None


def test_month_change_compares_the_same_number_of_days_into_the_previous_month():
    # NOW is 2026-07-30, so "same point" in June is through 2026-06-30.
    records = [
        rec("m1", "2026-07-05"),  # month to date: $25
        rec("m2", "2026-06-30"),  # in prev-month-to-date window (day 30 <= 30)
        rec("m3", "2026-06-15"),  # also in prev-month-to-date window
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.prev_month_to_date_cost == pytest.approx(50.0)
    assert data.month_change == pytest.approx((25.0 - 50.0) / 50.0)


def test_month_change_clamps_to_a_shorter_previous_month():
    """31 March has no equivalent day in a 28-day February, so the clamp
    must fall back to comparing against the whole of February rather than
    producing a partial or empty window."""
    now = dt.datetime(2026, 3, 31, 12, 0, 0)
    records = [
        rec("m1", "2026-03-31"),  # month to date: $25
        rec("m2", "2026-02-01"),  # in Feb, in
        rec("m3", "2026-02-28"),  # last day of Feb, in
    ]
    data = aggregate.build(records, {}, now=now)
    assert data.prev_month_label == "2026-02"
    assert data.prev_month_to_date_cost == pytest.approx(50.0)  # all of Feb


def test_month_change_previous_month_crosses_a_year_boundary():
    now = dt.datetime(2026, 1, 3, 9, 0, 0)
    records = [
        rec("m1", "2026-01-03"),   # month to date: $25
        rec("m2", "2025-12-03"),   # same point (day 3) in December, in
        rec("m3", "2025-12-10"),   # day 10 in December, out (cutoff is day 3)
    ]
    data = aggregate.build(records, {}, now=now)
    assert data.prev_month_label == "2025-12"
    assert data.prev_month_to_date_cost == pytest.approx(25.0)  # only m2


def test_money_panel_splits_the_four_token_classes():
    record = rec(
        "m1",
        "2026-07-30",
        input_tokens=1_000_000,      # $5.00
        output_tokens=1_000_000,     # $25.00
        cache_read_tokens=1_000_000, # $0.50
        cache_write_1h=1_000_000,    # $10.00
    )
    labels = {bar.label: bar.cost for bar in aggregate.build([record], {}, now=NOW).scoped.money}
    assert labels["cache read"] == pytest.approx(0.50)
    assert labels["cache write"] == pytest.approx(10.00)
    assert labels["output"] == pytest.approx(25.00)
    assert labels["fresh input"] == pytest.approx(5.00)


def test_money_panel_shares_sum_to_one():
    record = rec("m1", "2026-07-30", input_tokens=1_000_000, cache_read_tokens=1_000_000)
    total = sum(bar.share for bar in aggregate.build([record], {}, now=NOW).scoped.money)
    assert total == pytest.approx(1.0)


def test_by_model_groups_and_sorts_by_cost_descending():
    records = [
        rec("m1", "2026-07-30", model="claude-haiku-4-5"),   # $5
        rec("m2", "2026-07-30", model="claude-opus-4-8"),    # $25
    ]
    bars = aggregate.build(records, {}, now=NOW).scoped.by_model
    assert [bar.label for bar in bars] == ["claude-opus-4-8", "claude-haiku-4-5"]


def test_by_project_and_by_skill_group_correctly():
    records = [
        rec("m1", "2026-07-30", project="alpha", skill="graphify"),
        rec("m2", "2026-07-30", project="beta", skill="graphify"),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert {bar.label for bar in data.scoped.by_project} == {"alpha", "beta"}
    assert [bar.label for bar in data.scoped.by_skill] == ["graphify"]


def test_groupings_are_limited_to_the_top_n_rows():
    """TOP_N is a module constant rather than an argument: production never
    varied it, and as a parameter its only effect was to let this assertion
    use a smaller number than the page actually renders."""
    records = [rec(f"m{i}", "2026-07-30", project=f"p{i}") for i in range(aggregate.TOP_N + 5)]
    assert len(aggregate.build(records, {}, now=NOW).scoped.by_project) == aggregate.TOP_N


def test_top_sessions_uses_titles_and_falls_back_when_missing():
    records = [
        rec("m1", "2026-07-30", session_id="sess-a"),
        rec("m2", "2026-07-30", session_id="sess-b"),
    ]
    bars = aggregate.build(records, {"sess-a": "/graphify"}, now=NOW).scoped.top_sessions
    labels = {bar.label for bar in bars}
    assert "/graphify" in labels
    assert "(untitled session)" in labels


def test_averages_are_per_message_and_per_session():
    records = [
        rec("m1", "2026-07-30", session_id="sess-a"),
        rec("m2", "2026-07-30", session_id="sess-a"),
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.scoped.avg_cost_per_message == pytest.approx(25.0)
    assert data.scoped.avg_cost_per_session == pytest.approx(50.0)


def test_averages_are_zero_with_no_records():
    data = aggregate.build([], {}, now=NOW)
    assert data.scoped.avg_cost_per_message == 0.0
    assert data.scoped.avg_cost_per_session == 0.0


def test_unpriced_models_are_reported_and_deduplicated():
    records = [
        rec("m1", "2026-07-30", model="claude-future-9"),
        rec("m2", "2026-07-30", model="claude-future-9"),
        rec("m3", "2026-07-30", model="<synthetic>"),
    ]
    assert aggregate.build(records, {}, now=NOW).unpriced_models == ["<synthetic>", "claude-future-9"]


def test_unpriced_model_tokens_are_counted_but_cost_nothing():
    """Silent under-reporting is the failure mode being guarded here."""
    records = [rec("m1", "2026-07-30", model="claude-future-9")]
    data = aggregate.build(records, {}, now=NOW)
    assert data.all_time.messages == 1
    assert data.all_time.cost == 0.0


def test_daily_series_is_limited_and_chronological():
    """Spans two months so the cap is doing real work: 45 days of history
    against a 30-day chart."""
    start = dt.date(2026, 6, 16)
    records = [
        rec(f"m{i}", (start + dt.timedelta(days=i)).isoformat())
        for i in range(45)
    ]
    daily = aggregate.build(records, {}, now=NOW, range_key="all").scoped.daily
    assert len(daily) == aggregate.DAILY_DAYS
    assert daily[0].day < daily[-1].day
    assert daily[-1].day == "2026-07-30"


def test_records_with_no_day_are_ignored_by_windows():
    assert aggregate.build([rec("m1", "")], {}, now=NOW).all_time.messages == 0


def test_empty_input_produces_a_valid_zeroed_dashboard():
    data = aggregate.build([], {}, now=NOW)
    assert data.all_time.cost == 0.0
    assert data.scoped.money == []
    assert data.scoped.daily == []
    assert data.effective_multiple == 0.0


# Change 7 tests

def test_main_and_subagent_costs_are_split():
    records = [
        rec("m1", "2026-07-30"),                      # $25, main
        rec("m2", "2026-07-30", is_subagent=True),    # $25, subagent
    ]
    data = aggregate.build(records, {}, now=NOW)
    assert data.scoped.main_cost == pytest.approx(25.0)
    assert data.scoped.subagent_cost == pytest.approx(25.0)
    assert data.scoped.subagent_share == pytest.approx(0.5)


def test_subagent_cost_still_counts_toward_every_window_and_grouping():
    """Subagents are attributed to their parent session and project, so they
    must not vanish from the totals."""
    records = [rec("m1", "2026-07-30", is_subagent=True, project="alpha")]
    data = aggregate.build(records, {}, now=NOW)
    assert data.all_time.cost == pytest.approx(25.0)
    assert data.today.cost == pytest.approx(25.0)
    assert [bar.label for bar in data.scoped.by_project] == ["alpha"]


def test_subagent_share_is_zero_with_no_records():
    assert aggregate.build([], {}, now=NOW).scoped.subagent_share == 0.0
