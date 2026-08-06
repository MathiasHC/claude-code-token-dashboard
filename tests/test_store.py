from __future__ import annotations

from pathlib import Path

from dashboard import scan, store
from dashboard.models import UsageRecord
from dashboard.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def a_record(message_id: str = "m1", **overrides) -> UsageRecord:
    base = dict(
        message_id=message_id,
        ts="2026-07-30T08:00:00.000Z",
        day="2026-07-30",
        model="claude-opus-4-8",
        project="alpha",
        skill="(none)",
        session_id="sess-a",
        input_tokens=1,
        output_tokens=2,
        cache_read_tokens=3,
        cache_write_5m=4,
        cache_write_1h=5,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


def test_ingest_returns_the_number_of_rows_inserted():
    with Store(":memory:") as store:
        inserted = store.ingest(scan.ScanResult(records=[a_record("m1"), a_record("m2")]))
        assert inserted == 2


def test_ingest_is_idempotent():
    """The core durability property: re-scanning forever must not double-count."""
    with Store(":memory:") as store:
        payload = scan.ScanResult(records=[a_record("m1"), a_record("m2")])
        assert store.ingest(payload) == 2
        assert store.ingest(payload) == 0
        assert len(store.records()) == 2


def test_records_round_trip_every_field():
    original = a_record("m1")
    with Store(":memory:") as store:
        store.ingest(scan.ScanResult(records=[original]))
        assert store.records() == [original]


def test_titles_round_trip():
    with Store(":memory:") as store:
        store.ingest(scan.ScanResult(titles={"sess-a": "/graphify"}))
        assert store.titles() == {"sess-a": "/graphify"}


def test_a_later_title_replaces_an_earlier_one_for_the_same_session():
    with Store(":memory:") as store:
        store.ingest(scan.ScanResult(titles={"sess-a": "old"}))
        store.ingest(scan.ScanResult(titles={"sess-a": "new"}))
        assert store.titles() == {"sess-a": "new"}


def test_file_stats_round_trip():
    state = scan.FileState(size=120, mtime=1.5, offset=120, head="abc")
    with Store(":memory:") as store:
        store.ingest(scan.ScanResult(file_stats={"/tmp/x.jsonl": state}))
        assert store.file_stats() == {"/tmp/x.jsonl": state}


def test_file_stats_are_updated_when_a_file_grows():
    """The offset must advance with the file, or the next pass re-reads
    from a stale position and misses everything appended since."""
    first = scan.FileState(size=120, mtime=1.5, offset=120, head="abc")
    grown = scan.FileState(size=300, mtime=9.0, offset=300, head="abc")
    with Store(":memory:") as store:
        store.ingest(scan.ScanResult(file_stats={"/tmp/x.jsonl": first}))
        store.ingest(scan.ScanResult(file_stats={"/tmp/x.jsonl": grown}))
        assert store.file_stats() == {"/tmp/x.jsonl": grown}


def test_history_survives_transcripts_disappearing():
    """The whole point of the store. Ingest, then scan an empty tree; the
    earlier records must still be queryable."""
    with Store(":memory:") as store:
        store.ingest(scan.scan(FIXTURES))
        before = len(store.records())
        store.ingest(scan.scan(FIXTURES / "gone"))
        assert len(store.records()) == before
        assert before == 5


def test_store_persists_across_reopen(tmp_path):
    db = tmp_path / "history.db"
    with Store(db) as store:
        store.ingest(scan.ScanResult(records=[a_record("m1")]))
    with Store(db) as store:
        assert [r.message_id for r in store.records()] == ["m1"]


def test_schema_is_created_on_a_fresh_database(tmp_path):
    db = tmp_path / "fresh.db"
    with Store(db) as store:
        assert store.records() == []
        assert store.titles() == {}
        assert store.file_stats() == {}


def test_is_subagent_round_trips_as_a_bool_not_an_int():
    """SQLite stores it as INTEGER; a naive read back returns 0/1."""
    original = a_record("m1", is_subagent=True)
    with Store(":memory:") as db:
        db.ingest(scan.ScanResult(records=[original]))
        got = db.records()[0]
        assert got.is_subagent is True
        assert got == original


def test_default_db_path_honours_the_override_env_var(monkeypatch, tmp_path):
    """Mirrors scan.default_projects_dir()'s CLAUDE_PROJECTS_DIR override —
    without this, anything exercising the real server writes into the
    user's real ~/.claude-token-dashboard/history.db."""
    override = tmp_path / "somewhere-else" / "history.db"
    monkeypatch.setenv("CLAUDE_DASHBOARD_DB", str(override))
    assert store.default_db_path() == override


def test_default_db_path_falls_back_to_data_dir_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_DASHBOARD_DB", raising=False)
    assert store.default_db_path() == store.DATA_DIR / "history.db"


def test_columns_match_usage_record_fields_in_order():
    """store.records() maps rows to UsageRecord by _FIELDS. Adding a field to
    UsageRecord without updating _FIELDS (or vice versa) leaves every other
    test green while the field is silently never persisted — this is the
    guard that catches that drift directly."""
    column_names = [c.strip() for c in store._COLUMNS.split(",")]
    assert list(UsageRecord._fields) == column_names
    assert list(store._FIELDS) == column_names


# --- migration: databases written before the `source` column existed ------

# The pre-source schema, verbatim. CREATE TABLE IF NOT EXISTS leaves an
# existing table alone, so without an explicit migration every read against an
# already-populated database would fail on the missing column.
_LEGACY_SCHEMA = """
CREATE TABLE usage (
  message_id        TEXT PRIMARY KEY,
  ts                TEXT NOT NULL,
  day               TEXT NOT NULL,
  model             TEXT NOT NULL,
  project           TEXT,
  skill             TEXT,
  session_id        TEXT,
  input_tokens      INTEGER NOT NULL DEFAULT 0,
  output_tokens     INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_5m    INTEGER NOT NULL DEFAULT 0,
  cache_write_1h    INTEGER NOT NULL DEFAULT 0,
  speed             TEXT,
  is_subagent       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE session_title (session_id TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE scanned_file (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL);
"""


def _legacy_db(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO usage (message_id, ts, day, model, project, skill, session_id,"
        " input_tokens, output_tokens, cache_read_tokens, cache_write_5m,"
        " cache_write_1h, speed, is_subagent)"
        " VALUES ('old1','2026-07-01T00:00:00.000Z','2026-07-01','claude-opus-4-8',"
        "'alpha','(none)','s1',1,2,3,4,5,NULL,0)"
    )
    conn.commit()
    conn.close()


def test_opening_a_legacy_database_adds_the_source_column(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    with Store(db_path) as db:
        columns = {row[1] for row in db._conn.execute("PRAGMA table_info(usage)")}
    assert "source" in columns


def test_legacy_rows_are_backfilled_as_claude_code(tmp_path):
    """Every row written before multi-source scanning came from Claude Code,
    so 'code' is the correct backfill, not merely a convenient default."""
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    with Store(db_path) as db:
        records = db.records()
    assert [r.source for r in records] == ["code"]


def test_migrating_preserves_the_existing_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    with Store(db_path) as db:
        records = db.records()
    assert len(records) == 1
    assert records[0].message_id == "old1"
    assert records[0].output_tokens == 2


def test_migration_is_idempotent_across_reopens(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    for _ in range(3):
        with Store(db_path) as db:
            records = db.records()
    assert len(records) == 1


def test_source_round_trips_through_the_store(tmp_path):
    with Store(tmp_path / "h.db") as db:
        db.ingest(
            scan.ScanResult(records=[a_record("m1", source="cowork"), a_record("m2")])
        )
        by_id = {r.message_id: r.source for r in db.records()}
    assert by_id == {"m1": "cowork", "m2": "code"}


def test_legacy_scanned_file_table_gains_offset_and_head(tmp_path):
    """The pre-incremental scanned_file table has only (path, size, mtime).
    Without the migration, file_stats() fails on the missing columns and the
    dashboard cannot refresh at all against an existing database."""
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    with Store(db_path) as db:
        db._conn.execute(
            "INSERT INTO scanned_file (path, size, mtime) VALUES ('/tmp/a.jsonl', 10, 1.0)"
        )
        stats = db.file_stats()
    state = stats["/tmp/a.jsonl"]
    assert (state.size, state.mtime) == (10, 1.0)
    # offset 0 / empty head is the "re-read me once" signal, not a resume point.
    assert state.offset == 0
    assert state.head == ""
    assert scan._resume_offset(None, state, 10) == 0
