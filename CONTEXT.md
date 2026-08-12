# Domain language

The words this codebase uses for its own subject matter. Architecture
vocabulary (module, interface, seam, depth) is a separate register and is not
defined here.

## Usage record

One assistant message's token counts, normalised out of a transcript.
`models.UsageRecord`. The unit everything else is derived from: it is what
`scan` produces, what `store` persists one row per, and what `aggregate`
walks. Identified by `message_id`, which is also the store's primary key and
therefore the thing that makes re-scanning idempotent.

## Source

A Claude surface whose usage lands on this machine as transcripts — today
Claude Code and Claude Desktop's Cowork mode. Surfaces that bill server-side
(Desktop chat, Claude in Chrome, claude.ai) write nothing to disk, so their
absence from the page is a property of the data rather than an omission.
A record carries its source; `ingest.default_sources` says where each lives.

## Window

A span of days a figure covers, expressed as a `day -> bool` predicate.
`ranges` owns all of them. Two kinds:

- the **catalogue** — the five the reader can select (`today`, `7d`, `30d`,
  `month`, `all`), each a `ranges.Range` with a key and two labels;
- the **comparisons** — yesterday, the prior 7 days, the previous month to
  date. Not selectable, but the same question, so the same module answers it.

A day that will not parse is outside every window. That decision is made once,
in `dates.parse_day`.

## Scope

Which windows a figure obeys. The page has exactly two, and mixing them is the
mistake the type system now prevents:

- **Global** — the hero row (today / 7 days / month to date / all time), their
  deltas, the month-end projection, the burn rate. Fixed summary; does not move
  when a range is selected.
- **Scoped** — everything below the selector: where the money goes, the
  breakdowns, the bands, the daily chart. Re-computed for the selected range.

## Range view

`models.RangeView`: all the scoped figures together with the label that says
which window they belong to. Reached as `DashboardData.scoped`. Its reason to
exist is that the label travels with the numbers, so a panel cannot render a
scoped figure without being able to say what it is scoped to.

## Api-equivalent cost

What the recorded usage would have cost at Claude API list rates. Not a bill.
The page's headline comparison is this figure against what the reader actually
pays for their **plan** (`models.Plan` — a key, a label and a monthly amount,
which travel together so a page can never name one plan while dividing by
another's price).

## Cache saving

A counterfactual: what the cache-read tokens would have cost at full input
rates, minus what they did cost. Cache reads bill at
`pricing.CACHE_READ_MULTIPLIER` of the input rate, so the saving is the rest.
Money that was never in play, which is why the page is required to print the
qualifier "same tokens at uncached rates" beside it — there is no tooltip on
the browser this targets.

## Active time

Today's sum of gaps between consecutive messages, each capped at
`aggregate.IDLE_CAP_SECONDS`. The denominator of the burn rate. Deliberately
not wall-clock since the first message: a session left open overnight would
dilute the rate to nothing and the figure would stop describing the work.

## Active day

A day that carried any spend. `active_days` counts them across all history;
`RangeView.active_day_count` counts them inside the selected range, and is the
number of bars in the daily chart. Distinct from the *width* of a window — a
30-day range with three active days plots three bars, and saying "last 3 days"
about it is wrong.

## Freshness

How current the numbers on screen are. `freshness.Freshness` owns the whole
answer: when to re-read transcripts, what to keep between requests, and what
to serve when a refresh fails — fresh data, the same range's previous data
under a staleness banner, or an empty page that says so. Never raises: a wall
display showing yesterday's figures under a banner beats one showing a 500.
