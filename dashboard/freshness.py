"""How current the numbers are, and what to show when they are not.

Three policies used to be fused into one method on the web App: how often to
re-read the transcripts, what to keep between requests, and what to serve when
a refresh fails. Fusing them had a concrete cost. Because the cache held
*rendered HTML*, the degradation path had thrown the data away by the time it
needed to add a warning, so it patched the warning into the markup with a
string search for `<div class="titlebar">` — markup owned by render_html. When
that search missed, the function returned the page unchanged and the staleness
warning silently disappeared.

Caching DashboardData instead removes the need for that entirely: a stale page
is re-rendered through the `warning=` parameter render_html already has.

The seam is real rather than hypothetical: `scan` has two adapters, the
filesystem walk in production and a stub in tests, which is what the tests
were already simulating by reassigning a private method.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import aggregate, cowork, ingest, plans, ranges, store
from .models import DashboardData

DEFAULT_MIN_INGEST_INTERVAL = 10.0


@dataclass(frozen=True)
class Refresh:
    """The newest data for one range, and anything the reader should know
    about how newest it actually is."""

    data: DashboardData
    warning: str | None = None


class Freshness:
    """Keeps the most recent DashboardData per range.

    Rescans no more often than `min_interval`. Never raises: a wall display
    that 500s is worse than one showing yesterday's numbers under a banner
    saying so.
    """

    def __init__(
        self,
        db_path: str | Path,
        projects_dir: Path,
        *,
        cowork_dir: Path | None = None,
        plan: plans.Plan | None = None,
        min_interval: float = DEFAULT_MIN_INGEST_INTERVAL,
        clock=time.monotonic,
        now=dt.datetime.now,
        scan=None,
    ) -> None:
        self.db_path = db_path
        self.projects_dir = Path(projects_dir)
        self.cowork_dir = (
            Path(cowork_dir) if cowork_dir is not None else cowork.default_cowork_dir()
        )
        # Never resolved here: plans.resolve() can prompt, and this is
        # constructed inside a request path in tests. The CLI settles it.
        self.plan = plan or plans.DEFAULT
        self.min_interval = min_interval
        self._clock = clock
        self._now = now
        #: The adapter at this module's one real seam: `skip -> ScanResult`.
        #: The filesystem walk in production, a stub in tests. Public because
        #: swapping it is the supported way to drive the failure paths, which
        #: tests previously did by reassigning a private method.
        self.scan = scan or self._scan_sources
        self._last_ingest_at: float | None = None
        self._last_success_at: dt.datetime | None = None
        # One built view per range. Cleared whenever an ingest lands, because
        # every range's numbers move when new rows arrive.
        self._views: dict[str, DashboardData] = {}
        self.ingest_count = 0
        # Guards the check-then-act in view(): _due() and the ingest that
        # follows it must be atomic, or concurrent requests (the server is
        # threaded) can all observe "due" before any of them stamps
        # _last_ingest_at, and each triggers its own full scan. Holding the
        # lock for the whole method — not just the check — means threads that
        # lose the race wait for the scan already in flight and then fall into
        # the "not due" branch, returning what it just built.
        self._lock = threading.Lock()

    def _scan_sources(self, skip):
        return ingest.scan_sources(
            ingest.default_sources(self.projects_dir, self.cowork_dir), skip
        )

    def _due(self) -> bool:
        if self._last_ingest_at is None:
            return True
        return (self._clock() - self._last_ingest_at) >= self.min_interval

    def _build(self, records, titles, selected: ranges.Range) -> DashboardData:
        return aggregate.build(
            records,
            titles,
            now=self._now(),
            plan=self.plan,
            range_key=selected.key,
        )

    def view(self, selected: ranges.Range) -> Refresh:
        # Whole method under the lock: the _due() check and the ingest it
        # gates must be atomic across threads. See the note on self._lock.
        with self._lock:
            if self._due():
                try:
                    with store.Store(self.db_path) as db:
                        result = self.scan(db.file_stats())
                        db.ingest(result)
                        records, titles = db.records(), db.titles()
                    self.ingest_count += 1
                    self._last_ingest_at = self._clock()
                    self._last_success_at = self._now()
                    # Every cached range is stale the moment new rows land.
                    self._views.clear()
                    built = self._build(records, titles, selected)
                    self._views[selected.key] = built
                    return Refresh(built)
                except Exception as error:  # noqa: BLE001 - a wall display must not 500
                    self._last_ingest_at = self._clock()
                    return self._degraded(selected, f"refresh failed: {error}")

            cached = self._views.get(selected.key)
            if cached is not None:
                return Refresh(cached)

            # Throttled with nothing cached for this range. If no ingest has
            # *ever* worked, say so — an empty page and a broken scanner look
            # identical otherwise, and silently showing zeroes is worse.
            never_succeeded = self._last_success_at is None
            warning = "no successful refresh yet" if never_succeeded else None

            # Otherwise this is just a range the user switched to between
            # scans. Read the store rather than making them wait out the
            # throttle; do not re-scan.
            try:
                with store.Store(self.db_path) as db:
                    built = self._build(db.records(), db.titles(), selected)
                if not never_succeeded:
                    self._views[selected.key] = built
                return Refresh(built, warning)
            except Exception as error:  # noqa: BLE001 - a wall display must not 500
                return Refresh(
                    self._build([], {}, selected),
                    f"could not read history: {error}",
                )

    def _degraded(self, selected: ranges.Range, warning: str) -> Refresh:
        """What to serve when a refresh just failed.

        Only this range's own cached view. Falling back to whatever else
        happens to be cached served a different window's numbers under a
        banner that only claimed the data was stale.
        """
        stale = self._views.get(selected.key)
        if stale is None:
            return Refresh(
                self._build([], {}, selected),
                f"{warning} — no data to show for this range",
            )
        # The age clause is only true when there is in fact older data on
        # screen. Attached to an empty page it claims figures nobody can see.
        if self._last_success_at is not None:
            age = int((self._now() - self._last_success_at).total_seconds())
            warning += f" — showing data from {age}s ago"
        return Refresh(stale, warning)
