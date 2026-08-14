# How machine time is measured

The dashboard shows a line like:

```
MACHINE TIME   SUBAGENTS · 32.4%   WAITING ON YOU   SESSIONS
    1d 1h            8h 7m             19h 58m      10 · 2h 30m each
```

Unlike the [footprint estimate](footprint.md), this is **measured, not
modelled**. It still rests on one assumption, and this page is about that.

## What the transcripts give us

One timestamp per record. That is all. There is no duration field anywhere —
`message.diagnostics` carries only cache-miss reasons, and nothing records how
long a model turn or a tool call took.

So the only measurable unit is the **gap between consecutive records**: model
generation plus whatever tool ran in between.

## The rule

A gap counts as machine time **unless the record that ends it is a human
turn**. Then it is the person thinking, and it is excluded.

Separating those is not as simple as the record type. Tool results come back
as `user` records too — the agent feeding itself. A human turn carries plain
text; a tool result carries `tool_result` blocks; `isMeta` records are
injected by the harness and are nobody typing. `scan._is_human_turn` makes
that distinction.

Only messages carrying token usage become records, so a tool run that
produces no message has nowhere to land. Its time accumulates and is
attributed to the next message that does — and if a transcript ends on one,
to the last message that file produced.

## The one assumption: what counts as idle

Gaps above `scan.MAX_WORK_GAP_SECONDS` (300s) are **dropped, not clamped.**

That choice was made by measurement, not taste. On a 71,000-record history:

- 88% of gaps are under ten seconds — that is what real model turns look like.
- 94 gaps exceed two hours and sum to **2,993 hours**: sessions left open and
  resumed days later. The largest single gap was 344 hours.
- Clamping those to the threshold rather than dropping them **adds about 79
  hours of work that never happened.** The first version of this feature did
  exactly that and the total was inflated by 14%.

The threshold is the whole ballgame. On the same history:

| threshold | measured machine time |
|---|---|
| 60s | 74 h |
| **300s** | **110 h** |
| 900s | 135 h |
| 1800s | 169 h |

300s is defensible as the middle, but the honest statement is "3 to 7 days",
and the ambiguity is real: some 15-minute gaps are genuine long tool runs — a
multi-agent research workflow in this project took 26 minutes — while others
are somebody making coffee.

## The other side of the clock

A gap ending at a human turn is the person reading and typing. It is counted
separately as `wait_seconds` rather than discarded, and shown as **WAITING ON
YOU**.

Its threshold is deliberately more generous than the machine one —
`MAX_WAIT_GAP_SECONDS` is 900s against 300s. Reading a long answer and
composing a reply legitimately takes minutes, where a model turn that takes
five is already an outlier.

Above that threshold nobody is at the keyboard, and those seconds belong to
neither side. The page does not show an "away" figure for exactly that
reason: it would be an artefact of where the two thresholds happen to sit,
not a measurement.

## Parallelism

Agents run concurrently, so summing them exceeds wall-clock time. On the
reference history the sum was 110 h against 76 h of real elapsed time, a
**1.44× parallelism factor**.

The page shows the sum and says "parallel agents summed, so this exceeds
wall-clock" underneath, because the sum is the larger and more flattering
number and quoting it bare would be misleading.

## Accuracy

The implementation was validated against an independent script written before
it, walking the same transcripts. They agree to within 3%, and the residual is
fully explained: the scanner streams records in file order while the script
sorted them by timestamp, and 1,138 records are out of order. Buffering whole
files to sort them is not worth 2% on a figure whose threshold assumption
moves it by 2×.

Two bugs were found by that comparison and are now pinned by tests:

- discarding pending machine time at a human turn (**10.9 hours lost**)
- stranding tool runs that followed the last usage-bearing message in a
  transcript (**4.8 hours lost across 441 files**)

## What this is not

**It is not time saved.** That question needs a counterfactual — what the work
would have taken without the tool — and nothing in a transcript supplies one.

It is also not a settled question elsewhere. The strongest evidence available
is a randomised controlled trial ([METR
2025](https://arxiv.org/abs/2507.09089)) in which experienced open-source
maintainers were measured **19% slower** on real issues in their own
repositories while estimating they had been 20% faster — a 39-point error with
the sign inverted. Its 2026 agentic follow-up returned intervals straddling
zero and the authors withdrew the design.

So the page says **worked**, never **saved**, and a test enforces the wording.

Rows written before this column existed read back as zero. That understates
history rather than inventing it: those gaps were never recorded and cannot be
recovered without re-reading every transcript from the top.
