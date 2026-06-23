# Regression-adjusted multiples

How the skill predicts what the subject's multiple "should be" given
its growth and margin, compared to peers. This is the most important
section in the rendered output for a pitch deck: the headline read at
the bottom of the table comes directly from this regression.

## The intent

A raw comp table shows that CRM trades at 18.4x EV/EBITDA and the
median peer trades at 22.1x. Naively, CRM trades at a 17% discount.
But CRM also has lower revenue growth (+11%) than the median peer
(+13%) and similar margin. After controlling for growth and margin,
how much of the discount is justified by fundamentals and how much
is residual?

The regression-adjusted multiples view answers that. For each
multiple, fit:

```
multiple_i = a + b1 * growth_i + b2 * margin_i + e_i
```

across the peer set. Predict the subject's multiple:

```
implied_subject = a + b1 * subject_growth + b2 * subject_margin
```

Compare:

```
discount_or_premium = (actual_subject / implied_subject) - 1
```

Negative = subject trades at a discount to its peers after controlling
for growth and margin. Positive = premium. The "Read" line at the
bottom of the table summarizes this in one sentence.

## The fit

The skill uses `numpy.linalg.lstsq` (ordinary least squares). The
implementation:

```python
import numpy as np

def fit_implied_multiple(peers, subject, multiple_key,
                          controls=("revenue_growth_ttm", "ebitda_margin")):
    # Build (X, y) from peers with non-null multiple AND controls
    rows = []
    for p in peers:
        m = p["multiples"].get(multiple_key)
        if m is None or m <= 0:
            continue
        ctrl_vals = [p["metrics"].get(c) for c in controls]
        if any(v is None for v in ctrl_vals):
            continue
        rows.append((m, ctrl_vals))
    if len(rows) < 4:
        return None  # not enough peers for a meaningful fit
    y = np.array([r[0] for r in rows])
    X = np.column_stack([np.ones(len(rows))]
                        + [np.array([r[1][i] for r in rows]) for i in range(len(controls))])
    coef, residuals, rank, sv = np.linalg.lstsq(X, y, rcond=None)
    # Predict subject
    subj_ctrl = [subject["metrics"].get(c) for c in controls]
    if any(v is None for v in subj_ctrl):
        return None
    implied = coef[0] + sum(coef[i+1] * subj_ctrl[i] for i in range(len(controls)))
    actual = subject["multiples"].get(multiple_key)
    discount = (actual / implied) - 1 if implied and implied > 0 else None
    # R-squared
    y_pred = X @ coef
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    return {
        "implied": float(implied),
        "actual": float(actual) if actual else None,
        "discount_or_premium": float(discount) if discount is not None else None,
        "coefficients": {
            "intercept": float(coef[0]),
            **{c: float(coef[i+1]) for i, c in enumerate(controls)},
        },
        "r_squared": float(r2) if r2 is not None else None,
    }
```

OLS is the right tool for this scale of problem: 6-10 peers, 2-3
regressors. Anything more sophisticated (Huber regression, Lasso,
WLS by market cap) is over-engineering for the pitch-deck use case
and produces a fit that's harder for the banker to defend.

## Outlier handling

Multiples > 80x are excluded from the regression but kept in the displayed
table. Verified on the 2026-06-23 CRM run: PANW's TTM operating income
was anomalously depressed (one-quarter charge), producing EV/EBITDA = 232x
and P/E = 239x. With 6-7 peers in the cohort, a single 230x multiple
dominates the OLS fit and produces negative implied multiples for any
subject with growth or margin outside the peer cluster.

The 80x cap reflects the empirical ceiling where multiples become
accounting noise rather than market signal:

- Software comps typically trade 5-50x EV/EBITDA
- Hyper-growth names (CRWD, NET) sometimes hit 60-80x temporarily
- Above 100x, the denominator is collapsing (one-time charges, restructuring,
  near-zero quarter) and the multiple stops being informative

The cap is implemented at the regression layer only. The displayed table
shows the raw multiple (so the banker sees PANW at 232x and knows to flag
it). The schema records the cap as `regression_adjusted.outlier_cap` so the
consumer can re-fit on a different threshold.

When the cap drops more than half the peer cohort, the skill emits a
warning in the rendered "Regression note" line because the remaining set
is too small to fit reliably.

## Degrees of freedom: the n < 8 caveat

With `n` peers and `k` regressors plus an intercept, the regression has
`n - k - 1` degrees of freedom. For the default `controls = (growth,
margin)`:

| n peers | DoF | Status                                |
|---------|-----|---------------------------------------|
| 4       | 1   | Barely identified; R² always ~1; skip |
| 5       | 2   | Tight; predictions are unstable       |
| 6       | 3   | Workable; flag the caveat             |
| 8       | 5   | Reasonable                            |
| 10      | 7   | Good                                  |

The schema's `low_n_warning: true` fires when `n_peers_used < 8`. The
rendered output adds a caveat line:

```
Regression note: n=6 peers, degrees of freedom tight; coefficients
indicative, not definitive.
```

The CRM run on the default 8-peer software override map typically lands
at `n_peers_used = 6` or `7` after SAP (foreign issuer empty financials)
and CRWD (negative EBITDA) drop out. The caveat fires.

## What the regression actually answers

The regression answers: "if the subject's growth and margin sat
on the peer curve, what multiple would the market assign?" That's
the implied multiple. The actual minus implied is the residual; the
discount or premium is the residual normalized to the implied.

It does NOT answer:

- **Is the subject mispriced?** The regression captures growth and
  margin; it does not capture moat, growth trajectory, end-market mix,
  M&A optionality, or capital allocation. A 20% residual after
  controlling for growth and margin tells the banker where to focus
  the rest of the analysis, not what to do.
- **Should the subject re-rate up?** Multiples mean-revert to the
  cohort over multi-year periods, but the path is path-dependent on
  earnings revisions. The regression is descriptive, not predictive.
- **What's the target price?** Banker uses the regression as one
  input to the football-field valuation chart, not as a standalone
  fair value.

The rendered "Read" line at the bottom of the table sticks to this
honest framing. No "mispriced upside" or "fair value implies $X."

## Choosing controls

The default `(revenue_growth_ttm, ebitda_margin)` is the smallest
set that captures the two dimensions a banker cares about most:
growth and profitability. The schema's `regression_adjusted.controls`
records what was used.

Other reasonable choices:

- `(revenue_growth_ttm,)` only: appropriate when EBITDA isn't
  comparable across the peer set (different D&A conventions, or
  one peer is pre-profit). The skill falls back to growth-only
  when the regression returns `n_peers_used < 4` with two controls.
- `(revenue_growth_ttm, ebitda_margin, market_cap_log)`: a
  size-adjusted version, sometimes used for bank comps where larger
  banks trade at lower P/B. Out of scope for v1; queued for v2.
- `(revenue_growth_ttm, ebitda_margin, rule_of_40_score)`: software-
  specific composite (growth + margin > 40%); collinear with growth +
  margin, so doesn't add information for the OLS fit. Not used.

## Why OLS, not a coefficient walk

A common alternative is to walk peers individually and compare the
subject's growth and margin to each peer's, then "pick" a peer to
benchmark against. That's the manual banker workflow and it produces
inconsistent results across deals.

OLS is the lightweight way to summarize the peer cohort as a single
implied curve. The banker can still inspect individual peers (they're
in the table), but the headline implied multiple uses the whole cohort.

## What the rendered output shows

The regression-adjusted block is optional in the rendered table.
Render when `regression_adjusted.results` has at least one multiple
with non-null `implied`. Format:

```
Regression-adjusted (controls for growth + EBITDA margin)
- Implied EV/Sales:    8.4x  vs subject 7.2x  → subject trades at 14% discount
- Implied EV/EBITDA:  23.1x  vs subject 18.4x → subject trades at 20% discount
- Implied P/E:        45.6x  vs subject 28.1x → subject trades at 38% discount
```

Each row reads: "if peers traded the subject's growth/margin profile,
the implied multiple would be X; the actual is Y; the residual is Z%."

When `low_n_warning` is true, a one-line caveat follows the block:

```
Regression note: n=6 peers, DoF tight; coefficients indicative.
```

The renderer reads the structured `regression_adjusted` block in the
JSON and emits this block without the user needing to know the
methodology.

## Edge cases

- **All peers cluster on growth and margin.** When the X matrix is
  near-collinear (every peer has growth in a narrow band, margin in
  a narrow band), the coefficients are unstable. The skill records
  the R² and the rendered output suppresses the per-multiple line
  when R² < 0.2 with a one-line note: "Implied EV/Sales not shown;
  peer cohort too tight on growth/margin to fit."
- **Subject controls outside peer range.** When the subject's growth
  is far above or below every peer (e.g. PLTR's growth in a software
  comp set), the regression extrapolates. The implied multiple is still
  reported because the OLS prediction is well-defined, but the analyst
  should know they're extrapolating. The schema records the
  subject's growth and margin in `subject.metrics`; visualizing where
  the subject sits on the peer scatter is a UI extension.
- **Negative implied multiple.** Can happen when the intercept is
  negative and the subject's growth/margin doesn't compensate.
  Renders as `n/a` (negative multiples aren't meaningful) and the
  "Read" line falls back to the median-vs-actual comparison.

## Reference reading

For background on cross-sectional regression of multiples on
fundamentals, see Damodaran ("The Dark Side of Valuation," 3rd ed.,
Ch. 8 on relative valuation). The pitch-deck framing of "implied vs.
actual" comes from McKinsey's "Valuation" (8th ed., Ch. 17 on
multiples-based analysis); the framework is the same OLS with growth
and margin as controls.
