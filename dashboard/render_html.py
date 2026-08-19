"""Renders DashboardData as one self-contained HTML document.

Targets iOS 5.1.1 Safari: table layout, px units, no flexbox, no CSS grid,
no custom properties, no JavaScript. Pure — no I/O, no clock reads.
"""

from __future__ import annotations

from html import escape

from . import ranges, scan
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
MODE_COLOURS = ("#58a6ff", "#8b949e", "#d29922", "#3fb950", "#a371f7")
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
/* The right padding is the leaderboard ribbon's width plus its tail. The
   ribbon is fixed 100px down the right edge, which is exactly where the
   month comparison ends, and without the gap it runs underneath and loses
   its last few words. */
.maxrow { background:#161b22; border:1px solid #30363d;
          padding:6px 74px 6px 10px; margin-bottom:6px; font-size:13px;
          color:#8b949e; }
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

/* One card style, shared by the machine-time row and the footprint row so
   the two read as a pair rather than as two bolted-on strips. The label is
   the quiet part and the figure is what carries across a room. */
table.cards { width:100%; table-layout:fixed; border-collapse:separate;
              border-spacing:5px; margin-top:1px; }
td.card { background:#161b22; border:1px solid #30363d; padding:11px 6px 10px 6px;
          text-align:center; vertical-align:top; }
.cardlabel { font-size:12px; letter-spacing:1.5px; color:#8b949e; margin-top:4px;
             white-space:nowrap; }
.cardvalue { font-size:20px; color:#e6edf3; margin-top:2px; white-space:nowrap; }
.cardnote { font-size:10px; color:#6e7681; text-align:center; margin-top:3px; }
/* The machine-time caption sits between the two card rows rather than under
   the last one, so it needs the same chrome to read as part of the stack
   instead of as a stray line. Full width on purpose: it qualifies all four
   cards above it, and boxing it per-card would imply otherwise. */
.cardnote.framed { background:#161b22; border:1px solid #30363d;
                   padding:6px 10px; font-size:11px; margin-top:5px; }

/* All-time leaderboards. A full-width heading rather than a per-panel one:
   the "these ignore the range selector" caveat is true of the whole block,
   and repeating it twelve times would cost more room than the boards. */
.section { background:#161b22; border:1px solid #30363d; padding:6px 10px;
           font-size:11px; letter-spacing:1.5px; color:#e6edf3; margin-top:5px; }
.section .quiet { letter-spacing:0; color:#8b949e; }
table.lrows { width:100%; table-layout:fixed; border-collapse:collapse; }
table.lrows td { font-size:13px; padding:2px 0; white-space:nowrap;
                 overflow:hidden; }
td.rank { width:13px; font-size:11px; color:#6e7681; }
/* Medal colours, which is the entire reason anybody reads a leaderboard.
   Third place is deliberately dimmer than second rather than bronze-brown:
   brown on #161b22 is unreadable from the far side of a room. */
.rank1 { color:#e3b341; }
.rank2 { color:#c9d1d9; }
.rank3 { color:#a06e3b; }
td.lname { text-overflow:ellipsis; padding-right:6px; color:#e6edf3; }
td.lval { width:74px; text-align:right; color:#e6edf3; }
.lnote { color:#6e7681; font-size:10px; }
/* A ragged final row must not stretch the boards that are in it, so the
   empty cells stay in the table and only lose their chrome. */
table.grid > tbody > tr > td.pad { background:transparent; border:0; }

/* The leaderboard ribbon, and the sheet it opens.

   Server-rendered from a query string rather than toggled in the page. There
   is no JavaScript here and pointer-state selectors are banned by the
   compatibility lint, which leaves the CSS :target trick — and that would
   have to hide the boards by default, so a browser without it would have no
   way to reach them at all. A link that asks the server for the same page
   with the sheet open is understood by every browser ever shipped, marks its
   own state, and survives the 30-second reload because the state lives in
   the URL that the reload re-requests. */
/* A maroon band with solid gold edges, forked at the free end. The fork is
   two border triangles rather than a picture: an element with no width or
   height, a solid top and bottom border and a *transparent* left one, is a
   swallowtail — and because the notch is transparent rather than painted in
   the page colour, it works over whatever the ribbon happens to be lying on.
   Two of them stacked, the gold one fractionally larger, give the tail the
   same rim the band has. Border triangles are as old as CSS2 and render on
   everything this page targets. */
a.ribbon { position:fixed; top:100px; right:0; z-index:40;
           width:46px; height:42px;
           background:#7a1520;
           border-top:2px solid #e3b341; border-bottom:2px solid #e3b341;
           border-left:0; border-right:0;
           text-decoration:none; }
/* 6px, not a guess: the band's content box is 38px and the trophy is
   26px, so 6px above leaves 6px below and the cup sits centred. The
   `auto` either side is what centres it horizontally. */
a.ribbon .rtrophy { display:block; margin:6px auto 0 auto; }
/* Behind: the gold rim, spanning the band's whole outer height. In front:
   the maroon, spanning its content height. Both halves of each are half that
   height, because a border triangle's height is its top plus its bottom.

   The numbers only read correctly against the border-box reset at the top of
   this stylesheet: `height:42px` on the band is the OUTER height, so the rim
   is 21+21 = 42 and the content it encloses is 42 - 2 - 2 = 38, hence the
   maroon at 19+19. Deriving them as if the box were content-box makes the
   rim 46px, and it hangs 4px below the band — which is exactly the bug this
   replaced, invisible at page scale and obvious the moment anything is
   measured.

   A diagonal cannot match a horizontal on both counts: holding the rim at 2px
   where they meet makes it read a little under 2px along the slope, and
   holding it at 2px along the slope would put a step back at the join. The
   join is the part the eye lands on, so that is the one that is kept honest.
   Both sit to the left of the band, hence the negative placement. */
a.ribbon .tailedge { position:absolute; left:-19px; top:-2px;
                     width:0; height:0; border-left:19px solid transparent;
                     border-top:21px solid #e3b341;
                     border-bottom:21px solid #e3b341; }
a.ribbon .tail { position:absolute; left:-17px; top:0;
                 width:0; height:0; border-left:17px solid transparent;
                 border-top:19px solid #7a1520;
                 border-bottom:19px solid #7a1520; }
/* Translucent rather than opaque, so the dashboard stays faintly visible
   underneath and the sheet reads as sitting on top of it rather than as a
   different page. */
.overlay { position:fixed; top:0; left:0; width:100%; height:100%; z-index:60;
           background:rgba(1,4,9,0.93); }
a.scrim { position:absolute; top:0; left:0; width:100%; height:100%;
          text-decoration:none; }
.sheet { position:absolute; top:34px; left:26px; right:26px;
         background:#0d1117; border:1px solid #30363d; padding:9px 11px 11px 11px; }
.sheethead { font-size:11px; letter-spacing:1.5px; color:#e6edf3;
             padding-bottom:7px; }
.sheethead .quiet { letter-spacing:0; color:#8b949e; }
a.close { float:right; color:#8b949e; text-decoration:none; font-size:10px;
          letter-spacing:1.5px; border:1px solid #30363d; padding:2px 9px; }

/* Placings. The medal is a coloured disc rather than a drawing: at 17px a
   drawn medal is a smudge, and thirty-six inline SVGs would cost more page
   than the whole rest of the section. */
.bicon { vertical-align:-4px; margin-right:5px; }
td.rank { width:24px; }
.medal { position:relative; display:inline-block; width:17px; height:17px;
         line-height:17px; border-radius:9px; text-align:center;
         font-size:10px; color:#0d1117; }
.medal1 { background:#e3b341; }
.medal2 { background:#c9d1d9; }
.medal3 { background:#c08040; }
.medal .num { position:relative; }
/* First place catches the light every few seconds. Opacity only, same as the
   card icons, and for the same reason: if it never runs you are left with a
   plain gold disc, which is a perfectly good outcome. */
.gleam { position:absolute; left:0; top:0; width:17px; height:17px;
         border-radius:9px; background:#fff6cc; opacity:0;
         -webkit-animation:gleam 2.6s ease-in-out infinite;
                 animation:gleam 2.6s ease-in-out infinite; }
@-webkit-keyframes gleam { 0%,72%,100% { opacity:0 } 84% { opacity:0.85 } }
@keyframes gleam { 0%,72%,100% { opacity:0 } 84% { opacity:0.85 } }
td.barrow { padding:0 0 5px 0; }
td.barrow .bar, td.barrow .fill { height:6px; }

/* Card icons. Each is a fixed outline plus up to three frames of the one
   moving part, cross-faded — a flipbook.

   Deliberately opacity-only. CSS transforms on SVG children are unreliable
   on the browser this page targets, and SMIL is worse; opacity is the one
   property every animating browser since about 2010 agrees on. If the
   animation does not run at all, every frame after the first stays hidden
   and you are left with a static line drawing, which is a perfectly good
   outcome and the reason this approach was chosen over a GIF. */
.cardicon { display:block; margin:0 auto; }
.frame { opacity:0; }
.frame1 { opacity:1; -webkit-animation:framecycle 1.5s steps(1,end) infinite;
        animation:framecycle 1.5s steps(1,end) infinite; }
.frame2 { -webkit-animation:framecycle 1.5s steps(1,end) -1.0s infinite;
        animation:framecycle 1.5s steps(1,end) -1.0s infinite; }
.frame3 { -webkit-animation:framecycle 1.5s steps(1,end) -0.5s infinite;
        animation:framecycle 1.5s steps(1,end) -0.5s infinite; }
@-webkit-keyframes framecycle { 0% { opacity:1 } 33% { opacity:1 }
                             34% { opacity:0 } 100% { opacity:0 } }
@keyframes framecycle { 0% { opacity:1 } 33% { opacity:1 }
                     34% { opacity:0 } 100% { opacity:0 } }
@media (prefers-reduced-motion: reduce) {
  .frame1, .frame2, .frame3 { -webkit-animation:none; animation:none; }
  .frame1 { opacity:1 } .frame2, .frame3 { opacity:0 }
}
""".strip()


#: Boards per row in the all-time section. Twelve boards divide evenly into
#: both three and four, so either reads as a full block rather than as a grid
#: with a hole in it. Three won on legibility: at four across a cache-miss
#: reason truncates to "system_change", the longest streak loses its year and
#: a session title gets about twenty characters. The cost is 92px of page.
LEADERBOARD_COLUMNS = 3


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


def _duration(seconds: float) -> str:
    """A span in at most two units. "4d 14h" beats "4d 14h 21m 29s" on a
    display read from across a room, and the third unit is noise at that
    scale anyway."""
    total = int(seconds)
    if total <= 0:
        return "0s"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def _worked_band(view: RangeView) -> str:
    """Where the clock went, for the selected range.

    Measured, not modelled — the one row near the footprint cards that
    carries no error bar worth speaking of. It is *not* time saved: that
    needs a counterfactual nothing in the transcripts can supply, so the
    wording says "machine time" and never "saved".

    The three spans are the three things a second can be: the machine
    working, the machine waiting for you, or nobody there at all. The third
    is not shown — gaps that long are dropped rather than attributed, and a
    figure for "away" would be an artefact of the two thresholds rather
    than a measurement.
    """
    if view.worked_seconds <= 0:
        return ""
    cards = [("worked", "MACHINE TIME", _duration(view.worked_seconds))]
    if view.subagent_worked_seconds > 0:
        cards.append(
            (
                "subagents",
                f"SUBAGENTS &middot; {_pct(view.subagent_worked_share)}",
                _duration(view.subagent_worked_seconds),
            )
        )
    if view.waited_seconds > 0:
        cards.append(("waiting", "WAITING ON YOU", _duration(view.waited_seconds)))
    if view.sessions:
        each = _duration(view.worked_seconds / view.sessions)
        cards.append(
            ("sessions", "SESSIONS", f"{view.sessions:,} &middot; {each} each")
        )

    cells = "".join(
        f'<td class="card">{_icon(kind)}'
        f'<div class="cardlabel">{label}</div>'
        f'<div class="cardvalue">{value}</div></td>'
        for kind, label, value in cards
    )
    return (
        f'<table class="cards" id="worked"><tbody><tr>{cells}</tr></tbody></table>'
        f'<div class="cardnote framed">AI WORKED &middot; {escape(view.label)} '
        "&middot; measured from transcript timestamps &middot; gaps over "
        f"{int(scan.MAX_WORK_GAP_SECONDS / 60)} min counted as idle "
        "&middot; parallel agents summed, so this exceeds wall-clock "
        "&middot; not a measure of time saved</div>"
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
#: three frames of the one moving part, cross-faded by the .frame* classes.
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
    # A clock, hand sweeping. The most literal possible reading of "how
    # long", and rotation is exactly what three opacity frames can fake.
    "worked": (
        "#58a6ff",
        '<path d="M12 3.4a8.6 8.6 0 1 0 .1 0"/>'
        '<path d="M12 4.6v1.7M19.4 12h-1.7M12 19.4v-1.7M4.6 12h1.7"/>'
        '<path d="M12 11.2a.8 .8 0 1 0 .1 0"/>',
        (
            '<path d="M12 12V6.9"/>',
            '<path d="M12 12l4.4 2.6"/>',
            '<path d="M12 12l-4.4 2.6"/>',
        ),
    ),
    # One parent, three children, and a pulse travelling across them —
    # which is what parallel subagents look like from the outside.
    "subagents": (
        "#a371f7",
        '<path d="M10 4.6a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/>'
        '<path d="M12 6.6v2.2M5.5 8.8h13M5.5 8.8v2.2M12 8.8v2.2M18.5 8.8v2.2"/>'
        '<path d="M3.5 13a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/>'
        '<path d="M10 13a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/>'
        '<path d="M16.5 13a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/>',
        (
            '<path d="M5.5 16.6v3.2"/>',
            '<path d="M12 16.6v3.2"/>',
            '<path d="M18.5 16.6v3.2"/>',
        ),
    ),
    # An hourglass running down. Waiting has an obvious icon and there is
    # no reason to be clever about it.
    "waiting": (
        "#d29922",
        '<path d="M6.6 3.6h10.8M6.6 20.4h10.8"/>'
        '<path d="M7.8 3.6c0 4 4.2 5.6 4.2 8.4s-4.2 4.4-4.2 8.4"/>'
        '<path d="M16.2 3.6c0 4-4.2 5.6-4.2 8.4s4.2 4.4 4.2 8.4"/>',
        (
            '<path d="M9 6.2h6"/>',
            '<path d="M10.2 8.4h3.6M12 12.4v3"/>',
            '<path d="M9 18.2h6"/>',
        ),
    ),
    # A terminal window with a blinking cursor. The blank middle frame is
    # the blink — the same trick the power station uses for its bolt.
    "sessions": (
        "#8b949e",
        '<path d="M3.4 5.4h17.2v13.2H3.4z"/>'
        '<path d="M3.4 9h17.2"/>'
        '<path d="M6.6 12.4l2.2 2-2.2 2"/>',
        (
            '<path d="M10.8 16.4h3.6"/>',
            "",
            '<path d="M10.8 16.4h3.6"/>',
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
    # A coffee carafe, brewing. Tapered glass body, lid, handle and a
    # pour lip; the steam above it climbs across the three frames.
    "coffee": (
        "#3fb950",
        '<path d="M8.2 4.6h7.6v1.9H8.2z"/>'
        '<path d="M8.7 6.5L7.3 16.9a2.4 2.4 0 0 0 2.4 2.7h4.6a2.4 2.4 0 0 0 2.4-2.7'
        'L15.3 6.5"/>'
        '<path d="M8 5.2L6.5 4.4"/>'
        '<path d="M16.9 9.6c2.1.5 2.6 1.9 2.6 3s-.7 2.3-2.2 2.7"/>'
        '<path d="M7.7 13.4h8.6"/>',
        (
            '<path d="M10.2 4.1c.9-.7.1-1.5 1-2.2"/>',
            '<path d="M10.2 3.3c.9-.7.1-1.5 1-2.2M13.5 4.1c.7-.5.1-1.2.8-1.7"/>',
            '<path d="M10.2 2.5c.9-.7.1-1.5 1-2.2M13.5 3.3c.7-.5.1-1.2.8-1.7"/>',
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
        f'<g class="frame frame{index}">{frame}</g>'
        for index, frame in enumerate(frames, start=1)
        if frame
    )
    # Stroke width is in user units, so it scales with the box. 1.15 at 46px
    # renders about 2.2 device px — a drawn line rather than a slab.
    return (
        f'<svg class="cardicon" width="{ICON_PX}" height="{ICON_PX}" '
        'viewBox="0 0 24 24" '
        f'fill="none" stroke="{colour}" stroke-width="1.15" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true">'
        f"{outline}{animated}</svg>"
    )


def _coffee_pots(pots: float) -> str:
    """The equivalence, at the same one significant figure as everything else
    beside it. "212" would be three, and would quietly claim the model is a
    hundred times more precise than it is."""
    if pots < 1:
        return "half a pot" if pots >= 0.25 else "less than a pot"
    if pots < 10:
        rounded = round(pots)
    else:
        magnitude = 10 ** (len(str(int(pots))) - 1)
        rounded = round(pots / magnitude) * magnitude
    return f"{rounded:,}"


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
    pots = fp.coffee_pots
    items = (
        ("energy", _one_sig_fig(fp.kwh, "kWh", "Wh", 1000), "ELECTRICITY"),
        ("water", _one_sig_fig(fp.litres, "L", "mL", 1000), "WATER"),
        ("carbon", _one_sig_fig(fp.g_co2e / 1000, "kg CO2e", "g CO2e", 1000), "CARBON"),
        ("coffee", _coffee_pots(pots), "POTS OF COFFEE"),
    )
    # The category leads and the figure sits under it in small type. That
    # ordering suits a number with an order-of-magnitude error bar: the card
    # says what is being counted first, and how much second.
    cells = "".join(
        f'<td class="card">{_icon(kind)}'
        f'<div class="cardlabel">{label}</div>'
        f'<div class="cardvalue">{value}</div></td>'
        for kind, value, label in items
    )
    return (
        f'<table class="cards" id="footprint"><tbody><tr>{cells}</tr></tbody></table>'
        '<div class="cardnote">modelled from published research, not measured '
        "&middot; order of magnitude only &middot; excludes training</div>"
    )


def _miss_note(view: RangeView) -> str:
    """What the cache failing to hold cost.

    The counterpart to the line above it. Caching saved is a counterfactual;
    this is money actually spent re-processing a prefix that should have been
    a cheap read. The figure is the *gap* between write and read rates, not
    the whole charge — the tokens had to be paid for either way.
    """
    if view.cache_missed_tokens <= 0 or view.cache_miss_cost <= 0:
        return ""
    because = f" &middot; mostly {escape(view.cache_miss_reason)}" if view.cache_miss_reason else ""
    return (
        f'<div class="note">cache misses cost {_money(view.cache_miss_cost)} '
        f"&middot; {view.cache_missed_tokens / 1e6:,.0f}M tokens re-read at "
        f"write rates{because}</div>"
    )


def _trivia(view: RangeView) -> str:
    """The shape of the work rather than its size.

    Everything here is measured and none of it is actionable, which is the
    point — it sits in one line at the bottom rather than taking a panel.
    """
    bits = []
    if view.reply_messages:
        bits.append(f"{view.tools_per_reply:.0f} tool calls per reply")
    if view.busiest_hour >= 0:
        bits.append(f"busiest at {view.busiest_hour:02d}:00")
    if view.weekend_share > 0:
        bits.append(f"{_pct(view.weekend_share)} at weekends")
    if view.priciest_message > 0:
        bits.append(f"priciest message {_money(view.priciest_message)}")
    if view.denials:
        bits.append(f"{view.denials:,} tool calls refused")
    if view.injections:
        bits.append(f"{view.injections:,} context injections")
    if not bits:
        return ""
    return (
        '<div class="cardnote framed">'
        + " &middot; ".join(bits)
        + "</div>"
    )


def _origin_note(view: RangeView) -> str:
    """Who reached for the skills.

    Three traces that partition cleanly on real data, so unlike the mode
    panel this one has no unknown bucket to apologise for: a Skill tool call
    the model made, a slash command the person typed, or a subagent that
    inherited the skill from whoever spawned it.
    """
    if not view.by_skill_origin:
        return ""
    parts = " &middot; ".join(
        f"{_pct(bar.share)} {escape(bar.label)}" for bar in view.by_skill_origin
    )
    return f'<div class="note">invoked by: {parts}</div>'


def _skill_runs(runs) -> str:
    """When each skill was called, newest first.

    Same two-column shape as TOP SESSIONS, so the eye reads the pair the
    same way: what it was on the left, what it cost on the right.
    """
    if not runs:
        return '<div class="note">no skill runs yet</div>'
    out = ['<table class="srows">']
    for run in runs:
        when = f"{escape(run.started)} " if run.started else ""
        out.append(
            "<tr>"
            f'<td class="stitle">{when}{escape(run.skill)}'
            f' <span class="pct">{escape(run.origin)}</span></td>'
            f'<td class="amt">{_money(run.cost)}</td>'
            "</tr>"
        )
    out.append("</table>")
    return "".join(out)


#: Rendered size of a board glyph. Small enough to sit inside an 11px
#: heading without pushing the row apart.
BOARD_ICON_PX = 18

#: One drawing per board subject, in the same line-art language as the
#: footprint cards but static: twelve animations competing for attention in
#: one sheet is noise, and the gleam on first place is the moving part that
#: is meant to be noticed.
_BOARD_ICONS = {
    # A coin, edge-on stroke through the middle for the currency mark.
    "coin": (
        "#d29922",
        '<circle cx="12" cy="12" r="8.2"/><path d="M12 7v10"/>'
        '<path d="M14.4 9.4c-.5-.7-1.4-1.1-2.4-1.1-1.4 0-2.5.7-2.5 1.8 0 2.4 4.9 1.3 4.9 3.7 '
        '0 1.1-1.1 1.8-2.5 1.8-1 0-1.9-.4-2.4-1.1"/>',
    ),
    "calendar": (
        "#58a6ff",
        '<path d="M4 6.6h16v13.4H4zM4 10.8h16M8.2 3.6v4M15.8 3.6v4"/>'
        '<path d="M7.6 13.8h2.2v2.2H7.6z"/>',
    ),
    "terminal": (
        "#3fb950",
        '<path d="M3.4 5h17.2v14H3.4zM7 9.6l2.8 2.4L7 14.4M12.6 15h4.6"/>',
    ),
    "bubble": (
        "#a371f7",
        '<path d="M4 5.4h16v10.8H9.4L5.2 19.8v-3.6H4z"/><path d="M7.6 9h8.8M7.6 12.2h5.4"/>',
    ),
    "hourglass": (
        "#db6d28",
        '<path d="M7 3.6h10M7 20.4h10"/>'
        '<path d="M8.2 3.6c0 4 3.8 5.4 3.8 8.4s-3.8 4.4-3.8 8.4"/>'
        '<path d="M15.8 3.6c0 4-3.8 5.4-3.8 8.4s3.8 4.4 3.8 8.4"/>',
    ),
    "flame": (
        "#f85149",
        '<path d="M12 3.4c3.4 3.9 5.4 6.2 5.4 9.4a5.4 5.4 0 0 1-10.8 0c0-2 .8-3.4 2-4.8'
        '.3 1.5 1.1 2.3 1.9 2.3 1.2 0 1.8-1.2 1.5-6.9z"/>',
    ),
    "pause": (
        "#8b949e",
        '<circle cx="12" cy="12" r="8.4"/><path d="M10 8.8v6.4M14 8.8v6.4"/>',
    ),
    "moon": (
        "#79c0ff",
        '<path d="M20.2 14.4A8.4 8.4 0 0 1 9.6 3.8a8.4 8.4 0 1 0 10.6 10.6z"/>'
        '<path d="M17.4 3.2l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    ),
    # A stack with a fault line through it — the prefix that had to be read
    # again because it no longer lined up.
    "crack": (
        "#f0883e",
        '<path d="M4 6.2h16M4 17.8h16"/><path d="M13.4 6.2l-3 4.2h3.4l-3.4 7.4"/>'
        '<path d="M4.6 12h3.2M19.4 12h-3.2"/>',
    ),
    "agents": (
        "#f778ba",
        '<circle cx="12" cy="5.4" r="2.4"/><circle cx="5.6" cy="18.4" r="2.4"/>'
        '<circle cx="18.4" cy="18.4" r="2.4"/>'
        '<path d="M12 7.8v4.4M12 12.2H5.6v3.8M12 12.2h6.4v3.8"/>',
    ),
}


def _glyph(kind: str) -> str:
    """The drawing beside a board heading, or nothing for an unknown key.

    Silent rather than raising: a board naming a picture that does not exist
    should lose its picture, not take the page down.
    """
    entry = _BOARD_ICONS.get(kind)
    if entry is None:
        return ""
    colour, outline = entry
    return (
        f'<svg class="bicon" width="{BOARD_ICON_PX}" height="{BOARD_ICON_PX}" '
        'viewBox="0 0 24 24" fill="none" '
        f'stroke="{colour}" stroke-width="1.6" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true">'
        f"{outline}</svg>"
    )


def _tokens(value: float) -> str:
    """Token counts at a glance. Nobody reads 759,426 off a wall display."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:,.0f}"


#: How each Leaderboard.unit renders. A table rather than a chain of ifs so
#: that a board naming a unit nobody implemented fails visibly at its own
#: row instead of silently formatting a duration as a count.
_LEADER_UNITS = {
    "money": _money,
    "duration": _duration,
    "tokens": _tokens,
    "clock": lambda value: f"{int(value):02d}:00",
    "days": lambda value: f"{int(value)} day" + ("" if int(value) == 1 else "s"),
    "count": lambda value: f"{value:,.0f}",
}


#: Gold, silver, bronze — the medal disc and the bar under it share a colour
#: so a placing reads as one object rather than two.
_MEDAL_FILL = ("#e3b341", "#c9d1d9", "#c08040")

#: Units whose magnitudes sit on a ratio scale, and so can carry a bar. The
#: clock is deliberately absent: 02:00 is not twice 01:00, and a bar twice
#: the length would say it was.
_SCALED_UNITS = frozenset({"money", "count", "duration", "tokens", "days"})


def _leader_rows(board) -> str:
    if not board.leaders:
        return '<div class="note">not enough history yet</div>'
    render_value = _LEADER_UNITS.get(board.unit, _LEADER_UNITS["count"])
    top = max((leader.value for leader in board.leaders), default=0.0)
    scaled = board.unit in _SCALED_UNITS and top > 0
    out = ['<table class="lrows">']
    for place, leader in enumerate(board.leaders, start=1):
        note = f' <span class="lnote">{escape(leader.note)}</span>' if leader.note else ""
        gleam = '<span class="gleam"></span>' if place == 1 else ""
        out.append(
            "<tr>"
            f'<td class="rank"><span class="medal medal{place}">{gleam}'
            f'<span class="num">{place}</span></span></td>'
            f'<td class="lname">{escape(leader.label)}{note}</td>'
            f'<td class="lval">{escape(render_value(leader.value))}</td>'
            "</tr>"
        )
        if scaled:
            # Against the leader rather than against the sum: these are three
            # placings out of a long tail, so shares of a visible total would
            # not add up to anything and the bars would all be slivers.
            width = max(0.0, min(100.0, leader.value / top * 100))
            out.append(
                '<tr><td class="barrow" colspan="3"><div class="bar">'
                f'<div class="fill" style="width:{width:.1f}%;'
                f'background:{_MEDAL_FILL[place - 1]}"></div>'
                "</div></td></tr>"
            )
    out.append("</table>")
    if board.note:
        out.append(f'<div class="note">{escape(board.note)}</div>')
    return "".join(out)


#: Query key that opens the sheet, alongside ranges.QUERY_KEY. Its value is
#: checked rather than merely present so a bookmarked "?boards=" cannot
#: half-open anything.
BOARDS_QUERY_KEY = "boards"
BOARDS_OPEN = "open"

#: A cup with handles, on a plinth.
_TROPHY = (
    '<path d="M7.2 3.8h9.6v4.8a4.8 4.8 0 0 1-9.6 0z"/>'
    '<path d="M7.2 5.2H4.8v1.5A3.4 3.4 0 0 0 8.2 10M16.8 5.2h2.4v1.5A3.4 3.4 0 0 1 15.8 10"/>'
    '<path d="M12 13.4v3.2M8.8 20.2h6.4M9.4 20.2c0-1.9 1.1-3.2 2.6-3.2s2.6 1.3 2.6 3.2"/>'
)


def _page_href(base_path: str, range_key: str, *, boards: bool) -> str:
    """The page's own URL with the state that belongs in it.

    Both bits of view state travel in the query string, so a reload — the
    automatic one included — lands on the same view rather than resetting it.
    """
    params = []
    if range_key != ranges.DEFAULT.key:
        params.append(f"{ranges.QUERY_KEY}={range_key}")
    if boards:
        params.append(f"{BOARDS_QUERY_KEY}={BOARDS_OPEN}")
    return f"{base_path}?{'&'.join(params)}" if params else base_path


def _ribbon(view: RangeView, base_path: str) -> str:
    """The ribbon that opens the sheet, pinned below the hero row on the right.

    Fixed rather than in flow so it stays reachable at any scroll position,
    and 100px down so it clears the title bar and the hero row's top edge
    instead of sitting on top of the all-time figure.

    No wording on it. A trophy at 26px on a maroon band reads as "prizes"
    from across a room, where two lines of 10px capitals read as a smudge —
    and the label was the only reason the band had to be 92px wide.
    """
    href = _page_href(base_path, view.key, boards=True)
    # The trophy carries the whole meaning now that the wording is gone, so
    # the link needs a name of its own: a title for the pointer, an aria
    # label for anything reading the page aloud.
    return (
        f'<a class="ribbon" href="{escape(href)}" title="All-time leaderboards" '
        'aria-label="All-time leaderboards">'
        '<span class="tailedge"></span><span class="tail"></span>'
        '<svg class="rtrophy" width="26" height="26" viewBox="0 0 24 24" '
        'fill="none" stroke="#f0c352" stroke-width="1.4" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{_TROPHY}</svg></a>'
    )


def _leaderboards(boards, columns: int) -> str:
    """The boards laid out `columns` to a row, for the sheet.

    The heading says ALL TIME because it has to. Every other panel on the
    page re-scopes when a range is picked and these do not, so without the
    caveat the sheet reads as a range that silently refused to apply.
    """
    if not boards:
        return ""
    out = ['<table class="grid"><tbody>']
    for start in range(0, len(boards), columns):
        row = boards[start : start + columns]
        out.append("<tr>")
        for board in row:
            out.append(
                f"<td><h2>{_glyph(board.icon)}{escape(board.title)}</h2>"
                f"{_leader_rows(board)}</td>"
            )
        out.extend('<td class="pad"></td>' for _ in range(columns - len(row)))
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _sheet(data: DashboardData, view: RangeView, base_path: str, columns: int) -> str:
    """The whole overlay: scrim, sheet, boards.

    The scrim is a link, so tapping anywhere outside the sheet closes it —
    the gesture people expect from a modal, and the only way to offer it
    without a click handler.
    """
    if not data.leaderboards:
        return ""
    close = escape(_page_href(base_path, view.key, boards=False))
    return (
        '<div class="overlay">'
        f'<a class="scrim" href="{close}" aria-label="close"></a>'
        '<div class="sheet">'
        f'<div class="sheethead"><a class="close" href="{close}">CLOSE</a>'
        "ALL-TIME LEADERBOARDS"
        ' <span class="quiet">&middot; top three ever &middot; '
        "not affected by the range behind this</span></div>"
        f"{_leaderboards(data.leaderboards, columns)}"
        "</div></div>"
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
    leaderboard_columns: int | None = None,
    boards_open: bool = False,
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
<div class="note">{_cache_note(view)}</div>{_miss_note(view)}</td>
<td><h2>BY MODEL &middot; {escape(view.label)}</h2>{_rows(view.by_model)}
<div class="note">avg {_money(view.avg_cost_per_message)}/msg &middot;
{_money(view.avg_cost_per_session)}/session</div>{unpriced}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr>
<td><h2>BY PROJECT &middot; {escape(view.label)}</h2>{_rows(view.by_project)}</td>
<td><h2>BY SKILL &middot; {escape(view.label)} &middot; ATTRIBUTED</h2>{_rows(view.by_skill)}
{_origin_note(view)}</td>
<td><h2>BY MODE &middot; {escape(view.label)}</h2>{_rows(view.by_mode)}</td>
<td><h2>TOP SESSIONS &middot; {escape(view.label)}</h2>{_session_rows(view.top_sessions)}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr>
<td><h2>BY EFFORT &middot; {escape(view.label)}</h2>{_rows(view.by_effort)}</td>
<td><h2>BY BRANCH &middot; {escape(view.label)}</h2>{_rows(view.by_branch)}</td>
<td><h2>BY MCP SERVER &middot; {escape(view.label)}</h2>{_rows(view.by_mcp)}</td>
<td><h2>SKILL RUNS &middot; {escape(view.label)}</h2>{_skill_runs(view.skill_runs)}</td>
</tr>
</tbody></table>
<table class="grid"><tbody>
<tr><td><h2>{_daily_heading(view)}</h2>{_daily(view, data.today_day)}</td></tr>
</tbody></table>
{_trivia(view)}
{_worked_band(view)}
{_footprint_note(view)}
{_ribbon(view, base_path)}
{_sheet(data, view, base_path, leaderboard_columns or LEADERBOARD_COLUMNS) if boards_open else ""}
</body></html>
"""
