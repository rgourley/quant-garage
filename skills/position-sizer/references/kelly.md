# Fractional Kelly sizing

The conviction-weighted method. Kelly says: size each position by
its edge per unit of risk, accounting for correlations between
positions. Take the full-Kelly bet and scale it down by a fraction
to handle the bias in your edge estimates.

## The math

Full Kelly, multi-asset matrix form:

```
f = Σ⁻¹ μ
```

Where:

- `μ` is the vector of expected annualized returns (the "edges"),
  one per name in the basket
- `Σ` is the annualized covariance matrix of returns
- `f` is the vector of full-Kelly weights

A diagonal-cov special case makes the intuition clear: when names
are uncorrelated, `f_i = μ_i / σ_i²`. High edge and low vol both
increase the allocation. The matrix form generalizes this to handle
correlation: if two names are highly correlated and both have
positive edge, Kelly halves their individual weights to avoid
double-counting the same bet.

Fractional Kelly: scale the full-Kelly book down by a constant.

```
f_frac = scale * f
```

Default `scale = 0.25` ("quarter Kelly"). This is the convention
among practitioners not because the 0.25 is mathematically optimal
but because:

1. Edge estimates are noisy. A 20% expected return estimate is
   really "between 5% and 35% with a wide standard error." Full
   Kelly assumes the point estimate is correct; fractional Kelly
   absorbs the estimation error by under-sizing.
2. Full Kelly maximizes long-run log wealth — it's optimal for
   geometric mean return. But the path is brutal: full-Kelly
   drawdowns routinely exceed 50%. Quarter-Kelly gives roughly
   3/4 of the long-run return at 1/4 of the drawdown variance.
3. Practitioners with real client books cannot tolerate full-Kelly
   drawdowns regardless of long-run optimality.

The 0.25 is the conservative-but-standard default; 0.5 is the
"half-Kelly" that aggressive PMs sometimes use; >0.5 starts feeling
reckless to most readers.

## Edges are an input, not an output

The skill does NOT estimate edges. The CLI flag `--kelly-edges`
takes per-ticker annualized expected return estimates from the user.
Without the flag, the Kelly column is skipped and a `tier_caveats`
note explains why.

This is a deliberate constraint. Predicting returns is a different
job than sizing them, and combining the two would be a category
error. The PM brings the view; the script does the sizing math
honestly given the view.

If you don't have edge estimates per name, the right move is not
"guess edges." It's either run vol-target / risk-parity (which don't
need edges) or build the edges with a separate workflow (a factor
model, fundamental research, a forecasting system) and feed the
output back in.

## Why the matrix form, not the closed-form-per-name version

The closed-form `f_i = μ_i / σ_i²` answer ignores correlations. If
you size NVDA and AMD independently using that formula and both
have positive edge, you end up double-counting the semi-cycle bet.
The matrix form `f = Σ⁻¹ μ` is the only correct way to size a
multi-asset Kelly book with correlations present.

The cost is that Σ has to be invertible. Empirical covariance
matrices on 4-15 names with 252 daily observations are usually fine,
but degenerate cases (perfectly collinear names, n < d, all-zero
vols) will fail. The skill shrinks the correlation matrix 5% toward
the identity before computing Σ, which is enough to handle every
empirical case the test runs surfaced. Hard failures bubble up as
`ValueError` from `np.linalg.inv`.

## Long-only floor in v1

Kelly's matrix form can produce negative weights when one name's
edge is dominated by another's (the math says "short the dominated
name to fund the dominant one"). v1 floors negatives at zero and
surfaces a `negative_signals_floored: true` flag in the JSON. The
remaining weights are renormalized to preserve gross.

A long-short v2 is the obvious extension. For now, if a user wants
short exposure, the right workflow is to compute the long-only
Kelly book and use a separate process for the short leg.

## What the binding constraints mean here

Three caps can bind, same precedence as vol-target:

1. **`leverage_cap`**: full fractional Kelly often produces
   `Σ|f_frac| > 1`. If the cap is 1.0, the book is rescaled to gross
   1.0 and `leverage_cap` is the binding. The PM is told "Kelly
   wanted more leverage than your cap allows."
2. **`max_weight`**: per-name cap binds when one name's
   edge-per-variance dominates the others.
3. **No binding**: the rare case where the fractional Kelly book
   naturally fits inside the leverage cap and no name hits the
   per-name cap.

## Edge cases

- **No positive edge in any name** (every `f_i < 0` after the long-
  only floor): the script returns a zero-weight book and surfaces
  `binding_constraint: "no_positive_edge"`. The reader interprets
  this as "Kelly says don't be in this book at all under your stated
  edges."
- **Edges supplied as zero**: the corresponding Kelly weight is
  zero. Kelly correctly says "no edge, no weight."
- **Singular covariance matrix**: raises `ValueError`. Shouldn't
  happen with the 5% identity shrinkage; if it does, the basket is
  pathological (e.g., two identical tickers).
- **Edges supplied for tickers not in the basket**: ignored
  silently. The skill iterates `tickers` not `edges.keys()`.

## What the rendered output looks like

The column header is `Kelly(0.25)` reflecting the chosen scale. The
"Take" line reads the actual edges and vols and identifies the name
with the highest edge-per-variance — that's the name Kelly tilts
toward. If you see Kelly putting most of the book in the name with
the lowest vol despite a smaller edge, that's the correlation
penalty visible in the output.
