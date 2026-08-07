from __future__ import annotations

import datetime as dt
import errno
import http.client
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
        token="tok123",
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


def test_fallback_branch_does_not_expose_token_during_write(tmp_path):
    """Verify that re-writing an empty pre-existing token file does not
    temporarily expose the token at a permissive mode.

    This tests the fallback branch in load_or_create_token that truncates
    an existing empty file. Without os.fchmod on the descriptor, a file that
    was created at 0o644 by an older build would still be world-readable
    when the token is written to it, until the final chmod catches up.

    We create an empty 0o644 file, then call load_or_create_token on a
    background thread while an observer thread polls the file continuously,
    recording (mode, has_content) pairs. The assertion is that we never
    observe non-empty content at a mode other than 0o600.
    """
    token_path = tmp_path / "token"

    # Create an empty token file at permissive mode to trigger the fallback branch
    token_path.touch()
    os.chmod(token_path, 0o644)

    observations = []
    stop_observing = threading.Event()
    sync_event = threading.Event()

    def observer():
        """Poll the token file, recording (mode, has_content) pairs."""
        while not stop_observing.is_set():
            try:
                st = os.stat(token_path)
                mode = st.st_mode & 0o777
                try:
                    content = token_path.read_text(encoding="utf-8").strip()
                except:
                    content = ""
                has_content = bool(content)
                observations.append((mode, has_content))
            except (FileNotFoundError, OSError):
                pass
            time.sleep(0.00001)  # Poll very frequently (every 10 microseconds)

    def writer():
        """Run load_or_create_token in a background thread."""
        # Hook to synchronize: give observer thread time to see the fchmod effect
        def sync_hook():
            sync_event.set()
            time.sleep(0.001)

        original_sync = web._test_sync_after_fchmod
        web._test_sync_after_fchmod = sync_hook
        try:
            web.load_or_create_token(tmp_path)
        finally:
            web._test_sync_after_fchmod = original_sync

    observer_thread = threading.Thread(target=observer, daemon=True)
    observer_thread.start()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join(timeout=5)

    stop_observing.set()
    observer_thread.join(timeout=1)

    # Verify the final state
    final_mode = (token_path.stat().st_mode & 0o777)
    final_content = token_path.read_text(encoding="utf-8").strip()
    assert final_mode == 0o600, f"Final mode is {oct(final_mode)}, expected 0o600"
    assert len(final_content) >= 16, "Final token is empty or too short"

    # Verify no observation recorded non-empty content at a non-0o600 mode.
    # This is the critical safety check: the token was never exposed.
    for mode, has_content in observations:
        if has_content and mode != 0o600:
            pytest.fail(
                f"Observed non-empty token content at mode {oct(mode)}, "
                f"expected mode 0o600. This is a permission race condition."
            )


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
    real_scan = app._scan

    def slow_scan(*args, **kwargs):
        time.sleep(0.2)
        return real_scan(*args, **kwargs)

    app._scan = slow_scan  # type: ignore[method-assign]

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

    app._scan = explode  # type: ignore[method-assign]
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

    app._scan = explode  # type: ignore[method-assign]
    app._last_ingest_at = None  # force a re-ingest attempt
    degraded = app.page()
    assert 'class="warn"' in degraded
    assert "CLAUDE TOKENS" in degraded


def test_first_ingest_failure_still_renders_a_page(tmp_path):
    app = make_app(tmp_path)

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    app._scan = explode  # type: ignore[method-assign]
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
