# Risk-parity (Equal Risk Contribution) sizing

The "every name contributes equally to portfolio variance" method.
A favorite of risk-budgeting desks and multi-asset funds; the
philosophical opposite of equal-weight.

## The math

Marginal contribution of name `i` to portfolio variance:

```
MC_i = w_i * (Σ w)_i
```

Where `Σ w` is the matrix-vector product (the i-th element of `Σw`
is the covariance of name i with the portfolio, scaled by all
weights). Each name's share of total portfolio variance:

```
share_i = MC_i / (w' Σ w)
```

Risk parity (ERC) targets `share_i = 1/N` for every name. No name
contributes more than its share to portfolio variance.

## The iterative fixed-point algorithm

There's no closed-form solution for ERC weights with a general
covariance matrix. The standard iterative update (Maillard, Roncalli,
Teiletche 2010) is:

```
1. Start from inverse-vol weights: w_i = (1/σ_i) / Σ(1/σ_j)
   This is a strong warm start; for uncorrelated assets it IS the
   ERC solution.

2. Repeat:
     port_var = w' Σ w
     mc       = Σ w                  # marginal contributions
     rc       = w * mc / port_var    # shares of total variance
     w_new    = w * sqrt(target / max(rc, eps))   # scale step
     w_new    = w_new / sum(w_new)   # renormalize to Σw = 1

3. Stop when max|rc - target| < tol (default tol = 1e-6), or after
   max_iters (default 200).
```

The scale step `sqrt(target / rc)` is the key: when a name's risk
contribution is too high (`rc > 1/N`), its weight gets shrunk; when
it's too low, its weight grows. The square root makes the update
smooth enough to converge stably without overshooting.

For most real-world covariance matrices (positive definite, 3-15
names), the algorithm converges in 1-10 iterations from the inverse-
vol warm start. Pathological cases (near-singular cov, very high
correlations like ρ > 0.95) can take 50-100 iterations.

## Convergence fallback

If the algorithm doesn't converge within `max_iters`, the skill
falls back to plain inverse-vol weights and surfaces
`converged: false` in the JSON plus a tier caveat. Inverse-vol IS
the exact ERC solution for the diagonal-cov special case, so the
fallback is a reasonable approximation when the matrix is
problematic.

In practice, the 5% identity shrinkage on the correlation matrix
makes non-convergence very rare. The shrinkage is what keeps the
matrix well-conditioned enough for the fixed-point iteration to
behave.

## Why 5% identity shrinkage

Empirical correlation matrices on small baskets (4-15 names) with
~252 observations are NOT guaranteed to be positive definite, and
even when they are, they often have eigenvalues close to zero that
make inversion unstable. Shrinking 5% toward identity:

```
Σ_shrunk = 0.95 * Σ_empirical + 0.05 * I
```

guarantees the smallest eigenvalue is at least 0.05 (in correlation
space). Enough for both ERC iteration and Kelly's matrix inversion
to behave numerically, without distorting the cohort structure to
the point where the analysis becomes meaningless.

The shrinkage is reported in the JSON (`correlation_matrix.shrinkage`),
and both raw and shrunk matrices are emitted so the consumer can
inspect the gap.

A more sophisticated approach is Ledoit-Wolf shrinkage, which
estimates the optimal shrinkage intensity from the data. Worth
considering for v2; for v1 a constant 5% is the conservative-but-
adequate choice.

## Why this isn't the same as equal-weight or inverse-vol

ERC and inverse-vol AGREE in two special cases: (1) uncorrelated
assets, (2) one name with isolated risk. Otherwise they diverge.
ERC accounts for the diversification benefit of correlated names —
two highly-correlated names contribute MORE to portfolio variance
than the inverse-vol weighting would suggest, so ERC underweights
them relative to inverse-vol.

Equal-weight ignores both vol and correlation. The three methods
form a meaningful spectrum:

- Equal-weight: maximally agnostic
- Inverse-vol: accounts for vol, ignores correlation
- Risk parity (ERC): accounts for both

PMs comparing the columns side-by-side can see exactly how much of
the divergence is the vol effect vs the correlation effect.

## What the binding constraints mean here

Same precedence as vol-target:

1. **`leverage_cap`**: ERC weights naturally sum to 1 (the iteration
   renormalizes every step). If `leverage_cap < 1`, the book is
   rescaled and the cap binds.
2. **`max_weight`**: per-name cap, applied after the iteration
   converges. Common to bind when one name has very low vol relative
   to the cohort.
3. **No binding**: the iteration converged inside both caps.

## Edge cases

- **Zero diagonal in cov**: raises `ValueError` (one or more vols
  are zero). Shouldn't happen with realized returns; will happen if
  a synthetic test passes constant prices.
- **Convergence failure**: warm-start to inverse-vol is the fallback;
  flagged as `converged: false`. In testing this hit only with very
  high uniform correlation (ρ > 0.99) and degenerate matrices.
- **N=1**: trivially 100% on that name; ERC has no meaningful answer.
- **Very tight `max_weight`**: forces a strongly-concentrated ERC
  book to spread out, but the MRC equalization breaks. The script
  reports the MRC shares post-cap so the consumer sees the gap.

## What the rendered output looks like

The column header is `Risk-Parity`. The "Take" line reads the actual
top weight and frames it as "the name whose vol + correlation profile
carries the smallest share at equal weight" — which is exactly what
ERC tilts toward. The marginal_risk_contributions field in the JSON
shows the realized share of portfolio variance per name; at
convergence each share should equal 1/N.
