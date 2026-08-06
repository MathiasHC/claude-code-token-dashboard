"""Render the dashboard from synthetic demo data.

Used to produce the README screenshot, and to preview the layout without
having any real usage on the machine:

    python3 tools/demo_page.py /tmp/demo.html

Every figure below is invented. The shape is meant to be representative of
heavy agentic use — cache reads dominating cost, a long tail of models and
projects, most spend outside any named skill — so the screenshot shows what
a populated dashboard looks like rather than a near-empty one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import render_html  # noqa: E402
from dashboard.models import Bar, DashboardData, DayCost, Window  # noqa: E402

ALL_TIME = 4182.55

# Cost is overwhelmingly cache reads on real agentic workloads: every turn
# replays the conversation, and replay bills at 0.1x input.
MONEY = [
    ("cache read", 2634.01),
    ("output", 836.51),
    ("cache write", 543.73),
    ("fresh input", 168.30),
]
BY_MODEL = [
    ("claude-opus-5", 2341.23),
    ("claude-sonnet-5", 920.16),
    ("claude-opus-4-8", 585.56),
    ("claude-fable-5", 251.00),
    ("claude-haiku-4-5", 84.60),
]
BY_PROJECT = [
    ("api-gateway", 1463.89),
    ("mobile-app", 1003.81),
    ("data-pipeline", 669.21),
    ("infra-terraform", 460.08),
    ("docs-site", 292.78),
]
BY_SKILL = [
    ("(none)", 1881.15),
    ("brainstorming", 836.51),
    ("test-driven-development", 585.56),
    ("code-review", 418.26),
    ("systematic-debugging", 209.13),
]
BY_SOURCE = [
    ("Claude Code", 3554.17),
    ("Desktop (Cowork)", 628.38),
]
TOP_SESSIONS = [
    ("Migrate the billing service off the legacy queue", 184.20),
    ("/tdd add idempotency keys to the payments endpoint", 141.77),
    ("Investigate the p99 latency regression in the gateway", 118.03),
    ("Rewrite the onboarding flow for the mobile app", 96.44),
    ("(untitled session)", 72.10),
]

# 30 days, weekends visibly lower — a flat series looks synthetic.
DAILY = [
    ("2026-07-08", 41.20), ("2026-07-09", 52.75), ("2026-07-10", 47.13),
    ("2026-07-11", 18.44), ("2026-07-12", 9.02),  ("2026-07-13", 33.87),
    ("2026-07-14", 55.61), ("2026-07-15", 61.94), ("2026-07-16", 44.28),
    ("2026-07-17", 39.75), ("2026-07-18", 14.60), ("2026-07-19", 6.31),
    ("2026-07-20", 28.49), ("2026-07-21", 50.02), ("2026-07-22", 58.36),
    ("2026-07-23", 46.71), ("2026-07-24", 35.18), ("2026-07-25", 21.93),
    ("2026-07-26", 11.07), ("2026-07-27", 30.66), ("2026-07-28", 48.85),
    ("2026-07-29", 57.40), ("2026-07-30", 43.12), ("2026-07-31", 37.29),
    ("2026-08-01", 16.85), ("2026-08-02", 8.44),  ("2026-08-03", 34.51),
    ("2026-08-04", 53.08), ("2026-08-05", 49.66), ("2026-08-06", 38.42),
]


def _bars(rows: list[tuple[str, float]]) -> list[Bar]:
    return [Bar(label=name, cost=cost, share=cost / ALL_TIME) for name, cost in rows]


def demo_data() -> DashboardData:
    return DashboardData(
        generated_at="06 Aug 2026 09:41",
        today=Window(label="today", cost=38.42, messages=214),
        last_7_days=Window(label="7 days", cost=291.18, messages=1_584),
        month_to_date=Window(label="month to date", cost=612.90, messages=3_320),
        all_time=Window(label="all time", cost=ALL_TIME, messages=25_472),
        active_days=96,
        max_plan_monthly_usd=200.0,
        prev_month_label="2026-07",
        prev_month_cost=1_204.83,
        yesterday_cost=34.18,
        prior_7_days_cost=317.50,
        prev_month_to_date_cost=502.00,
        money=_bars(MONEY),
        by_model=_bars(BY_MODEL),
        by_project=_bars(BY_PROJECT),
        by_skill=_bars(BY_SKILL),
        by_source=_bars(BY_SOURCE),
        top_sessions=[
            Bar(label=title, cost=cost, share=cost / ALL_TIME)
            for title, cost in TOP_SESSIONS
        ],
        daily=[DayCost(day=day, cost=cost) for day, cost in DAILY],
        cache_hit_rate=0.9871,
        avg_cost_per_message=0.1642,
        avg_cost_per_session=8.94,
        main_cost=2_718.66,
        subagent_cost=1_463.89,
    )


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo.html")
    out.write_text(render_html.render(demo_data()), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
