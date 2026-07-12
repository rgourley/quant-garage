# Methodology: pairs-scanner

The compute layer sits in `quant_garage/skills/pairs_scanner.py`. This
file explains the choices, not the code.

## Log prices, not levels

Cointegration is fitted on log prices. Two reasons:

1. **Multiplicative invariance.** A pair like KO-PEP should be
   tradeable regardless of whether the base prices are $30 and $150
   or $300 and $1500. Log prices normalize scale.
2. **Beta interpretation.** With `log(y) = alpha + beta * log(x)`,
   beta is an elasticity: a 1% move in x maps to a beta% move in y.
   That's the ratio a trader hedges with.

The correlation prefilter uses log RETURNS, not log prices. Log-price
correlations are spuriously high for any two non-stationary series
that trend; log-return correlations measure actual co-movement.

## Engle-Granger direction selection

The Engle-Granger two-step regresses one series on the other, then
tests the residual for stationarity. The choice of which side is the
dependent affects the residual (though the two residual series are
close for well-cointegrated pairs).

Standard practice: fit both directions, pick the one with the more
stationary residual (more negative ADF t-stat). The chosen dependent
is what the pair label reports as the left-hand ticker. Trading
implication: the hedge ratio `beta` says "for every $1 long in
`independent`, hold `beta * $1` (short if beta > 0) in `dependent`."

## ADF: lag=1, no constant

The Dickey-Fuller regression is `Δe_t = γ · e_{t-1} + ε_t`. No
constant (the residual comes from a regression with intercept, so its
mean is ~0 by construction, and adding a second constant over-fits).
No augmenting lags: lag=1 is the plain Dickey-Fuller test. Augmenting
lags with AIC selection is standard for daily equities and would
sharpen edge cases; queued.

The t-statistic is `γ / SE(γ)`. Compare against MacKinnon 2010
critical values for N=2 cointegration with constant:

- 1% critical value: -3.90
- 5% critical value: -3.34
- 10% critical value: -3.04

These are *tighter* than standard ADF critical values (roughly -3.43
at 5%) because the residual is not directly observed: it comes from
a regression, and testing on estimated residuals biases the standard
distribution.

The reported `pvalue_upper_bound` is the upper bound on the p-value
implied by the bucket. A pair with `adf_tstat = -4.5` gets
`bucket = significant_1pct` and `pvalue_upper_bound = 0.01`. The
real p-value is smaller, but the bucket is enough for a screen. When
you need exact p-values, statsmodels' `coint()` returns them via
interpolation of the MacKinnon tables.

## OU half-life

Ornstein-Uhlenbeck models the residual as reverting toward zero at
rate θ. Discrete-time OLS: `Δe_t = a + b · e_{t-1} + ε_t`, so
`b = -θ`. Half-life = `ln(2) / θ`, expressed in trading days.

Returned as `null` in three cases:

- `b >= 0`: no mean reversion; the residual drifts, not reverts.
- `hl < 0.5 days`: implausibly fast, usually a fit artifact.
- `hl > 365 days`: implausibly slow, effectively no reversion on any
  practical horizon.

The half-life is the historical tempo, not a forecast. Under a stable
OU process, the spread should close half the way to its mean in
`hl` days. In practice, half-life shifts with the regime, which is
why the OS stability check exists.

## 70/30 out-of-sample stability

Naïve cointegration screens overstate their edge because the same
sample fits the beta AND tests the residual. The 70/30 split is a
minimum honest check:

1. Fit `beta` on the first 70% of the sample.
2. Apply that `beta` to the last 30% to compute out-of-sample
   residuals `e_os = y_os - alpha_is - beta_is * x_os`.
3. Compute the OS residual std and the IS residual std.
4. Ratio `os_std / is_std` above 1.5 flips `stability_label` to
   `regime_shift`.

The 1.5x threshold is judgment, not statistics. It caught the
consumer-staples pairs that broke in 2020 (COVID spike) in
back-of-envelope testing. Callers who need more sensitivity can
filter the JSON on `os_std_ratio` directly.

`insufficient_os_sample` fires when the OS chunk has fewer than 20
bars. A 60-bar total sample splits into 42 IS / 18 OS and gets the
flag; that's rare in practice with the default 252-day lookback.

## Current z-score

`z_current = (residual_last - spread_mean) / spread_std`. Positive
means the dependent is above its fitted level relative to the
independent (spread is wide); negative means below (spread is wide
in the other direction).

The `z_entry` filter uses `abs(z_current)`. Direction of the trade
is implied by the sign: `z > 0` says short dependent / long
independent; `z < 0` says the opposite.

## What this doesn't estimate

- **Confidence intervals** on beta, half-life, or z_current. Point
  estimates only. Bootstrap CIs would let callers filter on
  `hl_upper_bound < 60` instead of the point estimate; queued.
- **Cost of the trade.** No borrow-cost, spread cost, or short-side
  fees. `slippage-cost` and `position-sizer` handle those.
- **Regime probability.** `stability_label` is binary. A Markov
  regime-switching cointegration model would give a probability of
  the current regime; queued.
- **Higher-dimensional cointegration.** Two variables only. Johansen
  for triples / quads is queued as a separate skill.

## Reading list

- Engle & Granger (1987), *Co-integration and Error Correction*.
- MacKinnon (2010), *Critical Values for Cointegration Tests*:
  the source of the critical values used here.
- Ornstein-Uhlenbeck process: any stochastic-calculus text.
  For a stat-arb angle, see Ehrman *The Handbook of Pairs Trading*
  (2006) or Gatev, Goetzmann, Rouwenhorst (2006), *Pairs Trading:
  Performance of a Relative-Value Arbitrage Rule*.
