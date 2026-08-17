"""A deliberately crude environmental estimate for the tokens on the page.

This is the least certain module in the project and it is important that it
says so. Nobody publishes per-token energy for a frontier model — not
Anthropic, not anyone — so every figure here is modelled from public research
and applied to token counts the dashboard does know exactly.

The honest error bar is about an order of magnitude. That is not false
modesty: published estimates for the same quantity span four orders of
magnitude, and almost none of that spread is disagreement about physics. It
is disagreement about scope. Three traps account for most of it:

  - **What counts as "the datacentre."** Google publishes 0.24 Wh and 0.10 Wh
    for the same prompts on the same day — comprehensive versus
    accelerator-only. A 2.4x gap inside one vendor's own table.
  - **Which water.** On-site cooling is a fraction of the total; most water
    is consumed generating the electricity, off site. The famous ~100x gap
    between vendor and academic water figures is a scope gap, not an
    efficiency gap. Withdrawal is ~14x consumption and is a different
    quantity again.
  - **Which carbon accounting.** Market-based accounting (net of renewable
    purchases) runs ~3.7x below location-based. Microsoft's market-based
    intensity rose 8.4x in one year with no physical change, purely because
    the rules for counting certificates changed.

So: one significant figure, an inline caveat, and no leaderboards. See
docs/footprint.md for the derivation and every source.

Deliberately NOT modelled: any difference between Opus, Sonnet and Haiku. No
published evidence separates models within a vendor, and inventing a
multiplier would make the output look more informed than it is.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Watt-hours per generated token, including datacentre overhead.
#: Anchored on the only two sources that state both an energy figure and the
#: token count it refers to: Epoch AI (~600 Wh/1M output) and Oviedo et al.,
#: Joule 2026 (~1,033 Wh/1M at a code-verified median of 300 output tokens).
#: 1 mWh/token — i.e. 1 kWh per million — sits between them and is round
#: enough to signal that it is not a measurement.
WH_PER_OUTPUT_TOKEN = 1.0e-3

#: Cost of each token type relative to one generated token.
#:
#: `input` comes from Epoch's ~250 vs ~600 Wh/1M split. The two cache figures
#: are PRICE PROXIES, not measurements — providers bill cache reads at ~10% of
#: uncached input and cache writes at ~1.25x, and no per-token energy figure
#: for a cache hit has ever been published. This is the weakest assumption in
#: the module, and for a coding agent it is also the load-bearing one: cache
#: reads dominate the token counts this dashboard sees.
WEIGHTS = {
    "output": 1.00,
    "input": 0.40,
    "cache_read": 0.04,
    "cache_write": 0.50,
}

#: Datacentre overhead beyond the servers themselves. Google reports 1.09,
#: Microsoft's model a median nearer 1.30.
PUE = 1.12

#: Litres consumed on site per kWh of IT load, cooling. Microsoft 0.27,
#: Azure 0.30, Google 1.15 — the spread is climate and cooling design.
WUE_ONSITE_L_PER_KWH = 0.6

#: Litres consumed off site per kWh, generating the electricity. US average
#: consumption, NOT withdrawal (43.8 L/kWh), which is a different quantity.
#: Regionally this runs 1.3 (Texas gas) to 9.5 (Washington hydro) — a hydro
#: grid is low carbon and high water, so the two figures do not move together.
EWIF_OFFSITE_L_PER_KWH = 3.1

#: Grams CO2e per kWh, LOCATION-based: what the local grid actually emitted.
#: Google's fleet 345, Microsoft FY25 325. The market-based figure — net of
#: renewable procurement — would be roughly a quarter of this, and is a fact
#: about certificate accounting rather than about electrons.
GRID_G_CO2E_PER_KWH = 335

#: Manufacturing the hardware, as a flat uplift on the operational total.
EMBODIED_UPLIFT = 1.10

#: Energy to brew a pot of coffee: 1.2 litres taken from 15 to 93 °C is
#: 1.2 x 4.18 kJ/kg·K x 78 K = 391 kJ = 0.109 kWh, divided by ~85% brewer
#: efficiency. Chosen as the equivalence for the same reason the kettle it
#: replaced was: the arithmetic is checkable in five seconds by anyone who
#: doubts it. Excludes the warming plate, which on a real machine can use
#: more than the brew did — so this is a floor, not a full cup-to-cup cost.
KWH_PER_COFFEE_POT = 0.128


@dataclass(frozen=True)
class Footprint:
    """Modelled resource use. One significant figure is all it supports."""

    kwh: float
    litres: float
    g_co2e: float

    @property
    def coffee_pots(self) -> float:
        return self.kwh / KWH_PER_COFFEE_POT

    def __bool__(self) -> bool:
        return self.kwh > 0


EMPTY_FOOTPRINT = Footprint(0.0, 0.0, 0.0)


def estimate(
    *,
    output_tokens: int = 0,
    input_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Footprint:
    """Tokens in, modelled kWh / litres / gCO2e out.

    Every token is converted to a "generated-token equivalent" through
    WEIGHTS, and energy is the only quantity estimated directly. Water and
    carbon are derived from it, so the three figures on the page cannot
    contradict each other.
    """
    equivalents = (
        output_tokens * WEIGHTS["output"]
        + input_tokens * WEIGHTS["input"]
        + cache_read_tokens * WEIGHTS["cache_read"]
        + cache_write_tokens * WEIGHTS["cache_write"]
    )
    kwh = equivalents * WH_PER_OUTPUT_TOKEN / 1000.0
    return Footprint(
        kwh=kwh,
        # On-site cooling is charged against IT load, so PUE comes back out
        # first; off-site generation is charged against the whole draw.
        litres=kwh / PUE * WUE_ONSITE_L_PER_KWH + kwh * EWIF_OFFSITE_L_PER_KWH,
        g_co2e=kwh * GRID_G_CO2E_PER_KWH * EMBODIED_UPLIFT,
    )
