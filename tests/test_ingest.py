"""Composing the scan across sources.

`scan_sources` had no tests of its own — it was only ever exercised through
test_web and test_end_to_end, which is how a redundant cross-source dedupe
survived in it unexamined.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard import ingest, scan, store
from dashboard.models import UsageRecord

CODE_FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
COWORK_FIXTURES = Path(__file__).parent / "fixtures" / "cowork"


def a_record(message_id: str, source: str) -> UsageRecord:
    return UsageRecord(
        message_id=message_id,
        ts="2026-08-11T10:00:00.000Z",
        day="2026-08-11",
        model="claude-opus-4-8",
        project="p",
        skill="s",
        session_id="sess-a",
        input_tokens=0,
        output_tokens=1_000_000,   # $25 exactly
        cache_read_tokens=0,
        cache_write_5m=0,
        cache_write_1h=0,
        speed="standard",
        source=source,
    )


def test_the_schema_is_a_backstop_under_the_cross_source_dedupe(tmp_path):
    """Two guards cover a mirrored message, and this pins the lower one.

    scan_sources dedupes so that its own result is genuinely merged. Even if
    it did not, message_id is the store's primary key and the insert is
    INSERT OR IGNORE, so the totals would survive. Worth a test precisely
    because the upper guard looks removable without it — the measurement that
    made it look removable only exercised this layer.
    """
    unmerged = scan.ScanResult(
        records=[
            a_record("m1", "code"),
            a_record("m2", "code"),
            a_record("m1", "cowork"),
            a_record("m2", "cowork"),
        ]
    )
    with store.Store(tmp_path / "history.db") as db:
        inserted = db.ingest(unmerged)
        rows = db.records()

    assert inserted == 2, "four records, two distinct ids"
    assert {r.message_id for r in rows} == {"m1", "m2"}


def test_the_first_listed_source_wins_a_mirrored_message(tmp_path):
    """Attribution has to be deterministic, not whichever scan ran last —
    otherwise BY SOURCE would flicker between refreshes. Goes through the real
    scan_sources over a real mirrored transcript, so it pins the ordering
    rather than a reimplementation of it."""
    mirror = tmp_path / "mirror" / "proj"
    mirror.mkdir(parents=True)
    original = next((CODE_FIXTURES / "-Users-demo-beta").glob("*.jsonl"))
    (mirror / "copy.jsonl").write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    result = ingest.scan_sources(
        [
            ingest.Source(name="code", root=CODE_FIXTURES),
            ingest.Source(name="cowork", root=tmp_path / "mirror"),
        ]
    )
    ids = [r.message_id for r in result.records]
    assert len(ids) == len(set(ids)), "scan_sources must return a merged result"
    assert "cowork" not in {r.source for r in result.records}, "first source listed wins"


def test_a_source_whose_root_is_missing_is_skipped_not_an_error(tmp_path):
    """Cowork does not exist on a machine without Claude Desktop. An absent
    surface is absent, not a failed refresh."""
    sources = [
        ingest.Source(name="code", root=CODE_FIXTURES),
        ingest.Source(name="cowork", root=tmp_path / "does-not-exist"),
    ]
    result = ingest.scan_sources(sources)
    assert result.records
    assert {r.source for r in result.records} == {"code"}


def test_every_source_reaches_the_merged_result():
    result = ingest.scan_sources(
        ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES)
    )
    assert {r.source for r in result.records} == {"code", "cowork"}


def test_titles_merge_first_wins():
    """Unlike records, titles are dicts being combined rather than rows being
    inserted, so the merge order is this module's own decision."""
    first = ingest.Source(name="code", root=CODE_FIXTURES)
    result = ingest.scan_sources([first, first])
    assert result.titles, "the fixture set has at least one titled session"
    single = ingest.scan_sources([first])
    assert result.titles == single.titles


@pytest.mark.parametrize("skip", [None, {}])
def test_an_empty_skip_map_is_a_full_scan(skip):
    result = ingest.scan_sources([ingest.Source(name="code", root=CODE_FIXTURES)], skip)
    assert result.files_read > 0
