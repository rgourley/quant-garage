# historical-analog-finder

Regime-conditional forecasting. Takes today's SPY-based regime feature
vector, finds the K nearest historical periods, and reports the
forward return distribution across those analogs.

## Quick start

```bash
python3 examples/run-historical-analog-finder.py --format render
python3 examples/run-historical-analog-finder.py --k 30 --horizons 30,60,90,252 --format render
```

## What you get back

```
Historical Analog Finder — 2026-07-03
K=20 nearest analogs over 20y history · Benchmark SPY · Feature set: 9 regime features

Current regime snapshot (raw · z-score):
  spy_ret_5d                            +1.4%  ·  z +0.49
  spy_ret_60d                          +13.0%  ·  z +1.39
  spy_above_sma_50                        yes  ·  z +0.69
  spy_rsi_14                             54.6  ·  z -0.08

Forward SPY return distribution across 20 analogs:
 Horizon     n       p10       p25    median       p75       p90      mean     >0
     30d    20     -7.6%     -5.3%     -0.6%     +2.8%     +4.3%     -1.9%    45%
     60d    20     -8.5%     -0.6%     +1.8%     +5.5%     +8.2%     +1.2%    75%
     90d    20     -7.6%     -2.5%     +3.8%     +8.6%    +10.4%     +2.6%    70%
    252d    20     -7.8%     +3.3%    +14.3%    +18.3%    +20.1%     +9.9%    75%

Top analog dates (nearest first):
  2021-05-28  (z-dist 0.79)  ->  30d +3.7% · 60d +6.7% · 90d +3.5% · 252d -1.1%
  ...
```

## Methodology

- Feature set: 9 SPY-derived features (returns at 4 windows, above 50/
  200-day flags, RSI 14, realized vol, drawdown from 252d high).
- Distance: z-scored Euclidean.
- Dedupe: reject any candidate within 30 calendar days of an already-
  accepted analog so one crisis window can't dominate the K.
- Search space excludes the last 90+ days of history to avoid look-
  ahead.

## Plan requirement

Stocks Starter — one 20-year SPY range-aggs call. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
