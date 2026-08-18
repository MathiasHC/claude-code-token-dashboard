# ADR 0003 — A re-scan may fill in a column that did not exist yet

**Status:** accepted, 2026-08-18

## Context

The store is insert-only: `INSERT OR IGNORE` on the message id, so a second
pass over the same transcripts leaves existing rows alone. That is what makes
re-scanning safe, idempotent, and cheap, and nothing here disputes it.

It has one failure mode, and it fires every time the schema grows. `_migrate`
adds a new column with a default, so every row already in the database holds
that default. A later scan re-derives the real value from the transcript,
reaches the `OR IGNORE`, and drops it. The column stays empty for the whole of
history, and the panel built on it renders blank — permanently, because the
next pass does the same thing.

This is not hypothetical. `effort`, `branch`, `mcp_server`, `hour` and the two
skill-run columns all shipped this way, and the BY EFFORT panel showed real
figures only because the database happened to be rebuilt from scratch at some
point afterwards. `prompt_run` made it visible: two of the twelve all-time
leaderboards are built from it, and on an existing database both would have
been empty forever while every test passed.

The documented workaround was `rm ~/.claude-token-dashboard/history.db`. It
works, but it is not free: a transcript that has since been deleted or rotated
takes its rows with it. On a real history 27 of 18_671 rows had no surviving
transcript, so the rebuild that fixes the new column silently loses a little
of the old data.

## Decision

A re-scan may write a column on a row that already exists, but only where:

1. the column is listed in `store._BACKFILLABLE`, and
2. the stored value is the empty string.

Both conditions are in one SQL clause, so neither can be forgotten:

```sql
ON CONFLICT(message_id) DO UPDATE SET
  prompt_run = CASE WHEN usage.prompt_run = '' THEN excluded.prompt_run
               ELSE usage.prompt_run END
```

`_migrate` also rewinds every row of `scanned_file` to offset 0 whenever it
adds a column, because incremental reads would otherwise resume past the
records the backfill needs.

## Why this is not a relaxation of insert-only

The rule insert-only protects is *a usage fact never changes once recorded*. A
column added after a row was written holds no fact to change. Writing it
records one for the first time.

The `= ''` guard is what keeps that distinction real rather than rhetorical:
the moment a value is there, every later pass leaves it alone. Two tests pin
both halves — one that a blank is filled, one that a value already present
survives a scan carrying a different one.

## Why only empty strings

Numeric columns are deliberately excluded. A measured `0` is a measurement,
and no clause can tell it apart from an unwritten one, so admitting numbers
would make every zero in the database mutable. That is precisely the
mutation this design forbids.

Sentinel columns are excluded for the same reason: `effort` defaults to
`'(none)'`, and `'(none)'` is a recorded value meaning *no effort was set*,
not a missing one. Only the empty string is unambiguous.

The cost of the exclusion is real and accepted: `work_seconds` and
`wait_seconds` on pre-existing rows still need a full rebuild, and
[machine-time.md](../machine-time.md) says so.

## Consequences

- A schema change now costs one full re-scan on next refresh — 4.3s over
  18_644 records, once.
- Every future added text column self-heals, because `_BACKFILLABLE` is a
  list somebody extends rather than a code path somebody has to remember.
- `ingest()` still returns the number of rows *inserted*, so a backfill does
  not read as fresh usage to the refresh counter.
