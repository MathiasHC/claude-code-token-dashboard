"""Render the dashboard from synthetic demo data.

Used to produce the README screenshots, and to preview the layout without
having any real usage on the machine:

    python3 tools/demo_page.py /tmp/demo.html

There are two screenshots, and the second one needs a deliberate height.
`docs/screenshot.jpg` is the whole page, so it is captured at whatever
`document.body.scrollHeight` reports. `docs/screenshot-leaderboards.jpg` is
the sheet, which is `position:fixed` and therefore as tall as the viewport it
is shot in — capture it at the sheet's own bottom edge plus a small margin:

    document.querySelector(".sheet").getBoundingClientRect().bottom

At the time of writing that lands at 648px, and the image is shot at 668.
Shooting it at the page height instead leaves 600px of dimmed dashboard under
the sheet, which reads as an unpolished black band rather than as an overlay.

Every figure is invented, but nothing here is hand-written: this builds a
few thousand fake `UsageRecord`s and runs them through the real
`aggregate.build`, so the totals, shares, averages and cache economics are
all derived exactly the way the live dashboard derives them.

That matters for more than tidiness. An earlier version wrote the panel
figures directly, back-computed from round percentages, and the result was
obviously fake — every share landed on an exact tenth (63.0%, 20.0%, 13.0%,
4.0%). Real usage never does that. Deriving from records produces the
uneven numbers a real week actually looks like.

Seeded, so the same page comes out every time.
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import aggregate, render_html  # noqa: E402
from dashboard.models import UsageRecord  # noqa: E402

SEED = 20260807
NOW = dt.datetime(2026, 8, 21, 9, 41, 0)
ACTIVE_DAYS = 96

# Weighted so one project dominates the way real work does, rather than
# everything being suspiciously even.
PROJECTS = [
    ("api-gateway", 34),
    ("mobile-app", 23),
    ("data-pipeline", 17),
    ("infra-terraform", 14),
    ("docs-site", 8),
    ("scratch", 4),
]
MODELS = [
    ("claude-opus-5", 42),
    ("claude-sonnet-5", 28),
    ("claude-opus-4-8", 16),
    ("claude-fable-5", 9),
    ("claude-haiku-4-5", 5),
]
SKILLS = [
    ("(none)", 46),
    ("brainstorming", 19),
    ("test-driven-development", 14),
    ("code-review", 12),
    ("systematic-debugging", 9),
]
EFFORTS = (("xhigh", 68), ("(none)", 20), ("high", 8), ("max", 4))
BRANCHES = (("main", 30), ("feature/checkout", 18), ("fix/auth-token", 14),
            ("worktree-billing", 12), ("release/2.4", 9), ("spike/vectors", 8),
            ("docs/onboarding", 5), ("hotfix/rate-limit", 4))
MCP_SERVERS = (("", 82), ("browser", 7), ("database", 6), ("issue-tracker", 3),
               ("design", 2))
MISS_REASONS = (("", 88), ("system_changed", 7), ("tools_changed", 3),
                ("messages_changed", 2))

ORIGINS = (("model", 71), ("subagent", 19), ("you", 10))

MODES = (
    ("auto", 78),
    ("(not recorded)", 11),
    ("default", 8),
    ("plan", 2),
    ("acceptEdits", 1),
)

SOURCES = [("code", 86), ("cowork", 14)]

SESSION_TITLES = [
    "Migrate the billing service off the legacy queue",
    "/tdd add idempotency keys to the payments endpoint",
    "Investigate the p99 latency regression in the gateway",
    "Rewrite the onboarding flow for the mobile app",
    "Backfill the events table without locking writes",
    "/code-review the auth refactor before it ships",
    "Trace why the nightly export silently drops rows",
    "Split the monolith's config into per-service files",
    "Add retry semantics to the webhook dispatcher",
    "Work out why staging costs 4x what production does",
]


def _weighted(rng: random.Random, table: list[tuple[str, int]]) -> str:
    return rng.choices([name for name, _ in table], weights=[w for _, w in table])[0]


def demo_records(rng: random.Random) -> tuple[list[UsageRecord], dict[str, str]]:
    records: list[UsageRecord] = []

    # A fixed pool of named sessions, weighted so a few long-running ones
    # dominate. Spawning a fresh anonymous session per burst instead left
    # the "top sessions" panel showing five untitled rows worth pennies —
    # technically correct and completely uninformative.
    titles = {f"sess-{i}": title for i, title in enumerate(SESSION_TITLES)}
    session_ids = list(titles)
    # Which prompt each session is currently on. A prompt is one thing the
    # person typed plus everything the machine did answering it, so messages
    # arrive in runs rather than one per turn — ~12 apiece here, against 23
    # on the real history the boards were checked against.
    prompt_seq = dict.fromkeys(session_ids, 0)
    session_weights = [round(1 / (i + 1) ** 0.7, 4) for i in range(len(session_ids))]

    for day_offset in range(ACTIVE_DAYS - 1, -1, -1):
        day = (NOW.date() - dt.timedelta(days=day_offset)).isoformat()
        # Days genuinely off, not merely quiet. Without them every day in
        # the window is active, so the streak board is one unbroken run and
        # the break board has nothing at all to show.
        if day_offset and rng.random() < 0.07:
            continue
        weekend = dt.date.fromisoformat(day).weekday() >= 5
        # Weekends are quieter, and effort drifts rather than being uniform.
        base = rng.randint(4, 40) if weekend else rng.randint(90, 340)
        # Occasional heavy day — a migration, an incident.
        if not weekend and rng.random() < 0.12:
            base = int(base * rng.uniform(1.6, 2.4))

        # A handful of short-lived sessions per day alongside the named
        # long-runners. Reusing only the fixed pool put all ten sessions on
        # every weekday, so the sessions-per-weekday board read a flat 10
        # across the board and said nothing.
        walk_ins = [f"sess-{day}-{n}" for n in range(rng.randint(1, 5))]

        for _ in range(base):
            session_id = (
                rng.choice(walk_ins)
                if rng.random() < 0.18
                else rng.choices(session_ids, weights=session_weights)[0]
            )
            prompt_seq.setdefault(session_id, 0)

            model = _weighted(rng, MODELS)
            # Context replay dominates cost on agentic workloads, so these
            # are calibrated to land near the split real usage shows: cache
            # reads around three fifths, output a fifth, writes a little
            # over a tenth, fresh input a sliver.
            cache_read = int(rng.lognormvariate(12.35, 0.6))
            output = int(rng.lognormvariate(7.4, 0.8))
            fresh = int(rng.lognormvariate(6.8, 1.0))
            write_1h = int(rng.lognormvariate(8.5, 1.0)) if rng.random() < 0.22 else 0
            write_5m = int(rng.lognormvariate(8.0, 0.9)) if rng.random() < 0.16 else 0

            if rng.random() < 0.085:
                prompt_seq[session_id] += 1

            # Spread through the working day. A fixed timestamp gave every
            # record on a day the same instant, so active time was zero and
            # the live burn rate could never appear in the screenshot.
            #
            # One evening in ten runs late. Without a tail the LATEST NIGHT
            # board tops out at whenever the working day is set to end, which
            # is a board about nothing.
            offset = dt.timedelta(
                minutes=rng.randint(0, 9 * 60) if rng.random() < 0.90
                else rng.randint(9 * 60, 15 * 60)
            )
            stamp = dt.datetime.fromisoformat(f"{day}T08:00:00") + offset
            records.append(
                UsageRecord(
                    message_id=f"m{len(records)}",
                    ts=stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    day=day,
                    model=model,
                    project=_weighted(rng, PROJECTS),
                    skill=(skill := _weighted(rng, SKILLS)),
                    session_id=session_id,
                    input_tokens=fresh,
                    output_tokens=output,
                    cache_read_tokens=cache_read,
                    cache_write_5m=write_5m,
                    cache_write_1h=write_1h,
                    speed="standard",
                    is_subagent=rng.random() < 0.33,
                    source=_weighted(rng, SOURCES),
                    # Machine time per message. Shaped like the real thing:
                    # on a 71,000-record history 88% of gaps were under ten
                    # seconds, with a thin tail of long tool runs. A uniform
                    # draw would make the demo's total wrong by ~3x.
                    work_seconds=(
                        rng.uniform(1.5, 9.0) if rng.random() < 0.88
                        else rng.uniform(9.0, 240.0)
                    ),
                    # Most messages are mid-turn and nobody is waiting; the
                    # ones that follow a human turn carry the whole wait.
                    wait_seconds=(
                        rng.uniform(8.0, 240.0) if rng.random() < 0.12 else 0.0
                    ),
                    mode=_weighted(rng, MODES),
                    effort=_weighted(rng, EFFORTS),
                    branch=_weighted(rng, BRANCHES),
                    mcp_server=_weighted(rng, MCP_SERVERS),
                    cache_miss_reason=(miss := _weighted(rng, MISS_REASONS)),
                    cache_missed_tokens=(rng.randint(40_000, 400_000) if miss else 0),
                    stop_reason=("tool_use" if rng.random() < 0.93 else "end_turn"),
                    hour=stamp.hour,
                    denials=(1 if rng.random() < 0.006 else 0),
                    injections=(1 if rng.random() < 0.22 else 0),
                    # One run per session per skill, which is close enough
                    # to how they actually cluster.
                    skill_origin=(
                        _weighted(rng, ORIGINS) if skill != "(none)" else ""
                    ),
                    skill_run=(f"{session_id}:{skill}" if skill != "(none)" else ""),
                    prompt_run=f"{session_id}:p{prompt_seq[session_id]}",
                )
            )

    return records, titles


def demo_data():
    rng = random.Random(SEED)
    records, titles = demo_records(rng)
    return aggregate.build(records, titles, now=NOW)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo.html")
    data = demo_data()
    out.write_text(render_html.render(data), encoding="utf-8")
    print(
        f"{out}  ({data.all_time.messages:,} messages, "
        f"${data.all_time.cost:,.2f} all time)"
    )


if __name__ == "__main__":
    main()
