"""Shared data types. No logic lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from .footprint import EMPTY_FOOTPRINT, Footprint


class UsageRecord(NamedTuple):
    """One assistant message's token usage, normalised out of a transcript.

    A NamedTuple rather than a frozen dataclass purely for construction
    speed: the store rebuilds one of these per row on every refresh, and a
    frozen dataclass's __init__ (an object.__setattr__ per field) measured
    ~7x slower over a real history. Same immutability, same keyword
    construction; use ._replace() where you would reach for
    dataclasses.replace().
    """

    message_id: str
    ts: str
    day: str
    model: str
    project: str
    skill: str
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    speed: str | None = None
    is_subagent: bool = False
    #: Which Claude surface produced this message — see ingest.SOURCE_LABELS.
    #: "code" is the default so records predating the multi-source scan, and
    #: every existing row in an old database, read back as Claude Code.
    source: str = "code"
    #: Seconds the machine spent producing this message: model generation
    #: plus any tool runs since the last message, excluding time spent
    #: waiting for a human. See scan.MAX_WORK_GAP_SECONDS for what is
    #: counted and what is discarded as idle.
    work_seconds: float = 0.0
    #: Seconds spent waiting for the person before this message: the gap
    #: ending at their turn. The other side of the same clock.
    wait_seconds: float = 0.0
    #: Permission mode in force — auto, default, plan, acceptEdits. Tracked
    #: as state by scan, since the message itself never carries it.
    mode: str = "(not recorded)"
    #: Reasoning effort the message was produced at.
    effort: str = "(none)"
    #: Git branch the working directory was on.
    branch: str = "(none)"
    #: MCP server the message attributed a tool call to. Empty for the
    #: overwhelming majority, which call no MCP tool at all.
    mcp_server: str = ""
    #: Prefix tokens that had to be re-processed because the cache did not
    #: hold, and why. Billed at write rates instead of read rates, so this
    #: is the one measured quantity on the page that is straightforwardly
    #: wasted money.
    cache_missed_tokens: int = 0
    cache_miss_reason: str = ""
    #: Why the model stopped. "tool_use" means it is mid-turn and about to
    #: call something; "end_turn" means it finished talking.
    stop_reason: str = ""
    #: Local hour, so the page can show when the work happens without
    #: re-parsing every timestamp.
    hour: int = -1
    #: Tool calls refused since the previous message, and context blocks the
    #: harness injected. Both accumulate across records that carry no usage,
    #: like work_seconds.
    denials: int = 0
    injections: int = 0
    #: Who started the skill run this message belongs to, and an id shared
    #: by every message in that run — the id of its first message.
    skill_origin: str = ""
    skill_run: str = ""
    #: The prompt this message belongs to — one human turn and everything
    #: the machine did in response, tool results included. Transcripts put
    #: `promptId` on user records only, never on assistant ones, so like
    #: `mode` this is carried forward as scan state rather than read off
    #: the message that needs it.
    prompt_run: str = ""


class Plan(NamedTuple):
    """A subscription to compare api-equivalent cost against.

    Label and amount travel together. They were previously split into two
    arguments at the one call site that had both, which meant nothing stopped
    a page rendering "vs Pro ... / $200.00 actual".
    """

    key: str
    label: str
    monthly_usd: float


@dataclass(frozen=True)
class CostBreakdown:
    fresh_input: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    output: float = 0.0

    @property
    def total(self) -> float:
        return self.fresh_input + self.cache_read + self.cache_write + self.output


@dataclass(frozen=True)
class Bar:
    """One labelled row in a breakdown panel."""

    label: str
    cost: float
    share: float


@dataclass(frozen=True)
class Window:
    cost: float
    messages: int


@dataclass(frozen=True)
class DayCost:
    day: str
    cost: float


@dataclass(frozen=True)
class Leader:
    """One placed row on a leaderboard.

    `value` stays a raw number and the board says what unit it is in, for
    the same reason Bar carries a float: a pre-formatted string cannot be
    asserted against without pinning the display format into every test.
    """

    label: str
    value: float
    note: str = ""


@dataclass(frozen=True)
class Leaderboard:
    """A top three, and what it is a top three of.

    Global by construction. Nothing here narrows to the selected range —
    see dashboard/leaderboards.py for why that is a property of the claim
    rather than an oversight.
    """

    title: str
    #: How the renderer should format `value`: one of the keys in
    #: render_html._LEADER_UNITS. Carried per board rather than per row
    #: because a board whose rows disagreed about their unit would be
    #: ranking incomparable things.
    unit: str
    #: Which drawing sits beside the heading: a key in
    #: render_html._BOARD_ICONS. Named here rather than derived from the
    #: title in the renderer, so a board cannot be retitled into having the
    #: wrong picture.
    icon: str = ""
    leaders: list[Leader] = field(default_factory=list)
    #: An extra fact that belongs with this board but is not a placing —
    #: the counterpart figure, e.g. the earliest start under the latest
    #: nights. Empty for most boards.
    note: str = ""


@dataclass(frozen=True)
class SkillRun:
    """One stretch of messages attributed to the same skill."""

    skill: str
    started: str
    cost: float
    seconds: float
    origin: str


@dataclass(frozen=True)
class RangeView:
    """Everything the selected range re-scopes, plus the label that says so.

    The page has exactly two scopes. The hero row and its deltas are global
    by construction — they are the fixed summary and do not move when a range
    is picked. Everything here does move, and every panel built from it has to
    admit which window it is showing.

    That invariant used to live only in prose, in aggregate.build, while all
    thirty-odd figures sat in one flat namespace on DashboardData. Two panels
    had already drifted: the DELEGATION and BY SOURCE bands rendered
    range-scoped numbers with no label at all, and the DAILY heading derived
    its window from `len(daily)` — the number of days that happened to carry
    spend — so a 30-day range with three active days announced "LAST 3 DAYS".

    Nesting them behind one value with `label` on it makes rendering a scoped
    figure without saying which range it belongs to something you have to go
    out of your way to do.
    """

    #: The catalogue key, e.g. "7d". What the selector marks as current.
    key: str
    #: The long form for panel headings, e.g. "LAST 7 DAYS".
    label: str
    money: list[Bar] = field(default_factory=list)
    by_model: list[Bar] = field(default_factory=list)
    by_project: list[Bar] = field(default_factory=list)
    by_skill: list[Bar] = field(default_factory=list)
    by_source: list[Bar] = field(default_factory=list)
    by_mode: list[Bar] = field(default_factory=list)
    by_effort: list[Bar] = field(default_factory=list)
    by_branch: list[Bar] = field(default_factory=list)
    by_mcp: list[Bar] = field(default_factory=list)
    by_skill_origin: list[Bar] = field(default_factory=list)
    skill_runs: list[SkillRun] = field(default_factory=list)
    top_sessions: list[Bar] = field(default_factory=list)
    daily: list[DayCost] = field(default_factory=list)
    main_cost: float = 0.0
    subagent_cost: float = 0.0
    #: Counterfactual: what the cache-read tokens would have cost at full
    #: input rates. Never money that was in play — see render_html, which is
    #: required to say so on the page.
    cache_saved: float = 0.0
    #: Kept alongside cache_saved so the page can tell "no cache reads"
    #: apart from "cache reads we have no rate for".
    cache_read_tokens: int = 0
    avg_cost_per_message: float = 0.0
    avg_cost_per_session: float = 0.0
    #: Seconds the machine spent working inside this range, summed across
    #: agents — so parallel subagents add up beyond wall-clock time.
    worked_seconds: float = 0.0
    subagent_worked_seconds: float = 0.0
    #: Seconds the machine spent waiting for the person.
    waited_seconds: float = 0.0
    #: Distinct sessions that carried spend inside this range.
    sessions: int = 0
    #: Prefix tokens re-processed because the cache did not hold, what that
    #: cost above a cache read, and the reason that dominated.
    cache_missed_tokens: int = 0
    cache_miss_cost: float = 0.0
    cache_miss_reason: str = ""
    #: Trivia. Cheap to carry, and the only figures on the page that are
    #: about the shape of the work rather than its size.
    tool_use_messages: int = 0
    reply_messages: int = 0
    denials: int = 0
    injections: int = 0
    #: Who started the skill run this message belongs to, and an id shared
    #: by every message in that run — the id of its first message.
    skill_origin: str = ""
    skill_run: str = ""
    priciest_message: float = 0.0
    busiest_hour: int = -1
    weekend_share: float = 0.0

    @property
    def tools_per_reply(self) -> float:
        return self.tool_use_messages / self.reply_messages if self.reply_messages else 0.0
    #: Modelled energy/water/carbon for the tokens in this range. Order of
    #: magnitude only — see dashboard/footprint.py for why.
    footprint: Footprint = EMPTY_FOOTPRINT

    @property
    def subagent_share(self) -> float:
        total = self.main_cost + self.subagent_cost
        return self.subagent_cost / total if total else 0.0

    @property
    def subagent_worked_share(self) -> float:
        return self.subagent_worked_seconds / self.worked_seconds if self.worked_seconds else 0.0

    @property
    def active_day_count(self) -> int:
        """Days inside the range that carried any spend — which is what the
        daily chart plots, one bar each."""
        return len(self.daily)


@dataclass(frozen=True)
class DashboardData:
    generated_at: str
    today: Window
    last_7_days: Window
    month_to_date: Window
    all_time: Window
    active_days: int
    prev_month_label: str
    prev_month_cost: float
    #: Which slice of history the panels below the hero row cover. The hero
    #: row itself is always the same four windows.
    scoped: RangeView
    #: What the api-equivalent cost is being compared against.
    plan: Plan
    yesterday_cost: float = 0.0
    prior_7_days_cost: float = 0.0
    prev_month_to_date_cost: float = 0.0
    unpriced_models: list[str] = field(default_factory=list)
    #: Month-to-date plus the trailing-7-day rate over the days remaining.
    #: None early in a month, where the trailing window is mostly last month.
    on_pace: float | None = None
    #: Cost per hour of *active* time today, and minutes since the last
    #: message. None when today has too little activity to divide by.
    burn_rate_hourly: float | None = None
    idle_minutes: int | None = None
    #: Today as a local ISO date, so the daily chart can mark its own
    #: column without the renderer reading a clock.
    today_day: str = ""
    #: All-time top threes. On DashboardData rather than RangeView because
    #: they are all-time: a leaderboard that re-ranked when somebody picked
    #: LAST 7 DAYS would not be the thing its heading claims.
    leaderboards: list[Leaderboard] = field(default_factory=list)

    @property
    def effective_multiple(self) -> float:
        if self.plan.monthly_usd <= 0:
            return 0.0
        return self.month_to_date.cost / self.plan.monthly_usd

    @property
    def day_change(self) -> float | None:
        """Today vs yesterday, as a fraction, or None if no prior-day data."""
        if self.yesterday_cost <= 0:
            return None
        return (self.today.cost - self.yesterday_cost) / self.yesterday_cost

    @property
    def week_change(self) -> float | None:
        """Last 7 days vs the 7 days before that, or None if no prior-week data."""
        if self.prior_7_days_cost <= 0:
            return None
        return (self.last_7_days.cost - self.prior_7_days_cost) / self.prior_7_days_cost

    @property
    def month_change(self) -> float | None:
        """Month-to-date vs the same number of days into the previous month,
        or None if there is no prior like-for-like data."""
        if self.prev_month_to_date_cost <= 0:
            return None
        return (self.month_to_date.cost - self.prev_month_to_date_cost) / self.prev_month_to_date_cost
