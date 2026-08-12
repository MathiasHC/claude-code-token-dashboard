"""Which subscription the api-equivalent cost is compared against.

The dashboard's headline comparison is "what this usage would have cost at
API list rates, versus what you actually pay". The second half of that is a
number only you know, so it is asked once on first run and remembered.

What is stored is a monthly USD figure, not a plan name: the plan is just a
convenient way to pick the figure. Anyone on annual billing, a Team seat, or
a negotiated rate enters the amount directly.

Resolution order, highest priority first:

    1. an explicit --plan argument
    2. the CLAUDE_DASHBOARD_PLAN environment variable
    3. ~/.claude-token-dashboard/config.json, written on first run
    4. an interactive prompt, only when there is a terminal to prompt on
    5. the default (Max 20x)

Step 4 is skipped whenever stdin is not a TTY. A dashboard started by systemd,
a login autostart, or a container has nobody to answer the question, and a
blocked service that never binds its port is a far worse failure than a wrong
default — so in that case it falls through to step 5 and says so on stderr.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Re-exported: `plans.Plan` is how every caller names it, but the type itself
# belongs with the other shared types rather than with config loading.
from .models import Plan

#: Monthly-billing prices, current as of August 2026. Annual billing is
#: cheaper (Pro is $17/mo on annual), and Team seats and Enterprise vary —
#: those cases pick a custom amount rather than bloating this table into a
#: pricing matrix that will rot.
CATALOGUE: tuple[Plan, ...] = (
    Plan("api", "API only", 0.0),
    Plan("pro", "Pro", 20.0),
    Plan("max-5x", "Max 5×", 100.0),
    Plan("max-20x", "Max 20×", 200.0),
    Plan("team", "Team", 25.0),
)
DEFAULT = CATALOGUE[3]
ENV_VAR = "CLAUDE_DASHBOARD_PLAN"


def config_path() -> Path:
    override = os.environ.get("CLAUDE_DASHBOARD_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".claude-token-dashboard" / "config.json"


def by_key(key: str) -> Plan | None:
    wanted = key.strip().lower()
    for plan in CATALOGUE:
        if plan.key == wanted:
            return plan
    return None


def parse(value: str) -> Plan | None:
    """A catalogue key ("max-5x") or a bare monthly amount ("149", "$149.50")."""
    if not value or not value.strip():
        return None
    known = by_key(value)
    if known is not None:
        return known
    cleaned = value.strip().lstrip("$").replace(",", "")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    if amount < 0:
        return None
    return custom(amount)


def custom(amount: float) -> Plan:
    """Label a hand-entered amount.

    Cents are shown only when there are any: a plan costing $149.50 must not
    be labelled "$150/mo", which misreports what the user told us they pay.
    """
    if not amount:
        return Plan("custom", "API only", 0.0)
    exact = f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"
    return Plan("custom", f"{exact}/mo", amount)


def load(path: Path | None = None) -> Plan | None:
    """Read the saved plan. A corrupt or partial file is treated as absent —
    it is a convenience cache, never worth crashing the dashboard over."""
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        amount = float(raw["monthly_usd"])
    except (KeyError, TypeError, ValueError):
        return None
    if amount < 0:
        return None
    key = raw.get("plan")
    label = raw.get("label")
    if not isinstance(key, str) or not isinstance(label, str) or not label:
        return custom(amount)
    return Plan(key, label, amount)


def save(plan: Plan, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"plan": plan.key, "label": plan.label, "monthly_usd": plan.monthly_usd}
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def from_env() -> Plan | None:
    value = os.environ.get(ENV_VAR)
    return parse(value) if value else None


def prompt(read=input, write=print) -> Plan:
    """Ask which plan, once. Returns DEFAULT if the answer is empty."""
    write("Which Claude plan are you on? This is only used for the")
    write("'vs plan' comparison — everything else is unaffected.")
    write("")
    for index, plan in enumerate(CATALOGUE, start=1):
        marker = "  (default)" if plan == DEFAULT else ""
        price = "no subscription" if not plan.monthly_usd else f"${plan.monthly_usd:,.0f}/month"
        write(f"  {index}. {plan.label} — {price}{marker}")
    write(f"  {len(CATALOGUE) + 1}. Something else — enter the monthly amount you pay")
    write("")
    while True:
        try:
            answer = read("Plan [4]: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            return DEFAULT
        if not answer:
            return DEFAULT
        if answer.isdigit():
            choice = int(answer)
            if 1 <= choice <= len(CATALOGUE):
                return CATALOGUE[choice - 1]
            if choice == len(CATALOGUE) + 1:
                amount = _ask_amount(read, write)
                if amount is not None:
                    return custom(amount)
                continue
        parsed = parse(answer)
        if parsed is not None:
            return parsed
        write("Not a listed option or an amount. Try again, or press Enter for the default.")


def _ask_amount(read, write) -> float | None:
    try:
        raw = read("Monthly amount in USD: ").strip()
    except (EOFError, KeyboardInterrupt):
        write("")
        return None
    parsed = parse(raw)
    if parsed is None:
        write("Not a number.")
        return None
    return parsed.monthly_usd


def resolve(
    explicit: str | None = None,
    *,
    path: Path | None = None,
    interactive: bool | None = None,
    read=input,
    write=print,
    warn=lambda message: print(message, file=sys.stderr),
) -> Plan:
    """Settle on a plan, saving the answer if one had to be asked for."""
    if explicit:
        chosen = parse(explicit)
        if chosen is None:
            raise ValueError(
                f"--plan expects one of {', '.join(p.key for p in CATALOGUE)}, "
                f"or a monthly amount; got {explicit!r}"
            )
        save_quietly(chosen, path)
        return chosen

    from_environment = from_env()
    if from_environment is not None:
        return from_environment

    stored = load(path)
    if stored is not None:
        return stored

    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        warn(
            f"No plan configured; comparing against {DEFAULT.label} "
            f"(${DEFAULT.monthly_usd:,.0f}/month). Set {ENV_VAR} or run "
            "`python3 -m dashboard --plan <plan>` once to change it."
        )
        return DEFAULT

    chosen = prompt(read=read, write=write)
    save_quietly(chosen, path)
    return chosen


def save_quietly(plan: Plan, path: Path | None = None) -> None:
    """Persist the choice, but never fail the run over an unwritable config."""
    try:
        save(plan, path)
    except OSError:
        pass
