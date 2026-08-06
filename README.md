# Claude Code Token Dashboard

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

![The dashboard, rendered from the project's test fixture](docs/screenshot.jpg)

*Figures above are the synthetic test fixture, not real usage.*

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
ExecStart=/usr/bin/python3 -m dashboard --port 8420
Restart=always
[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now token-dashboard
loginctl enable-linger "$USER"   # keep it running when you are logged out
```

### 3. A second monitor, or no extra hardware at all

The lowest-effort option: run it on the machine you already work on and leave
`http://localhost:8420/d/YOUR-TOKEN` open in a pinned browser tab, or full
screen on a second monitor. Everything above about networks and IP addresses
stops mattering, because there is only one machine.

## What the numbers mean

- **api-equivalent cost** — what the usage would have cost at Claude API list
  rates. It models no batch discounts and no promotional rates. It is a
  comparison figure, not a bill.
- **vs MAX 20×** — that figure against a $200/month subscription. Change
  `MAX_PLAN_MONTHLY_USD` in `dashboard/pricing.py` if you are on a different
  plan.
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
| Claude API / Console | no | billed server-side; needs the Admin API and a key |
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
