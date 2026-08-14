"""How a transcript timestamp and a stored day become datetimes.

Two questions get asked over and over, and both have a wrong obvious answer:

- *When did this message happen?* Slicing "2026-08-11T23:40:00Z" at the T
  buckets work done after local midnight onto the previous day.
- *Is this day inside that window?* A malformed day has to answer "no"
  rather than raise, because one bad row in a transcript must not take the
  page down.

Both were previously written out at each call site — six copies of the same
try/except between aggregate, ranges and scan. Malformed input is a single
decision, so it lives in one module and every caller inherits it.
"""

from __future__ import annotations

import datetime as dt


def parse_day(day: str) -> dt.date | None:
    """A stored ISO day, or None if it is not one.

    None rather than an exception: an unparseable day cannot be placed on a
    timeline, so it is outside every window — which is what each caller does
    with it anyway.
    """
    try:
        return dt.date.fromisoformat(day)
    except (ValueError, TypeError):
        return None


def instant(ts: str) -> dt.datetime | None:
    """A transcript timestamp as an aware UTC datetime.

    Durations are computed in UTC rather than in local wall-clock time.
    Subtracting naive local times is wrong by the offset across a DST change,
    and across a fall-back fold two instants an hour apart map to the same
    local time, so a real gap computes as zero and the burn rate disappears.
    UTC has neither problem.
    """
    if not ts:
        return None
    try:
        moment = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def local_day(ts: str) -> str:
    """Which *local* calendar day a transcript timestamp falls on.

    Transcript timestamps are UTC with a Z suffix. This is the one place that
    converts before taking the date, so a message at 23:40 local counts
    against the day the person was working, not the UTC one.
    """
    moment = instant(ts)
    return moment.astimezone().date().isoformat() if moment is not None else ""


def local_hour(ts: str) -> int:
    """Hour of the local day a transcript timestamp falls in, or -1.

    Same UTC-to-local conversion as local_day, for the same reason: a
    message at 23:40 local belongs to that evening, not to the UTC one.
    """
    moment = instant(ts)
    return moment.astimezone().hour if moment is not None else -1
