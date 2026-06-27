---
name: position-sizer
description: Run vol-target, fractional Kelly, risk parity, and equal-weight position sizes side-by-side on a basket of tickers. Use when a PM has names they want in the book and asks "how much of each?" The script doesn't pick names or predict returns; it shows what each sizing method gives so the PM can pick the one whose worldview matches their conviction. Requires Stocks Starter.
---

# position-sizer

You hand over a basket of tickers and (optionally) per-name edges.
The skill returns position sizes under four canonical methods,
side-by-side, plus the portfolio-level vol, exposure, and binding
constraints for each.

This is NOT alpha. The script doesn't pick names — you bring the
names. It doesn't predict returns — you bring the edges (for Kelly).
What it does is the descriptive math that says "given these names
and these vols, here's how each sizing method allocates."

## When to invoke

- PM has 3-15 names they want in the book; deciding how to weight
- Researcher wants to compare Kelly vs vol-target on the same basket
- Risk team checking that a proposed book sits inside a target vol
- A trader sanity-checking that the discretionary book they built by
  feel doesn't have one position dominating the variance budget

## What you need

- 3-15 tickers (more is fine; risk-parity slows quadratically in N)
- `MASSIVE_API_KEY` exported

Optional:

- Edges per ticker (only needed for Kelly; e.g., "I think NVDA
  returns 15% annualized, AMZN 10%")
- Target vol (default 12%)
- Leverage cap (default 1.0x; Σ|w| ≤ cap across every method)
- Max single-position weight (default no cap)

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-name vol + observation count, the raw and shrunk correlation
matrices, every sizing method's full output (weights, portfolio vol,
gross exposure, binding constraint), and `tier_caveats` for anything
that got dropped or capped.

**Layer 2: rendered side-by-side table.** One row per ticker, one
column per method, footer rows for Σ|w|, portfolio vol, and the
binding constraint. The "Take" reads the actual numbers (highest-vol
name in the basket, highest-edge-per-variance name, the risk-parity
top weight) and explains what each method's tilt means in plain
English. Pick the method whose worldview matches your conviction.

## How it works

1. **Pull daily aggs** per ticker over `--lookback-days` (default 252
   trading days). Massive's `/v2/aggs/ticker/{T}/range/1/day/...`
   endpoint with `adjusted=true` so dividends and splits don't
   contaminate the vol estimate.
2. **Compute log returns** close-to-close. Align all series to the
   intersection of date indices so the same N observations feed every
   pairwise correlation.
3. **Drop short series.** A ticker with fewer than 60 aligned trading
   days is excluded from the book; the caller is told in
   `tier_caveats` and `tickers_excluded`.
4. **Per-name annualized vol** via `np.std(daily_returns, ddof=1) *
   sqrt(252)`.
5. **Pairwise Pearson correlation matrix** across the aligned panel,
   shrunk 5% toward identity:
   `shrunk = 0.95 * empirical + 0.05 * I`. This is enough to make
   nearly any 4-15 name matrix positive definite without distorting
   the cohort structure. See [`references/risk-parity.md`](./references/risk-parity.md).
6. **Covariance** from per-name vols and the shrunk correlation:
   `Σ[i,j] = ρ[i,j] * σ_i * σ_j`. One source of truth feeds every
   sizing method.
7. **Run each requested method** against the same Σ:
   - `vol_target_weights` (inverse-vol normalized, scaled to target vol)
   - `fractional_kelly_weights` (matrix form `f = Σ⁻¹ μ`, scaled)
   - `risk_parity_weights` (ERC iterative fixed-point)
   - `equal_weights` (1/N baseline)
   See [`references/vol-target.md`](./references/vol-target.md),
   [`references/kelly.md`](./references/kelly.md),
   [`references/risk-parity.md`](./references/risk-parity.md).
8. **Apply caps.** Per-name `max_weight` cap iteratively redistributes
   excess to the uncapped names. Gross `leverage_cap` rescales the
   final book. The `binding_constraint` field on each method says
   which cap (if any) actually bound the result.
9. **Generate the take.** The narrative reads the actual numbers —
   which ticker has the highest vol, which has the best edge per
   variance, where risk-parity put the largest weight — and explains
   each method's worldview in those terms.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, rate
  limiting, the snapshot fallback chain conventions.

## Output mode: table

A wide, scannable side-by-side table is the right canvas for "compare
methods at a glance." Each column is one sizing method; each row is
one ticker. The footer rows (Σ|w|, port vol, binding) summarize the
book-level properties. The take is the narrative bridge from the
table to the decision. See [`references/`](./references/) for the
per-method methodology.

## MC mode

N/A. This skill is the position-sizer, not the Monte Carlo. For
distribution-of-outcomes sweeps see `valuation-sanity-check --mc`.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily aggregates per ticker. One call per ticker per run.

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- **Long-only v1.** Kelly's matrix form can produce negative weights
  when one name's edge is dominated by another's. v1 floors negative
  signals at zero and surfaces a `negative_signals_floored: true` flag.
  A long-short v2 PR is the obvious extension; queued.
- **Static vol estimate.** The 252-day realized vol is a backward-
  looking number. Forward-looking vol from options IV (calls into the
  Options chain at the ATM strike) would be more honest at the cost of
  pulling the chain per name. Queued for v2.
- **Single horizon.** All methods assume the user's holding horizon
  matches the lookback window's regime. A multi-horizon view (e.g.,
  60-day vol vs 252-day vol) would show how regime-sensitive each
  method is; queued.
- **No transaction-cost penalty.** The methods produce target weights
  but don't account for the cost of rebalancing from current to target.
  Pairing this skill with `slippage-cost` covers that gap.
- **Correlation is in-sample.** No shrinkage to a peer-derived prior;
  no factor-model decomposition. The 5% identity-shrink keeps the
  matrix PD without baking in a view on the true correlation structure.
  Documented in `references/risk-parity.md`.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
