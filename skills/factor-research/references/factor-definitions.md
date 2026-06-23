# Factor definitions

Canonical formulas for the four factors this skill ships in v1.
Conventions: all factors are signed so that "higher score = better
expected forward return." Where the raw academic factor goes the other
way (low vol, low valuation, low size), the skill takes the reciprocal.

## Momentum (12M minus 1M)

```
mom_12_1 = (price_t-21d / price_t-252d) - 1
```

The academic standard. Total return over the trailing 12 months but
**skipping the most recent month** to avoid the short-term reversal
documented in Jegadeesh and Titman (1993) and confirmed by every
follow-up. The most recent month carries a negative serial correlation
that contaminates a pure momentum signal. Skip it.

Direction: `higher_is_better` (winners keep winning at multi-month
horizon, in most regimes).

## Value (inverse price-to-book)

```
value_score = book_value_per_share / price_t
            = (shareholders_equity / shares_outstanding) / price_t
            = 1 / (P/B)
```

The reciprocal of P/B so higher score = cheaper. Book value pulled
from `/vX/reference/financials?timeframe=annual&limit=2` (latest
annual filing's shareholders' equity). Shares outstanding from
`/v3/reference/tickers/{T}`.

Other reasonable value formulas: `1/(P/E)`, `EV/EBITDA` inverse,
`FCF/EV`. P/B is the most defensible single-factor proxy in a
cross-sectional study because it doesn't break on negative-earnings
names (which `1/(P/E)` does) and doesn't need a clean EBITDA (which
software comps lack). The schema's `factors[]` array supports adding
the others as named factors in a future PR.

Direction: `higher_is_better` after the inverse.

## Quality (return on equity)

```
quality_score = net_income_ttm / shareholders_equity_avg
```

ROE on a TTM-net-income / average-shareholders-equity basis. The
classic quality factor. Captures whether the business is generating
returns on the capital invested in it.

Other quality variants: gross profitability (Novy-Marx 2013, gross
profit / total assets), low leverage (1 / debt-to-equity), accruals.
ROE alone is the most-defensible single-metric proxy and what FactSet
Alpha Testing uses as the default quality factor. Gross profitability
is a clean extension; queued for v2 along with a composite quality
score.

Direction: `higher_is_better`.

## Low-vol (inverse realized volatility)

```
realized_vol_252d = stdev(daily_log_returns_t-252:t) * sqrt(252)
low_vol_score = 1 / realized_vol_252d
```

The reciprocal of trailing 1-year realized volatility, annualized.
Higher score = lower realized vol = "low-vol anomaly" exposure
(Frazzini and Pedersen 2014; Baker, Bradley, Wurgler 2011).

Direction: `higher_is_better` after the inverse. Note that the
low-vol anomaly is regime-dependent: works in 2003-2007 and 2018-2020;
breaks in 2009-2010 risk-on and 2020 post-COVID rip. The IC sign is
itself the diagnostic.

## Winsorization

Before ranking, raw factor values are clipped at the 1st and 99th
percentile within the universe each month. This is the standard
outlier-handling rule in cross-sectional factor research:

- A single bankrupt name with `P/B = 0.01` becomes the highest-scoring
  value name in the universe even though the data is broken
- A name with a one-quarter earnings surge produces `ROE = 200%` and
  dominates the quality rank
- A SPAC with three months of trading history produces realized vol
  that doesn't reflect the underlying business

Winsorization prevents one bad data point from skewing the entire
factor rank. Truncation at 1/99 is the convention; some shops use
2.5/97.5 for noisier signals. The schema records the winsorization
threshold per factor (`winsorization_pctile`) so the consumer knows
what was clipped.

After winsorization, the **rank** (not the raw value) is what enters
the IC computation. Rank correlation is robust to monotone transforms;
the choice of value vs. rank changes the IC numbers slightly but not
the sign or the ordering of factors.

## Cross-sectional standardization

Each factor is z-scored within the universe each month before the
decile sort. Z-scoring is informational redundant with ranking for
the decile cuts, but useful when combining factors into a composite
sleeve (so the contributions are commensurate). v1 doesn't ship a
composite-sleeve view; queued for v2.

## What this skill does not cover

- **Composite multi-factor sleeve.** The skill emits per-factor
  results. Combining them into a single signal (equal-weighted z-scores,
  factor-tilted portfolio, mean-variance optimization) is the next
  workflow step and belongs to a separate skill.
- **Factor IC against a benchmark.** ICs here are computed against
  raw forward returns. Some shops compute against benchmark-relative
  returns. The relative version is a clean PR extension.
- **Dynamic factor weights.** v1 treats all four factors equally in
  the take. A regime-conditional weighting (overweight momentum in
  trending regimes, value in mean-reverting regimes) is a follow-on
  skill.
