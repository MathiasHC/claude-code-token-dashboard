"""The environmental estimate.

Most of these pin *restraint* rather than arithmetic. The model is good to an
order of magnitude, so the risk is not that a number is 5% out — it is that
the page presents it as if it were measured.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from dashboard import aggregate, footprint, render_html
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 8, 11, 18, 0, 0)


def rec(message_id: str, **overrides) -> UsageRecord:
    base = dict(
        message_id=message_id,
        ts="2026-08-11T10:00:00.000Z",
        day="2026-08-11",
        model="claude-opus-4-8",
        project="alpha",
        skill="(none)",
        session_id="sess-a",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m=0,
        cache_write_1h=0,
        speed="standard",
    )
    base.update(overrides)
    return UsageRecord(**base)


# --- the arithmetic -----------------------------------------------------

def test_one_million_generated_tokens_is_about_a_kilowatt_hour():
    """The single anchor the whole module hangs off: Epoch ~600 Wh/1M and
    Oviedo et al. ~1,033 Wh/1M, so 1 kWh sits between them."""
    assert footprint.estimate(output_tokens=1_000_000).kwh == pytest.approx(1.0)


def test_water_and_carbon_are_derived_from_energy_not_estimated_separately():
    """Three independently-estimated figures could contradict each other on
    the page. These cannot: both are a fixed ratio of the kWh."""
    small = footprint.estimate(output_tokens=1_000)
    large = footprint.estimate(output_tokens=1_000_000)
    assert large.litres / large.kwh == pytest.approx(small.litres / small.kwh)
    assert large.g_co2e / large.kwh == pytest.approx(small.g_co2e / small.kwh)


def test_the_worked_example_from_the_research_brief():
    """One heavy Claude Code day: 200k output, 1M input, 20M cache read,
    2M cache write -> 2.4M generated-token equivalents."""
    fp = footprint.estimate(
        output_tokens=200_000,
        input_tokens=1_000_000,
        cache_read_tokens=20_000_000,
        cache_write_tokens=2_000_000,
    )
    assert fp.kwh == pytest.approx(2.4, abs=0.05)
    assert fp.litres == pytest.approx(8.7, abs=0.2)
    assert fp.g_co2e == pytest.approx(884, abs=10)


def test_a_cache_read_token_is_far_cheaper_than_a_generated_one():
    """The ordering that matters for a coding agent, where cache reads
    dominate. Note this rests on a price proxy, not a measurement — see the
    module docstring."""
    read = footprint.estimate(cache_read_tokens=1_000_000).kwh
    written = footprint.estimate(output_tokens=1_000_000).kwh
    assert read < written / 20


def test_no_tokens_is_falsy_so_the_page_can_omit_the_row():
    assert not footprint.estimate()
    assert footprint.estimate(output_tokens=1)


# --- restraint ----------------------------------------------------------

def test_the_estimate_does_not_vary_by_model():
    """No published evidence separates models within a vendor. Shipping one
    constant is more defensible than inventing a split, and this pins that
    decision so a future 'improvement' has to argue with it."""
    opus = aggregate.build([rec("m1", output_tokens=1_000_000)], {}, now=NOW)
    haiku = aggregate.build(
        [rec("m1", model="claude-haiku-4-5", output_tokens=1_000_000)], {}, now=NOW
    )
    assert opus.scoped.footprint.kwh == haiku.scoped.footprint.kwh


def test_an_unpriced_model_still_has_a_footprint():
    """Cost goes to zero without a rate; energy does not. A model we cannot
    price still drew power, and showing $0.00 beside 0 kWh would imply the
    work never happened."""
    data = aggregate.build(
        [rec("m1", model="claude-future-9", output_tokens=1_000_000)], {}, now=NOW
    )
    assert data.scoped.money == []
    assert data.scoped.footprint.kwh > 0


def _strip_values(out: str) -> list[str]:
    """The four values in the footprint strip, without their icons."""
    return [
        v.strip()
        for v in re.findall(r'<td class="fpitem">.*?</svg>([^<]*)</td>', out, re.S)
    ]


def test_the_page_never_shows_more_than_one_significant_figure():
    records = [rec(f"m{i}", output_tokens=1_234_567, cache_read_tokens=7_654_321)
               for i in range(3)]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    values = _strip_values(out)
    assert len(values) == 4, values
    for shown in values:
        digits = re.match(r"[\d,]+(?:\.(\d+))?", shown)
        assert digits, shown
        assert len(digits.group(1) or "") <= 1, f"too precise: {shown}"


@pytest.mark.parametrize(
    "boils,expected",
    [
        (0.1, "less than a kettle boiled"),
        (0.5, "half a kettle boiled"),
        (1.0, "1 kettle boiled"),
        (3.4, "3 kettles boiled"),
        (212.0, "200 kettles boiled"),
        (2074.0, "2,000 kettles boiled"),
    ],
)
def test_the_equivalence_is_rounded_as_hard_as_everything_beside_it(boils, expected):
    """"212 kettles" is three significant figures on a line that claims one,
    and reads as a hundred times more precise than the model supports."""
    assert render_html._kettles(boils) == expected


def test_the_caveat_is_inline_and_not_behind_a_link():
    """There is no tooltip on iOS 5.1.1. A number this uncertain shown bare
    would be a worse lie than showing nothing."""
    data = aggregate.build([rec("m1", output_tokens=1_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert "modelled from published research, not measured" in out
    assert "order of magnitude only" in out
    assert "excludes training" in out


@pytest.mark.parametrize(
    "framing",
    ["bottle", "flight", "flown", "tree", "offset", "carbon debt", "neutral"],
)
def test_the_page_refuses_the_indefensible_framings(framing):
    """Each of these is either unsupported by the evidence or turns a rough
    estimate into a moral ledger. The 500 mL-bottle framing in particular
    traces to a per-request water figure that is ~16x stale."""
    records = [rec("m1", output_tokens=5_000_000, cache_read_tokens=50_000_000)]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert framing not in out.lower()


# --- the illustrated strip ----------------------------------------------

def test_all_four_quantities_get_a_drawing():
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert out.count('<td class="fpitem">') == 4
    assert out.count('class="fpicon"') == 4


def test_the_animation_degrades_to_a_static_drawing():
    """Every frame past the first is hidden by default and revealed only by
    the animation. A browser that cannot animate — which is the whole reason
    this is opacity frames rather than transforms or SMIL — shows frame one
    and nothing else, which is a complete line drawing on its own."""
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert ".fpf { opacity:0; }" in out
    assert ".fpf1 { opacity:1;" in out


def test_the_animation_is_css_only_and_prefixed_for_the_target_browser():
    """No JavaScript and no SMIL: the page's whole compatibility story is
    that it carries no script at all, and an <animate> element would be a
    second animation mechanism with worse support than the CSS one."""
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert "<animate" not in out
    assert "<script" not in out.lower()
    assert "@-webkit-keyframes fpcycle" in out
    assert "@keyframes fpcycle" in out
    assert "-webkit-animation:fpcycle" in out


def test_motion_can_be_turned_off_by_the_reader():
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert "@media (prefers-reduced-motion: reduce)" in out


def test_the_drawings_carry_no_text_for_a_screen_reader_to_announce():
    """They repeat the number beside them; announcing them twice is noise."""
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert out.count('aria-hidden="true"') >= 4


def test_the_strip_sits_after_the_daily_chart():
    """Bottom right of the page, below everything else — it is decoration
    with a number attached, not a panel."""
    data = aggregate.build([rec("m1", output_tokens=5_000_000)], {}, now=NOW)
    out = render_html.render(data)
    assert out.index("DAILY &middot;") < out.index('class="fpstrip"')


def test_nothing_is_rendered_when_there_are_no_tokens():
    """An all-zero footprint row on a fresh install is noise, and '0 kWh'
    reads as a measurement of nothing rather than an absence of data."""
    out = render_html.render(aggregate.build([], {}, now=NOW))
    assert "kettle" not in out
    assert "kWh" not in out


# --- units --------------------------------------------------------------

def test_small_quantities_switch_to_smaller_units_rather_than_showing_zero():
    assert render_html._one_sig_fig(0.004, "kWh", "Wh", 1000) == "4.0 Wh"
    assert render_html._one_sig_fig(2.5, "kWh", "Wh", 1000) == "2.5 kWh"
    assert render_html._one_sig_fig(340.0, "kWh", "Wh", 1000) == "340 kWh"


def test_the_footprint_follows_the_selected_range():
    """It is a property of the tokens in the window, like every other panel
    below the selector."""
    records = [
        rec("m1", output_tokens=1_000_000),
        rec("m2", day="2026-01-02", ts="2026-01-02T10:00:00.000Z", output_tokens=9_000_000),
    ]
    today = aggregate.build(records, {}, now=NOW, range_key="today")
    everything = aggregate.build(records, {}, now=NOW, range_key="all")
    assert today.scoped.footprint.kwh == pytest.approx(1.0)
    assert everything.scoped.footprint.kwh == pytest.approx(10.0)
