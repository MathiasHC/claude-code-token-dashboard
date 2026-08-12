"""Which slice of history a figure covers.

The hero row and its deltas are always the same four windows — they are the
glanceable summary and do not move. What a range changes is everything below
it: where the money went, by model, project, skill, session and source, and
the daily chart.

Ranges are picked with ordinary links (`?range=7d`), not JavaScript, so the
page still works on the browsers this dashboard targets. That means the whole
catalogue has to be renderable as a row of anchors, which is why it is a short
fixed list rather than arbitrary dates.

This module owns *every* day window on the page, not only the five in the
catalogue. The hero row's comparisons — yesterday, the prior 7 days, the same
point in the previous month — are the same kind of question, and they used to
be re-derived inside aggregate.build with their own copies of the malformed-day
handling. One module answers "is this day inside that window?" for all of them.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Callable
from typing import NamedTuple

from .dates import parse_day

#: The query parameter ranges travel in. Both halves of the round trip live
#: here: render_html writes it into the selector's hrefs, web.py reads it back
#: off the request. It was previously a bare "range" literal in each of those
#: modules, which never reference each other.
QUERY_KEY = "range"

#: A day predicate. Given a stored ISO day, is it inside this window?
DayFilter = Callable[[str], bool]


class Range(NamedTuple):
    key: str
    #: Short form for the selector row.
    label: str
    #: Long form for panel headings, e.g. "BY MODEL · LAST 7 DAYS".
    panel_label: str


CATALOGUE: tuple[Range, ...] = (
    Range("today", "TODAY", "TODAY"),
    Range("7d", "7 DAYS", "LAST 7 DAYS"),
    Range("30d", "30 DAYS", "LAST 30 DAYS"),
    Range("month", "MONTH", "THIS MONTH"),
    Range("all", "ALL TIME", "ALL TIME"),
)

#: 30 days. An all-time default made every panel below the selector the most
#: static content on the page — one day's work moves nothing against a year.
#: Thirty days moves daily, which is the point of a wall display. ALL TIME is
#: one click away and the hero row is global regardless.
DEFAULT = CATALOGUE[2]

#: How many days of catalogue span a range covers, for the ones that are a
#: fixed number of days back from today.
_SPANS = {"7d": 7, "30d": 30}


def resolve(key: str | None) -> Range:
    """Never raise on a bad range — an unknown `?range=` is a typo or a stale
    bookmark, and showing the default beats showing an error page."""
    wanted = (key or "").strip().lower()
    for candidate in CATALOGUE:
        if candidate.key == wanted:
            return candidate
    return DEFAULT


# --- the day windows ----------------------------------------------------
#
# Each returns a `day -> bool`. A day that will not parse is outside every
# window: it cannot be placed on a timeline, so it can only ever be counted
# in all-time totals. That decision is made once, in dates.parse_day.


def everything() -> DayFilter:
    return lambda day: bool(day)


def is_today(now: dt.datetime) -> DayFilter:
    wanted = now.date().isoformat()
    return lambda day: day == wanted


def is_yesterday(now: dt.datetime) -> DayFilter:
    wanted = now.date() - dt.timedelta(days=1)
    return lambda day: parse_day(day) == wanted


def within_last(days: int, now: dt.datetime) -> DayFilter:
    """The `days` days ending today, today included."""
    today = now.date()

    def within(day: str) -> bool:
        parsed = parse_day(day)
        return parsed is not None and 0 <= (today - parsed).days < days

    return within


def within_prior(days: int, now: dt.datetime) -> DayFilter:
    """The `days` days immediately before `within_last(days)` — the baseline
    a trailing window is compared against."""
    today = now.date()

    def within(day: str) -> bool:
        parsed = parse_day(day)
        return parsed is not None and days <= (today - parsed).days < days * 2

    return within


def in_month(now: dt.datetime) -> DayFilter:
    prefix = now.strftime("%Y-%m")
    return lambda day: day.startswith(prefix)


def previous_month_label(now: dt.datetime) -> str:
    """The previous month as YYYY-MM. Also what the page prints for it."""
    return (now.date().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


def in_previous_month(now: dt.datetime) -> DayFilter:
    prefix = previous_month_label(now)
    return lambda day: day.startswith(prefix)


def in_previous_month_to_date(now: dt.datetime) -> DayFilter:
    """The same number of days into the previous month, clamped to that
    month's length so 31 March compares against all of February rather than
    an empty window."""
    prefix = previous_month_label(now)
    first = now.date().replace(day=1)
    previous = first - dt.timedelta(days=1)
    cutoff = min(now.day, calendar.monthrange(previous.year, previous.month)[1])

    def within(day: str) -> bool:
        if not day.startswith(prefix):
            return False
        parsed = parse_day(day)
        return parsed is not None and parsed.day <= cutoff

    return within


def contains(selected: Range, now: dt.datetime) -> DayFilter:
    """Build the day predicate for one catalogue range."""
    if selected.key == "all":
        return everything()
    if selected.key == "today":
        return is_today(now)
    if selected.key == "month":
        return in_month(now)
    return within_last(_SPANS[selected.key], now)
