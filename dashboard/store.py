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
  source            TEXT NOT NULL DEFAULT 'code'
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
)
_COLUMNS = ", ".join(_FIELDS)
_PLACEHOLDERS = ",".join("?" * len(_FIELDS))


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
        if "source" not in existing:
            self._conn.execute(
                "ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'code'"
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
            f"INSERT OR IGNORE INTO usage ({_COLUMNS}) VALUES ({_PLACEHOLDERS})",
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
