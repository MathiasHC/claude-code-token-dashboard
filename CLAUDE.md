# Claude Code Token Dashboard

A self-hosted dashboard for Claude Code token usage and cost. Reads local
transcripts, stores them in SQLite, serves one static HTML page over the LAN.

## Setting this up for someone

**Ask which Claude plan they're on before starting the dashboard.** The
headline comparison is "what this usage would have cost at API list rates
versus what you actually pay", and the second half is a number only they
know. Guessing it makes the most prominent figure on the page wrong.

Offer these, and let them give any monthly amount instead:

| Option | Monthly (billed monthly) |
|---|---|
| API only — no subscription | $0 |
| Pro | $20 |
| Max 5× | $100 |
| Max 20× | $200 (the default if they don't care) |
| Team | $25 per seat |
| Something else | whatever they actually pay |

Annual billing is cheaper (Pro is $17/month annually), Team seats vary, and
Enterprise is seat price plus usage — so if they mention any of those, ask for
the amount rather than mapping them to a row above. Prices are Anthropic's to
change; what matters is the figure they give you.

Then pass it through, which also saves it so nobody is asked twice:

```bash
python3 -m dashboard --plan max-5x     # a listed plan
python3 -m dashboard --plan 149        # any monthly amount
```

Running it with no `--plan` in an interactive terminal asks the same question
itself. **Do not rely on that when you are the one running it** — an agent's
shell is usually not a TTY, so the prompt is skipped and the default is used
silently. Ask them, then pass `--plan`.

If they are setting it up as a systemd service or a kiosk autostart, `--plan`
belongs in the unit file for the same reason.

## Layout

```
dashboard/
  scan.py         parse transcripts (the only module that knows the JSONL shape)
  cowork.py       locate + label Claude Desktop Cowork sessions
  ingest.py       compose the scan across sources, dedupe by message id
  store.py        SQLite, insert-only, migrates older databases on open
  aggregate.py    records -> DashboardData (pure; `now` is injected)
  pricing.py      model rate table and cost arithmetic (pure)
  plans.py        which subscription to compare against
  render_html.py  DashboardData -> one HTML document (pure)
  web.py          stdlib http.server, LAN-bound, token-gated
tools/demo_page.py  render synthetic demo data (used for the README screenshot)
```

## Conventions

- **No dependencies outside the standard library** in `dashboard/`. pytest is
  dev-only. Adding a runtime dependency is a design change, not a detail.
- **The renderer targets iOS 5.1.1 Safari**: table layout, `px` units, no
  JavaScript, no flexbox, no CSS Grid, no custom properties, no `rem`.
  `tests/test_render_html.py` enforces this — if a test there fails, the fix
  is the markup, not the test.
- **`aggregate.build` and `render_html.render` are pure.** No clock reads, no
  I/O. `now` is a parameter so windows are testable.
- **Never assert against a real `~/.claude` tree in tests** — its totals change
  with every message. Fixtures live in `tests/fixtures/`. The one live check is
  opt-in (`DASHBOARD_LIVE_SMOKE=1`) and asserts shape only.
- **Column order in `store.py` is a contract.** SQL and the field mapping both
  derive from `_FIELDS`; add a column there, not in two places.
- **The golden file is the renderer's regression guard.** After a deliberate
  markup change, regenerate with
  `UPDATE_GOLDEN=1 pytest tests/test_render_html.py` and read the diff before
  committing it.

## Tests

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```
