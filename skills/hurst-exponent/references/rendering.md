# Rendering: hurst-exponent

Note-mode. Layout:

1. Header (identity + H + classification tag)
2. Bootstrap band (when computed)
3. Per-block R/S table
4. Take + caveats

## Header

```
Hurst exponent: AAPL · 504d lookback · 502 log returns
H = 0.516 → random walk
```

Line 1: identity + inputs.
Line 2: H value + classification tag. Tags:
- `MEAN-REVERTING` (uppercase for emphasis)
- `random walk`
- `TRENDING` (uppercase)

## Bootstrap band

```
Bootstrap band (n=100): p5 0.472 · p50 0.512 · p95 0.549
```

Omitted when `n_bootstrap=0`. p50 is the bootstrap median; direct H
estimate is the OLS on the full sample, so they may differ slightly.

## Per-block R/S

Compact table of block_size → mean R/S. Shows the reader that R/S is
scaling roughly log-linearly with n (or if it isn't, the log-log fit
may be poor).

## Take

- Mean-reverting: `Mean-reversion setup. Pair strategies, range
  trading, and z-score entries have historically had structural
  edge on this name.`
- Trending: `Trend / momentum setup. Breakout strategies and
  trend-following have historically had structural edge;
  mean-reversion has not.`
- Random walk: `No persistence detected. Neither trend nor
  mean-reversion strategies have a structural edge over the sample.
  Trade the fundamentals, not the tape.`

When bootstrap band crosses both classification thresholds, append:
`Bootstrap band crosses both 0.45 and 0.55 thresholds; regime is
ambiguous.`

## What UI devs do instead

- Rolling Hurst chart over N-day windows to catch regime shifts.
- Cross-name scatter of H vs realized vol.
- Sector heatmap of H values.
