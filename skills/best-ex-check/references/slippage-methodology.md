## Slippage methodology

Basis-point math, signed by side. The formula is the same one every
execution desk uses; what matters is consistency in sign convention so
that "positive bps is bad" reads the same way across every flagged
fill regardless of side.

## The core formula

```
slippage_bps = (fill_price - reference_price) / reference_price × 10000
```

Then sign-adjust by side:

- **BUY**: positive bps means paid more than reference (bad).
  `signed_slippage_bps = +slippage_bps` (no flip needed; the buy paid
  above reference)
- **SELL**: positive bps means sold above reference (good). Flip it
  so positive always reads as bad:
  `signed_slippage_bps = -slippage_bps`

After the sign flip, the rule across both sides is identical: positive
is bad, negative is good. The exception report shows positive as the
red number to investigate.

## Reference price

The reference price depends on what you're comparing to:

- **Inside-quote slippage**: ask on a BUY (the offer the buyer should
  pay), bid on a SELL (the bid the seller should hit). This is the
  "did I get the inside" measure. A BUY at $100.10 against an ask of
  $100.08 is +20bps. A BUY at $100.08 against the same ask is 0bps
  (exact inside-take, which is the expected outcome for a market order
  against a normal-spread name).
- **Midpoint slippage**: `(bid + ask) / 2`. Used in some compliance
  contexts where price improvement against the mid is the metric. The
  skill emits the inside-quote slippage by default; mid-slippage is
  computable from the same fields (`reference_bid`, `reference_ask`
  in the schema) downstream.
- **VWAP slippage**: see below.
- **Arrival price slippage**: requires knowing the parent order's
  arrival timestamp, which isn't in the fill-level CSV. Not computed
  in v1.

## VWAP slippage

Session VWAP is the volume-weighted average price across the session
up to the fill timestamp. Computed from minute aggregates:

```
session_vwap_at_fill = sum(bar.vw * bar.v for bar in [open, fill_time]) / sum(bar.v for bar in [open, fill_time])
```

Then:

```
vwap_slippage_bps = (fill_price - session_vwap_at_fill) / session_vwap_at_fill × 10000
```

Sign-adjusted the same way: positive bps on a BUY = bought above VWAP
(bad on average); positive bps on a SELL after sign flip = sold below
VWAP (bad on average).

The 25bp threshold for `high_vwap_slippage` is a deliberate choice.
Most large-cap names trade in a daily range of 50-150bp; a 25bp
deviation from VWAP at a single fill represents meaningful timing
risk that the execution algorithm or trader took. Below 25bp is
typically inside the normal microstructure noise.

VWAP slippage is informational, not a violation. A trader who bought
at the morning low and watched VWAP drift up will look "bad" on VWAP
slippage even though the execution was excellent. The flag tells the
operator "this fill deserves a story," not "this fill was wrong."

## Adverse-selection bps

See [`adverse-selection.md`](./adverse-selection.md) for the
methodology. The bps value is the price movement against the trader
in the 30 seconds after the fill, signed so positive = adverse:

```
adverse_bps = (final_price - fill_price) / fill_price × 10000  # for a SELL (price rose against the seller)
adverse_bps = (fill_price - final_price) / fill_price × 10000  # for a BUY (price fell against the buyer)
```

## Implementation shortfall

The dollar cost of the slippage on each fill:

```
implementation_shortfall_usd = abs(signed_slippage_bps) / 10000 * fill_price * qty
```

Summed across all flagged fills, this is the headline number for the
PM: "what did this session's execution problems cost us?" The
exception report shows it in the summary block.

## Sign-convention examples

Walking through a few cases to make the signs unambiguous:

- BUY 1,000 AAPL at $300.85 vs reference ask $300.31:
  - `slippage_bps = (300.85 - 300.31) / 300.31 * 10000 = +17.98`
  - Sign for BUY: no flip. `signed_slippage_bps = +17.98`
  - Reads as: paid 18bps above the inside ask. Bad.

- SELL 500 TSLA at $379.40 vs reference bid $380.10:
  - `slippage_bps = (379.40 - 380.10) / 380.10 * 10000 = -18.42`
  - Sign for SELL: flip. `signed_slippage_bps = +18.42`
  - Reads as: sold 18bps below the inside bid. Bad.

- SELL 500 TSLA at $380.60 vs reference bid $380.10:
  - `slippage_bps = (380.60 - 380.10) / 380.10 * 10000 = +13.15`
  - Sign for SELL: flip. `signed_slippage_bps = -13.15`
  - Reads as: sold 13bps above the inside bid (price improvement).
    Good. Not flagged.

The skill emits `signed_slippage_bps` (after the sign flip) as
`slippage_bps` in the output schema. Always positive = bad.

## Why basis points, not percentages

Buy-side execution desks read TCA in basis points. Reading "0.18%"
forces the analyst to translate to "18bps" mentally. The exception
report stays in basis points throughout. Internal dollars are
reported in dollars (implementation shortfall), not bps.
