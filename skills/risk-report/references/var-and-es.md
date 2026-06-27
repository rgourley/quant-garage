# Value at Risk and Expected Shortfall

The two questions VaR answers: "at what loss level do I cross the
threshold I care about?" and "how often does the book lose more than
that?" Expected Shortfall (ES, aka CVaR) answers the harder one:
"given that we DID cross that threshold, how bad was it on average?"

## The math, in two flavors

**Historical VaR.** Take the daily portfolio return series. Sort it
ascending. The VaR at confidence `c` is the negation of the
`100 × (1 - c)` percentile.

```
VaR_historical = -percentile(returns, 100 * (1 - confidence))
```

For confidence 0.95, that's the 5th percentile. If the empirical
5th percentile is -0.022, historical VaR is reported as 0.022 — a
positive number meaning "5% of days the book lost more than 2.2%."

**Parametric (Gaussian) VaR.** Fit a normal distribution to the
returns. Use the closed-form quantile.

```
VaR_parametric = z * sigma_daily - mu_daily
                 z = Phi^-1(confidence)
```

For confidence 0.95, z ≈ 1.645. With a zero-mean and 2% daily std,
parametric VaR ≈ 0.033.

## Why both

Historical VaR is the empirical truth — exactly what happened — but
it's noisy on small samples. With 252 days, the 1% VaR is read off
the 2.5th observation; a single outlier moves it. With 60 days the
estimate is essentially decorative.

Parametric VaR is smooth and stable but assumes the return
distribution is Gaussian. Equity returns aren't. They have fat
tails, skew, and serial correlation. Parametric VaR almost always
UNDERSTATES tail risk because the normal distribution's tails decay
too fast.

Reporting both lets the consumer see the gap. When historical >
parametric, the empirical distribution has fatter tails than the
Gaussian fit predicts — the more common case. When parametric >
historical, the sample window has been quiet relative to its own
volatility, and you should not get comfortable.

## Expected Shortfall: the more honest cousin

VaR has a known weakness: it ignores the shape of the tail beyond
the threshold. A book that loses 3% on its worst 5% of days is
treated identically to a book that loses 8% on its worst 5% of days,
as long as the 5th-percentile day is -2.5% in both. The fat-tailed
book is obviously riskier; VaR doesn't see that.

Expected Shortfall (ES) is the mean of the tail.

```
ES_historical = -mean(returns where returns <= VaR_threshold)
```

It captures the average severity of the bad-day distribution
conditional on being a bad day. Two books with identical VaR but
different ES tell you which one really hurts when it hurts.

Gaussian ES has a closed form:

```
ES_parametric = -(mu - sigma * phi(z) / (1 - confidence))
                 z = Phi^-1(1 - confidence)
                 phi(z) = standard normal PDF at z
```

Same fat-tail caveat: the Gaussian ES will understate the realized
ES on most equity baskets.

## Sample-size rules

The skill enforces n ≥ 30 for any VaR or ES estimate. The 99% VaR on
30 observations reads off the 0.3-th percentile, which is
extrapolation from a single observation. The 99% on a 252-day window
reads off the 2.52-th observation, which is the 2nd and 3rd worst
days blended. Still noisy, but defensible.

In `tier_caveats` every run gets a "tail estimates noisy when n=N"
warning. Risk officers should treat the 99% number as a soft
indicator at 252 days; the 95% number is sturdier.

## Sign convention

All VaR and ES values in the JSON are **positive numbers
representing loss magnitudes.** `0.022` means "2.2% loss." The
rendered output prepends a minus sign (`-2.2%`) so PMs read it the
way they think about it. The signed convention shows up in the
schema as `historical`, `parametric`, `expected_shortfall_*`.

This matches industry standard. Bloomberg, RiskMetrics, and most
risk management software report VaR as a positive loss.

## Edge cases the math handles

- **Rounding-tight tail.** On very small samples or repeated values,
  the historical VaR threshold can sit exactly on a value with
  nothing strictly below it. ES then has no observations to average.
  The lib falls back to the single worst-day return rather than
  raising — the resulting ES equals or slightly exceeds VaR, which
  is the correct interpretation.
- **Zero variance.** A constant return series has zero std, so the
  Gaussian z * std = 0. Parametric VaR collapses to `-mu`. The
  historical version reads the same value at every percentile.
  Trivial but mathematically consistent.
- **All-positive returns.** VaR can come out negative (i.e., a "loss"
  level that's actually a gain). The skill emits this as-is; the
  consumer sees that the empirical 5th percentile is +0.4% and
  knows the lookback window has been exceptional.
