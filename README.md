# Claude Code Token Dashboard

[![tests](https://github.com/MathiasHC/claude-code-token-dashboard/actions/workflows/tests.yml/badge.svg)](https://github.com/MathiasHC/claude-code-token-dashboard/actions/workflows/tests.yml)

A self-hosted dashboard for **Claude Code token usage and cost**. It reads the
transcripts Claude Code already writes on your own machine, shows what you
spent tokens on, and works out what that usage *would* have cost at Claude API
list rates if you were paying per token instead of on a subscription.

It is built for an always-on display — a spare tablet, a small screen on a
Raspberry Pi, a corner of a second monitor. The page carries no JavaScript and
refreshes itself with a `<meta>` tag, so it runs on browsers far too old for
anything modern.

Everything stays on your machine: no account, no telemetry, no network calls.

**No dependencies outside the Python standard library.**

![The dashboard showing token spend broken down by model, project, skill and source](docs/screenshot.jpg)

*Every figure above is synthetic demo data, not real usage. Reproduce it with
`python3 tools/demo_page.py demo.html` and open the file — a way to see the
layout before you have any history of your own.*

## Requirements

Python 3.11 or newer, and Claude Code installed and used at least once — the
dashboard reads the transcripts it leaves in `~/.claude/projects`. There is
nothing to install and nothing to configure; a virtualenv is needed only to
run the tests.

## Install and run

```bash
git clone https://github.com/MathiasHC/claude-code-token-dashboard.git
cd claude-code-token-dashboard
python3 -m dashboard
```

The first run asks one question — **which Claude plan you're on** — because the
headline comparison is "what this usage would have cost at API rates versus
what you actually pay", and only you know the second half. The answer is saved
and never asked again. See [Which plan you're on](#which-plan-youre-on).

It prints a URL like `http://192.168.1.42:8420/d/AbC123…`. Open it locally to
check, then on whatever device you want it to live on.

Options: `--host` (default all interfaces), `--port` (default 8420).

## How it fits together

There are two roles, and they are usually different machines:

- **The host** reads the transcripts and serves the page. It must be the
  machine Claude Code actually runs on, because it reads that machine's
  `~/.claude` directory. It must also be awake — if it sleeps, the display
  shows a browser error until it wakes.
- **The display** is just a browser pointed at the printed URL. It needs
  nothing installed and does no work.

Give the host a reserved DHCP address on your router, or the URL breaks the
next time its IP changes.

## Deployment examples

### 1. An old tablet as a wall display

Almost any tablet too old for current apps still has a working browser, which
is all this needs.

1. Put the tablet on the **same network as the host**. The printed URL is a
   LAN address and is not reachable from anywhere else.
2. Turn off auto-lock (on iOS: **Settings → General → Auto-Lock → Never**) and
   leave it on a charger.
3. Open the printed URL in the browser.
4. Add it to the home screen (**Share → Add to Home Screen** on iOS, **⋮ → Add
   to Home screen** on Android) and launch from that icon, which drops the
   browser chrome and gives you the whole screen.

This is why the page is built the way it is. It was developed against
**iOS 5.1.1** — a browser with no CSS Grid, no modern flexbox, no custom
properties, no `rem` units and no usable ES5 — so the layout is tables and
`px`, and there is no JavaScript at all. `tests/test_render_html.py` enforces
that as a compatibility lint, so it cannot quietly regress. If it renders
there, it renders anywhere.

The page is laid out for roughly **1024×768** and fits in one screen without
scrolling. Smaller screens still work, but will scroll.

### 2. A Raspberry Pi driving a small screen

A Pi with an HDMI or DSI panel makes a tidy dedicated dashboard. Here the Pi
is only the display — the host is still your development machine.

On Raspberry Pi OS with the desktop, start Chromium in kiosk mode at login by
creating `~/.config/autostart/dashboard.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Token dashboard
Exec=chromium-browser --kiosk --incognito --noerrdialogs --disable-infobars http://HOST-IP:8420/d/YOUR-TOKEN
X-GNOME-Autostart-enabled=true
```

Stop the screen blanking by adding this to the same autostart directory, or to
your session startup:

```bash
xset s off && xset -dpms && xset s noblank
```

On a Pi running Wayland (Bookworm and later), `wlr-randr` and your compositor's
idle settings replace `xset`; the Chromium flags are unchanged.

If Claude Code runs **on** the Pi — or on any always-on Linux box you SSH into
— run the dashboard there too and keep it up with a user service, so it
survives reboots:

```ini
# ~/.config/systemd/user/token-dashboard.service
[Unit]
Description=Claude token dashboard
[Service]
WorkingDirectory=%h/claude-token-dashboard
ExecStart=/usr/bin/python3 -m dashboard --port 8420 --plan max-20x
Restart=always
[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now token-dashboard
loginctl enable-linger "$USER"   # keep it running when you are logged out
```

`--plan` is spelled out in the unit because a service has no terminal to be
asked on — see [Which plan you're on](#which-plan-youre-on).

### 3. A second monitor, or no extra hardware at all

The lowest-effort option: run it on the machine you already work on and leave
`http://localhost:8420/d/YOUR-TOKEN` open in a pinned browser tab, or full
screen on a second monitor. Everything above about networks and IP addresses
stops mattering, because there is only one machine.

## Which plan you're on

The first interactive run asks, offering the common plans and letting you type
any amount instead:

```
Which Claude plan are you on? This is only used for the
'vs plan' comparison — everything else is unaffected.

  1. API only — no subscription
  2. Pro — $20/month
  3. Max 5× — $100/month
  4. Max 20× — $200/month  (default)
  5. Team — $25/month
  6. Something else — enter the monthly amount you pay

Plan [4]:
```

What actually gets stored is **a monthly dollar figure**, not a plan name — the
menu is just a fast way to pick one. Annual billing is cheaper than the prices
above (Pro is $17/month on an annual plan), Team seats vary, and Enterprise is
seat price plus usage, so anyone in those cases picks option 6 and enters what
they really pay. Prices here are monthly-billing rates as of August 2026 and
are Anthropic's to change; the amount you enter is the one that's used.

Picking **API only** drops the multiple entirely — with no subscription the
api-equivalent figure *is* your bill, and a ratio against $0 would be noise.

Set it without the prompt, or change it later:

```bash
python3 -m dashboard --plan max-5x     # a listed plan, saved for next time
python3 -m dashboard --plan 149        # any monthly amount
CLAUDE_DASHBOARD_PLAN=pro python3 -m dashboard   # one-off, not saved
```

Resolution order is `--plan` → `CLAUDE_DASHBOARD_PLAN` → the saved config →
the prompt → Max 20×. The answer lives in
`~/.claude-token-dashboard/config.json` (override with
`CLAUDE_DASHBOARD_CONFIG`); delete that file to be asked again.

> **Running as a service? Set the plan first.** A systemd unit, a login
> autostart, or a container has nobody to answer a prompt, so the dashboard
> never asks when stdin isn't a terminal — it warns on stderr and falls back to
> Max 20× rather than blocking forever on a port that never opens. Run it once
> by hand, or pass `--plan` in the unit file.

## Follow-up: real billed cost from the Claude API

Everything this dashboard shows is **reconstructed** from local transcripts and
priced at list rates. If you also call the Claude API directly, Anthropic will
tell you what you were *actually billed* — and that is the one number this tool
can't derive.

This section is a guide, not a feature: **nothing here is wired into the
dashboard**, and no credentials belong in this repository. Follow it if you
want the real figures alongside the estimate.

### First, check whether it applies to you

| You are | What you get |
|---|---|
| On a **Console (Claude Platform) organization** | The Usage & Cost Admin API below |
| On **Claude Enterprise** (claude.ai) | A different API — the [Enterprise Analytics API](https://platform.claude.com/docs/en/api/admin/analytics), with an Analytics key |
| An **individual account** | Nothing — see below |
| Using **Claude Platform on AWS** | Not available programmatically; use the Console's Usage and Cost pages |

> **The Admin API is unavailable for individual accounts.** If you use Claude
> Code on a personal Pro or Max subscription and have never set up a Console
> organization, there is no key to create and this section does not apply. A
> Max subscription is not an API account — the two are billed separately.

### 1. Create an Admin API key

In the Claude Console, under organization settings — see
[Create an Admin API key](https://platform.claude.com/docs/en/manage-claude/admin-api-keys).
It is **not** the same as a normal API key and looks like `sk-ant-admin01-…`.

Treat it as a high-privilege credential: it reads organization-wide billing.
Keep it in your shell environment or a secrets manager, never in a file in a
repository:

```bash
export ANTHROPIC_ADMIN_KEY='sk-ant-admin01-...'   # not committed anywhere
```

### 2. Tokens — the usage report

```bash
curl -s "https://api.anthropic.com/v1/organizations/usage_report/messages?\
starting_at=2026-08-01T00:00:00Z&\
ending_at=2026-08-08T00:00:00Z&\
bucket_width=1d&\
group_by[]=model" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: $ANTHROPIC_ADMIN_KEY"
```

Buckets are `1m`, `1h`, or `1d`, capped at 1440 / 168 / 31 buckets per request
respectively. You can group and filter by model, workspace, API key, service
tier, and context window. Token classes come back split the same way this
dashboard splits them — uncached input, cached input, cache creation, output —
so the two are directly comparable.

### 3. Dollars — the cost report

```bash
curl -s "https://api.anthropic.com/v1/organizations/cost_report?\
starting_at=2026-08-01T00:00:00Z&\
ending_at=2026-08-31T00:00:00Z&\
group_by[]=description" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: $ANTHROPIC_ADMIN_KEY"
```

Daily granularity only. **Costs are returned as decimal strings in the
currency's lowest unit (cents)** — divide before comparing them to anything on
this page, and don't parse them as floats if you care about exactness.

### 4. Things that will bite you

- **Both endpoints paginate.** Check `has_more` and pass `next_page` back as
  `page` until it is false, or you will silently read only the first slice.
- **Priority Tier spend is missing from the cost report** — track it through
  the usage report's `service_tier` instead.
- **Code execution is the mirror image**: it appears only in the *cost* report,
  under a `Code Execution Usage` description, and not in the usage report.
- **Data lags a few minutes**, and poll no more than about once a minute.
- Console Workbench usage has a `null` api_key_id; the default workspace has a
  `null` workspace_id. Neither is an error.

### 5. Why this isn't merged into the dashboard

The two sources are not the same measurement, and adding them together would
produce a number that means nothing. This tool reconstructs what *subscription*
usage would have cost at list rates; the Admin API reports what an *API
organization* was actually charged. If you wire this up, keep it as its own
panel with its own heading — as the "By source" split already does for the
surfaces that are covered.

Reference: [Usage and Cost API](https://platform.claude.com/docs/en/api/usage-cost-api).

## What the numbers mean

- **api-equivalent cost** — what the usage would have cost at Claude API list
  rates. It models no batch discounts and no promotional rates. It is a
  comparison figure, not a bill.
- **vs &lt;your plan&gt;** — that figure against what you actually pay per month,
  and the resulting multiple. See [Which plan you're on](#which-plan-youre-on).
- **cache read / cache write** — cache reads bill at 0.1× the input rate;
  writes at 1.25× (5-minute TTL) or 2× (1-hour TTL). Most cost is usually
  cache reads rather than output, because context replay dominates.
- **Delegation** — how much of the spend came from subagents rather than your
  direct session. Subagent cost is attributed to the parent session and
  project as well, so it is counted once in the totals and shown again in the
  split.
- **By source** — which Claude surface the spend came from. Only surfaces that
  keep token counts on disk can appear; see "Which surfaces are covered".
- **Most panels are all-time.** The top row and the Max comparison are
  windowed, and the Daily chart shows only the **last 30 active days** — each
  panel states its own range in its heading. Once you have more than 30 active
  days of history the Daily chart stops at 30 while every "ALL TIME" panel
  keeps counting everything.

Rates live in `dashboard/pricing.py` as a plain table. They will go stale as
models are released and repriced; editing that table is the whole update.

## Which surfaces are covered

| Surface | Counted? | Why |
|---|---|---|
| Claude Code | yes | writes transcripts with per-message `usage` |
| Claude Desktop — Cowork | yes | same transcript format, different root |
| Claude API / Console | no | billed server-side — see [Follow-up: real billed cost](#follow-up-real-billed-cost-from-the-claude-api) |
| Claude Desktop — chat | no | no token counts stored locally |
| Claude in Chrome | no | proxies claude.ai, same as chat |

The three "no" rows are a property of the data, not a gap in this tool. Those
surfaces bill server-side and persist no usage on disk — the claude.ai
IndexedDB, Local Storage and web log contain no token fields at all. The only
local trace of them is Claude Desktop's `plan-usage-history.json`, which
records *percentage of your plan's 5-hour and 7-day limits consumed*:
account-wide, and not convertible to tokens or dollars, so it is deliberately
not mixed into these totals.

## Where the data lives

- Read recursively from `~/.claude/projects/**/*.jsonl` (override with
  `CLAUDE_PROJECTS_DIR`). This includes both main-session transcripts and
  subagent transcripts nested under `<session>/subagents/`, so delegated work
  is counted alongside direct sessions.
- And from Claude Desktop's Cowork sessions (override with
  `CLAUDE_COWORK_DIR`, skipped silently if absent — so this costs nothing on
  a machine without Claude Desktop, including Linux):

  ```
  ~/Library/Application Support/Claude/local-agent-mode-sessions/
    <install>/<org>/local_<id>/.claude/projects/<encoded-cwd>/<session>.jsonl
  ```

  Cowork runs Claude Code with HOME redirected per session, so each session
  keeps a full private transcript tree in the same format. Every session also
  writes an `audit.jsonl` repeating the same `message.id` values; dedup drops
  it, so the mirror adds nothing. Because every Cowork session runs in a
  directory called `outputs`, project labels come from the session's sidecar
  JSON — the folder you pointed it at, else its title — rather than the cwd.
- Accumulated in `~/.claude-token-dashboard/history.db` (override with
  `CLAUDE_DASHBOARD_DB`), insert-only and keyed on message ID, so re-scanning
  never double-counts and history survives Claude Code deleting old
  transcripts. Older databases are migrated in place on open.
- The access token is `~/.claude-token-dashboard/token`. Delete it to roll the
  URL; a new one is generated on the next start.
- Your plan answer is `~/.claude-token-dashboard/config.json` (override with
  `CLAUDE_DASHBOARD_CONFIG`). Delete it to be asked again.

Nothing leaves your machine. There is no telemetry, no network egress, and no
account of any kind — the dashboard only ever reads local files and serves
them back on your own LAN.

## Refresh cost

A refresh re-reads only what changed. Transcripts are append-only, so each one
records a byte offset and a digest of the bytes already consumed; the next
pass resumes from that offset instead of re-parsing the file. On a
multi-megabyte transcript that turns an append from hundreds of milliseconds
into well under one.

The offset is only trusted when the file is demonstrably the same one,
extended — a truncated file, a rewritten one, or a row predating offsets falls
back to a full re-read. Records are keyed on message ID, so a redundant
re-read costs time and nothing else.

What remains scales with total history rather than with the number of files:
reading every row back out of SQLite and rebuilding the all-time panels.
Making that sublinear means keeping a rollup rather than aggregating from raw
rows, which has not been needed yet.

## Security

Plain HTTP on your LAN behind an unguessable path. No TLS, no login. That is
enough to stop someone on your home network stumbling across it; it is **not**
safe on a hostile network, and the token appears in the URL, so it will sit in
browser history and any proxy logs in between.

**Do not forward the port to the internet.** If you need it away from home,
put it behind a VPN or an authenticating reverse proxy rather than exposing it
directly.

## Tests

Some Python installations are "externally managed" (PEP 668 — Homebrew,
Debian, Raspberry Pi OS) and refuse a bare `pip install`, so use a virtualenv:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

The suite never asserts against your real `~/.claude` tree, because its totals
change with every message. There is an opt-in smoke check that runs against
live data and asserts only shape:

```bash
DASHBOARD_LIVE_SMOKE=1 .venv/bin/python -m pytest -k live
```

## Licence

MIT — see [LICENSE](LICENSE).
