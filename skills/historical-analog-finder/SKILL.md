---
name: historical-analog-finder
description: Regime-conditional forecasting. Takes today's market-regime feature vector (5/20/60/120-day return, above 50/200-day SMA, RSI, realized vol, drawdown from 252-day high) and finds K historical periods with the most similar setup via z-scored Euclidean distance. Deduplicates overlapping matches so one crisis window doesn't dominate. Reports the forward SPY return distribution at multiple horizons across accepted analogs. Use when the operator wants "what usually happens next from setups like this," honest about the IQR rather than a point forecast.
---

# historical-analog-finder

You hand over K (default 20) and horizon_days (default [30, 60, 90,
252]) and get back the K historical periods most similar to today's
market regime, plus the forward SPY return distribution across those
analogs.

Regime-conditional forecasting. The mean is not a point forecast; the
IQR is the honest read.

## When to invoke

- The operator asks "what usually happens after a setup like this",
  "any historical analogs to now", "regime analog"
- Portfolio-review workflow: after market-regime tells you WHAT, this
  tells you WHAT USUALLY FOLLOWS
- Sizing / cash-management decisions where forward return
  distribution matters more than a point estimate

## What you need

- `MASSIVE_API_KEY` (Stocks Starter). One SPY range-aggs call for the
  full history window (default 20 years).

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Current regime feature snapshot (raw + z-scores), the K analogs with
distance + forward returns per horizon, forward return distribution
stats per horizon (p10, p25, median, p75, p90, mean, hit rate > 0).

**Layer 2 rendered note**. Current-regime snapshot block, forward-
distribution table across horizons, top-analog date list with per-
horizon returns. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Fetch SPY** over `history_years` (default 20).
2. **Compute a daily feature panel**: for every day with enough
   history (>= 260 bars), build a 9-dim vector:
   - 5/20/60/120-day return (4 features)
   - Above 50-day SMA (binary)
   - Above 200-day SMA (binary)
   - RSI 14
   - 20-day realized vol
   - Drawdown from 252-day high
3. **Z-score the panel column-wise**. Today's z-score vector is the
   reference.
4. **Compute Euclidean distance** from today to every prior day.
5. **Rank by distance**, then dedupe: reject any candidate within
   `min_gap_days` (default 30) of an already-accepted analog so one
   historical window doesn't dominate.
6. **Look up forward SPY returns** at each requested horizon for each
   accepted analog.
7. **Aggregate to distribution stats** per horizon.

## Endpoints used

- `GET /v2/aggs/ticker/SPY/range/1/day/{from}/{to}` (one call, 20yr
  history)

## Doesn't handle (yet)

- **SPY-only feature set.** Sector rotation and rates aren't
  captured. A richer analog would fold in sector-rotation-signal +
  fixed-income-context features. Documented as a caveat.
- **Regime-conditional forecasting works UNTIL the world changes
  structurally.** Analog periods pre-2008, pre-QE, or pre-2020 sample
  from different macro machinery.
- **The mean is not a forecast.** Every render surfaces the IQR and
  hit-rate-above-zero as the honest reads.
- **K < requested when history is thin.** With 20-year history and
  30-day dedupe, K=20 is achievable, but a caller passing K=50 will
  often see fewer than 50 accepted. The payload reports the actual
  count.
