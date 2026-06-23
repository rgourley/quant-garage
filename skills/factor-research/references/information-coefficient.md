# Information coefficient

The information coefficient (IC) is the rank correlation between a
factor score and the forward return it's supposed to predict. It's the
single most important quant diagnostic for a factor: positive and
stable IC = real predictive power; near-zero or negative IC = noise or
the wrong sign.

## Computation

Per rebalance period t (monthly in this skill):

```
ic_t = spearmanr(factor_score_i_at_t, forward_return_i_t_to_t+h)
```

Where `i` indexes the universe at period `t` and `h` is the forward
horizon (1M, 3M, 6M, 12M). Spearman rather than Pearson because
factors and returns often have nonlinear monotone relationships;
rank correlation is robust to outliers and non-Gaussianity.

The skill uses `scipy.stats.spearmanr` and drops `(score, return)`
pairs where either is null.

## Mean IC across the window

The headline IC reported per factor per horizon is the simple mean
across all monthly observations:

```
mean_IC_h = (1/T) * sum_t ic_t_h
```

Where T is the number of rebalance months that had at least 30
non-null observations (the minimum for a stable rank correlation).
Months with fewer observations are dropped from the average and
recorded as `n_months_dropped`.

## Standard error and t-stat

The standard error of mean IC across months:

```
IC_se = stdev(ic_t) / sqrt(T)
```

And the t-stat:

```
t_stat = mean_IC / IC_se
       = mean_IC / (stdev(ic_t) / sqrt(T))
       = mean_IC * sqrt(T) / stdev(ic_t)
```

The conventional bar for "this factor has statistically significant
predictive power" is `|t-stat| >= 2.0`, which corresponds roughly to
a 5% two-sided significance level.

Typical numbers for real factors in real US equity universes:

- Mean monthly IC: 0.02 to 0.08 (anything above 0.10 is suspicious or
  a survivorship-bias artifact)
- IC standard deviation: 0.10 to 0.20
- t-stat over 5 years (60 months): 1.0 to 3.5 for a real factor
- IC over a single year: noisy, often not significant; 3-5 years is
  the minimum window to make a statistical claim

## IC decay

The decay curve compares mean IC across forward horizons:

```
IC_1M, IC_3M, IC_6M, IC_12M
```

A healthy alpha factor has positive IC at all horizons and decays
gradually. Pattern interpretation:

- **Monotone decay (1M > 3M > 6M > 12M):** classic momentum signature.
  The signal is strongest near-term and bleeds out as the position
  ages. Rebalance monthly to harvest.
- **Flat or increasing (1M < 12M):** value signature. Mean reversion
  is a multi-year phenomenon; the signal takes time to play out.
  Rebalance quarterly is often optimal because monthly turnover eats
  the gross return.
- **Sign flip (positive 1M, negative 12M):** classic reversal signal,
  short-horizon only. Often shows up in residual-momentum or
  short-term-reversal factors. Real, but turnover-expensive.
- **All near zero:** the factor doesn't work in this regime, or the
  universe doesn't have enough cross-sectional dispersion on this
  dimension.

## Why monthly, not weekly

Monthly rebalancing is the convention for quant factor research at the
diversified-equity scale (hundreds to thousands of names). Weekly
introduces more transaction-cost drag and increases noise relative to
signal. Daily is for high-frequency strategies and isn't comparable
to the literature. The skill defaults to monthly.

## Edge cases

- **Single-month spikes.** A single month with IC = +0.40 can inflate
  the mean if the standard deviation is also large. The t-stat
  accounts for this (high std deflates the t-stat) but the rendered
  table should note when a single month accounts for more than 30%
  of the cumulative IC. Schema field: `single_month_dominance`.
- **NaN forward returns.** Names that delisted during the forward
  window get null forward returns. Drop them from the IC computation
  for that month. For a survivorship-clean backtest you'd impute
  the delist return (often -100% for fraud delists, 0% for going-
  private buyouts). v1 just drops; documented as a limitation.
- **High autocorrelation in monthly ICs.** If IC_t is highly correlated
  with IC_t-1, the t-stat overstates significance because the
  observations aren't iid. Newey-West-adjusted t-stats are the
  standard fix; queued for v2. The naive t-stat is still informative
  for ranking factors against each other.

## What the IC tells you

- "Is this factor working in this regime?" Look at the mean IC and
  t-stat.
- "Should I rebalance monthly or quarterly?" Look at the decay
  pattern.
- "Is the signal real or did one good month carry it?" Look at the
  IC standard deviation relative to the mean.
- "Should I combine this with another factor?" Look at the IC
  correlation (separate from the signal correlation matrix this
  skill emits).

What the IC does NOT tell you:

- The dollar magnitude of the spread. IC = 0.08 sounds small; the
  associated decile spread might be 8-12% annualized. The decile
  analysis (in [`decile-analysis.md`](./decile-analysis.md)) gives
  the dollar view.
- Whether the spread is implementable. IC is computed gross of
  transaction costs and assumes equal-weight portfolios.
