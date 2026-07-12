---
name: signal-decay
description: Estimate the half-life of a candidate signal by computing rolling information coefficient (IC) vs forward returns over a 5-year window and fitting an exponential decay to the IC series. Motivated by 2024-25 factor decay literature showing most published signals have decayed sharply post-publication. Reports the fitted half-life in trading days, the recent vs early IC delta (regime break check), and a full performance tearsheet on the signed-signal PnL. Four built-in signals: momentum, mean_reversion, vol_expansion, trend_break. Requires Stocks Basic. Runs on the free tier.
---

# signal-decay

You hand over a ticker and pick a candidate signal (momentum,
mean-reversion, vol expansion, or trend break). The skill pulls 5 years
of daily bars, builds the signal, computes rolling 63-day IC vs 5-day
forward returns, fits an exponential decay to the IC series, and reports
the half-life in trading days along with a full tearsheet on the signed-
signal PnL.

Motivated by the 2024-25 factor decay literature (Israel-Moskowitz-Ross,
Falck-Rej-Thesmar 2024, Chen-Zimmermann factor zoo) showing most
published signals have decayed sharply post-publication.

## When to invoke

- "Is 20-day momentum still working on SPY?"
- Screening candidate signals before adding to a live strategy
- Auditing a factor that used to work but no longer does
- The user says "signal decay", "factor half-life", "does this still
  work"

Not for: signal discovery. This measures decay of a specified signal;
it doesn't search the space.

## What you need

- A ticker (`--ticker`)
- A signal kind (`--signal-kind`, one of momentum / mean_reversion /
  vol_expansion / trend_break)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--signal-window` (default 20)
- `--forward-horizon` (default 5)
- `--ic-window` (default 63)
- `--lookback-days` (default 1260, ~5 years)

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON**.
Fitted `half_life_trading_days`, `decay_rate_per_day`, `classification`
(fast_decay / moderate_decay / slow_decay / essentially_stable /
not_significantly_decaying), `ic_mean`, `ic_mean_early`,
`ic_mean_recent`, `ic_delta_recent_minus_early`, and a full
`signal_tearsheet` (CAGR, Sharpe, deflated Sharpe p-value, Sortino,
Calmar, max drawdown, ulcer index, profit factor, tail ratio, hit rate
daily + monthly).

**Layer 2: rendered note**. Header + classification + IC stats +
tearsheet block + one-line Take.

## How it works

1. **Pull 5 years of daily bars** for the ticker.
2. **Build the signal** at every bar using the chosen builder.
3. **Compute rolling 63-day IC** = Pearson correlation between signal
   values and forward-5-day log returns within a 63-day window.
4. **Fit exponential decay** to |IC|:
   `|IC(t)| = a * exp(-lambda * t)`. OLS on log |IC| vs t. Slope is
   -lambda. `half_life = ln(2) / lambda`.
5. **Compare recent vs early IC**: mean of last 63-day quarter vs
   first 63-day quarter. Delta < -0.02 fires a regime-break note.
6. **Tearsheet on signed-signal PnL**: sign(signal) applied to
   forward return, scaled to daily equivalent. Full performance stats
   including deflated Sharpe.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST + aggs.
- Internal `quant_garage.backtest.rolling_ic_series` and
  `quant_garage.performance.tearsheet` helpers.

## Output mode: note

Narrative note with a per-signal decay + tearsheet block.

## Endpoints used

- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`
  One call per run.

## Doesn't handle (yet)

- **User-supplied signals.** Currently limited to the four built-in
  builders. A `--signal-file` mode that takes a CSV of custom signal
  values would extend cleanly.
- **Cross-sectional decay.** Applies to one ticker at a time.
  Cross-sectional factor IC (across a universe) is a different lens;
  factor-research covers that.
- **Regime-conditional decay.** No breakdown by regime label. Chain
  with market-regime and change-point-detector for that.
- **Deflation on trials search.** Deflated Sharpe corrects for search
  bias but only if you tell it n_trials. Default assumes 1; tune the
  helper directly if you've grid-searched N signals.

These are clean PR extensions.
