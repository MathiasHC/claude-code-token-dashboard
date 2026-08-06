from __future__ import annotations

from pathlib import Path

from dashboard import scan

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def test_project_from_plain_cwd_is_the_basename():
    assert scan.project_from_cwd("/Users/demo/alpha") == "alpha"


def test_project_from_worktree_folds_into_the_parent_repo():
    """15 worktrees of one repo must not appear as 15 projects."""
    assert scan.project_from_cwd("/Users/demo/beta/.claude/worktrees/feature-x") == "beta"


def test_project_from_missing_cwd_is_labelled_unknown():
    assert scan.project_from_cwd(None) == "(unknown)"
    assert scan.project_from_cwd("") == "(unknown)"


def test_project_strips_a_trailing_slash():
    assert scan.project_from_cwd("/Users/demo/alpha/") == "alpha"


def test_session_title_unwraps_a_slash_command():
    raw = "<command-message>graphify</command-message><command-name>/graphify</command-name>"
    assert scan.session_title(raw) == "/graphify"


def test_session_title_collapses_whitespace_in_prose():
    assert scan.session_title("Refactor  the   billing module") == "Refactor the billing module"


def test_local_day_uses_local_time_not_utc():
    """23:30 UTC on the 29th is already the 30th in any positive offset zone.
    Bucketing on the raw UTC string would file late-night work under the
    wrong day."""
    import datetime as dt

    ts = "2026-07-29T23:30:00.000Z"
    expected = dt.datetime.fromisoformat("2026-07-29T23:30:00+00:00").astimezone().date().isoformat()
    assert scan.local_day(ts) == expected


def test_local_day_of_empty_timestamp_is_empty():
    assert scan.local_day("") == ""


def test_scan_deduplicates_by_message_id_across_files():
    """msg_a appears in both sess-a.jsonl and sess-a-resumed.jsonl."""
    result = scan.scan(FIXTURES)
    ids = [r.message_id for r in result.records]
    assert ids.count("msg_a") == 1


def test_scan_finds_every_usage_bearing_message_once():
    result = scan.scan(FIXTURES)
    assert sorted(r.message_id for r in result.records) == [
        "msg_a",
        "msg_b",
        "msg_legacy",
        "msg_sub",
        "msg_unpriced",
    ]


def test_scan_finds_subagent_transcripts_nested_below_the_session():
    """The old glob was one level deep and missed 79% of files."""
    ids = {r.message_id for r in scan.scan(FIXTURES).records}
    assert "msg_sub" in ids


def test_subagent_records_are_flagged():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_sub")
    assert record.is_subagent is True


def test_main_session_records_are_not_flagged_as_subagent():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_a")
    assert record.is_subagent is False


def test_subagent_records_attribute_to_the_parent_session_and_project():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_sub")
    assert record.session_id == "sess-a"
    assert record.project == "alpha"


def test_scan_skips_assistant_messages_with_no_usage_block():
    result = scan.scan(FIXTURES)
    assert "msg_no_usage" not in {r.message_id for r in result.records}


def test_scan_counts_malformed_lines_without_raising():
    result = scan.scan(FIXTURES)
    assert result.malformed_lines == 1


def test_scan_reads_cache_write_ttls_separately():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_a")
    assert record.cache_write_5m == 4000
    assert record.cache_write_1h == 6000


def test_scan_falls_back_to_the_legacy_flat_cache_creation_field():
    """Older rows carry cache_creation_input_tokens instead of the nested
    object. Treat them as 5-minute writes."""
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_legacy")
    assert record.cache_write_5m == 8000
    assert record.cache_write_1h == 0


def test_scan_attributes_project_and_skill():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_a")
    assert record.project == "alpha"
    assert record.skill == "graphify"


def test_scan_defaults_missing_skill_to_none_label():
    record = next(r for r in scan.scan(FIXTURES).records if r.message_id == "msg_b")
    assert record.skill == "(none)"


def test_scan_collects_session_titles():
    titles = scan.scan(FIXTURES).titles
    assert titles["sess-a"] == "/graphify"
    assert titles["sess-b"] == "Refactor the billing module <please>"


def test_scan_ignores_is_meta_user_lines_when_titling():
    assert scan.scan(FIXTURES).titles["sess-b"] != "injected meta preamble"


def test_scan_reports_file_stats_for_every_file_read():
    result = scan.scan(FIXTURES)
    assert len(result.file_stats) == 4
    for state in result.file_stats.values():
        assert state.size > 0 and state.mtime > 0
        # A fully-read file ends at its own size and carries a head digest,
        # so the next pass can tell it is the same file extended rather
        # than a different one rewritten in place.
        assert state.offset == state.size
        assert state.head


def test_scan_skips_files_whose_size_and_mtime_are_unchanged():
    first = scan.scan(FIXTURES)
    second = scan.scan(FIXTURES, skip=first.file_stats)
    assert second.records == []
    assert second.files_read == 0


def test_scan_rereads_a_file_whose_stats_changed():
    first = scan.scan(FIXTURES)
    stale = {
        path: scan.FileState(size=state.size - 1, mtime=state.mtime)
        for path, state in first.file_stats.items()
    }
    second = scan.scan(FIXTURES, skip=stale)
    assert len(second.records) == 5


def test_scan_of_a_missing_directory_is_empty_not_an_error():
    result = scan.scan(FIXTURES / "does-not-exist")
    assert result.records == []
    assert result.files_read == 0


def test_a_file_that_fails_to_open_is_not_recorded_as_ingested(tmp_path, monkeypatch):
    """scan.py:85 promises file_stats maps a path to the (size, mtime) *last
    ingested*. A file that stats fine but then fails to open (a transient
    EMFILE, a permissions blip, a file replaced mid-scan) must not be
    recorded — otherwise the store treats it as durably ingested and it is
    silently skipped forever, even after the failure clears."""
    good = tmp_path / "good.jsonl"
    good.write_text('{"type":"user","sessionId":"s1","message":{"content":"hello"}}\n')
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"type":"assistant","sessionId":"s2",'
        '"message":{"id":"msg_x","model":"claude-opus-4-8","usage":{"input_tokens":1}}}\n'
    )

    real_open = Path.open
    should_fail = True

    def flaky_open(self, *args, **kwargs):
        if self == bad and should_fail:
            raise OSError("simulated transient failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    first = scan.scan(tmp_path)
    assert str(bad) not in first.file_stats
    assert str(good) in first.file_stats

    should_fail = False
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert "msg_x" in {r.message_id for r in second.records}
    assert str(bad) in second.file_stats
