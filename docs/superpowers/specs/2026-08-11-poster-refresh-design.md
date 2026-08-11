# Make the poster worth refreshing

**Status:** approved 2026-08-11
**Scope:** one focused change set, ~7 items, no new subsystems.

## The problem

The dashboard is close to feature-complete for what it claims to be. The
remaining weakness is not missing surface — it is that several of the numbers
already on the page are dead: they are saturated, near-static, or answer a
question nobody asks. On a screen that refreshes every 30 seconds and is read
from across a room, a number that never changes is furniture.

Measured on a real 27,400-message history:

| Element | Measurement | Verdict |
|---|---|---|
| `cache hit rate` | 99.68%–100.00% across 8 consecutive weeks | saturated; carries no information |
| `BY SKILL` | `(none)` is **90.2%**; the four real skills are 1.3–1.9% | unreadable |
| `ALL TIME` hero tile | moves <1% per day | near-static |
| Titlebar right half | a bare timestamp | unused space |
| `DAILY` chart | 30 identical bars | today is not findable at a glance |

## Decisions taken

Three questions were settled before design, and they constrain everything
below:

1. **It is a poster, not an instrument.** Nobody taps the screen. Optimise for
   what is readable at a glance and what changes behaviour. This rules out the
   whole "clickable filters / trend sub-page / drill-down" direction.
2. **The fold is soft.** The page is ~875px against a ~748px fold; a small
   scroll is acceptable. This removes the need to collapse `TOP SESSIONS` to
   buy back pixels, which was the only reason that idea existed.
3. **Projections belong in the plan band**, not in the hero row. The hero row
   keeps its four tiles.

## What changes

### 1. Fix the wrong-page cache fallback  *(defect)*

`dashboard/web.py` currently reads:

```python
stale = self._pages.get(selected.key) or next(iter(self._pages.values()), None)
```

On the refresh-failure path, when the requested range is not cached but another
is, this serves **a different range's page** — a request for `?range=today`
rendering the `ALL TIME` panels, under a generic "refresh failed" banner. The
warning tells the reader the data is stale; nothing tells them it is also the
wrong window.

Fix: fall back only to the *same* range's cached page. If that range has never
rendered, render it empty with the warning rather than substituting another.

This is a defect, not a feature, and ships regardless of the rest.

### 2. Cache economics replaces the saturated hit rate

`WHERE THE MONEY GOES` currently ends with `cache hit rate 99.9%`. Replace with
what caching actually bought:

```
caching saved $15,603 · same tokens at uncached rates
```

Cache reads bill at 0.1× the input rate. The saving is the counterfactual
difference: `cache_read_tokens × input_rate × 0.9`. On the reference history
that is **$15,603 saved against $1,734 actually paid** — roughly 9×.

Two constraints:

- The counterfactual framing (`same tokens at uncached rates`) must be in the
  visible markup. There are no tooltips on iOS 5.1.1, and "caching saved
  $15,603" alone reads as money that was in play, which it never was.
- Same 11px note line, so this is a zero-pixel swap.

Cache hit rate is not merely demoted — it is removed. Two saturated numbers are
worse than one.

### 3. `on pace` joins the plan band

The plan band gains a projection:

```
vs Max 20× $745.66 api-equivalent / $200.00 actual = 3.7× effective
                         · on pace for $2,082 by 31 Aug
```

Method: **month-to-date + (trailing 7-day mean daily cost × days remaining)**,
and the label says which method it is. The research flagged a 17% spread
between this and a month-mean method as a reason to avoid projecting at all;
measured on the real history the spread is **1%** ($2,081.77 vs $2,101.40), so
the ambiguity that made this risky is not present. The label still states the
method, because that spread can widen in a month containing a real behaviour
change.

Suppressed entirely for the first 3 days of a month, where a trailing-7-day
mean is mostly last month and the projection is noise.

### 4. Live burn rate in the titlebar

The titlebar's right half holds only a timestamp. It gains the one figure on
the page that can differ between two consecutive 30-second refreshes:

```
CLAUDE TOKENS      $53.80/hr · idle 4m      11 Aug 2026 19:04
```

- **Active time** is the sum of gaps between consecutive message timestamps
  today, each capped at 5 minutes. This measures time Claude was working, not
  wall-clock since the first message — a session left open overnight must not
  dilute the rate to nothing.
- **Idle** is minutes since the most recent message.
- With no messages today, both are suppressed rather than rendering `$0.00/hr`.

Zero pixels: it occupies space the timestamp was not using.

### 5. Highlight today in the daily chart

One extra CSS class on one `<td>` in `_daily()`, rendered in the hero blue
(`#58a6ff`) against the existing green. Thirty identical bars currently force a
squint to locate the only one still moving. Zero pixels, entirely inside the
existing 44px box.

### 6. `BY SKILL` drops the unattributed bucket

`(none)` is 90.2% of skill-attributed spend, which compresses the four real
skills into slivers of 1.3–1.9% and makes the panel unreadable. Filter the
unattributed bucket out of `by_skill` in `aggregate.build` and let the
remaining shares renormalise, so the panel answers "which skills cost me
something" rather than "most work has no skill attached", which the reader
already knows.

The panel heading gains `· ATTRIBUTED` so the exclusion is visible rather than
silently changing what the percentages mean.

### 7. Default range becomes 30 DAYS

`ranges.DEFAULT` is currently `all`. That is why every panel below the range
selector is the most static content on the page: on an all-time window, one
day's work moves nothing. A default of 30 days makes four existing panels move
daily, which is the entire point of a poster. `ALL TIME` remains one click
away, and the hero row is unaffected — it is global by construction and stays
that way.

## What is explicitly not in scope

- Collapsing `TOP SESSIONS` — existed only to buy pixels the soft fold makes
  unnecessary.
- Clickable breakdown rows, a trend sub-page, session drill-down (the
  "instrument" direction).
- Cost-per-prompt (needs a schema change plus a full re-ingest).
- Anything requiring JavaScript, a dependency, or an outbound network call.

## Architecture

No new modules. The change touches the existing pipeline in its existing shape:

```
scan.py     unchanged
store.py    unchanged (no schema change)
models.py   DashboardData gains: cache_saved, on_pace, burn_rate_hourly,
            idle_minutes, active_hours
aggregate.py computes them in the single existing pass; `now` stays injected
render_html.py displays them; the iOS-5 lint applies as before
ranges.py   DEFAULT changes
web.py      cache-fallback fix
```

`aggregate.build` and `render_html.render` stay pure. Nothing reads a clock
except through the injected `now`, which is what keeps the new time-derived
figures (burn rate, idle, on pace) testable.

## Testing

Each item ships with tests. Specific cases that must be covered, because they
are where these features go wrong:

- **Cache fallback:** a failed refresh with only *another* range cached must
  not serve that range's page.
- **On pace:** suppressed in the first 3 days of a month; correct at a month
  boundary; unaffected by a day with no usage.
- **Burn rate:** a gap longer than the idle cap contributes only the cap; no
  messages today suppresses the whole segment; a single message today does not
  divide by zero.
- **Cache saved:** an unpriced model contributes nothing; the figure is the
  counterfactual, not the amount paid.
- **BY SKILL:** shares renormalise to 1.0 after the exclusion; a history where
  *every* record is unattributed yields an empty panel rather than a crash.
- **Default range:** the hero row stays global when the default changes.
- **The golden file** is regenerated once, deliberately, and its diff read.

The iOS 5.1.1 compatibility lint covers every new element: no JavaScript, no
flexbox, no grid, no custom properties, no `rem`.

## Risks

- **`on pace` is the only number on the page that can be wrong rather than
  merely stale.** Mitigated by labelling the method and suppressing it early in
  a month, not by making it more clever.
- **`caching saved $X` is a counterfactual** and will be misread as savings
  against a bill that was never going to arrive. Mitigated by the inline
  qualifier; there is nowhere else to put it.
- **The burn rate broadcasts working hours** on a shared screen. Acceptable
  here (home office, single viewer) and noted in the README rather than made
  configurable for a case this deployment does not have.
- **Dropping `(none)` changes what BY SKILL's percentages mean.** Mitigated by
  the `· ATTRIBUTED` heading.
