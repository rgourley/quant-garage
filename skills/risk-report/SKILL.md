---
name: risk-report
description: VaR (historical + parametric), Expected Shortfall, max drawdown, beta, tracking error, position variance contributions, concentration metrics, and worst-N historical stress scenarios for a portfolio. Pairs with portfolio-mark (which marks the book — risk-report tells you what could happen to those marks). Use when a PM, risk officer, or quant needs the full risk picture on a current book.
---

# risk-report

You hand over a book — either inline weights or a positions JSON —
and the skill returns the empirical risk picture: how volatile this
book has been, how it co-moves with the benchmark, how bad the tail
gets (VaR + Expected Shortfall), how deep the recent drawdown was,
which historical days hurt most and which names did the damage, and
which positions are doing the heavy lifting in the variance budget.

This is descriptive risk math on a current book. The script does NOT
predict future returns. It tells you what the last N days of history
say about how a book like the one you have just handed over has
behaved.

## When to invoke

- PM needs the daily risk snapshot on a current book
- Risk officer running a tail-risk review
- Researcher comparing book risk vs a benchmark
- Pre-trade check: "what does this proposed book look like?"
- Post-portfolio-mark follow-up: "we know what the book is worth;
  what could it lose?"

## What you need

- A book: either inline `--positions T=w,T=w,...` or a `--book book.json`
  (see [`examples/sample-book.json`](./examples/sample-book.json))
- `MASSIVE_API_KEY` exported

Optional:

- Benchmark ticker (default `SPY`) for beta + tracking error + R²
- Lookback window in trading days (default 252)
- VaR confidence levels (default `0.95,0.99`)
- Number of worst historical days to surface (default 5)
- Vol estimator (default `realized`; `ewma` for RiskMetrics EWMA with
  configurable λ, default 0.94, that responds faster to recent regime
  shifts)

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-book stats (vol, return, Sharpe, beta, alpha, tracking error,
correlation, R²). A full VaR block keyed by each requested confidence
level with historical VaR, parametric VaR, historical ES, parametric
ES. Max drawdown with peak/trough/duration/recovery. Worst-N stress
days with per-name loss attribution. Per-position variance
contribution and per-position beta to the benchmark.
Concentration: top-1/3/5 weights, Herfindahl, effective N.
`tier_caveats` for excluded names and the always-on methodology
warnings.

**Layer 2: rendered PM report.** Header line with the book and
lookback, a Portfolio statistics block, a VaR table (rows: historical,
parametric, ES — columns: each confidence), one-liner max drawdown,
the worst-N stress days with per-name attribution and the
benchmark's return on the same date, a Position contribution table
sorted by variance share, a Concentration line, then the adaptive
Take. The Take reads what's actually true about the book (high beta,
concentrated, big drawdown, low tracking error) and surfaces 2-3 of
the most striking facts in plain English. See [`references/`](./references/)
for the full methodology.

## How it works

1. **Parse the book.** Inline `--positions` (weights sum to ≤ 1.0; the
   residual is implicit cash) or `--book` JSON. The JSON format supports
   either `weight` per position or `shares` + `price`, in which case
   weights are computed from the value share.
2. **Pull daily aggs** per position and per benchmark over
   `--lookback-days` (default 252). Massive's
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true` so
   dividends and splits don't contaminate the vol estimate.
3. **Compute log returns** close-to-close. Align all series to the
   intersection of date indices so every metric reads from the same
   panel.
4. **Drop short series.** A position with fewer than 60 aligned
   trading days is excluded (surfaced in `tier_caveats` and
   `positions_excluded`); the equivalent weight folds into the cash
   bucket so the math stays consistent.
5. **Per-name annualized vol** via `np.std(daily_returns, ddof=1) *
   sqrt(252)`.
6. **Pairwise Pearson correlation matrix**, 5%-shrunk toward identity
   for numerical safety (same pattern as `position-sizer`). Covariance
   from per-name vols × the shrunk correlation.
7. **Portfolio daily returns** as the weighted sum across names per
   day. This is the single time series every metric reads from.
8. **Stats:** annualized vol, annualized mean return, naïve Sharpe;
   beta + alpha + tracking error + correlation + R² vs the benchmark.
9. **VaR + ES at each confidence:**
   - Historical VaR = -percentile(returns, 100 × (1 - confidence))
   - Parametric VaR = z × σ - μ, where z = Φ⁻¹(confidence)
   - Historical ES = -mean(returns ≤ VaR threshold)
   - Parametric ES = -(μ - σ × φ(z) / (1 - confidence)) (Gaussian)
   See [`references/var-and-es.md`](./references/var-and-es.md).
10. **Max drawdown** on the cumulative NAV (reconstructed from log
    returns). Returns peak, trough, duration, and whether the series
    recovered to the peak. See [`references/max-drawdown.md`](./references/max-drawdown.md).
11. **Worst-N stress** picks the N most-negative days in the
    portfolio return series and attributes each day's loss to
    individual names via `w_i × r_i_on_that_day`. See
    [`references/stress-scenarios.md`](./references/stress-scenarios.md).
12. **Position variance contributions** via the
    `MRC_i = w_i × (Σw)_i` decomposition normalized to sum to 1.
13. **Concentration** stats: top-1/3/5, Herfindahl (Σw²), effective
    N (1 / HHI). See [`references/concentration.md`](./references/concentration.md).
14. **Adaptive Take.** Reads beta, top variance contributor share,
    Herfindahl, drawdown, and tracking error. Surfaces the 2-3 that
    actually matter for this book, in plain English.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth and
  rate limiting on the daily aggs pull.

## Output mode: report

The render is a top-to-bottom report — header, stats block, VaR table,
drawdown one-liner, stress days, position table, concentration line,
take, caveats. Closer to a daily risk note than a pure data dump,
because the PM/risk audience needs the take alongside the numbers.
See [`references/`](./references/) for the per-section methodology.

## MC mode

N/A. risk-report uses empirical historical distributions; it does not
run Monte Carlo. For distribution-of-outcomes sweeps see
`valuation-sanity-check --mc`.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily aggregates per name + per benchmark. One call per ticker per
  run; results cached per ticker.

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- **Single-regime lookback.** All math reads from one window. A
  multi-window comparison (60-day vs 252-day VaR) would show regime
  sensitivity; queued.
- **No factor decomposition.** Variance contribution is at the
  position level, not the factor level. A factor-attributed
  decomposition (size, value, momentum, quality) needs the
  `factor-research` machinery wired in; obvious follow-up.
- **Cornish-Fisher / Student-t parametric VaR.** Parametric VaR
  assumes normality; surfaces in caveats. A higher-moment variant is
  a clean PR.
- **No conditional / stressed-period VaR.** Worst-N is the closest
  thing; a regime-conditional VaR (e.g., compute VaR only on days
  when SPY was down) is a follow-up.
- **No correlation-shock stress.** "What if every correlation goes to
  0.9" is queued; the worst-N empirical stress carries the day for v1.
- **Single benchmark.** Beta is vs one ticker. Multi-benchmark (SPY +
  IWM + EFA + AGG) needs a multi-regression; queued.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
