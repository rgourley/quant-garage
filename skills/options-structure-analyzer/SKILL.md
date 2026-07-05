---
name: options-structure-analyzer
description: Given a view (direction_bullish, direction_bearish, vol_long, vol_short, hedge), a horizon, and a target move, enumerate candidate options structures (long call/put, bull/bear spreads, straddles, strangles, iron condor, protective put, collar), compute payoff-at-target, and rank by payoff/capital. Not a black-box recommendation — a structured comparison so the operator picks the structure whose tradeoffs match the view. Use when the operator has a directional or vol thesis and wants to see the options tradeoffs side by side.
---

# options-structure-analyzer

You hand over a ticker, a view (`direction_bullish`, `direction_bearish`,
`vol_long`, `vol_short`, or `hedge`), a horizon in days, and a target
move. The skill fetches the nearest expiry with priceable legs on
both sides, enumerates the candidate structures for your view,
computes payoff-at-target for each, and ranks them.

Not a recommendation. A structured comparison so you can pick the
structure whose tradeoffs match your thesis — capped-risk spread vs
unbounded-upside long, straddle vs strangle premium tradeoff, collar
credit vs protective-put cost.

## When to invoke

- The operator has a view and wants to know "how do I express this
  with options" — the answer depends on the tradeoff they prefer
- Comparing single-leg (long call) vs two-leg (spread) vs multi-leg
  (condor) structures before deciding
- Sizing hedges on an existing position (protective put vs collar)
- The user says "options structure", "how do I trade this with
  options", "should I buy the call or the spread"

## What you need

- `MASSIVE_API_KEY` with an options entitlement (Options Developer or
  higher). Chain snapshot is the primary data pull.

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Per-structure block with legs (buy/sell + type + strike + ticker +
premium + qty), net debit/credit, max profit, max loss, breakevens,
capital required, and payoff at your target price.

**Layer 2 rendered comparison**. One block per structure with a
plain-English read, legs listed, key metrics, and payoff-at-target.
On hedge structures, the payoff line includes "vs unhedged" delta
rather than a meaningless percent-of-capital ratio. See
[`references/rendering.md`](./references/rendering.md).

## Views supported

- `direction_bullish` — long call, bull call spread
- `direction_bearish` — long put, bear put spread
- `vol_long` — long straddle, long strangle
- `vol_short` — short iron condor
- `hedge` — protective put, collar (assumes 100 shares long
  underlying)

## How it works

1. **Fetch spot** via the snapshot endpoint. Walks a strict fallback
   chain (lastTrade > min > day > prevDay) that rejects zero values.
2. **Fetch chain snapshot** filtered to strikes within +/- 40% of
   spot and expiries in the target horizon window (target +/- ~30d).
3. **Pick the nearest expiry** with both calls and puts available.
4. **Build each candidate structure**: find contracts by ATM or OTM
   percentage, use `day.close` (or `fmv` fallback) as the entry
   price. Structures that lack a priceable leg are skipped.
5. **Compute payoff-at-target** analytically per structure.
6. **Rank** by payoff / capital (for directional/vol) or by the
   hedge value (P&L improvement vs unhedged for hedge structures).

## Endpoints used

- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` (spot)
- `GET /v3/snapshot/options/{ticker}` (chain)

## Doesn't handle (yet)

- **Prices are day.close, not live.** Delayed on non-realtime
  entitlements; every render surfaces this caveat.
- **Payoff-at-target assumes underlying at target price AT
  EXPIRATION.** Intra-life value depends on IV, theta, and time to
  expiry — not modeled.
- **Greeks omitted when the chain snapshot returns empty greeks**
  (chain endpoint doesn't populate greeks on all keys/tiers). No
  delta/vega/theta context in the current output.
- **Assignment risk and dividend risk on short legs** are ignored.
  Real selection between (say) a bull-call spread and a bull-put
  spread must factor these in outside the tool.
- **Multi-underlying spreads** (calendars, diagonals across expiries)
  are not enumerated. Same-expiry structures only.
