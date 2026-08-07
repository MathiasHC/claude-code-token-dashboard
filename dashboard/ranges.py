"""Which slice of history the breakdown panels cover.

The hero row and its deltas are always the same four windows — they are the
glanceable summary and do not move. What a range changes is everything below
it: where the money went, by model, project, skill, session and source, and
the daily chart.

Ranges are picked with ordinary links (`?range=7d`), not JavaScript, so the
page still works on the browsers this dashboard targets. That means the whole
catalogue has to be renderable as a row of anchors, which is why it is a short
fixed list rather than arbitrary dates.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Callable
from typing import NamedTuple


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

#: All time. The wall display returns here on every refresh, so what you see
#: from across the room never depends on what someone clicked earlier.
DEFAULT = CATALOGUE[-1]


def by_key(key: str | None) -> Range | None:
    if not key:
        return None
    wanted = key.strip().lower()
    for candidate in CATALOGUE:
        if candidate.key == wanted:
            return candidate
    return None


def resolve(key: str | None) -> Range:
    """Never raise on a bad range — an unknown `?range=` is a typo or a stale
    bookmark, and showing the default beats showing an error page."""
    return by_key(key) or DEFAULT


def contains(selected: Range, now: dt.datetime) -> Callable[[str], bool]:
    """Build a `day -> bool` test for one range.

    Days are local ISO dates (`scan.local_day`). An unparseable day is never
    in range: it cannot be placed on a timeline, so it can only be counted in
    all-time totals.
    """
    if selected.key == "all":
        return lambda day: bool(day)

    today = now.date()

    if selected.key == "today":
        wanted = today.isoformat()
        return lambda day: day == wanted

    if selected.key == "month":
        prefix = now.strftime("%Y-%m")
        return lambda day: day.startswith(prefix)

    span = {"7d": 7, "30d": 30}[selected.key]

    def within(day: str) -> bool:
        try:
            parsed = dt.date.fromisoformat(day)
        except ValueError:
            return False
        return 0 <= (today - parsed).days < span

    return within


def month_to_date_cutoff(now: dt.datetime) -> int:
    """Day-of-month to compare the previous month against, clamped to that
    month's length so 31 March compares against all of February."""
    first = now.date().replace(day=1)
    previous = first - dt.timedelta(days=1)
    return min(now.day, calendar.monthrange(previous.year, previous.month)[1])
