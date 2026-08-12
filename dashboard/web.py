"""Serves the dashboard on the LAN. Plain HTTP behind an unguessable path."""

from __future__ import annotations

import datetime as dt
import errno
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import freshness, plans, qr, ranges, render_html, scan, store, tokens

DEFAULT_PORT = 8420
DEFAULT_MIN_INGEST_INTERVAL = freshness.DEFAULT_MIN_INGEST_INTERVAL
DEFAULT_REFRESH_SECONDS = 30


def load_or_create_token(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "token"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = tokens.new_token()
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
    """Turns a request for a range into a page.

    Everything about *how current* the numbers are lives behind
    freshness.Freshness. What is left here is presentation: which prefix the
    range links point at, and how often the page reloads itself.
    """

    def __init__(
        self,
        db_path: str | Path,
        projects_dir: Path,
        *,
        cowork_dir: Path | None = None,
        base_path: str = "",
        plan: plans.Plan | None = None,
        refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
        min_ingest_interval: float = DEFAULT_MIN_INGEST_INTERVAL,
        clock=time.monotonic,
        now=dt.datetime.now,
        scan=None,
    ) -> None:
        self.freshness = freshness.Freshness(
            db_path,
            projects_dir,
            cowork_dir=cowork_dir,
            plan=plan,
            min_interval=min_ingest_interval,
            clock=clock,
            now=now,
            scan=scan,
        )
        # Prefix the range links point at, e.g. /d/<token>. Empty in tests,
        # where relative links are fine.
        self.base_path = base_path
        # How often the page reloads itself, in place — a selected range is
        # carried across reloads rather than reset.
        self.refresh_seconds = refresh_seconds

    @property
    def plan(self) -> plans.Plan:
        return self.freshness.plan

    @property
    def ingest_count(self) -> int:
        return self.freshness.ingest_count

    def page(self, range_key: str | None = None) -> str:
        # An unknown or malformed range falls back to the default rather than
        # erroring: it is a stale bookmark, not an attack.
        current = self.freshness.view(ranges.resolve(range_key))
        return render_html.render(
            current.data,
            warning=current.warning,
            base_path=self.base_path,
            refresh_seconds=self.refresh_seconds,
        )


def build_handler(app: App, token: str) -> type[BaseHTTPRequestHandler]:

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
            # A trailing slash is the single easiest way to mistype this URL,
            # and some clients add one on their own. It cannot change which
            # resource is meant, so accept it rather than answering 404 to a
            # request that is right in every way that matters.
            if raw_path.endswith("/") and raw_path != "/":
                raw_path = raw_path.rstrip("/")
            prefix, _, candidate = raw_path.rpartition("/")
            # The prefix is not secret, so an ordinary comparison is fine.
            # Only the token goes through the constant-time path.
            if prefix != "/d" or not tokens.matches(token, candidate):
                self.send_not_found()
                return
            requested = parse_qs(query).get(ranges.QUERY_KEY, [None])[0]
            body = app.page(requested).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_not_found(self) -> None:
            """404 that is explicitly not cacheable.

            send_error() sets no caching headers, and a 404 is cacheable by
            default under HTTP/1.1. A browser that mistypes the token once can
            therefore keep serving itself the cached failure after the URL is
            corrected — which looks exactly like the token still being wrong.
            """
            body = b"Not Found"
            self.send_response(404, "Not Found")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return  # a wall display polling every 30s would flood the console

    return Handler


def port_80_holder(host: str = "127.0.0.1", timeout: float = 0.4, port: int = 80) -> str | None:
    """Whatever is listening on port 80, or None.

    Not idle curiosity. If the URL loses its ":8420" — retyped on a phone,
    truncated in a note, "helpfully" tidied by something in between — the
    request goes to port 80 instead. When something else answers there the
    user sees ITS 404, which looks identical to the token being wrong and
    sends them debugging the wrong thing entirely.

    `port` is a parameter only so the header parsing below can be exercised
    against a server a test is allowed to start. Binding 80 needs root, so
    with the port hard-coded the only test that could exist was one that
    stood up its own server and then asserted against the standard library
    instead of against this function.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        if probe.connect_ex((host, port)) != 0:
            return None
        probe.sendall(b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        reply = probe.recv(512).decode("latin-1", "replace")
    except OSError:
        return None
    finally:
        probe.close()
    for line in reply.splitlines():
        if line.lower().startswith("server:"):
            return line.split(":", 1)[1].strip() or "an unidentified server"
    return "an unidentified server"


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
        plan=plan,
        base_path=f"/d/{token}",
        refresh_seconds=refresh_seconds,
    )
    httpd = bind(host, port, build_handler(app, token))
    url = f"http://{local_ip()}:{port}/d/{token}"
    print(f"Claude token dashboard on {url}")
    print(f"Comparing against {app.plan.label}. Change it with --plan.")
    print("Open that on the iPad, then Share > Add to Home Screen. Ctrl-C to stop.")

    if sys.stdout.isatty():
        # Only for a terminal: ANSI blocks are noise in a log file, and a
        # code nobody can point a camera at is just clutter.
        print()
        print(qr.render(url, ansi=True))

    other = port_80_holder()
    if other is not None:
        print()
        print(f"NOTE: {other} is listening on port 80 of this machine.")
        print(f"      The URL above must keep its \":{port}\". Without it the")
        print("      request goes to that server instead, and its 404 looks")
        print("      exactly like a wrong token.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
