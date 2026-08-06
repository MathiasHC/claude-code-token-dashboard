"""Incremental (resume-from-offset) transcript reading.

The active session's transcript is appended to on every message, so a naive
scanner re-parses the whole file on every refresh. Long sessions routinely
reach tens of megabytes, where that costs hundreds of milliseconds every
time. Resuming from a byte offset makes a refresh cost the appended bytes
instead.

The risk this buys is silently *missing* records, so most of these tests are
about the cases where resuming is not safe and must fall back to a full
re-read.
"""

from __future__ import annotations

import json

import pytest

from dashboard import pricing, scan

MODEL = "claude-opus-5"


def line(message_id: str, *, output: int = 1000, kind: str = "assistant") -> str:
    return json.dumps(
        {
            "type": kind,
            "sessionId": "s1",
            "cwd": "/Users/demo/alpha",
            "timestamp": "2026-07-29T10:00:00.000Z",
            "message": {
                "id": message_id,
                "model": MODEL,
                "usage": {"input_tokens": 0, "output_tokens": output},
            },
        }
    )


def user_line(text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "sessionId": "s1",
            "cwd": "/Users/demo/alpha",
            "timestamp": "2026-07-29T09:59:00.000Z",
            "message": {"role": "user", "content": text},
        }
    )


def write(path, *lines, mode="w"):
    with path.open(mode, encoding="utf-8") as handle:
        for item in lines:
            handle.write(item + "\n")


def ids(result):
    return [r.message_id for r in result.records]


def total(result):
    return sum(pricing.cost(r).total for r in result.records)


# --- the core behaviour -------------------------------------------------

def test_appended_lines_are_the_only_ones_re_read(tmp_path):
    path = tmp_path / "s1.jsonl"
    write(path, user_line("hello"), line("m1"))
    first = scan.scan(tmp_path)
    assert ids(first) == ["m1"]

    write(path, line("m2"), mode="a")
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert ids(second) == ["m2"], "m1 should not be parsed again"


def test_offset_advances_to_the_end_of_the_file(tmp_path):
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"))
    first = scan.scan(tmp_path)
    assert first.file_stats[str(path)].offset == path.stat().st_size

    write(path, line("m2"), mode="a")
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert second.file_stats[str(path)].offset == path.stat().st_size


def test_incremental_reads_produce_the_same_records_as_one_full_read(tmp_path):
    """The property that matters: however the file was consumed, the set of
    records and the total cost must be identical."""
    incremental_dir = tmp_path / "inc"
    incremental_dir.mkdir()
    path = incremental_dir / "s1.jsonl"

    collected = []
    stats = {}
    for index in range(1, 11):
        write(path, line(f"m{index}", output=index * 100), mode="a" if index > 1 else "w")
        result = scan.scan(incremental_dir, skip=stats)
        collected.extend(result.records)
        stats = result.file_stats

    whole_dir = tmp_path / "whole"
    whole_dir.mkdir()
    write(whole_dir / "s1.jsonl", *[line(f"m{i}", output=i * 100) for i in range(1, 11)])
    one_pass = scan.scan(whole_dir)

    assert [r.message_id for r in collected] == ids(one_pass)
    assert sum(pricing.cost(r).total for r in collected) == pytest.approx(
        total(one_pass), abs=1e-12
    )


# --- when resuming is NOT safe ------------------------------------------

def test_a_partial_trailing_line_is_not_consumed(tmp_path):
    """A transcript caught mid-append ends in half a line. Consuming it
    would lose that record forever, because the offset would move past it."""
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"))
    first = scan.scan(tmp_path)

    partial = line("m2")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(partial[: len(partial) // 2])  # no trailing newline
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert ids(second) == []
    assert second.file_stats[str(path)].offset < path.stat().st_size

    # The writer finishes the line; the record must now appear.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(partial[len(partial) // 2 :] + "\n")
    third = scan.scan(tmp_path, skip=second.file_stats)
    assert ids(third) == ["m2"]


def test_a_rewritten_file_is_read_from_the_top(tmp_path):
    """Same length, different content — only the head digest catches this."""
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"), line("m2"))
    first = scan.scan(tmp_path)

    write(path, line("m3"), line("m4"))  # truncate + rewrite
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert ids(second) == ["m3", "m4"]


def test_a_truncated_file_is_read_from_the_top(tmp_path):
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"), line("m2"), line("m3"))
    first = scan.scan(tmp_path)

    write(path, line("m1"))  # shorter than the recorded offset
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert ids(second) == ["m1"]


def test_state_without_a_head_forces_a_full_read(tmp_path):
    """Rows written before offsets were recorded carry offset 0 and an empty
    head. They must re-read rather than be trusted as a resume point."""
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"), line("m2"))
    info = path.stat()
    legacy = {str(path): scan.FileState(size=info.st_size - 1, mtime=info.st_mtime)}
    result = scan.scan(tmp_path, skip=legacy)
    assert ids(result) == ["m1", "m2"]


def test_an_unchanged_file_is_not_reopened_at_all(tmp_path):
    path = tmp_path / "s1.jsonl"
    write(path, line("m1"))
    first = scan.scan(tmp_path)
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert second.files_read == 0
    assert second.file_stats[str(path)] == first.file_stats[str(path)]


# --- titles -------------------------------------------------------------

def test_an_incremental_read_does_not_overwrite_the_session_title(tmp_path):
    """Titles come from the session's *first* user message. Reading only the
    tail, the first user message seen is a later one — emitting it would
    replace a correct stored title with a mid-session prompt."""
    path = tmp_path / "s1.jsonl"
    write(path, user_line("the real first prompt"), line("m1"))
    first = scan.scan(tmp_path)
    assert first.titles["s1"] == "the real first prompt"

    write(path, user_line("a much later prompt"), line("m2"), mode="a")
    second = scan.scan(tmp_path, skip=first.file_stats)
    assert "s1" not in second.titles


def test_a_full_re_read_still_derives_the_title(tmp_path):
    path = tmp_path / "s1.jsonl"
    write(path, user_line("first prompt"), line("m1"))
    assert scan.scan(tmp_path).titles["s1"] == "first prompt"
