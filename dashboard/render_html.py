"""Renders DashboardData as one self-contained HTML document.

Targets iOS 5.1.1 Safari: table layout, px units, no flexbox, no CSS grid,
no custom properties, no JavaScript. Pure — no I/O, no clock reads.
"""

from __future__ import annotations

from html import escape

from . import ranges
from .models import Bar, DashboardData, RangeView

BAR_COLOURS = ("#58a6ff", "#d29922", "#3fb950", "#8b949e", "#a371f7")
#: Hard cap on how much of a session title reaches the markup. This is a
#: payload guard, not a display cap — the browser decides what is visible.
#: Real titles are whole prompts: measured at 265 to 4,646 characters, which
#: is several KB of text per refresh that is never shown, and it carries the
#: full prompt into the page source when the screen displays ~30 characters
#: of it. 200 is far more than any column will ever render and small enough
#: that the tail stops mattering.
SESSION_TITLE_CAP = 200
SOURCE_COLOURS = ("#3fb950", "#a371f7", "#58a6ff", "#d29922", "#8b949e")
DAILY_CHART_HEIGHT_PX = 78

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0e1116; color:#e6edf3;
       font-family:"Helvetica Neue", Helvetica, Arial, sans-serif;
       font-size:16px; padding:7px; }
a { color:inherit; text-decoration:none; }
.titlebar { font-size:14px; letter-spacing:2px; color:#8b949e;
            border-bottom:1px solid #30363d; padding-bottom:6px; margin-bottom:8px; }
.titlebar .when { float:right; letter-spacing:0; }
.warn { background:#3d1d1d; border:1px solid #f85149; color:#ffa198;
        padding:6px 10px; margin-bottom:8px; font-size:13px; }
table.hero { width:100%; table-layout:fixed; border-collapse:collapse; margin-bottom:4px; }
table.hero td { vertical-align:top; }
.hlabel { font-size:11px; letter-spacing:1.5px; color:#8b949e; }
.hvalue { font-size:40px; font-weight:bold; line-height:46px; }
.hsub { font-size:11px; color:#8b949e; }
.hdelta { font-size:11px; color:#8b949e; }
.maxrow { background:#161b22; border:1px solid #30363d;
          padding:6px 10px; margin-bottom:6px; font-size:13px; color:#8b949e; }
.maxrow .mult { font-size:22px; font-weight:bold; color:#3fb950; }
.maxrow .mom { float:right; }
/* border-spacing matches table.grid so the bands line up with the panels
   below them; margin-bottom keeps the vertical rhythm the single full-width
   delegation band used to have. */
table.ranges { width:100%; table-layout:fixed; border-collapse:separate;
               border-spacing:6px 0; margin:0 0 4px 0; }
td.rangecell { padding:0; }
a.range { display:block; background:#161b22; border:1px solid #30363d;
          padding:5px 0; text-align:center; font-size:11px; letter-spacing:1.5px;
          color:#8b949e; }
a.range.on { background:#1f6feb; border-color:#1f6feb; color:#ffffff; }
table.bands { width:100%; table-layout:fixed; border-collapse:separate;
              border-spacing:6px 0; margin:0 0 6px 0; }
td.band { background:#161b22; border:1px solid #30363d; padding:6px 10px;
          font-size:13px; color:#8b949e; vertical-align:top; }
.bandtitle { color:#e6edf3; letter-spacing:1.5px; font-size:11px; margin-bottom:4px; }
table.grid { width:100%; table-layout:fixed; border-collapse:separate; border-spacing:5px; }
table.grid > tbody > tr > td { background:#161b22; border:1px solid #30363d;
                               padding:6px 9px; vertical-align:top; }
h2 { font-size:10px; letter-spacing:1.5px; color:#8b949e;
     font-weight:normal; margin-bottom:4px; }
table.rows { width:100%; border-collapse:collapse; }
table.srows { width:100%; table-layout:fixed; border-collapse:collapse; }
table.srows td { font-size:13px; padding:2px 0; white-space:nowrap;
                 overflow:hidden; }
/* Truncation is the browser's job: a fixed character cap cuts the same
   number of letters whatever the column is actually worth, and gets it
   wrong on every width but the one it was tuned for. */
td.stitle { text-overflow:ellipsis; padding-right:8px; }
table.rows td { font-size:13px; padding:2px 0; white-space:nowrap; overflow:hidden; }
td.amt { text-align:right; width:80px; }
td.pct { text-align:right; width:52px; color:#8b949e; }
td.barcell { padding-left:8px; }
.bar { background:#21262d; height:9px; }
.fill { height:9px; }
.note { font-size:11px; color:#8b949e; margin-top:4px; }
table.daily { width:100%; table-layout:fixed; border-collapse:collapse; }
table.daily td { vertical-align:bottom; text-align:center; padding:0 1px; }
.chartwrap { position:relative; }
.gridline { position:absolute; left:0; right:0; height:0; z-index:2;
            border-top:1px solid #30363d; }
.gridline .glabel { position:absolute; right:0; top:1px; font-size:10px;
                    color:#8b949e; background:#161b22; padding:0 0 0 4px; }
table.daily { position:relative; z-index:1; }
.col { background:#238636; }
.col.today { background:#58a6ff; }
.titlebar .live { float:right; letter-spacing:0; margin-right:14px; color:#e6edf3; }
.dscale { font-size:10px; color:#8b949e; }

/* The footprint strip. Four line drawings across the bottom right, each
   animated by cross-fading a few hand-drawn frames — a flipbook.

   Deliberately opacity-only. CSS transforms on SVG children are unreliable
   on the browser this page targets, and SMIL is worse; opacity is the one
   property every animating browser since about 2010 agrees on. If the
   animation does not run at all, every frame after the first stays hidden
   and you are left with a static line drawing, which is a perfectly good
   outcome and the reason this approach was chosen over a GIF. */
/* Four equal cards, matching the panel chrome above them so the row reads
   as part of the page rather than as a badge strip bolted on. */
table.fpstrip { width:100%; table-layout:fixed; border-collapse:separate;
                border-spacing:5px; margin-top:1px; }
td.fpcard { background:#161b22; border:1px solid #30363d; padding:7px 4px 6px 4px;
            text-align:center; vertical-align:top; }
/* The category leads at 12px and the figure sits under it at 16px, so the
   card reads heading-then-number while the number stays the bigger thing.
   Letter-spacing follows the uppercase category, not the figure — spaced
   digits read as a serial number rather than as a quantity. */
.fpvalue { font-size:12px; letter-spacing:1.5px; color:#e6edf3; margin-top:3px;
           white-space:nowrap; }
.fplabel { font-size:16px; color:#8b949e; margin-top:1px; white-space:nowrap; }
.fpnote { font-size:10px; color:#6e7681; text-align:center; margin-top:3px; }
.fpicon { display:block; margin:0 auto; }
.fpf { opacity:0; }
.fpf1 { opacity:1; -webkit-animation:fpcycle 1.5s steps(1,end) infinite;
        animation:fpcycle 1.5s steps(1,end) infinite; }
.fpf2 { -webkit-animation:fpcycle 1.5s steps(1,end) -1.0s infinite;
        animation:fpcycle 1.5s steps(1,end) -1.0s infinite; }
.fpf3 { -webkit-animation:fpcycle 1.5s steps(1,end) -0.5s infinite;
        animation:fpcycle 1.5s steps(1,end) -0.5s infinite; }
@-webkit-keyframes fpcycle { 0% { opacity:1 } 33% { opacity:1 }
                             34% { opacity:0 } 100% { opacity:0 } }
@keyframes fpcycle { 0% { opacity:1 } 33% { opacity:1 }
                     34% { opacity:0 } 100% { opacity:0 } }
@media (prefers-reduced-motion: reduce) {
  .fpf1, .fpf2, .fpf3 { -webkit-animation:none; animation:none; }
  .fpf1 { opacity:1 } .fpf2, .fpf3 { opacity:0 }
}
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
    out = ['<table class="srows">']
    for bar in bars:
        # Title first, amount right-aligned — the same shape as every other
        # breakdown panel, which reads label-then-money.
        # Cut well beyond anything the column can show, so CSS still owns
        # the visible truncation and stays responsive.
        title = bar.label[:SESSION_TITLE_CAP]
        out.append(
            "<tr>"
            f'<td class="stitle">{escape(title)}</td>'
            f'<td class="amt">{_money(bar.cost)}</td>'
            "</tr>"
        )
    out.append("</table>")
    return "".join(out)


#: Multipliers a value axis is allowed to land on, so labels read as round
#: money. Finer than the usual 1/2/5 set: with only two lines, a coarse set
#: leaves the tallest bar at half height and wastes the chart.
_AXIS_STEPS = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)


def _axis_step(peak: float) -> float:
    """Smallest round step whose double covers the peak.

    The chart shows two lines, so the top of the scale is 2 x step and the
    step has to be at least half the peak. A $86 peak gives $50, so the axis
    reads $50 / $100 and the tallest bar reaches 86% of the chart.
    """
    import math

    target = peak / 2
    magnitude = 10 ** math.floor(math.log10(target)) if target > 0 else 1
    for multiplier in _AXIS_STEPS:
        candidate = multiplier * magnitude
        if target <= candidate:
            return candidate
    return 10 * magnitude


def _axis_label(value: float) -> str:
    """Axis labels drop the cents unless the whole scale lives below a
    dollar — "$50" reads as a scale, "$50.00" reads as a measurement."""
    if value >= 10:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.1f}".rstrip("0").rstrip(".")
    return f"${value:,.2f}"


def _daily(view: RangeView, today_day: str) -> str:
    if not view.daily:
        return '<div class="note">no daily history yet</div>'
    peak = max(point.cost for point in view.daily) or 1.0
    # Bars are measured against the top gridline, not against the peak, so
    # the axis labels mean what they say.
    step = _axis_step(peak)
    top = step * 2

    lines = "".join(
        f'<div class="gridline" style="bottom:{round(fraction * DAILY_CHART_HEIGHT_PX)}px">'
        f'<span class="glabel">{_axis_label(value)}</span></div>'
        for value, fraction in ((step, 0.5), (top, 1.0))
    )

    cells = []
    for point in view.daily:
        height = max(1, min(DAILY_CHART_HEIGHT_PX, round(point.cost / top * DAILY_CHART_HEIGHT_PX)))
        # One bar out of thirty is the only one still moving; without a
        # colour it takes a squint to find.
        today = " today" if point.day and point.day == today_day else ""
        cells.append(
            f'<td><div class="col{today}" style="height:{height}px"></div></td>'
        )
    return (
        f'<div class="chartwrap" style="height:{DAILY_CHART_HEIGHT_PX}px">{lines}'
        f'<table class="daily" style="height:{DAILY_CHART_HEIGHT_PX}px">'
        f"<tr>{''.join(cells)}</tr></table></div>"
        f'<div class="dscale">{escape(view.daily[0].day)}'
        f'<span style="float:right">{escape(view.daily[-1].day)}'
        f" &middot; peak {_money(peak)}</span></div>"
    )


def _daily_heading(view: RangeView) -> str:
    """What the chart is actually showing.

    This used to read `LAST {len(daily)} DAYS`, which counted the days that
    carried spend and then presented that count as a window: a 30-day range
    with three active days announced "LAST 3 DAYS", and every range announced
    "LAST 1 DAYS" on a history one day long. The window and the bar count are
    two different facts, so the heading now states both.
    """
    days = view.active_day_count
    noun = "DAY" if days == 1 else "DAYS"
    return f"DAILY &middot; {escape(view.label)} &middot; {days} ACTIVE {noun}"


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


def _split_band(view: RangeView) -> str:
    total = view.main_cost + view.subagent_cost
    if total <= 0:
        return ""
    return (
        f'<div class="bandtitle">DELEGATION &middot; {escape(view.label)}</div>'
        + _meter("main", view.main_cost, view.main_cost / total, "#58a6ff")
        + _meter("subagents", view.subagent_cost, view.subagent_share, "#d29922", last=True)
    )


def _source_band(view: RangeView) -> str:
    """Which Claude surface the spend came from.

    Only surfaces that persist token counts locally can appear here — Claude
    Code and Desktop's Cowork mode. Desktop chat, Claude in Chrome and
    claude.ai bill server-side and write no usage to disk, so their absence
    is a property of the data, not an omission by this panel.
    """
    if not view.by_source:
        return ""
    parts = [f'<div class="bandtitle">BY SOURCE &middot; {escape(view.label)}</div>']
    for index, bar in enumerate(view.by_source):
        parts.append(
            _meter(
                bar.label,
                bar.cost,
                bar.share,
                SOURCE_COLOURS[index % len(SOURCE_COLOURS)],
                last=(index == len(view.by_source) - 1),
            )
        )
    return "".join(parts)


def _bands(view: RangeView) -> str:
    """Lay the bands side by side.

    Stacking them would cost ~60px of a 748px budget and push the daily
    chart off the iPad's first screen, so an absent band collapses the row
    to a single full-width cell rather than leaving a hole.

    Both bands name the range. They are as range-scoped as the panels below
    them and used to be the only figures on the page that did not say so.
    """
    cells = [band for band in (_split_band(view), _source_band(view)) if band]
    if not cells:
        return ""
    span = ' colspan="2"' if len(cells) == 1 else ""
    row = "".join(f'<td class="band"{span}>{cell}</td>' for cell in cells)
    return f'<table class="bands"><tbody><tr>{row}</tr></tbody></table>'


def _pace_clause(data: DashboardData) -> str:
    """The month-end projection, or nothing.

    Names the method: two defensible ones disagree, by 1% on the reference
    history but by more in a month containing a real change of behaviour.
    Kept short — the plan band is one line, and a longer clause wrapped it
    onto a second, growing the page by 16px.
    """
    if not data.on_pace:
        return ""
    return f" &middot; on pace {_money(data.on_pace)} (7-day rate)"


def _plan_comparison(data: DashboardData) -> str:
    """Month-to-date api-equivalent cost against what the plan actually costs.

    With no subscription the ratio is meaningless — dividing by zero would be
    the least of it, since the api-equivalent figure *is* the bill in that
    case. Say that instead of printing a 0.0x multiple.
    """
    if data.plan.monthly_usd <= 0:
        return (
            f"vs {escape(data.plan.label)} &nbsp; "
            f"{_money(data.month_to_date.cost)} api-equivalent &middot; "
            f"no subscription to compare against{_pace_clause(data)}"
        )
    return (
        f"vs {escape(data.plan.label)} &nbsp; {_money(data.month_to_date.cost)} "
        f"api-equivalent / {_money(data.plan.monthly_usd)} actual = "
        f'<span class="mult">{data.effective_multiple:.1f}&times;</span> effective'
        f"{_pace_clause(data)}"
    )


def _one_sig_fig(value: float, unit: str, small_unit: str, factor: float) -> str:
    """A quantity at one significant figure, in whichever unit reads better.

    One figure is not a stylistic choice. The underlying model is good to
    about an order of magnitude, and a second digit would be inventing
    precision that no published source supports.
    """
    if value <= 0:
        return f"0 {unit}"
    if value < 1:
        value, unit = value * factor, small_unit
    if value >= 100:
        return f"{value:,.0f} {unit}"
    digits = 0 if value >= 10 else 1
    return f"{value:.{digits}f} {unit}"


#: Four line drawings for the footprint strip. Each is a fixed outline plus
#: three frames of the one moving part, cross-faded by the .fpf* classes.
#: Drawn on a 24x24 grid, stroked not filled, so they stay legible at 22px on
#: a screen being read from across a room.
_ICONS = {
    # Cooling towers under a flickering bolt. The blank third frame is what
    # makes it read as electrical rather than as something merely pulsing.
    "energy": (
        "#d29922",
        '<path d="M3 21h18M5 21l1.2-8h3.6L11 21M13.5 21l.9-6h2.7l.9 6"/>'
        '<path d="M6.4 13c.6-1 2.4-1 3 0M14.4 15c.5-.8 1.9-.8 2.4 0"/>',
        (
            '<path d="M19 2l-2.6 4.4h2.4L16.4 11"/>',
            '<path d="M19.4 2.4l-2.2 4h2.1l-2.2 3.8"/>',
            "",
        ),
    ),
    # A tap, running. Two streams wave in opposite phase and the whole pair
    # steps down a third of a wavelength per frame, which reads as flow
    # without needing a transform. The frames start progressively lower, so
    # the gap that opens under the spout reads as the stream breaking up.
    "water": (
        "#58a6ff",
        '<path d="M2.4 4.6v6.6"/>'
        '<path d="M2.4 7.4h9v3.6"/>'
        '<path d="M9.9 11h3"/>'
        '<path d="M6.2 7.4V5.2M4.4 5.2h3.6M6.2 5.2V4"/>',
        (
            '<path d="M10.7 10.4c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3'
            'M12.1 10.4c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3"/>',
            '<path d="M10.7 11.5c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3'
            'M12.1 11.5c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3"/>',
            '<path d="M10.7 12.6c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3'
            'M12.1 12.6c-.5 1.1.5 2.2 0 3.3s.5 2.2 0 3.3s.5 2.2 0 3.3"/>',
        ),
    ),
    # A cow, side on, emitting. Methane from livestock is a real line in
    # carbon accounting, so the joke is at least on topic. The spot on the
    # flank is doing most of the work of saying "cow" at 22 pixels.
    "carbon": (
        "#8b949e",
        # A cow's head, face on. The side view read as a hippo at this size —
        # thin legs and a small head vanish, while ears, horns and a muzzle
        # survive. The puff comes from the mouth rather than the other end
        # because roughly 95% of cattle methane is belched, not farted, and
        # the accurate version is no less funny.
        '<path d="M6 9.8c0-2.1 2.2-3.5 5-3.5s5 1.4 5 3.5v2.3c0 2.7-2.2 4.7-5 4.7'
        's-5-2-5-4.7z"/>'
        '<path d="M6.2 9.4c-1.9-1.1-3.5-.9-3.9.2s.8 2.1 2.7 2.3"/>'
        '<path d="M15.8 9.4c1.9-1.1 3.5-.9 3.9.2s-.8 2.1-2.7 2.3"/>'
        '<path d="M7.7 6.9c-.7-1.3-.5-2.3.3-2.8M14.3 6.9c.7-1.3.5-2.3-.3-2.8"/>'
        '<path d="M9.1 10.3a.5 .5 0 1 0 .1 0M12.9 10.3a.5 .5 0 1 0 .1 0"/>'
        '<path d="M8.5 13.7c0-1 1.1-1.7 2.5-1.7s2.5.7 2.5 1.7-1.1 1.8-2.5 1.8'
        "-2.5-.8-2.5-1.8z\"/>"
        '<path d="M10 13.9a.35 .35 0 1 0 .1 0M12 13.9a.35 .35 0 1 0 .1 0"/>',
        (
            '<path d="M15.6 17.6a.85 .85 0 1 0 .1 0"/>',
            '<path d="M17.6 17.9a1.35 1.35 0 1 0 .1 0"/>',
            '<path d="M19.9 17.5a1.95 1.95 0 1 0 .1 0"/>',
        ),
    ),
    # A kettle with steam climbing off the spout.
    "kettle": (
        "#3fb950",
        '<path d="M6.4 12.6h9.2v5.2a2 2 0 0 1-2 2H8.4a2 2 0 0 1-2-2z"/>'
        '<path d="M8.6 12.6a2.6 2.6 0 0 1 4.8 0M15.6 14.2l2.8-1.8"/>',
        (
            '<path d="M18.6 11c.9-.6.1-1.4 1-2"/>',
            '<path d="M18.6 10c.9-.6.1-1.4 1-2M17 11.4c.7-.5.1-1.1.8-1.6"/>',
            '<path d="M18.6 9c.9-.6.1-1.4 1-2M17 10.4c.7-.5.1-1.1.8-1.6"/>',
        ),
    ),
}


#: Rendered size of a footprint drawing. Large enough to be readable as a
#: picture from across a room rather than as a smudge next to a number —
#: which is what 22px turned out to be.
ICON_PX = 46


def _icon(kind: str) -> str:
    colour, outline, frames = _ICONS[kind]
    animated = "".join(
        f'<g class="fpf fpf{index}">{frame}</g>'
        for index, frame in enumerate(frames, start=1)
        if frame
    )
    # Stroke width is in user units, so it scales with the box. 1.15 at 46px
    # renders about 2.2 device px — a drawn line rather than a slab.
    return (
        f'<svg class="fpicon" width="{ICON_PX}" height="{ICON_PX}" '
        'viewBox="0 0 24 24" '
        f'fill="none" stroke="{colour}" stroke-width="1.15" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true">'
        f"{outline}{animated}</svg>"
    )


def _kettles(boils: float) -> str:
    """The equivalence, at the same one significant figure as everything else
    beside it. "212 kettles" would be three, and would quietly claim the
    model is a hundred times more precise than it is."""
    if boils < 1:
        return "half a kettle boiled" if boils >= 0.25 else "less than a kettle boiled"
    if boils < 10:
        rounded = round(boils)
    else:
        magnitude = 10 ** (len(str(int(boils))) - 1)
        rounded = round(boils / magnitude) * magnitude
    return f"{rounded:,} kettle{'' if rounded == 1 else 's'} boiled"


def _footprint_note(view: RangeView) -> str:
    """The modelled environmental cost of the tokens in this range.

    Two lines, deliberately: the figures, then what they are worth. The
    caveat is inline rather than behind a link because there is no tooltip on
    the browser this targets, and a number this uncertain shown bare would be
    a worse lie than showing nothing.

    Framings this is not allowed to use, because the evidence does not
    support them: bottles of water, flights, trees, offsets, comparisons
    between models, or anything with two significant figures.
    """
    fp = view.footprint
    if not fp:
        return ""
    boils = fp.kettle_boils
    items = (
        ("energy", _one_sig_fig(fp.kwh, "kWh", "Wh", 1000), "ELECTRICITY"),
        ("water", _one_sig_fig(fp.litres, "L", "mL", 1000), "WATER"),
        ("carbon", _one_sig_fig(fp.g_co2e / 1000, "kg CO2e", "g CO2e", 1000), "CARBON"),
        ("kettle", _kettles(boils).replace(" boiled", ""), "SAME AS BOILING"),
    )
    # The category leads and the figure sits under it in small type. That
    # ordering suits a number with an order-of-magnitude error bar: the card
    # says what is being counted first, and how much second.
    cells = "".join(
        f'<td class="fpcard">{_icon(kind)}'
        f'<div class="fpvalue">{label}</div>'
        f'<div class="fplabel">{value}</div></td>'
        for kind, value, label in items
    )
    return (
        f'<table class="fpstrip"><tbody><tr>{cells}</tr></tbody></table>'
        '<div class="fpnote">modelled from published research, not measured '
        "&middot; order of magnitude only &middot; excludes training</div>"
    )


def _cache_note(view: RangeView) -> str:
    """What caching bought, rather than how often it hit.

    Hit rate is saturated on any real history — measured at 99.68%-100.00%
    across eight consecutive weeks — so it is a constant dressed as a metric.
    The saving is the number that moves.

    The qualifier is not optional. "caching saved $15,603" alone reads as
    money that was once in play; it never was. There is no tooltip on
    iOS 5.1.1, so the counterfactual has to sit in the visible text.
    """
    if view.cache_read_tokens <= 0:
        return "no cache reads yet"
    if view.cache_saved <= 0:
        # Reads happened, but on models with no rate — claiming "no cache
        # reads yet" would deny something that is on the page above.
        return "cache reads on unpriced models only"
    return f"caching saved {_money(view.cache_saved)} &middot; same tokens at uncached rates"


def _live(data: DashboardData) -> str:
    """Burn rate and idle time — the only figures that can differ between two
    consecutive refreshes. Absent entirely when today is too quiet to divide
    by, rather than rendering a misleading $0.00/hr."""
    if data.burn_rate_hourly is None:
        return ""
    idle = f" &middot; idle {data.idle_minutes}m" if data.idle_minutes is not None else ""
    return f'<span class="live">{_money(data.burn_rate_hourly)}/hr{idle}</span>'


def _range_selector(view: RangeView, base_path: str) -> str:
    """A row of ordinary links, one per range.

    Deliberately anchors rather than a <select> or clickable cards: this page
    carries no JavaScript, so a dropdown would need a visible submit button
    and cards would give no affordance that they are clickable. Links are
    understood by every browser ever shipped, and the current one is marked
    server-side rather than with :hover or :focus styling.
    """
    cells = []
    for entry in ranges.CATALOGUE:
        current = " on" if entry.key == view.key else ""
        href = (
            base_path
            if entry.key == ranges.DEFAULT.key
            else f"{base_path}?{ranges.QUERY_KEY}={entry.key}"
        )
        cells.append(
            f'<td class="rangecell">'
            f'<a class="range{current}" href="{escape(href)}">{escape(entry.label)}</a></td>'
        )
    return f'<table class="ranges"><tbody><tr>{"".join(cells)}</tr></tbody></table>'


def render(
    data: DashboardData,
    *,
    warning: str | None = None,
    refresh_seconds: int = 30,
    base_path: str = "",
) -> str:
    banner = f'<div class="warn">{escape(warning)}</div>' if warning else ""
    view = data.scoped

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
<div class="titlebar">CLAUDE TOKENS<span class="when">{escape(data.generated_at)}</span>{_live(data)}</div>
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
{_bands(view)}
{_range_selector(view, base_path)}
<table class="grid"><tbody>
<tr>
<td><h2>WHERE THE MONEY GOES &middot; {escape(view.label)}</h2>{_rows(view.money)}
<div class="note">{_cache_note(view)}</div></td>
<td><h2>BY MODEL &middot; {escape(view.label)}</h2>{_rows(view.by_model)}
<div class="note">avg {_money(view.avg_cost_per_message)}/msg &middot;
{_money(view.avg_cost_per_session)}/session</div>{unpriced}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr>
<td><h2>BY PROJECT &middot; {escape(view.label)}</h2>{_rows(view.by_project)}</td>
<td><h2>BY SKILL &middot; {escape(view.label)} &middot; ATTRIBUTED</h2>{_rows(view.by_skill)}</td>
<td><h2>TOP SESSIONS &middot; {escape(view.label)}</h2>{_session_rows(view.top_sessions)}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr><td><h2>{_daily_heading(view)}</h2>{_daily(view, data.today_day)}</td></tr>
</tbody></table>
{_footprint_note(view)}
</body></html>
"""
