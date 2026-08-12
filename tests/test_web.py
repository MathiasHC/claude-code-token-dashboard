from __future__ import annotations

import datetime as dt
import errno
import http.client
import http.server
import os
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard import web

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
COWORK_FIXTURES = Path(__file__).parent / "fixtures" / "cowork"


def make_app(tmp_path, **overrides):
    kwargs = dict(
        db_path=tmp_path / "history.db",
        projects_dir=FIXTURES,
        # Explicit, and deliberately absent by default: App falls back to the
        # real ~/Library/.../local-agent-mode-sessions, so an unset cowork_dir
        # would make every web test read whatever Cowork history happens to be
        # on the machine — non-deterministic, and against the rule that the
        # suite never asserts on live data. Tests that want it pass it in.
        cowork_dir=tmp_path / "no-cowork",
        now=lambda: dt.datetime(2026, 7, 30, 9, 12, 0),
    )
    kwargs.update(overrides)
    return web.App(**kwargs)


def test_token_is_created_and_persisted(tmp_path):
    first = web.load_or_create_token(tmp_path)
    assert len(first) >= 16
    assert web.load_or_create_token(tmp_path) == first


def test_token_file_is_not_world_readable(tmp_path):
    web.load_or_create_token(tmp_path)
    mode = (tmp_path / "token").stat().st_mode & 0o777
    assert mode == 0o600


def test_rewriting_an_empty_token_file_chmods_before_writing(tmp_path, monkeypatch):
    """The fallback branch truncates a pre-existing empty token file. Without
    os.fchmod on the descriptor, a file created at 0o644 by an older build
    stays world-readable while the token is written into it.

    Asserts the descriptor is narrowed *before* any content reaches it. The
    previous version of this test raced two threads and polled the file every
    10us hoping to catch the exposure — but its instrumentation hung off
    os.fchmod, so it only ran when the line under test was present. Deleting
    that line stopped the observer widening any window at all, which is
    exactly the regression it was written to catch.
    """
    token_path = tmp_path / "token"
    token_path.touch()
    os.chmod(token_path, 0o644)

    events: list[tuple[str, int]] = []
    real_fchmod = os.fchmod
    real_write = os.write

    def spy_fchmod(fd, mode):
        events.append(("fchmod", mode))
        return real_fchmod(fd, mode)

    def spy_write(fd, payload):
        events.append(("write", len(payload)))
        return real_write(fd, payload)

    monkeypatch.setattr(os, "fchmod", spy_fchmod)
    monkeypatch.setattr(os, "write", spy_write)
    web.load_or_create_token(tmp_path)

    assert ("fchmod", 0o600) in events, "descriptor was never narrowed to 0o600"
    narrowed = events.index(("fchmod", 0o600))
    wrote = next((i for i, (kind, _) in enumerate(events) if kind == "write"), None)
    if wrote is not None:
        assert narrowed < wrote, "token bytes were written before the chmod"
    assert (token_path.stat().st_mode & 0o777) == 0o600


def test_page_renders_html_from_the_fixture_tree(tmp_path):
    page = make_app(tmp_path).page()
    assert page.startswith("<!DOCTYPE html>")
    assert "CLAUDE TOKENS" in page


def test_ingest_is_throttled(tmp_path):
    """Several open clients must not each trigger a full scan.

    Clock ticks are consumed in this order: call 1 skips the _due() check
    (nothing ingested yet) and stamps 0.0 on success; call 2 reads 1.0 in
    _due() and is suppressed; call 3 reads 100.0, ingests, and stamps 101.0.
    """
    ticks = iter([0.0, 1.0, 100.0, 101.0])
    app = make_app(tmp_path, clock=lambda: next(ticks), min_ingest_interval=10.0)
    app.page()
    app.page()
    assert app.ingest_count == 1
    app.page()
    assert app.ingest_count == 2


def test_concurrent_requests_trigger_only_one_ingest(tmp_path):
    """Several open clients must not each trigger a full transcript scan.
    The delay widens the race window so this fails deterministically
    without the lock rather than passing by luck."""
    app = make_app(tmp_path)
    real_scan = app.freshness.scan

    def slow_scan(*args, **kwargs):
        time.sleep(0.2)
        return real_scan(*args, **kwargs)

    app.freshness.scan = slow_scan  # type: ignore[method-assign]

    threads = [threading.Thread(target=app.page) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert app.ingest_count == 1


def test_throttled_call_before_any_success_still_renders(tmp_path):
    """Regression guard: a failed first ingest leaves no cached page, and the
    next call arrives inside the throttle window. It must render something
    rather than trip an assertion."""
    app = make_app(tmp_path)

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app.freshness.scan = explode  # type: ignore[method-assign]
    app.page()
    second = app.page()
    assert second.startswith("<!DOCTYPE html>")
    assert 'class="warn"' in second


def test_page_serves_last_good_render_with_a_warning_when_ingest_fails(tmp_path):
    app = make_app(tmp_path)
    good = app.page()
    assert 'class="warn"' not in good

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app.freshness.scan = explode  # type: ignore[method-assign]
    app.freshness._last_ingest_at = None  # force a re-ingest attempt
    degraded = app.page()
    assert 'class="warn"' in degraded
    assert "CLAUDE TOKENS" in degraded


def test_first_ingest_failure_still_renders_a_page(tmp_path):
    app = make_app(tmp_path)

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app.freshness.scan = explode  # type: ignore[method-assign]
    page = app.page()
    assert page.startswith("<!DOCTYPE html>")
    assert 'class="warn"' in page


@pytest.fixture
def server(tmp_path):
    app = make_app(tmp_path)
    handler = web.build_handler(app, "tok123")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address
    httpd.shutdown()
    httpd.server_close()


def get(address, path):
    conn = http.client.HTTPConnection(*address, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode("utf-8", "replace")
    conn.close()
    return response.status, response.getheader("Content-Type"), body


def test_correct_token_path_returns_the_dashboard(server):
    status, content_type, body = get(server, "/d/tok123")
    assert status == 200
    assert content_type == "text/html; charset=utf-8"
    assert "CLAUDE TOKENS" in body


def test_wrong_token_returns_404(server):
    status, _, _ = get(server, "/d/wrong")
    assert status == 404


def test_root_returns_404_without_hinting_at_the_real_path(server):
    status, _, body = get(server, "/")
    assert status == 404
    assert "tok123" not in body


def test_unknown_path_returns_404(server):
    assert get(server, "/admin")[0] == 404


def test_non_ascii_path_returns_404_instead_of_raising(server):
    """secrets.compare_digest refuses non-ASCII str inputs and raises
    TypeError. That must not escape do_GET as an unhandled exception that
    closes the connection with no HTTP response — every non-matching path
    is specified to 404.

    http.client itself refuses to put non-ASCII bytes on the request line
    (it encodes with 'ascii'), so a raw socket is used to actually put a
    non-ASCII byte on the wire the way a stray client could."""
    with socket.create_connection(server, timeout=5) as sock:
        request = "GET /d/café HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode("utf-8"))
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    assert response, "expected an HTTP response, got a closed connection with no bytes"
    status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 404 " in status_line


# --- multi-source serving -----------------------------------------------

def test_served_page_includes_cowork_when_the_directory_exists(tmp_path):
    app = make_app(tmp_path, cowork_dir=COWORK_FIXTURES)
    page = app.page()
    assert "BY SOURCE" in page
    assert "Desktop (Cowork)" in page
    assert "Claude Code" in page


def test_served_page_omits_cowork_when_the_directory_is_absent(tmp_path):
    """A machine without Claude Desktop must still serve a page."""
    page = make_app(tmp_path).page()
    assert "Claude Code" in page
    assert "Desktop (Cowork)" not in page


def test_cowork_project_labels_reach_the_served_page(tmp_path):
    page = make_app(tmp_path, cowork_dir=COWORK_FIXTURES).page()
    assert "gamma" in page


# --- bind failures ------------------------------------------------------
# Unhandled, these reach the user as a socket traceback ending in
# `self.socket.bind(...)`. The most likely cause — the dashboard is already
# running — is entirely recoverable, so it must read as an instruction.

class _Handler(web.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # pragma: no cover - silence
        return


def test_binding_a_busy_port_exits_with_an_instruction_not_a_traceback():
    """The real-world case: run it twice. The second run must say what to do."""
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as caught:
            web.bind("127.0.0.1", port, _Handler)
    finally:
        occupied.close()

    message = str(caught.value)
    assert "already in use" in message
    assert str(port) in message
    assert "--port" in message, "should suggest a way out, not just name the fault"
    assert "Traceback" not in message


def test_bind_message_explains_a_privileged_port():
    error = OSError(errno.EACCES, "Permission denied")
    message = web.bind_message("0.0.0.0", 80, error)
    assert "below 1024" in message
    assert str(web.DEFAULT_PORT) in message


def test_bind_message_explains_an_unavailable_address():
    error = OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
    message = web.bind_message("10.9.9.9", 8420, error)
    assert "10.9.9.9" in message
    assert "--host" in message


def test_bind_message_falls_back_without_swallowing_the_cause():
    """An errno we haven't special-cased must still surface the OS text."""
    error = OSError(errno.ENOBUFS, "No buffer space available")
    message = web.bind_message("0.0.0.0", 8420, error)
    assert "No buffer space available" in message
    assert "0.0.0.0:8420" in message


def test_a_successful_bind_returns_a_usable_server():
    server = web.bind("127.0.0.1", 0, _Handler)
    try:
        assert server.server_address[1] > 0
    finally:
        server.server_close()


# --- range selection over HTTP ------------------------------------------

def test_query_string_selects_the_range(tmp_path):
    page = make_app(tmp_path).page("7d")
    assert "LAST 7 DAYS" in page


def test_an_unknown_range_serves_the_default_rather_than_erroring(tmp_path):
    """A stale bookmark must not produce a 500 or an error page."""
    page = make_app(tmp_path).page("last-tuesday")
    assert page.startswith("<!DOCTYPE html>")
    assert "ALL TIME" in page


def test_switching_range_does_not_wait_out_the_ingest_throttle(tmp_path):
    """Clicking a range is an interaction — it must repaint now, not in ten
    seconds — but it must not trigger a fresh scan either."""
    app = make_app(tmp_path, min_ingest_interval=10_000.0)
    app.page()
    assert app.ingest_count == 1
    other = app.page("today")
    assert "TODAY" in other
    assert app.ingest_count == 1, "switching range should not re-scan"


def test_each_range_is_cached_separately(tmp_path):
    """What the cache holds is the built DashboardData, one per range. The
    HTML is rebuilt each request — measured at 0.06ms against a 79ms build on
    30k records, so re-rendering costs nothing and keeps the degraded path
    able to re-render with a warning instead of patching markup."""
    app = make_app(tmp_path, min_ingest_interval=10_000.0)
    first = app.page("7d")
    assert app.page("7d") == first, "the same range should serve from cache"
    assert app.freshness._views["7d"] is app.freshness._views["7d"]
    assert app.page("30d") != first
    assert set(app.freshness._views) == {"7d", "30d"}


def test_an_ingest_invalidates_every_cached_range(tmp_path):
    """Stale ranges are worse than slow ones: after new rows land, a cached
    view for another range would show figures that no longer add up."""
    ticks = iter([0.0, 1.0, 2.0, 100.0, 101.0, 102.0, 103.0])
    app = make_app(tmp_path, clock=lambda: next(ticks), min_ingest_interval=10.0)
    app.page("7d")
    app.page("7d")  # inside the throttle: consumes a tick, does not re-ingest
    cached = app.freshness._views["7d"]
    app.page("30d")
    app.page("7d")  # clock jumps past the throttle -> re-ingest, cache cleared
    assert app.ingest_count == 2
    assert app.freshness._views["7d"] is not cached


def test_the_token_is_still_required_when_a_range_is_supplied(server):
    """The query string must not become a way around the token check."""
    status, _, _ = get(server, "/d/wrong-token?range=7d")
    assert status == 404


def test_a_range_is_served_over_http(server):
    status, _, body = get(server, "/d/tok123?range=today")
    assert status == 200
    assert "TODAY" in body


@pytest.mark.parametrize(
    "query", ["?range=", "?range=7d&range=30d", "?foo=bar", "?=", "?range=%zz", "?"]
)
def test_a_junk_query_string_does_not_break_routing(server, query):
    """parse_qs is lenient about malformed input; the handler must be too —
    these are stale links and crawlers, not something to 500 over."""
    status, _, _ = get(server, f"/d/tok123{query}")
    assert status == 200


def test_the_refresh_interval_reaches_the_page(tmp_path):
    page = make_app(tmp_path, refresh_seconds=90).page()
    assert 'content="90"' in page


# --- stale-page fallback ------------------------------------------------

def test_a_failed_refresh_never_serves_another_range(tmp_path):
    """The spec's required regression test. The fallback used to reach for
    next(iter(cache.values())), so a request for TODAY could be served the
    ALL TIME view under a banner that only said the data was stale — wrong
    window, no indication.

    Asserts that the *panels* carry TODAY, not merely that "LAST 30 DAYS" is
    absent. The earlier version checked only the absence, and the string it
    checked came from the DAILY heading, which was derived from the number of
    days carrying spend rather than from the range at all — so it could pass
    or fail for reasons unconnected to which window was served.
    """
    app = make_app(tmp_path, min_ingest_interval=0.0)
    app.page("30d")
    assert app.freshness._views["30d"].scoped.key == "30d"

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app.freshness.scan = explode  # type: ignore[method-assign]
    out = app.page("today")
    assert "WHERE THE MONEY GOES &middot; TODAY" in out
    assert "LAST 30 DAYS" not in out, "served a different range's page"
    assert 'class="warn"' in out


def test_the_banner_does_not_claim_data_it_is_not_showing(tmp_path):
    """'showing data from 120s ago' over an all-zero page describes figures
    the reader cannot see. The age clause belongs only to a real stale page."""
    app = make_app(tmp_path, min_ingest_interval=0.0)
    app.page("30d")

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app.freshness.scan = explode  # type: ignore[method-assign]
    empty = app.page("today")
    assert "showing data from" not in empty
    assert "no data to show for this range" in empty

    stale = app.page("30d")
    assert "showing data from" in stale, "a real stale page should still say so"


# --- reachability: the ways a correct-looking URL still 404s -------------

def test_a_trailing_slash_is_accepted(server):
    """The easiest way to mistype this URL, and some clients add one
    unprompted. It cannot change which resource is meant."""
    status, _, body = get(server, "/d/tok123/")
    assert status == 200
    assert "CLAUDE TOKENS" in body


def test_a_trailing_slash_still_requires_the_right_token(server):
    """Stripping the slash must not turn into stripping the check."""
    assert get(server, "/d/wrong/")[0] == 404
    assert get(server, "/d//")[0] == 404
    assert get(server, "//")[0] == 404


def test_the_404_forbids_caching(server):
    """A 404 is cacheable by default under HTTP/1.1, and send_error sets no
    caching headers. A browser that mistyped the token once could keep
    serving itself the cached failure after the URL was corrected — which
    looks exactly like the token still being wrong."""
    conn = http.client.HTTPConnection(*server, timeout=5)
    conn.request("GET", "/d/wrong-token")
    response = conn.getresponse()
    response.read()
    cache_control = response.getheader("Cache-Control") or ""
    conn.close()
    assert response.status == 404
    assert "no-store" in cache_control


def test_the_200_still_forbids_caching(server):
    conn = http.client.HTTPConnection(*server, timeout=5)
    conn.request("GET", "/d/tok123")
    response = conn.getresponse()
    response.read()
    cache_control = response.getheader("Cache-Control") or ""
    conn.close()
    assert "no-store" in cache_control


def test_port_80_probe_reports_nothing_when_nothing_listens():
    """An address with no listener must return None rather than hanging or
    raising — this runs on every startup."""
    assert web.port_80_holder(host="127.0.0.2", timeout=0.2) is None


def test_port_80_probe_names_what_it_found():
    """The probe reports the Server header so the startup warning can name the
    culprit.

    This used to stand up a server and then assert that http.client saw the
    Server header it had just set — it never called port_80_holder at all, and
    stayed green with the whole function deleted. It now points the probe at
    the fake server, which is what the `port` argument is for.
    """
    class Quiet(http.server.BaseHTTPRequestHandler):
        server_version = "PretendServer/9.9"
        sys_version = ""

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        found = web.port_80_holder(host="127.0.0.1", timeout=2.0, port=port)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert found is not None
    assert "PretendServer" in found


def test_the_server_accepts_a_mistyped_case(server):
    """The failure that prompted all of this: one character in the wrong
    case, and the dashboard was unreachable with no clue why."""
    assert get(server, "/d/TOK123")[0] == 200
    assert get(server, "/d/Tok123")[0] == 200
    assert get(server, "/d/tok123/")[0] == 200


def test_the_server_still_rejects_a_genuinely_wrong_token(server):
    """Forgiving transcription must not become forgiving the secret."""
    for wrong in ("/d/tok124", "/d/tok12", "/d/tok1234", "/d/", "/d/x"):
        assert get(server, wrong)[0] == 404, wrong
