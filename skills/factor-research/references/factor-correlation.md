# Factor correlation

The correlation matrix in this skill measures correlation of factor
SIGNALS (rank scores), not factor RETURNS. This distinction matters.

## Signal correlation vs return correlation

Two factors can have:

- **High signal correlation, high return correlation:** they're
  measuring the same thing. Quality (ROE) and gross profitability,
  for example, often run 0.7+ on both. Combining them in a sleeve
  doesn't diversify.
- **High signal correlation, low return correlation:** rare in
  practice; usually means one factor has a leverage component that
  dominates returns even though the score ordering matches.
- **Low signal correlation, high return correlation:** the factors
  pick different names but those names happen to move together this
  period. Means the two factors are loading on a common macro
  exposure (e.g. quality and low-vol both go up in risk-off).
- **Low signal correlation, low return correlation:** the canonical
  diversifier pair. Momentum and value are the classic example
  (historical signal correlation around -0.1 to -0.2).

The signal correlation is the structural property (the factors look
at different fundamentals). The return correlation is the realized
property in this regime (which can change). For sleeve construction,
the signal correlation is the right input because it tells you
whether the factors are picking different names.

## Computation

Per rebalance month t, build the (universe x factor) score matrix.
Compute the Spearman correlation between every pair of factor
columns. Average across months:

```
signal_corr_ij = (1/T) * sum_t spearmanr(score_t_i, score_t_j)
```

The matrix is symmetric by construction (Spearman is symmetric) and
the diagonal is 1.00 by definition. The skill emits the full N x N
matrix in the JSON; the rendered table shows the lower triangle.

## Typical magnitudes

In US large-cap universes (verified across multiple 5-year windows):

- Momentum and value: -0.10 to -0.20. Negative correlation; growth-y
  names with momentum tend to look expensive on book value.
- Momentum and quality: +0.10 to +0.20. Positive but modest.
  Quality names trend.
- Momentum and low-vol: -0.20 to -0.40. Strongly negative. Trending
  names are usually high-vol.
- Value and quality: -0.05 to +0.05. Roughly independent. Cheap
  names can be high-quality (mature cash cows) or low-quality
  (value traps); the signal doesn't load on either consistently.
- Value and low-vol: +0.10 to +0.30. Value names tend to be lower
  vol (utilities, financials, mature industrials).
- Quality and low-vol: +0.30 to +0.50. The strongest off-diagonal
  pair. Both proxy "stable, mature business." A multi-factor sleeve
  that equal-weights quality and low-vol gives a lot less
  diversification than the equal-weight implies.

If your run produces a value-momentum correlation of +0.5 or a
quality-low-vol correlation near zero, something is wrong: either
the universe is too narrow, the window is too short, or there's a
data issue.

## What the correlation matrix tells you

The most actionable read: when you build a multi-factor sleeve, the
**effective number of independent bets** is less than the count of
factors when off-diagonal correlations are high. Rule of thumb:

- All off-diagonals near zero: N factors = N independent bets
- One pair at 0.5: effective N is roughly N - 0.5
- Two pairs above 0.4: effective N is N - 1 or worse
- A factor with average |correlation| > 0.5 to every other factor
  is redundant; drop it or replace it

The take at the bottom of the rendered table should flag the highest
off-diagonal pair and the implication for sleeve construction.

## What the matrix does NOT tell you

- **Whether the factors work together.** Two uncorrelated factors that
  both have negative IC don't help each other; the sleeve still
  underperforms. The correlation is the diversification view; the
  IC table is the alpha view. Read both.
- **The full covariance structure of factor returns.** For
  mean-variance optimization of a factor sleeve, you need the
  factor-return covariance matrix (separate from the signal
  correlation). v1 doesn't ship that; queued for a `factor-sleeve`
  follow-on skill.
- **Conditional correlations.** Some factor pairs have correlations
  that shift across regimes (momentum-value flips from -0.2 in
  trending regimes to +0.3 in transitions). The single-number
  average understates this. Rolling correlation is a clean PR
  extension.

## Edge cases

- **All-NaN factor in a month.** If a fundamentals call fails for
  the universe in a month, value and quality columns are mostly
  null. Drop that month from the correlation average and record
  `n_months_dropped` so the consumer knows.
- **Constant factor in a month.** If every name in the universe has
  the same factor value (unlikely but possible for a binary
  indicator factor), the rank is degenerate and Spearman returns
  NaN. Drop the month.
- **Factor with extreme winsorization.** When winsorization clips a
  large fraction of the universe to the same value, the rank
  degenerates and the correlation is unstable. The schema records
  `winsorization_clip_fraction` per factor per month so the consumer
  can audit.
