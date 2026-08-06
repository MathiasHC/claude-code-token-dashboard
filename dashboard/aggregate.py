"""Turns records into everything the page needs. Pure — `now` is injected."""

from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter, defaultdict

from . import pricing
from .models import Bar, DashboardData, DayCost, UsageRecord, Window

UNTITLED = "(untitled session)"

#: Display names for the surfaces in UsageRecord.source. An unrecognised
#: source is shown verbatim rather than dropped, so a future surface still
#: appears on the page before this table learns about it.
SOURCE_LABELS = {
    "code": "Claude Code",
    "cowork": "Desktop (Cowork)",
}


def _bars(totals: dict[str, float], grand_total: float, top_n: int | None) -> list[Bar]:
    ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    if top_n is not None:
        ordered = ordered[:top_n]
    return [
        Bar(label=label, cost=value, share=(value / grand_total if grand_total else 0.0))
        for label, value in ordered
    ]


def _window(label: str, cost: float, messages: int) -> Window:
    return Window(label=label, cost=cost, messages=messages)


def build(
    records: list[UsageRecord],
    titles: dict[str, str],
    *,
    now: dt.datetime,
    max_plan_monthly_usd: float = pricing.MAX_PLAN_MONTHLY_USD,
    daily_days: int = 30,
    top_n: int = 5,
) -> DashboardData:
    dated = [r for r in records if r.day]
    # Costed once per record, not once here and again for the money
    # breakdown below: pricing.cost() was the single hottest call in
    # aggregate.build(), and it was being run twice over every row.
    parts_by_id = {r.message_id: pricing.cost(r) for r in dated}
    costs = {message_id: parts.total for message_id, parts in parts_by_id.items()}

    today_str = now.date().isoformat()
    month_prefix = now.strftime("%Y-%m")
    first_of_month = now.date().replace(day=1)
    prev_month_label = (first_of_month - dt.timedelta(days=1)).strftime("%Y-%m")

    def within_7_days(day: str) -> bool:
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError:
            return False
        return 0 <= (now.date() - parsed).days < 7

    def within_prior_7_days(day: str) -> bool:
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError:
            return False
        return 7 <= (now.date() - parsed).days < 14

    def is_yesterday(day: str) -> bool:
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError:
            return False
        return parsed == now.date() - dt.timedelta(days=1)

    # Previous month to date: same number of days into the previous month,
    # clamped to that month's actual length (e.g. 31 March compares against
    # all of February, not a partial or empty window).
    prev_month_end = first_of_month - dt.timedelta(days=1)
    days_in_prev_month = calendar.monthrange(prev_month_end.year, prev_month_end.month)[1]
    prev_month_to_date_cutoff = min(now.day, days_in_prev_month)

    def is_prev_month_to_date(day: str) -> bool:
        if not day.startswith(prev_month_label):
            return False
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError:
            return False
        return parsed.day <= prev_month_to_date_cutoff

    # Classify each *distinct* day once, then bucket the records in a single
    # pass. The obvious form — one list comprehension per window — walks the
    # whole history seven times and re-parses every record's date in each,
    # which on real data was ~90_000 fromisoformat calls to answer questions
    # about 42 distinct days.
    windows = ("today", "week", "month", "prev_month", "yesterday", "prior_7", "prev_mtd")
    membership = {
        day: (
            day == today_str,
            within_7_days(day),
            day.startswith(month_prefix),
            day.startswith(prev_month_label),
            is_yesterday(day),
            within_prior_7_days(day),
            is_prev_month_to_date(day),
        )
        for day in {r.day for r in dated}
    }
    bucket_cost = dict.fromkeys(windows, 0.0)
    bucket_count = dict.fromkeys(windows, 0)

    breakdown = Counter()
    by_model: dict[str, float] = defaultdict(float)
    by_project: dict[str, float] = defaultdict(float)
    by_skill: dict[str, float] = defaultdict(float)
    by_session: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    per_day: dict[str, float] = defaultdict(float)
    main_cost = subagent_cost = 0.0
    reads = fresh = 0

    # One pass. Every total on the page is accumulated here rather than by a
    # separate comprehension per figure, each of which was another full walk
    # of the history.
    for record in dated:
        value = costs[record.message_id]
        parts = parts_by_id[record.message_id]
        breakdown["cache read"] += parts.cache_read
        breakdown["cache write"] += parts.cache_write
        breakdown["output"] += parts.output
        breakdown["fresh input"] += parts.fresh_input

        by_model[record.model] += value
        by_project[record.project] += value
        by_skill[record.skill] += value
        by_session[record.session_id] += value
        by_source[SOURCE_LABELS.get(record.source, record.source)] += value
        per_day[record.day] += value

        if record.is_subagent:
            subagent_cost += value
        else:
            main_cost += value
        reads += record.cache_read_tokens
        fresh += record.input_tokens

        for name, inside in zip(windows, membership[record.day]):
            if inside:
                bucket_cost[name] += value
                bucket_count[name] += 1

    grand_total = sum(breakdown.values())
    recent_days = sorted(per_day)[-daily_days:]

    session_bars = [
        Bar(
            label=titles.get(session_id) or UNTITLED,
            cost=value,
            share=(value / grand_total if grand_total else 0.0),
        )
        for session_id, value in sorted(by_session.items(), key=lambda item: item[1], reverse=True)[:top_n]
    ]

    return DashboardData(
        generated_at=now.strftime("%d %b %Y %H:%M"),
        today=_window("today", bucket_cost["today"], bucket_count["today"]),
        last_7_days=_window("7 days", bucket_cost["week"], bucket_count["week"]),
        month_to_date=_window("month to date", bucket_cost["month"], bucket_count["month"]),
        all_time=_window("all time", grand_total, len(dated)),
        active_days=len(per_day),
        max_plan_monthly_usd=max_plan_monthly_usd,
        prev_month_label=prev_month_label,
        prev_month_cost=bucket_cost["prev_month"],
        yesterday_cost=bucket_cost["yesterday"],
        prior_7_days_cost=bucket_cost["prior_7"],
        prev_month_to_date_cost=bucket_cost["prev_mtd"],
        money=_bars(dict(breakdown), grand_total, None) if grand_total else [],
        by_model=_bars(dict(by_model), grand_total, top_n),
        by_project=_bars(dict(by_project), grand_total, top_n),
        by_skill=_bars(dict(by_skill), grand_total, top_n),
        top_sessions=session_bars,
        daily=[DayCost(day=day, cost=per_day[day]) for day in recent_days],
        cache_hit_rate=(reads / (reads + fresh) if (reads + fresh) else 0.0),
        avg_cost_per_message=(grand_total / len(dated) if dated else 0.0),
        avg_cost_per_session=(grand_total / len(by_session) if by_session else 0.0),
        unpriced_models=sorted({r.model for r in dated if not pricing.is_priced(r.model)}),
        main_cost=main_cost,
        subagent_cost=subagent_cost,
        # Every surface, not a top-N: a source that has been dropped off the
        # page is indistinguishable from one costing nothing.
        by_source=_bars(dict(by_source), grand_total, None),
    )
