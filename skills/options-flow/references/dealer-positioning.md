# Dealer positioning (GEX, gamma flip)

The skill v1 documents this methodology but does NOT compute it. Flag it
in `references/rendering.md` as a `↳` continuation line when known
externally (e.g., from a manual annotation file). v2 candidate to compute
in-skill.

## Gamma exposure (GEX)

Dealers (market makers) are typically short gamma when they sell calls
and puts to retail and institutional traders. Short gamma means they
have to buy stock as it rises and sell as it falls, which amplifies
moves. Long gamma is the opposite: they dampen moves.

GEX is the dollar exposure dealers have to a 1% move in the underlying,
aggregated across the chain weighted by gamma. Positive GEX (dealers
long gamma) suppresses volatility; negative GEX (dealers short gamma)
amplifies volatility.

## Gamma flip

The "gamma flip point" or "zero gamma level" is the spot price at which
total dealer GEX flips from positive to negative (or vice versa). Above
the flip, dealers are typically long gamma (calmer market). Below the
flip, dealers are short gamma (more volatile market).

For SPY/SPX, the flip is published by SpotGamma and similar services;
it's a closely-watched level. Crossing the flip from above is associated
with regime change to higher realized vol.

## How to compute

Per-contract gamma exposure:

```
gamma_exposure_per_contract = gamma * OI * 100 * spot
```

Total GEX:

```
total_gex = sum(call_gamma * call_OI - put_gamma * put_OI) * 100 * spot^2 / 100
```

The sign convention: assume dealers are net short calls and net long
puts (the standard retail-skew assumption). Aggregate across the full
chain, ideally across multiple expiries.

The gamma flip is the spot price at which `total_gex == 0`. Solve
numerically by computing total_gex at a range of spot perturbations
(±5% in 0.1% steps) and finding the zero crossing.

## Why v1 doesn't compute

Three reasons:

1. **Inputs are large.** A full SPY chain has ~6,000 contracts. Pulling
   per-contract gamma + OI requires the full snapshot, which is multiple
   paginated calls.
2. **Sign assumption is non-trivial.** "Dealers short calls long puts"
   is the standard heuristic but not always true. Industry tools
   (SpotGamma, Tier1Alpha) use more sophisticated estimators based on
   exchange-segregated volume.
3. **Output value is summary-level, not per-print.** GEX doesn't fit
   the stream format well; it's a single number for the underlying, not
   a per-print signal. It belongs in a future `gex-snapshot` skill or as
   a header line on the stream.

## What v1 does instead

When the user has an external GEX value (from SpotGamma, etc.), they can
annotate it on a per-ticker basis in a `gex.json` override:

```json
{
  "SPY": {
    "flip_price": 730.50,
    "as_of": "2026-06-23T13:00:00Z",
    "source": "spotgamma"
  },
  "TSLA": {
    "flip_price": 300.00,
    "as_of": "2026-06-23T13:00:00Z",
    "source": "tier1alpha"
  }
}
```

When a print's strike is within 2% of the flip price, render a `↳`
continuation line:

```
↳ near gamma flip ($300, spot $302)
```

This gives the operator dealer context without computing it. v2 should
move computation in-skill once the input cost is amortized across other
gamma-aware skills.

## Compounding effects to mention in rendered output

Two gamma-related patterns worth surfacing when known:

- **Gamma squeeze:** heavy call buying drives spot up; dealers buy stock
  to delta-hedge; spot moves further up; more call buying; loop. The
  ↳ note format: `↳ gamma squeeze setup: dealer short calls, OI 50K+`
- **Vol crush:** post-earnings, IV collapses; long-gamma positions lose
  to theta faster than gamma gains. The ↳ note format: `↳ IV crush risk:
  earnings T-1, IV at 95th %ile`

Both require knowing earnings dates and dealer positioning; the v1
skill flags them only if the user supplies the input.

## What this means for stream output

The `dealer_positioning` is the most-requested upgrade to the skill
based on industry comparables (Cheddar Flow surfaces SpotGamma's GEX as
a top-banner item). The v2 candidate is a dedicated `gex-snapshot`
skill that emits a header line for the stream:

```
GEX: SPY -$2.1B at spot $735 · flip $730.50 (below: dealers short gamma)
```

That feeds the stream as a single context line before any per-print
blocks. v1 leaves this to a separate skill; the rendering reference in
this skill documents the format so v2 lands clean.
