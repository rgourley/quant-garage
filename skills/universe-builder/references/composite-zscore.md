# Composite z-score

How to combine multiple factors into a single ranking metric. Read this
before adjusting weights or adding a new factor.

## The contract

Within the surviving universe (after the full filter chain runs),
compute a per-factor z-score for every name, sign-correct each factor
so that "higher is always better," weighted-average the corrected
z-scores into a `composite_zscore`, and rank descending.

The composite is what drives the final order in the rendered table.
The per-factor z-scores are stored in `factor_zscores[]` so the user
can see which factor drove a given name's rank.

## Per-factor z-score

For each factor `f`:

```
z_f(name) = (value_f(name) - mean_f) / stdev_f
```

Where `mean_f` and `stdev_f` are computed across the **surviving**
universe, not the starting universe. The screen has already filtered;
the z-score normalizes within the cohort the user is comparing.

Use sample standard deviation (Bessel's correction, `n-1` denominator).
On small samples (`n < 20`), the z-scores are noisier; the rendered
table caps at 20 rows so the reader isn't over-indexed on micro-cohort
distributions.

**Zero-variance guard.** If `stdev_f == 0` (every survivor has the
same value, which can happen on a top-quartile momentum filter where
all survivors clear the threshold), set `z_f = 0` for every name on
that factor. Don't divide by zero; don't drop the factor silently
(record `z_f = 0` so the consumer knows it was computed).

**Missing-value guard.** When a name lacks a factor (e.g. no
financials available because it's a new IPO with no quarterly filing
yet), set `z_f = 0` and record `factors[f] = null`. The composite
treats the missing factor as neutral, which is the right default.

## Sign correction by direction

Each factor has a known direction. Sign-corrected z-score:

| Direction | Sign rule |
|-----------|-----------|
| higher = better (mcap, momentum, ocf_yield, opt_adv) | `+z_f` |
| lower = better (P/E, P/B, debt-to-equity) | `-z_f` |
| band / centered (target IV ~30) | `-abs(z_f)` |

After sign correction, every factor reads "higher = better composite."
A factor with `lower = better` direction (P/E, valuation multiples)
gets its z-score flipped before going into the weighted average.

## Weights

The v1 reference implementation is equal-weighted. With `k` factors
in the composite:

```
composite_z(name) = (1/k) * sum(signed_z_f(name) for f in factors)
```

The schema's `composite_weights` field accepts an explicit override:

```json
{ "mom_3m": 0.5, "ocf_yield": 0.3, "opt_adv_contracts": 0.2 }
```

Weights must sum to 1.0; the audit script doesn't enforce this in v1
but the reference implementation normalizes on load.

Common weighting biases:

- **Momentum-only:** `{ "mom_3m": 1.0 }` (other factors are filters
  only, not ranking inputs)
- **Quality + momentum:** `{ "mom_3m": 0.4, "ocf_yield": 0.4,
  "mcap_log": 0.2 }`
- **Pure liquidity:** `{ "mcap_log": 0.5, "opt_adv_contracts": 0.5 }`

When a factor is used as a filter but not a ranker, exclude it from
`composite_weights`. The filter chain already removed names that
failed the threshold; including the post-filter z-score in the
composite double-counts the same signal.

## Why not single-factor sort

A pure "top 20 by 3M momentum" sort produces a watchlist where every
name has high momentum and nothing else. The composite z-score forces
the user to commit to which factors matter and produces a ranking that
reflects the joint distribution rather than one axis.

A senior PM running a screen for "high-momentum value names" expects
the rank to reflect both momentum and valuation, not just whichever
they typed last. The composite is how you express that intent.

## Log-space mcap

When market cap is in the composite, z-score `log10(market_cap)`, not
the raw value. Market cap is roughly log-normal across the US stock
universe; raw mcap z-scores are dominated by mega-caps and crush the
factor's information content for mid-and-large-cap screens.

The reference implementation handles this transparently: if `mcap`
appears in `composite_weights`, the underlying value being z-scored is
`log10(mcap)`. The raw `market_cap` is preserved in `factors.mcap` for
display.

## Reproducibility

The composite z-score depends on the survivor cohort, so re-running
the same filter chain with the same composite weights against the
same underlying data should produce identical ranks. When it doesn't,
the cause is usually:

- Drift in `/v3/reference/tickers/{ticker}` market cap (Massive updates
  daily)
- A grouped-aggs call landing on a different trading day (between
  yesterday and today the universe sees one more day of momentum)
- A new financials filing landing in the lookback window

The skill records `run_at` in the JSON so the consumer can see the
cohort timestamp. If reproducibility matters across days, the
implementation should accept a `--as-of-date` override and use
historical grouped aggs for momentum and the most recent financials
filing strictly before that date.

## Reference reading

For background on cross-sectional ranking and the z-score as a
factor-aggregation tool, see Asness, Moskowitz, and Pedersen
("Value and momentum everywhere," 2013). The skill's implementation
is a simplified equal-weight version of the same idea.
