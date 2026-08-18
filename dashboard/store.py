"""SQLite persistence. Insert-only: usage facts never change once emitted."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .models import UsageRecord
from .scan import FileState, ScanResult

DATA_DIR = Path.home() / ".claude-token-dashboard"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
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
  is_subagent       INTEGER NOT NULL DEFAULT 0,
  source            TEXT NOT NULL DEFAULT 'code',
  work_seconds      REAL    NOT NULL DEFAULT 0,
  wait_seconds      REAL    NOT NULL DEFAULT 0,
  mode              TEXT    NOT NULL DEFAULT '(not recorded)',
  effort            TEXT    NOT NULL DEFAULT '(none)',
  branch            TEXT    NOT NULL DEFAULT '(none)',
  mcp_server        TEXT    NOT NULL DEFAULT '',
  cache_missed_tokens INTEGER NOT NULL DEFAULT 0,
  cache_miss_reason TEXT    NOT NULL DEFAULT '',
  stop_reason       TEXT    NOT NULL DEFAULT '',
  hour              INTEGER NOT NULL DEFAULT -1,
  denials           INTEGER NOT NULL DEFAULT 0,
  injections        INTEGER NOT NULL DEFAULT 0,
  skill_origin      TEXT    NOT NULL DEFAULT '',
  skill_run         TEXT    NOT NULL DEFAULT '',
  prompt_run        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS usage_day ON usage(day);

CREATE TABLE IF NOT EXISTS session_title (
  session_id TEXT PRIMARY KEY,
  title      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanned_file (
  path   TEXT PRIMARY KEY,
  size   INTEGER NOT NULL,
  mtime  REAL    NOT NULL,
  offset INTEGER NOT NULL DEFAULT 0,
  head   TEXT    NOT NULL DEFAULT ''
);
"""

#: Column order is a contract shared by ingest() and records(). Both derive
#: their SQL and their field mapping from this one tuple, so adding a column
#: cannot leave the writer and reader disagreeing about position.
_FIELDS = (
    "message_id",
    "ts",
    "day",
    "model",
    "project",
    "skill",
    "session_id",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m",
    "cache_write_1h",
    "speed",
    "is_subagent",
    "source",
    "work_seconds",
    "wait_seconds",
    "mode",
    "effort",
    "branch",
    "mcp_server",
    "cache_missed_tokens",
    "cache_miss_reason",
    "stop_reason",
    "hour",
    "denials",
    "injections",
    "skill_origin",
    "skill_run",
    "prompt_run",
)
_COLUMNS = ", ".join(_FIELDS)
_PLACEHOLDERS = ",".join("?" * len(_FIELDS))

#: Columns a re-scan may fill in on a row that already exists, and only
#: while they are still empty. This is not a relaxation of insert-only: a
#: column added after a row was written holds no measurement to overwrite,
#: so writing one records a fact for the first time rather than changing
#: one. The `= ''` guard is what keeps that true — once a value is there,
#: every later pass leaves it alone.
#:
#: Only columns whose empty string genuinely means "never recorded" are
#: listed. Numeric columns are deliberately absent: a measured 0 is a
#: measurement, and a clause that could not tell it apart from an unwritten
#: one would make every zero mutable, which is the thing ADR-0001's
#: insert-only rule exists to prevent. Sentinel columns like effort's
#: '(none)' are absent for the same reason — '(none)' is recorded, not
#: missing.
_BACKFILLABLE = (
    "mcp_server",
    "cache_miss_reason",
    "stop_reason",
    "skill_origin",
    "skill_run",
    "prompt_run",
)
_BACKFILL = ", ".join(
    f"{name} = CASE WHEN usage.{name} = '' THEN excluded.{name} "
    f"ELSE usage.{name} END"
    for name in _BACKFILLABLE
)


def _row(record: UsageRecord) -> tuple:
    """One record as a row in _FIELDS order. SQLite has no boolean type."""
    return tuple(
        int(getattr(record, name)) if name == "is_subagent" else getattr(record, name)
        for name in _FIELDS
    )


def default_db_path() -> Path:
    override = os.environ.get("CLAUDE_DASHBOARD_DB")
    if override:
        return Path(override)
    return DATA_DIR / "history.db"


class Store:
    def __init__(self, path: str | Path) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Bring an older database up to the current schema.

        CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a
        database written before a column existed keeps its old shape and
        every read would fail on the missing name. Backfilling `source` as
        'code' is correct rather than merely convenient: every row that
        predates multi-source scanning came from Claude Code.
        """
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(usage)")}
        added = [name for name in _FIELDS if name not in existing]
        if "source" not in existing:
            self._conn.execute(
                "ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'code'"
            )
        # Machine time per message. Rows written before this column existed
        # get 0, which understates history rather than inventing it: the
        # gaps that produced those rows were never recorded and cannot be
        # recovered without re-reading every transcript from the top.
        if "work_seconds" not in existing:
            self._conn.execute(
                "ALTER TABLE usage ADD COLUMN work_seconds REAL NOT NULL DEFAULT 0"
            )
        if "wait_seconds" not in existing:
            self._conn.execute(
                "ALTER TABLE usage ADD COLUMN wait_seconds REAL NOT NULL DEFAULT 0"
            )
        if "mode" not in existing:
            self._conn.execute(
                "ALTER TABLE usage ADD COLUMN mode TEXT NOT NULL "
                "DEFAULT '(not recorded)'"
            )
        # Everything added since. Each defaults to the value that means
        # "we never recorded this", so an old row reads back as unknown
        # rather than as a measurement of zero.
        for column, declaration in (
            ("effort", "TEXT    NOT NULL DEFAULT '(none)'"),
            ("branch", "TEXT    NOT NULL DEFAULT '(none)'"),
            ("mcp_server", "TEXT    NOT NULL DEFAULT ''"),
            ("cache_missed_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("cache_miss_reason", "TEXT    NOT NULL DEFAULT ''"),
            ("stop_reason", "TEXT    NOT NULL DEFAULT ''"),
            ("hour", "INTEGER NOT NULL DEFAULT -1"),
            ("denials", "INTEGER NOT NULL DEFAULT 0"),
            ("injections", "INTEGER NOT NULL DEFAULT 0"),
            ("skill_origin", "TEXT    NOT NULL DEFAULT ''"),
            ("skill_run", "TEXT    NOT NULL DEFAULT ''"),
            ("prompt_run", "TEXT    NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE usage ADD COLUMN {column} {declaration}"
                )
        # Incremental reads. Existing rows get offset 0 and an empty head,
        # which _resume_offset treats as "re-read this once" — the next pass
        # rebuilds the offsets, and dedup makes the extra read harmless.
        scanned = {row[1] for row in self._conn.execute("PRAGMA table_info(scanned_file)")}
        if "offset" not in scanned:
            self._conn.execute(
                "ALTER TABLE scanned_file ADD COLUMN offset INTEGER NOT NULL DEFAULT 0"
            )
        if "head" not in scanned:
            self._conn.execute(
                "ALTER TABLE scanned_file ADD COLUMN head TEXT NOT NULL DEFAULT ''"
            )

        # A column only just added is empty on every row already stored, and
        # insert-only means a later pass over the same transcripts would skip
        # those rows and never fill it in — the BY EFFORT panel was blank for
        # exactly this reason until the database happened to be rebuilt.
        # Rewinding every file makes the next pass re-read from the top, which
        # is what gives _backfill something to write. Costs one full re-scan,
        # measured at 4.3s over 18_644 records, once per schema change.
        if added:
            self._conn.execute("UPDATE scanned_file SET offset = 0, head = ''")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def ingest(self, result: ScanResult) -> int:
        """Insert new usage rows, upsert titles and file stats.

        Returns the number of usage rows actually inserted. INSERT OR IGNORE
        on the message_id primary key is what makes this idempotent.
        """
        cursor = self._conn.cursor()
        before = cursor.execute("SELECT COUNT(*) FROM usage").fetchone()[0]

        cursor.executemany(
            f"INSERT INTO usage ({_COLUMNS}) VALUES ({_PLACEHOLDERS}) "
            f"ON CONFLICT(message_id) DO UPDATE SET {_BACKFILL}",
            [_row(r) for r in result.records],
        )
        cursor.executemany(
            "INSERT INTO session_title (session_id, title) VALUES (?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET title=excluded.title",
            list(result.titles.items()),
        )
        cursor.executemany(
            "INSERT INTO scanned_file (path, size, mtime, offset, head) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "offset=excluded.offset, head=excluded.head",
            [
                (path, state.size, state.mtime, state.offset, state.head)
                for path, state in result.file_stats.items()
            ],
        )
        self._conn.commit()

        after = cursor.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        return after - before

    def records(self) -> list[UsageRecord]:
        rows = self._conn.execute(f"SELECT {_COLUMNS} FROM usage ORDER BY ts").fetchall()
        records = []
        for row in rows:
            values = dict(zip(_FIELDS, row))
            values["is_subagent"] = bool(values["is_subagent"])
            records.append(UsageRecord(**values))
        return records

    def titles(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT session_id, title FROM session_title").fetchall()
        return {session_id: title for session_id, title in rows}

    def file_stats(self) -> dict[str, FileState]:
        rows = self._conn.execute(
            "SELECT path, size, mtime, offset, head FROM scanned_file"
        ).fetchall()
        return {
            path: FileState(size=size, mtime=mtime, offset=offset, head=head)
            for path, size, mtime, offset, head in rows
        }
