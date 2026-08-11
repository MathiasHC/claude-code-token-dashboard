"""Turns records into everything the page needs. Pure — `now` is injected."""

from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter, defaultdict

from . import pricing, ranges
from .models import Bar, DashboardData, DayCost, UsageRecord, Window

UNTITLED = "(untitled session)"

#: Records with no skill attributed. Excluded from the BY SKILL panel: it is
#: ~90% of spend on real histories, which compresses the skills that do carry
#: a cost into unreadable slivers. The panel heading says ATTRIBUTED so the
#: exclusion is visible rather than silently redefining the percentages.
UNATTRIBUTED_SKILL = "(none)"

#: A gap longer than this counts as "not working" rather than a very slow
#: message. Without a cap, a session left open overnight dilutes the burn
#: rate to nothing.
IDLE_CAP_SECONDS = 300

#: Below this many days into a month, a trailing-7-day projection is mostly
#: last month and says more about it than about this one.
MIN_DAYS_FOR_PACE = 3

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


def _local_naive(ts: str) -> dt.datetime | None:
    """A UTC transcript timestamp as a naive local datetime.

    `now` is injected as a naive local datetime, so timestamps have to be
    brought into the same frame before they can be subtracted. Mirrors what
    scan.local_day does for dates.
    """
    if not ts:
        return None
    try:
        moment = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone().replace(tzinfo=None)


def _burn_rate(
    todays: list[UsageRecord],
    costs: dict[str, float],
    now: dt.datetime,
) -> tuple[float | None, int | None]:
    """Cost per hour of active time today, and minutes since the last message.

    Active time is the sum of gaps between consecutive messages, each capped
    at IDLE_CAP_SECONDS. Wall-clock since the first message would be wrong:
    a session left open overnight would dilute the rate towards zero and the
    number would say nothing about how expensive the work is.

    Returns (None, None) when there is not enough of today to divide by.
    """
    moments = sorted(m for m in (_local_naive(r.ts) for r in todays) if m is not None)
    if len(moments) < 2:
        return None, None

    active_seconds = sum(
        min((later - earlier).total_seconds(), IDLE_CAP_SECONDS)
        for earlier, later in zip(moments, moments[1:])
    )
    if active_seconds <= 0:
        return None, None

    spent = sum(costs[r.message_id] for r in todays)
    idle = max(0, int((now - moments[-1]).total_seconds() // 60))
    return spent / (active_seconds / 3600), idle


def _on_pace(
    per_day: dict[str, float],
    month_to_date: float,
    now: dt.datetime,
) -> float | None:
    """Month-to-date plus the trailing-7-day rate over the days remaining.

    None for the first MIN_DAYS_FOR_PACE days of a month: a trailing-7-day
    window is then mostly last month, so the projection describes a month
    that has ended rather than the one in progress.

    Days with no usage count as zero rather than being skipped — a quiet
    weekend is part of the rate, not missing data.
    """
    if now.day < MIN_DAYS_FOR_PACE:
        return None
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    remaining = days_in_month - now.day
    if remaining <= 0:
        return month_to_date
    window = [
        per_day.get((now.date() - dt.timedelta(days=offset)).isoformat(), 0.0)
        for offset in range(1, 8)
    ]
    return month_to_date + (sum(window) / len(window)) * remaining


def _window(label: str, cost: float, messages: int) -> Window:
    return Window(label=label, cost=cost, messages=messages)


def build(
    records: list[UsageRecord],
    titles: dict[str, str],
    *,
    now: dt.datetime,
    max_plan_monthly_usd: float = pricing.MAX_PLAN_MONTHLY_USD,
    plan_label: str = "Max 20×",
    range_key: str | None = None,
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
    selected = ranges.resolve(range_key)
    in_selected_range = ranges.contains(selected, now)

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
    # Scoping is one more per-day lookup rather than a second filtering
    # pass over the history.
    scoped_days = {day for day in membership if in_selected_range(day)}
    bucket_cost = dict.fromkeys(windows, 0.0)
    bucket_count = dict.fromkeys(windows, 0)

    breakdown = Counter()
    by_model: dict[str, float] = defaultdict(float)
    by_project: dict[str, float] = defaultdict(float)
    by_skill: dict[str, float] = defaultdict(float)
    by_session: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    per_day: dict[str, float] = defaultdict(float)
    # Global, not range-scoped: the plan band talks about this month
    # regardless of which range the panels below are showing.
    per_day_all: dict[str, float] = defaultdict(float)
    main_cost = subagent_cost = 0.0
    reads = fresh = 0
    cache_saved = 0.0
    # All-time totals stay global no matter what range is selected: the
    # hero row is the fixed summary, and only the panels below it move.
    all_time_cost = 0.0
    all_time_messages = 0
    active = set()
    scoped_total = 0.0
    scoped_messages = 0

    # One pass. Every total on the page is accumulated here rather than by a
    # separate comprehension per figure, each of which was another full walk
    # of the history.
    for record in dated:
        value = costs[record.message_id]

        # Global, always: the hero row and its deltas do not follow the range.
        all_time_cost += value
        all_time_messages += 1
        active.add(record.day)
        per_day_all[record.day] += value
        for name, inside in zip(windows, membership[record.day]):
            if inside:
                bucket_cost[name] += value
                bucket_count[name] += 1

        if record.day not in scoped_days:
            continue

        # Everything from here down is what the selected range re-scopes.
        parts = parts_by_id[record.message_id]
        breakdown["cache read"] += parts.cache_read
        breakdown["cache write"] += parts.cache_write
        breakdown["output"] += parts.output
        breakdown["fresh input"] += parts.fresh_input

        by_model[record.model] += value
        by_project[record.project] += value
        if record.skill != UNATTRIBUTED_SKILL:
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
        # What those cache reads would have cost at full input rates. Cache
        # reads bill at CACHE_READ_MULTIPLIER, so the saving is the rest.
        found = pricing.rates_for(record.model, record.speed)
        if found is not None:
            per_token_in = found[0] / 1_000_000
            cache_saved += (
                record.cache_read_tokens
                * per_token_in
                * (1 - pricing.CACHE_READ_MULTIPLIER)
            )
        scoped_total += value
        scoped_messages += 1

    # Shares are within the selected range, so a panel's percentages always
    # add up to what that panel is showing.
    todays = [r for r in dated if r.day == today_str]
    burn_rate, idle_minutes = _burn_rate(todays, costs, now)

    grand_total = scoped_total
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
        all_time=_window("all time", all_time_cost, all_time_messages),
        active_days=len(active),
        max_plan_monthly_usd=max_plan_monthly_usd,
        plan_label=plan_label,
        prev_month_label=prev_month_label,
        prev_month_cost=bucket_cost["prev_month"],
        yesterday_cost=bucket_cost["yesterday"],
        prior_7_days_cost=bucket_cost["prior_7"],
        prev_month_to_date_cost=bucket_cost["prev_mtd"],
        money=_bars(dict(breakdown), grand_total, None) if grand_total else [],
        by_model=_bars(dict(by_model), grand_total, top_n),
        by_project=_bars(dict(by_project), grand_total, top_n),
        by_skill=_bars(dict(by_skill), sum(by_skill.values()), top_n),
        top_sessions=session_bars,
        daily=[DayCost(day=day, cost=per_day[day]) for day in recent_days],
        cache_hit_rate=(reads / (reads + fresh) if (reads + fresh) else 0.0),
        cache_saved=cache_saved,
        on_pace=_on_pace(per_day_all, bucket_cost["month"], now),
        burn_rate_hourly=burn_rate,
        idle_minutes=idle_minutes,
        today_day=today_str,
        avg_cost_per_message=(grand_total / scoped_messages if scoped_messages else 0.0),
        avg_cost_per_session=(grand_total / len(by_session) if by_session else 0.0),
        unpriced_models=sorted({r.model for r in dated if not pricing.is_priced(r.model)}),
        range_key=selected.key,
        range_label=selected.panel_label,
        main_cost=main_cost,
        subagent_cost=subagent_cost,
        # Every surface, not a top-N: a source that has been dropped off the
        # page is indistinguishable from one costing nothing.
        by_source=_bars(dict(by_source), grand_total, None),
    )
