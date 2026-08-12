# ADR 0002 — `ingest.scan_sources` keeps its cross-source dedupe

**Status:** accepted, 2026-08-12

## Context

A message id can be deduplicated in three places:

1. `scan.scan` — within a single pass, via `seen_ids`
2. `ingest.scan_sources` — across sources, via a `seen` set
3. `store.ingest` — `INSERT OR IGNORE` on the `message_id` primary key

An architecture review called (2) redundant, on the grounds that (3) already
guarantees the totals, and proposed deleting it — leaving `scan_sources` thin
enough to fold into the composition root.

The review was right that (2) is not what protects the totals. It was tested:
feeding four records with two distinct ids straight to the store yields two
rows, attributed to the first source listed. Removing (2) does not change a
single figure on the page, because in production `ScanResult.records` goes
nowhere except `store.ingest`.

## Decision

Keep it.

## Why

Removing it was tried and reverted.

**The cost is not the argument it looked like.** The guard measures 4.7ms on a
30,000-record history. Against a warm refresh of 156ms that is 3% — real, but
not worth buying with a weaker contract, on a page that refreshes twice a
minute and spends most of its time in the filesystem walk.

**The contract is the argument.** `scan_sources` is named for merging scans.
Returning a "merged" result that can contain the same message twice is a
surprise for any future caller that reads `result.records` without going
through the store — and `tests/test_cowork.py` already asserts cost directly
off `result.records`, which is how the removal was caught.

**Two guards, one backstop.** (3) genuinely covers (2), which is worth knowing
and is now pinned by `tests/test_ingest.py`. That makes (2) defence in depth
rather than load-bearing, which is a fine thing for it to be at 4.7ms.

## Consequences

- `scan_sources` guarantees unique records. Callers may rely on it.
- The store's primary key remains the backstop, tested independently, so a
  future refactor that does drop (2) will not silently double-count.
- `ingest` stays a module rather than folding into `freshness`. After the
  freshness seam landed, folding it in would have put transcript roots, glob
  patterns and resolver factories inside the module that answers "how current
  are these numbers" — two concerns, not one.

## Not re-litigating

"The dedupe is redundant, delete it" is true about the *totals* and false about
the *contract*. Reopen this only with a caller that needs the 4.7ms, not with
a fresh observation that the primary key exists — that is recorded here.
