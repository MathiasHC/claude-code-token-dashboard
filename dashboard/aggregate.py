"""Turns records into everything the page needs. Pure — `now` is injected."""

from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter, defaultdict

from . import footprint, plans, pricing, ranges
from .dates import instant
from .models import Bar, DashboardData, DayCost, Plan, RangeView, UsageRecord, Window

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

#: Up to and including this day of the month, a trailing-7-day projection is
#: mostly last month and says more about it than about this one. On the 3rd,
#: five of the seven days are still last month's.
MIN_DAYS_FOR_PACE = 3

#: A rate needs a denominator worth dividing by. The first two messages of a
#: day are typically seconds apart, and dividing a real cost by two seconds
#: of "active time" produced $2,219/hr against a true $20.59/hr on live data
#: — the idle cap guards dilution, this guards the inflation direction.
MIN_ACTIVE_SECONDS = 900

#: Most recent days plotted in the daily chart, and rows kept in a breakdown
#: panel. Fixed rather than parameters: production never varied either, and
#: as arguments their only effect was to let two tests assert against a
#: smaller number than the page actually renders.
DAILY_DAYS = 30
TOP_N = 5

#: Display names for the surfaces in UsageRecord.source. An unrecognised
#: source is shown verbatim rather than dropped, so a future surface still
#: appears on the page before this table learns about it.
#:
#: Kept here, next to the panel it labels, rather than beside
#: ingest.default_sources: aggregate is pure, and importing ingest to reach
#: the roots table would pull scan and cowork — and their filesystem access —
#: into this module's import graph. See docs/adr/0001-source-table-split.md.
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
    moments = sorted(m for m in (instant(r.ts) for r in todays) if m is not None)
    if len(moments) < 2:
        return None, None

    active_seconds = sum(
        min((later - earlier).total_seconds(), IDLE_CAP_SECONDS)
        for earlier, later in zip(moments, moments[1:])
    )
    # Too little of the day to divide by. Without this the first two messages
    # — usually seconds apart — set the headline rate two orders of magnitude
    # too high, and it stays visibly wrong for tens of minutes.
    if active_seconds < MIN_ACTIVE_SECONDS:
        return None, None

    spent = sum(costs[r.message_id] for r in todays)
    if spent <= 0:
        # Every message today was on an unpriced model. $0.00/hr reads as a
        # measurement rather than an absence.
        return None, None

    # `now` is naive local; compare instants, not wall clocks.
    now_utc = now.astimezone(dt.timezone.utc) if now.tzinfo else now.astimezone().astimezone(dt.timezone.utc)
    idle = max(0, int((now_utc - moments[-1]).total_seconds() // 60))
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
    if now.day <= MIN_DAYS_FOR_PACE:
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


def build(
    records: list[UsageRecord],
    titles: dict[str, str],
    *,
    now: dt.datetime,
    plan: Plan = plans.DEFAULT,
    range_key: str | None = None,
) -> DashboardData:
    dated = [r for r in records if r.day]
    # Costed once per record, not once here and again for the money
    # breakdown below: pricing.cost() was the single hottest call in
    # aggregate.build(), and it was being run twice over every row.
    parts_by_id = {r.message_id: pricing.cost(r) for r in dated}
    costs = {message_id: parts.total for message_id, parts in parts_by_id.items()}

    today_str = now.date().isoformat()
    prev_month_label = ranges.previous_month_label(now)

    # Every window on the page comes from `ranges`, including the hero row's
    # comparisons. They used to be re-derived here, each with its own copy of
    # the malformed-day handling.
    selected = ranges.resolve(range_key)
    in_selected_range = ranges.contains(selected, now)

    windows = ("today", "week", "month", "prev_month", "yesterday", "prior_7", "prev_mtd")
    tests = (
        ranges.is_today(now),
        ranges.within_last(7, now),
        ranges.in_month(now),
        ranges.in_previous_month(now),
        ranges.is_yesterday(now),
        ranges.within_prior(7, now),
        ranges.in_previous_month_to_date(now),
    )

    # Classify each *distinct* day once, then bucket the records in a single
    # pass. The obvious form — one list comprehension per window — walks the
    # whole history seven times and re-parses every record's date in each,
    # which on real data was ~90_000 fromisoformat calls to answer questions
    # about 42 distinct days.
    membership = {
        day: tuple(test(day) for test in tests)
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
    worked = subagent_worked = 0.0
    reads = 0
    cache_saved = 0.0
    # Raw token counts inside the range, for the footprint estimate. Kept
    # separate from the costed figures: the footprint weights token types by
    # energy, which is a different ratio from what they are billed at.
    tokens = dict.fromkeys(("output", "input", "cache_read", "cache_write"), 0)
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
            subagent_worked += record.work_seconds
        else:
            main_cost += value
        worked += record.work_seconds
        reads += record.cache_read_tokens
        tokens["output"] += record.output_tokens
        tokens["input"] += record.input_tokens
        tokens["cache_read"] += record.cache_read_tokens
        tokens["cache_write"] += record.cache_write_5m + record.cache_write_1h
        # What those cache reads would have cost at full input rates. Cache
        # reads bill at CACHE_READ_MULTIPLIER of the input rate, so the part
        # never spent is the rest of it — already priced, in parts.cache_read,
        # rather than worth a second rates_for() lookup per record.
        cache_saved += parts.cache_read * (
            (1 - pricing.CACHE_READ_MULTIPLIER) / pricing.CACHE_READ_MULTIPLIER
        )
        scoped_total += value
        scoped_messages += 1

    # Shares are within the selected range, so a panel's percentages always
    # add up to what that panel is showing.
    todays = [r for r in dated if r.day == today_str]
    burn_rate, idle_minutes = _burn_rate(todays, costs, now)

    grand_total = scoped_total
    recent_days = sorted(per_day)[-DAILY_DAYS:]

    session_bars = [
        Bar(
            label=titles.get(session_id) or UNTITLED,
            cost=value,
            share=(value / grand_total if grand_total else 0.0),
        )
        for session_id, value in sorted(by_session.items(), key=lambda item: item[1], reverse=True)[:TOP_N]
    ]

    scoped = RangeView(
        key=selected.key,
        label=selected.panel_label,
        money=_bars(dict(breakdown), grand_total, None) if grand_total else [],
        by_model=_bars(dict(by_model), grand_total, TOP_N),
        by_project=_bars(dict(by_project), grand_total, TOP_N),
        by_skill=_bars(dict(by_skill), sum(by_skill.values()), TOP_N),
        # Every surface, not a top-N: a source that has been dropped off the
        # page is indistinguishable from one costing nothing.
        by_source=_bars(dict(by_source), grand_total, None),
        top_sessions=session_bars,
        daily=[DayCost(day=day, cost=per_day[day]) for day in recent_days],
        main_cost=main_cost,
        subagent_cost=subagent_cost,
        cache_saved=cache_saved,
        cache_read_tokens=reads,
        avg_cost_per_message=(grand_total / scoped_messages if scoped_messages else 0.0),
        avg_cost_per_session=(grand_total / len(by_session) if by_session else 0.0),
        worked_seconds=worked,
        subagent_worked_seconds=subagent_worked,
        # Every token counts here, priced or not: an unpriced model still
        # drew power. This is the one figure on the page that does not go to
        # zero when a rate is missing.
        footprint=footprint.estimate(
            output_tokens=tokens["output"],
            input_tokens=tokens["input"],
            cache_read_tokens=tokens["cache_read"],
            cache_write_tokens=tokens["cache_write"],
        ),
    )

    return DashboardData(
        generated_at=now.strftime("%d %b %Y %H:%M"),
        today=Window(bucket_cost["today"], bucket_count["today"]),
        last_7_days=Window(bucket_cost["week"], bucket_count["week"]),
        month_to_date=Window(bucket_cost["month"], bucket_count["month"]),
        all_time=Window(all_time_cost, all_time_messages),
        active_days=len(active),
        plan=plan,
        prev_month_label=prev_month_label,
        prev_month_cost=bucket_cost["prev_month"],
        yesterday_cost=bucket_cost["yesterday"],
        prior_7_days_cost=bucket_cost["prior_7"],
        prev_month_to_date_cost=bucket_cost["prev_mtd"],
        scoped=scoped,
        on_pace=_on_pace(per_day_all, bucket_cost["month"], now),
        burn_rate_hourly=burn_rate,
        idle_minutes=idle_minutes,
        today_day=today_str,
        unpriced_models=sorted({r.model for r in dated if not pricing.is_priced(r.model)}),
    )
