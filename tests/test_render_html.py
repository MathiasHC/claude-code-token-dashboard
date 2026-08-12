from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

import pytest

from dashboard import aggregate, render_html
from dashboard.models import Bar, DashboardData, DayCost, UsageRecord, Window

NOW = dt.datetime(2026, 7, 30, 9, 12, 0)
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "golden_dashboard.html"


def rec(message_id: str, day: str, **overrides) -> UsageRecord:
    base = dict(
        message_id=message_id,
        ts=f"{day}T10:00:00.000Z",
        day=day,
        model="claude-opus-4-8",
        project="alpha",
        skill="(none)",
        session_id="sess-a",
        input_tokens=1_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_5m=0,
        cache_write_1h=1_000_000,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


@pytest.fixture
def page() -> str:
    records = [rec("m1", "2026-07-30"), rec("m2", "2026-07-29", project="beta")]
    data = aggregate.build(records, {"sess-a": "/graphify"}, now=NOW)
    return render_html.render(data)


@pytest.fixture
def kitchen_sink_page() -> str:
    """Exercises the branches the plain `page` fixture never reaches: the
    warning banner, the unpriced-models note, and the delegation band. The
    compatibility lint must cover these too — they are the least-rendered
    code paths and so the easiest place for a violation to hide."""
    records = [
        rec("m1", "2026-07-30", project="alpha"),
        rec("m2", "2026-07-30", is_subagent=True),
        rec("m3", "2026-07-30", model="claude-future-9"),
    ]
    data = aggregate.build(records, {"sess-a": "/graphify"}, now=NOW)
    return render_html.render(data, warning="refresh failed: disk gone")


def test_kitchen_sink_page_hits_the_warning_unpriced_and_delegation_branches(kitchen_sink_page):
    assert "DELEGATION" in kitchen_sink_page
    assert "unpriced" in kitchen_sink_page.lower()
    assert "refresh failed: disk gone" in kitchen_sink_page


# --- iOS 5.1.1 Safari compatibility -------------------------------------
# The target device is awkward to test by hand, so these are the guard.
# Each assertion runs against both `page` (the plain path) and
# `kitchen_sink_page` (warning banner + unpriced note + delegation band),
# since those three branches were previously unchecked by this lint.

def test_page_contains_no_javascript(page, kitchen_sink_page):
    for rendered in (page, kitchen_sink_page):
        assert "<script" not in rendered.lower()
        assert "onclick" not in rendered.lower()


def test_page_uses_no_flexbox(page, kitchen_sink_page):
    for rendered in (page, kitchen_sink_page):
        assert "display:flex" not in rendered.replace(" ", "")


def test_page_uses_no_css_grid(page, kitchen_sink_page):
    for rendered in (page, kitchen_sink_page):
        assert "display:grid" not in rendered.replace(" ", "")


def test_page_uses_no_css_custom_properties(page, kitchen_sink_page):
    for rendered in (page, kitchen_sink_page):
        assert "var(--" not in rendered


def test_page_uses_no_rem_units(page, kitchen_sink_page):
    for rendered in (page, kitchen_sink_page):
        assert not re.search(r"\d\s*rem\b", rendered)


def test_page_declares_the_fullscreen_web_app_meta_tags(page):
    assert 'name="apple-mobile-web-app-capable"' in page
    assert 'width=1024' in page


def test_page_sets_a_meta_refresh(page):
    assert 'http-equiv="refresh"' in page
    assert 'content="30"' in page


def test_refresh_interval_is_configurable():
    data = aggregate.build([], {}, now=NOW)
    assert 'content="90"' in render_html.render(data, refresh_seconds=90)


# --- content ------------------------------------------------------------

def test_page_shows_all_four_window_figures(page):
    for label in ("TODAY", "7 DAYS", "MONTH TO DATE", "ALL TIME"):
        assert label in page


def test_page_shows_the_plan_comparison_and_multiple(page):
    assert "Max 20" in page
    assert "$200" in page
    assert "effective" in page


def test_page_shows_every_panel_heading(page):
    for heading in (
        "WHERE THE MONEY GOES",
        "BY MODEL",
        "BY PROJECT",
        "BY SKILL",
        "TOP SESSIONS",
        "DAILY",
    ):
        assert heading in page


def test_panels_are_labelled_with_the_selected_range(page):
    """Asserting "all time" appears somewhere passed vacuously once the
    default changed — the phrase still occurs in the hero row and in the
    range selector. Check the panel headings themselves."""
    data = aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW)
    out = render_html.render(data)
    assert f"WHERE THE MONEY GOES &middot; {data.range_label}" in out
    assert f"BY MODEL &middot; {data.range_label}" in out


def test_page_renders_bars_as_divs_with_percentage_widths(page):
    assert re.search(r'class="fill"[^>]*width:\s*\d+(\.\d+)?%', page)


def test_page_renders_the_daily_chart_with_pixel_heights(page):
    """Percentage heights inside table cells are unreliable on iOS 5, so the
    renderer computes pixel heights instead."""
    assert re.search(r'class="col"[^>]*height:\s*\d+px', page)


def test_page_shows_the_session_title(page):
    assert "/graphify" in page


def test_page_shows_what_caching_saved_not_the_hit_rate(page):
    """Hit rate is saturated on any real history (99.68-100.00% across eight
    consecutive weeks), so it was replaced by the figure that actually moves.
    The counterfactual qualifier is part of the contract: without it the
    number reads as money that was once at stake, and iOS 5.1.1 has no
    tooltip to explain it."""
    assert "caching saved" in page
    assert "same tokens at uncached rates" in page
    assert "hit rate" not in page.lower()


# --- like-for-like deltas -------------------------------------------------
# Each hero tile (other than ALL TIME, which has nothing to compare to) now
# carries a same-length baseline comparison, replacing the old month-to-date
# vs whole-previous-month comparison that was meaningless in the first week
# of a month.

def test_hero_tile_shows_an_up_arrow_for_a_rising_delta():
    records = [
        rec("m1", "2026-07-30"),  # today: $25
        rec("m2", "2026-07-30"),  # today: $25 (today totals $50)
        rec("m3", "2026-07-29"),  # yesterday: $25 — today is double yesterday
    ]
    data = aggregate.build(records, {}, now=NOW)
    out = render_html.render(data)
    assert "▲" in out
    assert "vs yesterday" in out


def test_hero_tile_shows_a_down_arrow_for_a_falling_delta():
    records = [
        rec("m1", "2026-07-30"),  # today: $25
        rec("m2", "2026-07-29"),  # yesterday: $25
        rec("m3", "2026-07-29"),  # yesterday: $25 (yesterday totals $50) — today is down
    ]
    data = aggregate.build(records, {}, now=NOW)
    out = render_html.render(data)
    assert "▼" in out


def test_hero_tile_shows_no_prior_data_when_the_baseline_is_zero():
    """A single record with nothing on any prior day: every baseline is
    zero, so every delta must read 'no prior data' rather than a divide-by-
    zero percentage."""
    data = aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW)
    out = render_html.render(data)
    assert "no prior data" in out
    assert "▲" not in out
    assert "▼" not in out


def test_all_time_tile_carries_no_delta_line():
    data = aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW)
    out = render_html.render(data)
    all_time_cell = out.split("ALL TIME", 1)[1].split("</td>", 1)[0]
    assert "hdelta" not in all_time_cell


def test_max_band_states_the_month_change_as_a_same_point_comparison():
    """Guards against the Max band reading like a full-previous-month
    comparison now that it uses the like-for-like month_change instead of
    the old whole-month mom_change."""
    records = [rec("m1", "2026-07-30"), rec("m2", "2026-06-30")]
    data = aggregate.build(records, {}, now=NOW)
    out = render_html.render(data)
    assert "same point last month" in out
    # The full previous-month total stays on the page as context.
    assert "2026-06" in out


# --- escaping -----------------------------------------------------------

def test_session_titles_are_html_escaped():
    """Titles are arbitrary user text and routinely contain < and &."""
    data = aggregate.build([rec("m1", "2026-07-30")], {"sess-a": "<b>a & b</b>"}, now=NOW)
    out = render_html.render(data)
    assert "<b>a & b</b>" not in out
    assert "&lt;b&gt;" in out
    assert "&amp;" in out


def test_project_and_model_labels_are_escaped():
    data = aggregate.build([rec("m1", "2026-07-30", project="a<b>&c")], {}, now=NOW)
    assert "a&lt;b&gt;&amp;c" in render_html.render(data)


# --- warnings and empty state -----------------------------------------

def test_warning_banner_is_shown_when_supplied():
    data = aggregate.build([], {}, now=NOW)
    out = render_html.render(data, warning="data is 240s stale")
    assert "data is 240s stale" in out


def test_no_warning_banner_when_none_supplied():
    data = aggregate.build([], {}, now=NOW)
    assert 'class="warn"' not in render_html.render(data)


def test_unpriced_models_are_surfaced_on_the_page():
    data = aggregate.build([rec("m1", "2026-07-30", model="claude-future-9")], {}, now=NOW)
    out = render_html.render(data)
    assert "unpriced" in out.lower()
    assert "claude-future-9" in out


def test_empty_dashboard_still_renders_a_valid_page():
    out = render_html.render(aggregate.build([], {}, now=NOW))
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")
    assert "$0.00" in out


# --- Change 8: main/subagent delegation split ---------------------------

def test_page_shows_the_delegation_split():
    records = [
        rec("m1", "2026-07-30"),
        rec("m2", "2026-07-30", is_subagent=True),
    ]
    data = aggregate.build(records, {}, now=NOW)
    out = render_html.render(data)
    assert "DELEGATION" in out
    assert "subagents" in out


def test_delegation_band_is_omitted_when_there_is_no_data():
    assert "DELEGATION" not in render_html.render(aggregate.build([], {}, now=NOW))


# --- clamping: negative costs must not overflow the fixed-size chrome ---
# Not reachable through aggregate.build today (all rates and multipliers are
# non-negative), but scan.py builds token counts straight from parsed JSONL
# with no non-negativity check, so the input boundary doesn't guarantee it.
# DashboardData is built directly to reach these states.

def _bare_data(**overrides) -> DashboardData:
    zero_window = Window(label="w", cost=0.0, messages=0)
    base = dict(
        generated_at="30 Jul 2026 09:12",
        today=zero_window,
        last_7_days=zero_window,
        month_to_date=zero_window,
        all_time=zero_window,
        active_days=0,
        max_plan_monthly_usd=200.0,
        prev_month_label="2026-06",
        prev_month_cost=0.0,
    )
    base.update(overrides)
    return DashboardData(**base)


def test_split_band_clamps_widths_when_a_cost_is_negative():
    """main_cost=-10, subagent_cost=40 gives raw shares of -33.3% and
    133.3% — both invalid CSS widths. They must land inside 0-100."""
    data = _bare_data(main_cost=-10.0, subagent_cost=40.0)
    out = render_html.render(data)
    widths = [float(m) for m in re.findall(r'class="fill"[^>]*width:(-?\d+(?:\.\d+)?)%', out)]
    assert widths, "expected at least one .fill width in the delegation band"
    for width in widths:
        assert 0.0 <= width <= 100.0


def test_daily_chart_clamps_bar_height_when_costs_are_negative():
    """An all-negative series can send a more-negative day's height ratio
    above 1 against a less-negative peak; heights must stay within the
    fixed 44px chart (and never drop below the existing floor of 1)."""
    data = _bare_data(
        daily=[DayCost(day="2026-07-28", cost=-5.0), DayCost(day="2026-07-29", cost=-20.0)]
    )
    out = render_html.render(data)
    heights = [int(m) for m in re.findall(r'class="col"[^>]*height:(-?\d+)px', out)]
    assert heights, "expected at least one .col height in the daily chart"
    for height in heights:
        assert 1 <= height <= render_html.DAILY_CHART_HEIGHT_PX


# --- Finding 3: negative money is formatted with a leading minus sign ---

def test_negative_money_puts_the_minus_sign_before_the_dollar_sign():
    out = render_html.render(_bare_data(prev_month_cost=-12.345))
    assert "-$12.35" in out
    assert "$-12.35" not in out


# --- golden-file snapshot ------------------------------------------------
# The spec's testing strategy called for "golden-file comparison against a
# frozen DashboardData"; substring/regex assertions above miss a whole panel
# being deleted, one hero cell wired to the wrong field, or the <style>
# block being emptied, as long as some other assertion's substring still
# happens to appear on the page. A byte-for-byte compare against a committed
# snapshot catches all of those at once.


def _golden_data() -> DashboardData:
    """Fixed, hand-written values. Deliberately NOT produced by calling
    aggregate.build(), so this snapshot pins the renderer alone and cannot
    drift silently just because aggregation logic changes.

    today and all_time carry different cost/message figures on purpose —
    that is what catches a hero cell wired to the wrong DashboardData field.

    yesterday_cost/prior_7_days_cost/prev_month_to_date_cost are chosen so
    day_change is a rise (▲) and week_change/month_change are falls (▼) —
    between them the snapshot exercises both arrow glyphs."""
    return DashboardData(
        generated_at="30 Jul 2026 09:12",
        today=Window(label="today", cost=12.34, messages=7),
        last_7_days=Window(label="7 days", cost=45.67, messages=21),
        month_to_date=Window(label="month to date", cost=89.01, messages=48),
        all_time=Window(label="all time", cost=1234.56, messages=602),
        active_days=42,
        max_plan_monthly_usd=200.0,
        prev_month_label="2026-06",
        prev_month_cost=150.25,
        yesterday_cost=10.00,
        prior_7_days_cost=50.00,
        prev_month_to_date_cost=100.00,
        money=[
            Bar(label="cache read", cost=400.0, share=0.4),
            Bar(label="output", cost=300.0, share=0.3),
            Bar(label="fresh input", cost=200.0, share=0.2),
            Bar(label="cache write", cost=100.0, share=0.1),
        ],
        by_model=[
            Bar(label="claude-opus-4-8", cost=700.0, share=0.7),
            Bar(label="claude-sonnet-5", cost=300.0, share=0.3),
        ],
        by_project=[
            Bar(label="alpha", cost=600.0, share=0.6),
            Bar(label="beta", cost=400.0, share=0.4),
        ],
        # No "(none)" row: the panel is headed ATTRIBUTED and aggregate
        # excludes the unattributed bucket, so a fixture containing it would
        # pin markup the real pipeline can never produce.
        by_skill=[
            Bar(label="graphify", cost=550.0, share=0.55),
            Bar(label="code-review", cost=450.0, share=0.45),
        ],
        top_sessions=[
            Bar(label="/graphify refactor billing", cost=220.0, share=0.22),
            Bar(label="(untitled session)", cost=80.0, share=0.08),
        ],
        daily=[
            DayCost(day="2026-07-28", cost=10.0),
            DayCost(day="2026-07-29", cost=25.5),
            DayCost(day="2026-07-30", cost=12.34),
        ],
        cache_hit_rate=0.6234,
        avg_cost_per_message=2.05,
        avg_cost_per_session=41.15,
        unpriced_models=["claude-future-9"],
        main_cost=800.0,
        subagent_cost=434.56,
        by_source=[
            Bar(label="Claude Code", cost=1000.0, share=0.81),
            Bar(label="Desktop (Cowork)", cost=234.56, share=0.19),
        ],
    )


def test_render_matches_the_golden_snapshot():
    """Byte-for-byte comparison of render_html.render() against a committed
    fixture. To regenerate after a deliberate rendering change, run:

        UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_render_html.py::test_render_matches_the_golden_snapshot

    then inspect the diff on tests/fixtures/golden_dashboard.html before
    committing it — the point is that a real design change updates the
    golden with one command, while an accidental regression does not."""
    out = render_html.render(_golden_data())
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_PATH.write_text(out, encoding="utf-8")
    expected = GOLDEN_PATH.read_text(encoding="utf-8")
    assert out == expected


# --- per-source split ---------------------------------------------------

def test_page_shows_a_source_band_naming_each_surface():
    records = [
        rec("m1", "2026-07-30"),
        rec("m2", "2026-07-30", source="cowork"),
    ]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert "BY SOURCE" in out
    assert "Claude Code" in out
    assert "Desktop (Cowork)" in out


def test_source_band_is_omitted_when_there_is_no_data():
    assert "BY SOURCE" not in render_html.render(aggregate.build([], {}, now=NOW))


def test_bands_sit_side_by_side_to_protect_the_vertical_budget():
    """The iPad's first screen is 748px. Stacking DELEGATION above BY SOURCE
    costs ~60px and pushes the daily chart off it, so both must share one row."""
    records = [
        rec("m1", "2026-07-30"),
        rec("m2", "2026-07-30", is_subagent=True, source="cowork"),
    ]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    band_row = re.search(r'<table class="bands">.*?</table>', out, re.S)
    assert band_row, "expected a bands table"
    assert band_row.group(0).count('<td class="band"') == 2
    assert band_row.group(0).count("<tr>") == 1


def test_a_lone_band_spans_the_full_width_rather_than_leaving_a_hole():
    """Delegation needs main+subagent cost; a records set with neither leaves
    only the source band, which must not render as a half-width cell."""
    data = _bare_data(by_source=[Bar(label="Claude Code", cost=5.0, share=1.0)])
    out = render_html.render(data)
    assert 'colspan="2"' in out
    assert "DELEGATION" not in out


def test_source_band_clamps_widths_when_a_share_is_out_of_range():
    data = _bare_data(
        by_source=[
            Bar(label="Claude Code", cost=-10.0, share=-0.333),
            Bar(label="Desktop (Cowork)", cost=40.0, share=1.333),
        ]
    )
    out = render_html.render(data)
    widths = [float(m) for m in re.findall(r'class="fill"[^>]*width:(-?\d+(?:\.\d+)?)%', out)]
    assert widths, "expected at least one .fill width in the source band"
    for width in widths:
        assert 0.0 <= width <= 100.0


def test_source_labels_are_html_escaped():
    data = _bare_data(by_source=[Bar(label="<script>x</script>", cost=1.0, share=1.0)])
    out = render_html.render(data)
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


# --- plan comparison band -----------------------------------------------

def test_plan_band_names_the_configured_plan():
    """The band is the one place the user's own subscription appears, so it
    must say which plan it compares against rather than assume one."""
    out = render_html.render(_bare_data(max_plan_monthly_usd=100.0, plan_label="Max 5×"))
    assert "vs Max 5×" in out
    assert "$100.00 actual" in out


def test_plan_band_drops_the_multiple_when_there_is_no_subscription():
    """On an API-only account the api-equivalent figure *is* the bill, so a
    ratio against $0 is meaningless — and would render '0.0x effective'."""
    out = render_html.render(_bare_data(max_plan_monthly_usd=0.0, plan_label="API only"))
    assert "no subscription to compare against" in out
    assert "effective" not in out


def test_plan_label_is_html_escaped():
    out = render_html.render(_bare_data(plan_label="<script>x</script>"))
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_titles_reach_the_markup_untouched_up_to_the_payload_cap():
    """Display truncation is the browser's job, so nothing is cut at a width
    the server cannot know."""
    title = "x" * render_html.SESSION_TITLE_CAP
    data = aggregate.build([rec("m1", "2026-07-30")], {"sess-a": title}, now=NOW)
    out = render_html.render(data)
    assert title in out
    assert "…" not in out, "the ellipsis is CSS, not a character in the text"


def test_a_very_long_title_is_capped_before_it_reaches_the_page():
    """Real titles are whole prompts — measured between 265 and 4,646
    characters on live data. Without a cap that is several KB per refresh of
    text nobody sees, and it puts the entire prompt in the page source when
    the column shows about thirty characters of it."""
    data = aggregate.build([rec("m1", "2026-07-30")], {"sess-a": "y" * 5000}, now=NOW)
    out = render_html.render(data)
    assert "y" * render_html.SESSION_TITLE_CAP in out
    assert "y" * (render_html.SESSION_TITLE_CAP + 1) not in out


def test_the_cap_is_far_wider_than_anything_the_column_renders():
    """If the cap were near the visible width it would be a display cap
    again, and would fight the CSS at wide viewports."""
    assert render_html.SESSION_TITLE_CAP >= 150


def test_the_session_table_can_actually_ellipsize():
    """text-overflow does nothing in an auto-layout table: the cell simply
    grows to fit. The three properties have to arrive together, and on a
    table whose column widths are fixed."""
    data = aggregate.build([rec("m1", "2026-07-30")], {"sess-a": "a title"}, now=NOW)
    out = render_html.render(data)
    assert 'table.srows { width:100%; table-layout:fixed;' in out
    assert "text-overflow:ellipsis" in out
    assert "white-space:nowrap" in out
    assert 'class="srows"' in out


def test_sessions_read_title_then_amount_like_every_other_panel():
    data = aggregate.build([rec("m1", "2026-07-30")], {"sess-a": "the title"}, now=NOW)
    out = render_html.render(data)
    row = re.search(r"<tr><td class=\"stitle\">.*?</tr>", out, re.S).group(0)
    assert row.index("stitle") < row.index("amt"), "amount came before the title"


# --- the daily chart's value axis ---------------------------------------

def test_the_axis_lands_on_round_money_above_the_peak():
    """The example that defined the feature: a $86 peak should read $50 /
    $100, not $43 / $86. Two lines, so the top of the scale is twice the
    step and the step must cover half the peak."""
    assert render_html._axis_step(86) == 50
    assert render_html._axis_step(8.6) == 5
    assert render_html._axis_step(860) == 500


def test_the_axis_does_not_waste_the_chart():
    """A coarse 1/2/5 step set leaves the tallest bar at half height for
    unlucky peaks. Every peak here must fill at least two thirds."""
    for peak in (1, 3, 8.6, 20, 51, 86, 150, 337, 1234):
        top = render_html._axis_step(peak) * 2
        assert peak / top >= 0.66, f"{peak} only fills {peak / top:.0%}"
        assert top >= peak, f"{peak} exceeds its own axis"


def test_bars_are_measured_against_the_axis_not_the_peak():
    """If bars scaled to the peak, the tallest would always touch the top
    and the axis labels would be decoration rather than a scale."""
    days = [DayCost(day=f"2026-07-{d:02d}", cost=c) for d, c in ((28, 10.0), (29, 86.0))]
    out = render_html.render(_bare_data(daily=days))
    heights = [int(m) for m in re.findall(r'class="col[^"]*"[^>]*height:(\d+)px', out)]
    assert max(heights) < render_html.DAILY_CHART_HEIGHT_PX, "tallest bar hit the ceiling"


def test_axis_labels_drop_the_cents():
    assert render_html._axis_label(50) == "$50"
    assert render_html._axis_label(1234) == "$1,234"
    assert render_html._axis_label(0.5) == "$0.50"


def test_the_three_breakdowns_share_a_row_and_the_chart_spans_the_width():
    data = aggregate.build([rec("m1", "2026-07-30")], {}, now=NOW)
    out = render_html.render(data)
    assert "</td>\n<td><h2>BY SKILL" in out
    assert "</td>\n<td><h2>TOP SESSIONS" in out
    # The chart is alone in the last grid table, so it spans the full width.
    last = out.rsplit('<table class="grid">', 1)[1]
    assert last.count("<h2>") == 1
    assert "DAILY" in last


def test_the_chart_survives_the_ios5_lint():
    """position:absolute is fine on iOS 5.1.1; flexbox and grid are not."""
    days = [DayCost(day="2026-07-30", cost=5.0)]
    out = render_html.render(_bare_data(daily=days))
    assert "display:flex" not in out.replace(" ", "")
    assert "display:grid" not in out.replace(" ", "")
    assert "var(--" not in out
    assert "<script" not in out.lower()
