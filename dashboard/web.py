"""Serves the dashboard on the LAN. Plain HTTP behind an unguessable path."""

from __future__ import annotations

import datetime as dt
import errno
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import aggregate, cowork, ingest, plans, ranges, render_html, scan, store

DEFAULT_PORT = 8420
DEFAULT_MIN_INGEST_INTERVAL = 10.0
DEFAULT_REFRESH_SECONDS = 30


def load_or_create_token(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "token"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(16)
    if path.exists():
        # Pre-existing but empty (e.g. a previous run crashed mid-write) —
        # O_EXCL below would refuse to open it, so truncate instead.
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
        # Without O_CREAT, os.open ignores the mode argument for existing files,
        # so we must fchmod the descriptor immediately after opening, before writing.
        os.fchmod(fd, 0o600)
    else:
        # Atomic create-at-mode-0600: no window where the file exists
        # world-readable before we get a chance to chmod it.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    path.chmod(0o600)  # belt-and-suspenders against umask/pre-existing mode
    return token


def local_ip() -> str:
    """Best-effort LAN address, for printing a reachable URL."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class App:
    """Owns the store and the last good render."""

    def __init__(
        self,
        db_path: str | Path,
        projects_dir: Path,
        *,
        token: str,
        cowork_dir: Path | None = None,
        base_path: str = "",
        plan: plans.Plan | None = None,
        refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
        min_ingest_interval: float = DEFAULT_MIN_INGEST_INTERVAL,
        clock=time.monotonic,
        now=dt.datetime.now,
    ) -> None:
        self.db_path = db_path
        self.projects_dir = Path(projects_dir)
        self.cowork_dir = Path(cowork_dir) if cowork_dir is not None else cowork.default_cowork_dir()
        # Never resolved here: resolve() can prompt, and App is constructed
        # inside a request path in tests. The CLI settles the plan up front.
        self.plan = plan or plans.DEFAULT
        # Prefix the range links point at, e.g. /d/<token>. Empty in tests,
        # where relative links are fine.
        self.base_path = base_path
        # How often the page reloads itself, in place — a selected range is
        # carried across reloads rather than reset.
        self.refresh_seconds = refresh_seconds
        self.token = token
        self.min_ingest_interval = min_ingest_interval
        self._clock = clock
        self._now = now
        self._last_ingest_at: float | None = None
        # One rendered page per range. Cleared whenever an ingest lands,
        # because every range's numbers move when new rows arrive.
        self._pages: dict[str, str] = {}
        self._last_success_at: dt.datetime | None = None
        self.ingest_count = 0
        # Guards the check-then-act in page(): _due() and the ingest that
        # follows it must be atomic, or concurrent requests (the server is
        # threaded) can all observe "due" before any of them stamps
        # _last_ingest_at, and each triggers its own full scan. Holding the
        # lock for the whole method — not just the check — means threads
        # that lose the race simply wait for the one scan already in
        # flight and then fall into the "not due" branch, returning the
        # page it just rendered.
        self._lock = threading.Lock()

    def _scan(self, projects_dir: Path, skip):
        return ingest.scan_sources(
            ingest.default_sources(projects_dir, self.cowork_dir), skip
        )

    def _due(self) -> bool:
        if self._last_ingest_at is None:
            return True
        return (self._clock() - self._last_ingest_at) >= self.min_ingest_interval

    def _render(self, records, titles, selected, warning=None) -> str:
        data = aggregate.build(
            records,
            titles,
            now=self._now(),
            max_plan_monthly_usd=self.plan.monthly_usd,
            plan_label=self.plan.label,
            range_key=selected.key,
        )
        return render_html.render(
            data,
            warning=warning,
            base_path=self.base_path,
            refresh_seconds=self.refresh_seconds,
        )

    def page(self, range_key: str | None = None) -> str:
        # Whole method under the lock: the _due() check and the ingest it
        # gates must be atomic across threads. See the note on self._lock.
        selected = ranges.resolve(range_key)
        with self._lock:
            if self._due():
                try:
                    with store.Store(self.db_path) as db:
                        result = self._scan(self.projects_dir, db.file_stats())
                        db.ingest(result)
                        records, titles = db.records(), db.titles()
                    self.ingest_count += 1
                    self._last_ingest_at = self._clock()
                    self._last_success_at = self._now()
                    # Every cached range is stale the moment new rows land.
                    self._pages.clear()
                    rendered = self._render(records, titles, selected)
                    self._pages[selected.key] = rendered
                    return rendered
                except Exception as error:  # noqa: BLE001 - a wall display must not 500
                    self._last_ingest_at = self._clock()
                    warning = f"refresh failed: {error}"
                    if self._last_success_at is not None:
                        age = int((self._now() - self._last_success_at).total_seconds())
                        warning += f" — showing data from {age}s ago"
                    stale = self._pages.get(selected.key) or next(iter(self._pages.values()), None)
                    if stale is not None:
                        return _inject_warning(stale, warning)
                    return self._render([], {}, selected, warning=warning)

            cached = self._pages.get(selected.key)
            if cached is not None:
                return cached

            # Throttled with nothing cached. If no ingest has *ever* worked,
            # say so — an empty page and a broken scanner look identical
            # otherwise, and silently showing zeroes is the worse failure.
            never_succeeded = self._last_success_at is None
            warning = "no successful refresh yet" if never_succeeded else None

            # Otherwise this is just a range the user switched to between
            # scans. Read the store rather than making them wait out the
            # throttle; do not re-scan.
            try:
                with store.Store(self.db_path) as db:
                    rendered = self._render(db.records(), db.titles(), selected, warning=warning)
                if not never_succeeded:
                    self._pages[selected.key] = rendered
                return rendered
            except Exception as error:  # noqa: BLE001 - a wall display must not 500
                return self._render([], {}, selected, warning=f"could not read history: {error}")


def _inject_warning(page: str, warning: str) -> str:
    from html import escape

    banner = f'<div class="warn">{escape(warning)}</div>'
    marker = '<div class="titlebar">'
    index = page.find("</div>", page.find(marker))
    if index == -1:
        return page
    cut = index + len("</div>")
    return page[:cut] + banner + page[cut:]


def build_handler(app: App, token: str) -> type[BaseHTTPRequestHandler]:
    expected_path = f"/d/{token}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "ClaudeTokenDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            # secrets.compare_digest raises TypeError on non-ASCII str inputs.
            # A stray client sending raw non-ASCII bytes on the request line
            # (self.path is decoded permissively, so this reaches us as a
            # str rather than failing to parse) must still get a 404, not an
            # unhandled exception that drops the connection. Compare UTF-8
            # encoded bytes instead, which keeps the match constant-time.
            # Split the query string off first. Only the path carries the
            # secret, so `?range=…` must not participate in the comparison —
            # but the comparison itself stays constant-time over the path.
            raw_path, _, query = self.path.partition("?")
            if not secrets.compare_digest(
                raw_path.encode("utf-8", "surrogateescape"),
                expected_path.encode("utf-8"),
            ):
                self.send_error(404, "Not Found")
                return
            # An unknown or malformed range falls back to the default rather
            # than erroring: it is a stale bookmark, not an attack.
            requested = parse_qs(query).get("range", [None])[0]
            body = app.page(requested).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return  # a wall display polling every 30s would flood the console

    return Handler


def bind_message(host: str, port: int, error: OSError) -> str:
    """Turn a bind failure into something worth reading.

    Unhandled, these surface as a socket traceback ending in
    `self.socket.bind(...)`, which says nothing about what went wrong or what
    to do about it. Every case here is recoverable by whoever ran the command,
    so name the cause and give them the next step.
    """
    if error.errno == errno.EADDRINUSE:
        return (
            f"Port {port} is already in use — the dashboard is probably "
            "running already.\n"
            f"  See what has it:  lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  Or pick another:  python3 -m dashboard --port {port + 1}"
        )
    if error.errno == errno.EACCES:
        return (
            f"Not allowed to bind port {port}. Ports below 1024 need root — "
            f"pick a higher one, e.g. --port {DEFAULT_PORT}."
        )
    if error.errno == errno.EADDRNOTAVAIL:
        return (
            f"No interface on this machine has the address {host!r}, so there "
            "is nothing to bind to. Drop --host to listen on every interface."
        )
    return f"Could not bind {host}:{port} — {error}"


def bind(host: str, port: int, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as error:
        raise SystemExit(bind_message(host, port, error)) from error


def serve(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    plan: plans.Plan | None = None,
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
) -> None:
    token = load_or_create_token(store.DATA_DIR)
    app = App(
        db_path=store.default_db_path(),
        projects_dir=scan.default_projects_dir(),
        token=token,
        plan=plan,
        base_path=f"/d/{token}",
        refresh_seconds=refresh_seconds,
    )
    httpd = bind(host, port, build_handler(app, token))
    print(f"Claude token dashboard on http://{local_ip()}:{port}/d/{token}")
    print(f"Comparing against {app.plan.label}. Change it with --plan.")
    print("Open that on the iPad, then Share > Add to Home Screen. Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
