# Realized volatility methodology

This skill measures realized volatility two ways: a "current" 24h
window and a "trailing 30d distribution" used to percentile-rank the
current window.

## Definition

Realized vol is annualized standard deviation of close-to-close log
returns. For a window of N consecutive bars with closing prices
`c_0, c_1, ..., c_N`:

```
r_i  = ln(c_i / c_{i-1})       for i in 1..N
σ    = stdev(r_1, ..., r_N)
RV%  = σ × √(periods_per_year) × 100
```

For hourly bars, `periods_per_year = 24 × 365 = 8760`. For daily bars,
`periods_per_year = 365` (crypto trades 24/7, no calendar adjustment).

The skill defaults to **hourly close-to-close log returns** for the
current 24h window. That gives n=24 returns, which is the minimum for
a defensible stdev estimate. Smaller windows (15-min, 5-min) would
give more samples but introduce microstructure noise that inflates
realized vol for thin names (DOT, AVAX, LINK).

## Why not Parkinson or Garman-Klass

The classical estimators improve precision by using the OHLC range
instead of just close-to-close:

- **Parkinson:** uses `ln(H/L)`, more efficient than close-to-close
  by a factor of ~5x for symmetric Brownian motion
- **Garman-Klass:** uses all four prices, ~7x more efficient

They're great for low-frequency data (e.g. daily) when sample size is
tight. At hourly resolution with 24 samples, close-to-close is already
defensible and avoids the assumption-of-no-drift baggage Parkinson and
GK both carry. Crypto frequently trends intra-window (e.g. a slow grind
up over 6 hours), and Parkinson over-estimates vol when there's drift.

If a future version of the skill wants 5-minute or 1-minute bars,
switch to Garman-Klass. At hourly, simple stdev wins.

## The trailing 30d rolling distribution

To answer "is current vol high vs normal," compute a rolling 24h
realized vol every hour over the trailing 30 days, then take the
current window's percentile in that distribution.

Concretely: 30 days × 24 hourly steps = 720 rolling-24h windows. Each
window's σ is computed from the 24 hourly log returns inside it. The
sorted set of those 720 σ values is the empirical distribution; the
current window's σ is converted to a percentile by counting how many
historical windows it exceeds.

Threshold for flagging: `percentile > 0.90` (top 10% of trailing 30
days). Above 0.95 is rare and worth surfacing as a high-impact event.

## Why annualized

Crypto desks read realized vol in annualized %. A BTC trader knows
that 40% is "normal", 80% is "elevated", and 150% is "panic." Raw
hourly stdev (e.g. 0.012 = 1.2% per hour) doesn't carry that intuition.
Annualizing makes the number directly comparable to options-market IV
(also annualized) and to historical regime labels.

## Sample-size warnings

- 24 hourly returns is enough for a point estimate but the confidence
  interval is wide (~±15% relative). The skill emits both the absolute
  value and the percentile, and the percentile is the more robust
  signal.
- 30 days × 24 = 720 rolling windows is enough to estimate the 90th
  percentile reliably. The 99th percentile would need 3000+ samples to
  pin down; the skill caps at the 95th-percentile flag to stay
  defensible.
- Empty or sparse hourly aggs (rare for the top 10 by liquidity) cause
  the skill to skip that ticker with a `reason` in `skipped_tickers`.

## Numbers a crypto desk reads as normal

| Asset      | Normal RV range | Elevated  | Panic  |
|------------|-----------------|-----------|--------|
| BTC        | 30-60%          | 60-100%   | 100%+  |
| ETH        | 40-80%          | 80-120%   | 120%+  |
| SOL / AVAX | 60-120%         | 120-180%  | 180%+  |
| Memecoins  | 100-200%        | 200-300%  | 300%+  |

These ranges shift across regimes (the 2023 bear market had BTC RV in
the 20-30% range for months); the percentile rank vs trailing 30d is
the regime-adjusted version of these tables.
