# ADR 0001 — The source table stays split between `ingest` and `aggregate`

**Status:** accepted, 2026-08-12

## Context

Knowing about a **source** currently means touching four places:

| What | Where |
|---|---|
| default root per source | `ingest.default_sources` |
| glob pattern for Cowork | `cowork.TRANSCRIPT_PATTERN` |
| root override | `cowork.default_cowork_dir` |
| display name on the page | `aggregate.SOURCE_LABELS` |

An architecture review flagged this: adding a third surface is a four-file
change for one concept, and `SOURCE_LABELS` in particular looks stranded — it
is the only part of the table that does not live next to the other parts.

## Decision

Leave the split as it is. Specifically, `SOURCE_LABELS` stays in
`aggregate.py`, next to the panel it labels, rather than moving to `ingest.py`
next to the roots.

## Why

Every way of consolidating it costs more than the duplication does.

- **Move `SOURCE_LABELS` into `ingest`.** `aggregate` would then import
  `ingest`, which imports `scan` and `cowork`. `aggregate` is pure — no I/O, no
  clock — and that purity is what makes the whole page testable from injected
  `now`. Pulling filesystem-touching modules into its import graph to reach a
  two-entry dict trades a real property for a cosmetic one.
- **Move the roots into `aggregate`.** Wrong direction: it would put filesystem
  layout inside the pure module.
- **A new `sources.py` holding the whole table.** It would need `scan` and
  `cowork` for the default roots, so `aggregate` importing it reintroduces
  exactly the coupling above.
- **A new `sources.py` holding only names and labels.** This splits the concept
  in two — which is the problem being solved.

The friction is also small and rare. Adding a surface is a deliberate,
infrequent change, and the four sites are named consistently enough to find.
A `# see also` comment in each direction costs nothing and covers the
discoverability half of the complaint.

## Consequences

- Adding a source stays a four-file change; the comment in
  `aggregate.SOURCE_LABELS` names the other three.
- `aggregate` keeps its no-I/O import graph, and with it the property that the
  entire page can be built from records plus an injected `now`.
- If a source ever needs more than a display name in the pure module — a
  colour, an ordering, a per-source rate — revisit this: at that point the
  table is genuinely two tables and a shared pure one may pay for itself.

## Not re-litigating

A future architecture review will notice this split again. It is deliberate.
The question that would reopen it is not "is this duplicated?" (it is) but
"does `aggregate` still need to be import-pure?" — and while `aggregate.build`
is the seam every renderer test builds through, the answer is yes.
