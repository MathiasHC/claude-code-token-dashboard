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

The all-time leaderboards are global, which is the one place the "below the
selector means scoped" shorthand breaks. They are not in the page at all
until asked for — see **Sheet** — and the sheet heading says ALL TIME for
exactly that reason.

## Range view

`models.RangeView`: all the scoped figures together with the label that says
which window they belong to. Reached as `DashboardData.scoped`. Its reason to
exist is that the label travels with the numbers, so a panel cannot render a
scoped figure without being able to say what it is scoped to.

## Prompt run

One human turn and everything the machine did answering it — the reply, every
tool call, every subagent, up to the next thing the person typed. Transcripts
put `promptId` on user records only, never on assistant ones, so `scan` carries
the id forward as state the way it does the permission mode. Tool results
repeat their own prompt's id, which is what makes a whole turn group rather
than just its first reply.

A prompt's **length** is the machine time its messages add up to, not the
wall-clock span from first to last. Elapsed time would crown whichever prompt
happened to be open when somebody walked away from the keyboard.

## Leaderboard

An all-time top three: `models.Leaderboard`, built by `dashboard/leaderboards.py`
and reached as `DashboardData.leaderboards`. Twelve of them, global by
construction — see **Scope**.

Each carries an `icon` naming its drawing and a `unit` naming how the
renderer should format its numbers
(`money`, `count`, `duration`, `tokens`, `clock`, `days`) rather than
pre-formatted strings, so a board's placings can be asserted against without
pinning display format into a test. A board's `note` is a fact that belongs
with it but is not a placing — the earliest start under the latest nights.

## Sheet

The overlay the leaderboards live in, opened by the **ribbon** — the trophy tab
pinned 100px down the right edge — and closed by the CLOSE control or by
tapping the scrim behind it.

Open or closed is view state, and it travels in the query string
(`?boards=open`) exactly as the selected range does. Not a CSS or JavaScript
toggle: the page carries no JavaScript by design, and state in the URL is the
only kind that survives the page reloading itself every thirty seconds. It
also means the two pieces of view state compose — opening the sheet keeps the
range, and closing it returns to it.

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

## Skill run

A contiguous stretch of messages attributed to the same skill. Identified by
`skill_run` — the message id of the run's first message — so every message
in the run shares one key and the page can list runs rather than messages.

## Skill origin

Who started a run. Three traces that partition cleanly on real transcripts,
with no unknown bucket:

- **model** — the assistant called the `Skill` tool. A tool call stays
  pending for `scan.SKILL_TRIGGER_WINDOW` messages; without a bound an
  unconsumed call matched a run 56 messages later.
- **you** — a `<command-name>` slash command in a typed message.
- **subagent** — neither, which happens only inside subagent transcripts.
  That is structure, not missing data: the agent inherited the skill from
  whoever spawned it and the trigger is in the parent file.

One assistant message can be written to a transcript more than once, and the
`Skill` tool block may arrive in a later copy than the attribution did — so
the call is read *before* the message-id dedupe, and a run already begun is
upgraded retroactively. Missing that credited 42 of 45 model-invoked runs to
nobody.

## Cache miss

Prefix tokens the cache failed to hold, re-processed and billed at write
rates instead of the 0.1x read rate. `RangeView.cache_miss_cost` is the
**gap** between those two rates, not the whole charge — the tokens had to
be paid for either way, so only the difference is waste.

The counterpart to [cache saving](#cache-saving): one is a counterfactual,
this is money actually spent. `message.diagnostics.cache_miss_reason` says
why, and `aggregate.MISS_REASONS` puts it in words.

## Permission mode

Which mode a message ran under — `auto`, `default` (approve each action),
`plan`, `acceptEdits`, `bypassPermissions`. Shown as the BY MODE panel.

Assistant messages never carry it. It arrives as its own record
(`{"type": "permission-mode", "permissionMode": ...}`) and on some user
turns, neither of which has a timestamp, so `scan` tracks it as state in
file order and stamps the messages that follow. Measured across 663 real
transcripts, a session that records a mode does so before its first
assistant message, so nothing inside a covered session is misattributed.

Sessions that never record one stay `scan.UNKNOWN_MODE` — about 37% of
messages but only ~10% of spend, since they are mostly older and cheaper.
That bucket is displayed rather than filtered: dropping it would
renormalise the rest into looking like the whole picture.

## Trivia

The figures about the *shape* of the work rather than its size: tool calls
per reply, the modal hour, the weekend share, the priciest single message,
refused tool calls, and context injections. Measured, none of it
actionable, so it lives in one line at the bottom rather than taking a
panel each.

"Context injections" are what the harness pushes into the conversation —
task reminders, skill listings, tool deltas. Deliberately not called
"attachments": only ~6% are a file anybody pasted, and the tool and
instruction ones are what invalidate the prompt cache.

## Machine time

How long the model and its tools were actually working —
`RangeView.worked_seconds`, measured rather than modelled. A transcript
records one timestamp per message and no durations, so the unit is the gap
between consecutive records: model generation plus whatever tool ran in
between. A gap ending at a **human turn** is the person thinking and is
excluded; gaps above `scan.MAX_WORK_GAP_SECONDS` are dropped as idle rather
than clamped, because clamping invents time.

Summed across agents, so parallel subagents add past wall-clock — the page
says so rather than quoting the larger number bare.

`waited_seconds` is the mirror image: gaps that *do* end at a human turn,
capped more generously at `scan.MAX_WAIT_GAP_SECONDS`. Between them they
cover every second somebody was present. Gaps above either threshold belong
to neither and are not shown, since an "away" figure would be an artefact of
where the thresholds sit.

Deliberately **not** "time saved". That needs a counterfactual nothing in the
transcripts can supply, and the research is clear it is not derivable at all;
see [docs/machine-time.md](docs/machine-time.md).

## Footprint

Modelled energy, water and carbon for the tokens in a range —
`footprint.Footprint`, reached as `RangeView.footprint`. The one figure on the
page that is not derived from money, and the only one that is non-zero for an
unpriced model: a model nobody has a rate for still drew power.

Energy is the single estimated quantity; water and carbon are fixed ratios of
it, so the three displayed figures cannot contradict each other. Good to about
an order of magnitude, which is why the page shows one significant figure and
carries its caveat inline. See [docs/footprint.md](docs/footprint.md).

## Freshness

How current the numbers on screen are. `freshness.Freshness` owns the whole
answer: when to re-read transcripts, what to keep between requests, and what
to serve when a refresh fails — fresh data, the same range's previous data
under a staleness banner, or an empty page that says so. Never raises: a wall
display showing yesterday's figures under a banner beats one showing a 500.
