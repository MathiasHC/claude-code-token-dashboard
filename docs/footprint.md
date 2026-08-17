# How the environmental estimate is calculated

The dashboard shows a line like:

```
23 kWh · 85 L water · 8.6 kg CO2e · 200 pots of coffee
modelled from published research, not measured · order of magnitude only · excludes training
```

This page is the "not measured" part in full. Read it before quoting the
number anywhere.

## What is actually known

The dashboard knows your token counts exactly — they come from the
transcripts on your disk, split by input, output, cache read and cache write,
per model. That half is not in question.

Everything else is modelled. **No vendor publishes per-token energy for a
frontier model.** Not Anthropic, not anyone. Google and Microsoft publish
per-*prompt* figures without saying how many tokens a prompt is. So the
constants below are inferred from public research and applied to counts the
dashboard does know.

**The honest error bar is about an order of magnitude.**

## The model

One constant is estimated — energy. Water and carbon are derived from it, so
the three figures on the page cannot contradict each other.

```
generated-token-equivalents = output×1.00 + input×0.40
                            + cache_read×0.04 + cache_write×0.50

kWh    = equivalents × 1 mWh
litres = kWh ÷ PUE × 0.6      (on-site cooling)
       + kWh × 3.1            (off-site, generating the electricity)
gCO2e  = kWh × 335 × 1.10     (grid, plus embodied hardware)
```

| Constant | Value | Where it comes from |
|---|---|---|
| Wh per output token | 1.0 mWh | Between Epoch AI (~600 Wh/1M) and Oviedo et al., Joule 2026 (~1,033 Wh/1M at a code-verified median of 300 output tokens) |
| input multiplier | 0.40 | Epoch: ~250 vs ~600 Wh/1M |
| cache read multiplier | 0.04 | **Price proxy, not a measurement** |
| cache write multiplier | 0.50 | **Price proxy, not a measurement** |
| PUE | 1.12 | Google 1.09, Microsoft model median ~1.30 |
| On-site water | 0.6 L/kWh | Microsoft 0.27, Azure 0.30, Google 1.15 |
| Off-site water | 3.1 L/kWh | US average **consumption** (not the 43.8 L/kWh *withdrawal* figure) |
| Grid carbon | 335 gCO2e/kWh | Location-based: Google fleet 345, Microsoft FY25 325 |
| Embodied uplift | ×1.10 | ~10% of a location-based total |
| Pot of coffee | 0.128 kWh | 1.2 L × 4.18 kJ/kg·K × 78 K ÷ 0.85 brewer efficiency; excludes the warming plate, so it is a floor |

## The weakest link

**The cache-read multiplier**, and it is the one a coding agent leans on
hardest. Cache reads dominate the token counts this dashboard sees, and
there is no published energy figure for a cache-hit token anywhere. The 0.04
comes from providers billing cache reads at ~10% of uncached input — a
commercial price ratio used as an energy proxy, which is not sound. It is
also the only option available.

Related: both energy sources assume ~300–500 input tokens per query. An agent
re-sending 100k of context for a 200-token tool call is a prefill-dominated
workload neither paper was built for. **This dashboard measures exactly the
workload that sits outside its own sources' validated envelope.**

## Why published figures disagree by four orders of magnitude

Almost none of the spread is disagreement about physics. It is scope.

1. **What counts as "the datacentre."** Google publishes 0.24 Wh *and*
   0.10 Wh for the same prompts on the same day — comprehensive versus
   accelerator-only. A 2.4× gap inside one vendor's own table.
2. **Which water.** Vendor figures count on-site cooling; academic figures
   add the water consumed generating the electricity, which the IEA puts at
   about two-thirds of the total. The famous ~100× gap is a scope gap, not an
   efficiency gap. Withdrawal is ~14× consumption and is a different quantity
   again.
3. **Which carbon accounting.** Market-based (net of renewable purchases)
   runs ~3.7× below location-based. Microsoft's market-based intensity rose
   8.4× in one year with no physical change, purely because the rules for
   counting certificates changed. This dashboard uses location-based.

Also: hardware moves fast. Google measured a **33× reduction** in energy per
median prompt in twelve months. Any figure older than about a year is likely
obsolete by an order of magnitude — including, eventually, this one.

## What this does not claim

- **Not a measurement.** A public-literature estimate applied to your tokens.
- **Not Anthropic's figures.** Anthropic publishes no per-token data. Nothing
  here implies endorsement or insider knowledge.
- **Excludes** training and its amortisation, network transmission, your own
  device, and embodied manufacturing beyond the flat 10% carbon uplift.
- **Cannot rank models.** The estimate is deliberately identical for Opus,
  Sonnet and Haiku, because no published evidence separates models within a
  vendor. Inventing a multiplier would make the output look better informed
  than it is.
- **Is not an offsetting basis.** No trees, no offsets, no carbon ledger.
- **Is a lower bound on marginal carbon.** The right basis for "one more
  query" is *marginal* grid intensity, typically 400–600 gCO2e/kWh.

## Deliberately not shown

Framings that are wrong rather than merely unflattering:

- **Bottles of water.** Traces to a per-request water figure whose energy
  term is ~16× stale. The most-repeated wrong number in AI coverage.
- **Any water figure built on withdrawal.** 14× higher, and about a different
  thing.
- **"Carbon neutral, so it's zero."** Near-zero is a property of certificate
  accounting, not of electrons.
- **Flights, trees, offsets, cumulative guilt counters, model leaderboards,
  percentages of a national footprint**, or anything with two significant
  figures or a live-ticking counter.

A test pins several of these: `tests/test_footprint.py` fails if the rendered
page ever contains "bottle", "flight", "tree", "offset" or "neutral".

## References

- Google, *Measuring the environmental impact of delivering AI at Google Scale* (Aug 2025) — https://arxiv.org/abs/2508.15734
- Oviedo et al. (Microsoft), *Energy Use of AI Inference, Efficiency Pathways, and Test-Time Scaling*, Joule 2026 — https://arxiv.org/abs/2509.20241
- Simulation code for the above (median 300 output tokens) — https://github.com/microsoft/aienergy_simulation
- Epoch AI, *How much energy does ChatGPT use?* — https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use
- Li et al., *Making AI Less "Thirsty"* (v5) — https://arxiv.org/html/2304.03271v5 — **water intensity factors only; its per-request figure is ~16× stale**
- Luccioni, Jernite, Strubell, *Power Hungry Processing*, FAccT '24 — https://facctconference.org/static/papers24/facct24-6.pdf
- Microsoft datacentre PUE/WUE series — https://datacenters.microsoft.com/sustainability/efficiency/
- Google 2026 Environmental Report — https://blog.google/company-news/outreach-and-initiatives/sustainability/2026-environmental-report/
- IEA, *Energy and AI* (Apr 2025) — https://www.iea.org/reports/energy-and-ai
- Mistral/ADEME/Carbone 4 LCA — https://mistral.ai/news/our-contribution-to-a-global-environmental-standard-for-ai — **its 45 mL/400 tokens is a Water Consumption Potential impact indicator, not litres evaporated; not used here**
