"""Renders DashboardData as one self-contained HTML document.

Targets iOS 5.1.1 Safari: table layout, px units, no flexbox, no CSS grid,
no custom properties, no JavaScript. Pure — no I/O, no clock reads.
"""

from __future__ import annotations

from html import escape

from .models import Bar, DashboardData

BAR_COLOURS = ("#58a6ff", "#d29922", "#3fb950", "#8b949e", "#a371f7")
SOURCE_COLOURS = ("#3fb950", "#a371f7", "#58a6ff", "#d29922", "#8b949e")
DAILY_CHART_HEIGHT_PX = 44
SESSION_TITLE_MAX = 62

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0e1116; color:#e6edf3;
       font-family:"Helvetica Neue", Helvetica, Arial, sans-serif;
       font-size:16px; padding:10px; }
a { color:inherit; text-decoration:none; }
.titlebar { font-size:14px; letter-spacing:2px; color:#8b949e;
            border-bottom:1px solid #30363d; padding-bottom:6px; margin-bottom:8px; }
.titlebar .when { float:right; letter-spacing:0; }
.warn { background:#3d1d1d; border:1px solid #f85149; color:#ffa198;
        padding:6px 10px; margin-bottom:8px; font-size:13px; }
table.hero { width:100%; table-layout:fixed; border-collapse:collapse; margin-bottom:6px; }
table.hero td { vertical-align:top; }
.hlabel { font-size:11px; letter-spacing:1.5px; color:#8b949e; }
.hvalue { font-size:40px; font-weight:bold; line-height:46px; }
.hsub { font-size:11px; color:#8b949e; }
.hdelta { font-size:11px; color:#8b949e; }
.maxrow { background:#161b22; border:1px solid #30363d;
          padding:7px 10px; margin-bottom:8px; font-size:13px; color:#8b949e; }
.maxrow .mult { font-size:22px; font-weight:bold; color:#3fb950; }
.maxrow .mom { float:right; }
/* border-spacing matches table.grid so the bands line up with the panels
   below them; margin-bottom keeps the vertical rhythm the single full-width
   delegation band used to have. */
table.bands { width:100%; table-layout:fixed; border-collapse:separate;
              border-spacing:6px 0; margin:0 0 8px 0; }
td.band { background:#161b22; border:1px solid #30363d; padding:7px 10px;
          font-size:13px; color:#8b949e; vertical-align:top; }
.bandtitle { color:#e6edf3; letter-spacing:1.5px; font-size:11px; margin-bottom:4px; }
table.grid { width:100%; table-layout:fixed; border-collapse:separate; border-spacing:6px; }
table.grid > tbody > tr > td { background:#161b22; border:1px solid #30363d;
                               padding:7px 9px; vertical-align:top; }
h2 { font-size:10px; letter-spacing:1.5px; color:#8b949e;
     font-weight:normal; margin-bottom:5px; }
table.rows { width:100%; border-collapse:collapse; }
table.rows td { font-size:13px; padding:2px 0; white-space:nowrap; overflow:hidden; }
td.amt { text-align:right; width:80px; }
td.pct { text-align:right; width:52px; color:#8b949e; }
td.barcell { padding-left:8px; }
.bar { background:#21262d; height:9px; }
.fill { height:9px; }
.note { font-size:11px; color:#8b949e; margin-top:5px; }
table.daily { width:100%; table-layout:fixed; border-collapse:collapse; }
table.daily td { vertical-align:bottom; text-align:center; padding:0 1px; }
.col { background:#238636; }
.dscale { font-size:10px; color:#8b949e; }
""".strip()


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _rows(bars: list[Bar]) -> str:
    if not bars:
        return '<div class="note">no data yet</div>'
    out = ['<table class="rows">']
    for index, bar in enumerate(bars):
        colour = BAR_COLOURS[index % len(BAR_COLOURS)]
        width = max(0.0, min(100.0, bar.share * 100))
        out.append(
            "<tr>"
            f'<td>{escape(bar.label)}</td>'
            f'<td class="amt">{_money(bar.cost)}</td>'
            f'<td class="pct">{_pct(bar.share)}</td>'
            '<td class="barcell"><div class="bar">'
            f'<div class="fill" style="width:{width:.1f}%;background:{colour}"></div>'
            "</div></td>"
            "</tr>"
        )
    out.append("</table>")
    return "".join(out)


def _session_rows(bars: list[Bar]) -> str:
    if not bars:
        return '<div class="note">no sessions yet</div>'
    out = ['<table class="rows">']
    for bar in bars:
        label = bar.label
        if len(label) > SESSION_TITLE_MAX:
            label = label[: SESSION_TITLE_MAX - 1] + "…"
        out.append(
            "<tr>"
            f'<td class="amt">{_money(bar.cost)}</td>'
            f'<td style="padding-left:10px">{escape(label)}</td>'
            "</tr>"
        )
    out.append("</table>")
    return "".join(out)


def _daily(data: DashboardData) -> str:
    if not data.daily:
        return '<div class="note">no daily history yet</div>'
    peak = max(point.cost for point in data.daily) or 1.0
    cells = []
    for point in data.daily:
        height = max(1, min(DAILY_CHART_HEIGHT_PX, round(point.cost / peak * DAILY_CHART_HEIGHT_PX)))
        cells.append(f'<td><div class="col" style="height:{height}px"></div></td>')
    return (
        f'<table class="daily" style="height:{DAILY_CHART_HEIGHT_PX}px">'
        f"<tr>{''.join(cells)}</tr></table>"
        f'<div class="dscale">{escape(data.daily[0].day)}'
        f'<span style="float:right">{escape(data.daily[-1].day)}'
        f" &middot; peak {_money(peak)}</span></div>"
    )


def _change_text(change: float | None, comparison: str) -> str:
    if change is None:
        return "no prior data"
    arrow = "▲" if change >= 0 else "▼"
    return f"{arrow} {abs(change) * 100:.1f}% {comparison}"


def _hero_cell(
    label: str, window, extra: str = "", change: float | None = None, comparison: str = ""
) -> str:
    delta = f'<div class="hdelta">{escape(_change_text(change, comparison))}</div>' if comparison else ""
    return (
        f'<td><div class="hlabel">{escape(label)}{extra}</div>'
        f'<div class="hvalue">{_money(window.cost)}</div>'
        f'<div class="hsub">{window.messages:,} msgs</div>'
        f"{delta}</td>"
    )


def _meter(label: str, amount: float, share: float, colour: str, last: bool = False) -> str:
    """One labelled amount over a clamped proportional bar.

    The printed percentage is the true share; only the CSS width is clamped,
    so an out-of-range value is visible on the page rather than silently
    rounded into the chrome.
    """
    width = max(0.0, min(100.0, share * 100))
    margin = "4px 0 0 0" if last else "4px 0 6px 0"
    return (
        f"{escape(label)} {_money(amount)} ({_pct(share)})"
        f'<div class="bar" style="margin:{margin}">'
        f'<div class="fill" style="width:{width:.1f}%;background:{colour}"></div></div>'
    )


def _split_band(data: DashboardData) -> str:
    total = data.main_cost + data.subagent_cost
    if total <= 0:
        return ""
    return (
        '<div class="bandtitle">DELEGATION</div>'
        + _meter("main", data.main_cost, data.main_cost / total, "#58a6ff")
        + _meter("subagents", data.subagent_cost, data.subagent_share, "#d29922", last=True)
    )


def _source_band(data: DashboardData) -> str:
    """Which Claude surface the spend came from.

    Only surfaces that persist token counts locally can appear here — Claude
    Code and Desktop's Cowork mode. Desktop chat, Claude in Chrome and
    claude.ai bill server-side and write no usage to disk, so their absence
    is a property of the data, not an omission by this panel.
    """
    if not data.by_source:
        return ""
    parts = ['<div class="bandtitle">BY SOURCE</div>']
    for index, bar in enumerate(data.by_source):
        parts.append(
            _meter(
                bar.label,
                bar.cost,
                bar.share,
                SOURCE_COLOURS[index % len(SOURCE_COLOURS)],
                last=(index == len(data.by_source) - 1),
            )
        )
    return "".join(parts)


def _bands(data: DashboardData) -> str:
    """Lay the bands side by side.

    Stacking them would cost ~60px of a 748px budget and push the daily
    chart off the iPad's first screen, so an absent band collapses the row
    to a single full-width cell rather than leaving a hole.
    """
    cells = [band for band in (_split_band(data), _source_band(data)) if band]
    if not cells:
        return ""
    span = ' colspan="2"' if len(cells) == 1 else ""
    row = "".join(f'<td class="band"{span}>{cell}</td>' for cell in cells)
    return f'<table class="bands"><tbody><tr>{row}</tr></tbody></table>'


def _plan_comparison(data: DashboardData) -> str:
    """Month-to-date api-equivalent cost against what the plan actually costs.

    With no subscription the ratio is meaningless — dividing by zero would be
    the least of it, since the api-equivalent figure *is* the bill in that
    case. Say that instead of printing a 0.0x multiple.
    """
    if data.max_plan_monthly_usd <= 0:
        return (
            f"vs {escape(data.plan_label)} &nbsp; "
            f"{_money(data.month_to_date.cost)} api-equivalent &middot; "
            "no subscription to compare against"
        )
    return (
        f"vs {escape(data.plan_label)} &nbsp; {_money(data.month_to_date.cost)} "
        f"api-equivalent / {_money(data.max_plan_monthly_usd)} actual = "
        f'<span class="mult">{data.effective_multiple:.1f}&times;</span> effective'
    )


def render(data: DashboardData, *, warning: str | None = None, refresh_seconds: int = 30) -> str:
    banner = f'<div class="warn">{escape(warning)}</div>' if warning else ""

    unpriced = ""
    if data.unpriced_models:
        names = ", ".join(escape(name) for name in data.unpriced_models)
        unpriced = (
            f'<div class="note">{len(data.unpriced_models)} unpriced '
            f"model(s), counted but not costed: {names}</div>"
        )

    month_text = _change_text(data.month_change, "vs same point last month")

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<meta name="viewport" content="width=1024, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>Claude Tokens</title>
<style>{CSS}</style>
</head><body>
<div class="titlebar">CLAUDE TOKENS<span class="when">{escape(data.generated_at)}</span></div>
{banner}
<table class="hero"><tr>
{_hero_cell("TODAY", data.today, change=data.day_change, comparison="vs yesterday")}
{_hero_cell("7 DAYS", data.last_7_days, change=data.week_change, comparison="vs prior 7 days")}
{_hero_cell("MONTH TO DATE", data.month_to_date, change=data.month_change, comparison="vs same point last month")}
{_hero_cell("ALL TIME", data.all_time, f" &middot; {data.active_days} ACTIVE DAYS")}
</tr></table>
<div class="maxrow">
<span class="mom">{escape(data.prev_month_label)} {_money(data.prev_month_cost)}
 &middot; {month_text}</span>
{_plan_comparison(data)}
</div>
{_bands(data)}
<table class="grid"><tbody>
<tr>
<td><h2>WHERE THE MONEY GOES &middot; ALL TIME</h2>{_rows(data.money)}
<div class="note">cache hit rate {_pct(data.cache_hit_rate)}</div></td>
<td><h2>BY MODEL &middot; ALL TIME</h2>{_rows(data.by_model)}
<div class="note">avg {_money(data.avg_cost_per_message)}/msg &middot;
{_money(data.avg_cost_per_session)}/session</div>{unpriced}</td>
</tr>
<tr>
<td><h2>BY PROJECT &middot; ALL TIME</h2>{_rows(data.by_project)}</td>
<td><h2>BY SKILL &middot; ALL TIME</h2>{_rows(data.by_skill)}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr><td><h2>TOP SESSIONS &middot; ALL TIME</h2>{_session_rows(data.top_sessions)}</td></tr>
<tr><td><h2>DAILY &middot; LAST {len(data.daily)} DAYS</h2>{_daily(data)}</td></tr>
</tbody></table>
</body></html>
"""
