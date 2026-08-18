"""The all-time top threes.

Every board here is global, and that is the whole claim rather than an
implementation detail. A "top 3 all time" that quietly re-ranked itself
when somebody picked LAST 7 DAYS would be answering a different question
than its heading asks, so these hang off DashboardData beside the hero row
instead of off RangeView, and the page prints ALL TIME above them.

One pass, one call. `build` is the entire interface: twelve boards come
back ready to render, and no caller has to know that a streak is computed
from the set of active days while a prompt is computed from a scan-carried
id. That is deliberately more implementation behind a smaller interface
than the alternative of twelve exported helpers, each of which would need
its own walk of the history.
"""

from __future__ import annotations

import calendar
from collections import defaultdict

from . import dates
from .models import Leader, Leaderboard, UsageRecord

#: Placings per board. Three is what was asked for, and it is also roughly
#: where these stop being interesting: on a real history the fourth-place
#: weekday is within a rounding error of the third.
TOP = 3

#: Where a day is considered to start, for the night-owl board only. The raw
#: 0-23 clock ranks 23:00 above 02:00, which is backwards — nobody calls
#: eleven at night a later night than two in the morning. Re-basing the hour
#: here makes 06:00 the earliest point of a day and 05:00 the latest.
DAY_STARTS_AT = 6

#: Units the renderer knows how to format. Held as strings on the board
#: rather than as a formatter function so the aggregate layer stays pure
#: data and every display decision remains in render_html.
MONEY = "money"
COUNT = "count"
DURATION = "duration"
TOKENS = "tokens"
CLOCK = "clock"
DAYS = "days"


def _date_label(day: str) -> str:
    parsed = dates.parse_day(day)
    return parsed.strftime("%d %b %Y") if parsed else day


def _month_label(month: str) -> str:
    """"2026-07" as "07 2026" — the format the board was asked for."""
    year, _, number = month.partition("-")
    return f"{number} {year}"


def _span_label(start, end) -> str:
    """A run of days as one phrase, without repeating a month it shares."""
    if start == end:
        return start.strftime("%d %b %Y")
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%d}–{end:%d %b %Y}"
    return f"{start:%d %b}–{end:%d %b %Y}"


def _top(scores: dict, key=None) -> list:
    """The highest `TOP` keys, ties broken by key so the order is stable.

    Stability matters more than it looks: without it two weekdays a cent
    apart swap places between refreshes of a page that reloads every 30
    seconds, which reads as data changing when nothing has.
    """
    return sorted(scores.items(), key=key or (lambda kv: (-kv[1], kv[0])))[:TOP]


def _runs(days: set[str]) -> tuple[list, list]:
    """Consecutive-day streaks and the gaps between them.

    Both fall out of the same walk: a streak is what happens between two
    gaps, and a gap is what happens between two streaks.
    """
    ordered = sorted(
        parsed for day in days if (parsed := dates.parse_day(day)) is not None
    )
    if not ordered:
        return [], []
    streaks, gaps = [], []
    start = previous = ordered[0]
    for current in ordered[1:]:
        step = (current - previous).days
        if step > 1:
            streaks.append((start, previous))
            gaps.append((previous, current, step - 1))
            start = current
        previous = current
    streaks.append((start, previous))
    return streaks, gaps


def build(
    records: list[UsageRecord],
    costs: dict[str, float],
    titles: dict[str, str],
) -> list[Leaderboard]:
    """Twelve all-time boards from one walk of the history.

    `costs` is passed in rather than recomputed because aggregate.build has
    already priced every record once, and pricing was the hottest call in
    the whole build before it was hoisted.
    """
    weekday_cost: dict[int, float] = defaultdict(float)
    weekday_messages: dict[int, float] = defaultdict(float)
    weekday_sessions: dict[int, set] = defaultdict(set)
    month_cost: dict[str, float] = defaultdict(float)
    day_cost: dict[str, float] = defaultdict(float)
    subagent_messages: dict[str, float] = defaultdict(float)
    # day -> the latest / earliest re-based hour seen on it. Per day rather
    # than per record: three messages at 2am on one night would otherwise
    # fill the whole board with the same night.
    latest: dict[str, int] = {}
    earliest: dict[str, int] = {}
    prompts: dict[str, dict] = {}
    misses: list[tuple] = []
    days: set[str] = set()

    for record in records:
        parsed = dates.parse_day(record.day)
        if parsed is None:
            continue
        value = costs.get(record.message_id, 0.0)
        days.add(record.day)
        day_cost[record.day] += value
        month_cost[record.day[:7]] += value

        weekday = parsed.weekday()
        weekday_cost[weekday] += value
        weekday_messages[weekday] += 1
        if record.session_id:
            # A session running past midnight is counted on both weekdays.
            # It genuinely happened on both, and the alternative — crediting
            # the whole session to whichever day it opened on — would hand
            # every late night to the day before.
            weekday_sessions[weekday].add(record.session_id)

        if record.hour >= 0:
            hour = (record.hour - DAY_STARTS_AT) % 24
            if hour > latest.get(record.day, -1):
                latest[record.day] = hour
            if hour < earliest.get(record.day, 24):
                earliest[record.day] = hour

        if record.prompt_run:
            prompt = prompts.get(record.prompt_run)
            if prompt is None:
                prompt = prompts[record.prompt_run] = {
                    "cost": 0.0, "seconds": 0.0, "messages": 0, "ts": record.ts,
                }
            prompt["cost"] += value
            prompt["seconds"] += record.work_seconds
            prompt["messages"] += 1
            if record.ts < prompt["ts"]:
                prompt["ts"] = record.ts

        if record.cache_missed_tokens > 0:
            misses.append(
                (record.cache_missed_tokens, record.day, record.cache_miss_reason)
            )
        if record.is_subagent and record.session_id:
            subagent_messages[record.session_id] += 1

    streaks, gaps = _runs(days)
    ranked_prompts = sorted(prompts.values(), key=lambda p: -p["seconds"])[:TOP]
    priciest_prompts = sorted(prompts.values(), key=lambda p: -p["cost"])[:TOP]
    misses.sort(key=lambda m: (-m[0], m[1]))

    def weekday_board(title: str, scores: dict, unit: str) -> Leaderboard:
        return Leaderboard(
            title=title,
            unit=unit,
            leaders=[
                Leader(label=calendar.day_name[day], value=score)
                for day, score in _top(scores)
            ],
        )

    return [
        weekday_board("WEEKDAY · SPEND", weekday_cost, MONEY),
        weekday_board(
            "WEEKDAY · SESSIONS",
            {day: float(len(seen)) for day, seen in weekday_sessions.items()},
            COUNT,
        ),
        weekday_board("WEEKDAY · MESSAGES", weekday_messages, COUNT),
        Leaderboard(
            title="MONTH · SPEND",
            unit=MONEY,
            leaders=[
                Leader(label=_month_label(month), value=cost)
                for month, cost in _top(month_cost)
            ],
        ),
        Leaderboard(
            title="DATE · SPEND",
            unit=MONEY,
            leaders=[
                Leader(label=_date_label(day), value=cost)
                for day, cost in _top(day_cost)
            ],
        ),
        Leaderboard(
            title="LONGEST PROMPT",
            unit=DURATION,
            leaders=[
                Leader(
                    label=_date_label(dates.local_day(prompt["ts"])),
                    value=prompt["seconds"],
                    note=f"{prompt['messages']:,} msgs",
                )
                for prompt in ranked_prompts
            ],
            # Machine time, not elapsed: measuring a prompt end-to-end would
            # crown whichever one happened to be open when somebody walked
            # away from the keyboard.
            note="machine time, not wall clock",
        ),
        Leaderboard(
            title="PRICIEST PROMPT",
            unit=MONEY,
            leaders=[
                Leader(
                    label=_date_label(dates.local_day(prompt["ts"])),
                    value=prompt["cost"],
                    note=f"{prompt['messages']:,} msgs",
                )
                for prompt in priciest_prompts
            ],
        ),
        Leaderboard(
            title="LONGEST STREAK",
            unit=DAYS,
            leaders=[
                Leader(label=_span_label(start, end), value=(end - start).days + 1)
                for start, end in sorted(
                    streaks, key=lambda run: (-(run[1] - run[0]).days, run[0])
                )[:TOP]
            ],
        ),
        Leaderboard(
            title="LONGEST BREAK",
            unit=DAYS,
            leaders=[
                Leader(
                    label=f"{before:%d %b} → {after:%d %b %Y}",
                    value=length,
                )
                for before, after, length in sorted(
                    gaps, key=lambda gap: (-gap[2], gap[0])
                )[:TOP]
            ],
        ),
        Leaderboard(
            title="LATEST NIGHT",
            unit=CLOCK,
            leaders=[
                Leader(label=_date_label(day), value=(hour + DAY_STARTS_AT) % 24)
                for day, hour in _top(latest)
            ],
            note=(
                "earliest start "
                f"{(min(earliest.values()) + DAY_STARTS_AT) % 24:02d}:00"
                if earliest
                else ""
            ),
        ),
        Leaderboard(
            title="BIGGEST CACHE MISS",
            unit=TOKENS,
            leaders=[
                Leader(label=_date_label(day), value=tokens, note=reason)
                for tokens, day, reason in misses[:TOP]
            ],
            note="prefix re-read at write rates",
        ),
        Leaderboard(
            title="MOST SUBAGENTS",
            unit=COUNT,
            leaders=[
                Leader(label=titles.get(session, session[:8] or "(session)"), value=count)
                for session, count in _top(subagent_messages)
            ],
            note="subagent messages in one session",
        ),
    ]
