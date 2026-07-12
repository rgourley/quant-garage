---
name: rough-vol-forecast
description: Rough-volatility-scaled vol forecast (Bayer-Friz-Gatheral 2016) for a ticker across multiple horizons. Under rough vol, realized vol scales as h^H with H around 0.14 empirically (Livieri et al. 2018), much slower than the sqrt(t) growth of Brownian motion. This dampens long-horizon extrapolation and lifts short-horizon estimates. Reports the rough-vol forecast alongside traditional Brownian scaling and EWMA for direct comparison at each horizon. Requires Stocks Basic. Runs on the free tier.
---

# rough-vol-forecast

You hand over a ticker and a set of forecast horizons (default 1, 5,
20, 60, 120 trading days). The skill fits daily-return realized vol on
a 2-year window, then applies three vol-scaling models across each
horizon:

- **Traditional Brownian**: sigma(h) = sigma_daily × sqrt(h). Standard
  sqrt-time scaling.
- **EWMA (RiskMetrics)**: same sqrt-time scaling but on a
  decay-weighted vol estimate that responds faster to recent regime.
- **Rough vol (Bayer-Friz-Gatheral 2016)**: sigma(h) = sigma_daily ×
  h^H with H = 0.14 (Livieri et al. 2018 empirical default). Damps
  long-horizon growth substantially.

Answers "how much does horizon really matter for vol?" — which turns
out to be the big 2024-25 vol modeling debate.

## When to invoke

- "What's my 60-day forward vol on SPY?"
- Comparing vol assumptions in options pricing / position sizing
- Auditing whether sqrt-time scaling is over-estimating your
  scenario vol
- The user says "rough vol", "Bayer Friz Gatheral", "vol scaling",
  "horizon vol"

Not for: options pricing (this is not a calibrated rBergomi engine).
Not for regime detection (use change-point-detector or
market-regime).

## What you need

- A ticker (`--ticker`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--horizons` (default `1,5,20,60,120`)
- `--lookback-days` (default 504)
- `--hurst` (default 0.14, Livieri et al. 2018 estimate on daily
  equity data)
- `--ewma-lambda` (default 0.94, RiskMetrics)

## What you get back

Two output layers.

**Layer 1: canonical JSON**. Per-horizon `traditional_vol`,
`ewma_vol`, `rough_vol`, and `rough_over_traditional` ratio. Plus
`realized_annualized_vol`, `ewma_annualized_vol`, `hurst_used`, and
`hurst_estimated_on_returns` (for transparency, not used as default).

**Layer 2: rendered note**. Header + per-horizon table with the
three vol estimates side by side and the rough-vs-traditional ratio,
one-line Take.

## How it works

Rough volatility literature: realized vol has Hurst exponent H ~
0.05-0.20 empirically on financial series (Bayer-Friz-Gatheral 2016
established the framework; Livieri et al. 2018 estimated H ~ 0.14 on
daily equity data). Under rough vol, sigma(h) scales as h^H rather
than h^(1/2). For H < 0.5, this:

- **Damps long horizons**: 120-day vol forecasts drop meaningfully
  vs sqrt-time.
- **Lifts short horizons**: 1-day vol edges higher (though the
  effect is small at h=1).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST + aggs.
- Internal `quant_garage.monte_carlo.rough_vol_annualized` helper.

## Output mode: note

Narrative note with a per-horizon table. Fewer than 10 numbers per
run; table reads better than pure prose.

## Endpoints used

- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`
  One call per run.

## Doesn't handle (yet)

- **rBergomi Monte Carlo path simulation**. The
  `simulate_rough_vol_paths` helper is in `quant_garage.monte_carlo`
  and can be called directly, but it isn't yet wired into
  position-sizer or mc-portfolio-simulator as `--vol rough`. Clean
  extension.
- **Options-implied H calibration**. Real rBergomi calibration uses
  the options surface; this skill uses returns.
- **Multi-name H estimation**. Reports one H per run. Cross-name
  comparison is a workflow, not this skill.
- **Regime-conditional H**. Rough-vol H can shift with regime; this
  reports a single window estimate.

These are clean PR extensions.
