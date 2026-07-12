---
name: pairs-scanner
description: Screen every pair in a basket for cointegration on daily closes and rank the tradeable ones by spread z-score. Runs the Engle-Granger two-step on log prices, tests the residual with a Dickey-Fuller t-stat against MacKinnon 2010 critical values, estimates the Ornstein-Uhlenbeck half-life of mean reversion, and flags out-of-sample regime shifts via a 70/30 residual std ratio. Emits per-pair hedge ratio, ADF t-stat and p-value bucket, half-life, current z-score, and a stability label. Use when a PM or stat-arb desk has a sector basket and asks "which two names are statistically tethered right now, and how wide is the spread." Requires Stocks Starter.
---

# pairs-scanner

You hand over a basket of tickers. The skill tests every pair for
cointegration, estimates the historical tempo of mean reversion, and
flags pairs where the spread is currently wide enough to trade.

This is a screen, not a strategy. It tells you which pairs are worth
looking at, with the statistics that back the claim: hedge ratio,
Engle-Granger t-stat, OU half-life, current z-score, and an
out-of-sample stability read. The trader decides sizing, execution,
and stop rules from there.

## When to invoke

- A stat-arb desk with a 5-30 name sector basket asks "which pairs
  are cointegrated and currently wide"
- A PM building a pairs book wants to see the hedge ratio and
  mean-reversion tempo before committing
- The user says "run a pairs scan", "find cointegrated pairs",
  "test pair X-Y for mean reversion", "which of these are tethered"
- Follow-up work after `sector-rotation-signal` narrows to a single
  sector and you want the within-sector pairs read

Not for: universe-wide screens (this is O(n²); a 100-name basket is
4,950 pairs, too many to eyeball). Not for high-frequency pairs (this
uses daily closes; intraday cointegration needs tick data).

## What you need

- A basket of tickers (`--basket`, required, comma-separated)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter plan minimum (unlimited REST for daily aggs)

Optional:

- `--lookback-days` (default 252): trading-day window for the fit
- `--min-correlation` (default 0.6): |Pearson rho| on log returns
  below which a pair is skipped without a cointegration test
- `--min-pvalue` (default 0.05): EG bucket ceiling for the tradeable
  flag
- `--min-halflife` (default 2) and `--max-halflife` (default 60):
  half-life bounds in trading days
- `--z-entry` (default 2.0): minimum |z_current| to flag tradeable

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-pair `hedge_ratio_beta`, `adf_tstat`, `cointegration_bucket`,
`pvalue_upper_bound`, `half_life_days`, `z_current`, `os_std_ratio`,
`stability_label`, and a `tradeable` boolean with rejection reasons
when false. Top-level `tradeable[]` is sorted by |z_current|
descending; `considered_but_rejected[]` is sorted by ADF t-stat.

**Layer 2: rendered table**. Two sections: TRADEABLE (widest spreads
first), then CONSIDERED BUT REJECTED with per-pair rejection reasons.
See [`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily closes** for every ticker over `lookback_days * 1.6 + 21`
   calendar days via `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
   Intersect dates so every ticker has a close on every sample date.
2. **Prefilter on log-return correlation**. |Pearson rho| below
   `min_correlation` means the pair doesn't co-move enough to bother
   testing. Skipped pairs land in `skipped_correlation[]`.
3. **Engle-Granger two-step in both directions**. OLS log(A) on log(B)
   and log(B) on log(A). Pick the direction whose residual has the
   more negative ADF t-stat (the more stationary residual). The chosen
   dependent goes first in the pair label.
4. **Dickey-Fuller t-stat** on the residual, no constant, lag=1.
   Compared against MacKinnon 2010 critical values for N=2 cointegration
   with constant: -3.90 (1%), -3.34 (5%), -3.04 (10%).
5. **OU half-life** from OLS of Δresidual on lagged residual.
   half_life = ln(2) / θ, where θ = -slope. Returned as `None` when
   θ ≤ 0 (no mean reversion) or the estimate falls outside [0.5, 365]
   days.
6. **Current z-score** = (residual_last - mean) / std.
7. **Out-of-sample stability**. Split the sample 70/30. Fit beta on
   the in-sample chunk, apply it to the out-of-sample chunk, compare
   residual std ratio. `stable` when OS std < 1.5x IS std, else
   `regime_shift`.
8. **Tradeable flag** requires: p-value bucket ≤ `min_pvalue`, half-life
   in `[min_halflife, max_halflife]`, |z_current| ≥ `z_entry`, and
   stability_label ≠ `regime_shift`. Any failing filter goes into
   `tradeable_rejections[]`.

Methodology detail lives in [`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and the `/v2/aggs` daily endpoint conventions.

## Output mode: table

Two-section table. TRADEABLE (widest spreads first) is the headline;
CONSIDERED BUT REJECTED gives the transparency that says "here's what
the scan looked at and why it didn't recommend those."

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  One call per ticker per run.

## Doesn't handle (yet)

- **Johansen test** for higher-dimensional cointegration (triples,
  quads). Engle-Granger is a two-variable test. A basket-wide
  cointegration lens would need Johansen; queued.
- **Augmented lag selection**. The ADF uses lag=1 (Dickey-Fuller
  proper). AIC-selected augmenting lags are standard for daily equities
  and would sharpen edge cases; queued.
- **Rolling-window stability** past the single 70/30 split. A rolling
  60-day residual std series would show *when* the regime shifted,
  not just that it did; queued.
- **Half-life confidence interval**. Reported as a point estimate.
  Bootstrap CI would let a caller filter on `hl_upper_bound < 60`
  instead of the point value; queued.
- **Cost model**. No hedge ratio to notional, no borrow-cost or
  transaction-cost drag. Pair with `slippage-cost` or
  `position-sizer` when moving from screen to trade.
- **Sector membership**. `--basket` is caller-supplied. No auto-fill
  from a sector or industry; queued as `--sector` in v2.

These are clean PR extensions. The output schema is forward-compatible.
