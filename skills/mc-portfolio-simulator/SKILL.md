---
name: mc-portfolio-simulator
description: Monte Carlo forward P&L simulator for a book. Simulates 10,000 correlated return trajectories from the shrunk covariance matrix over a caller-specified horizon (default 60 trading days) and reports the full cumulative-return distribution, max-drawdown distribution, path VaR, and P(loss > X%) at 5/10/20/30% thresholds. Companion to position-sizer. Requires Stocks Basic. Runs on the free tier.
---

# mc-portfolio-simulator

You hand over a book (weights per ticker) and a horizon. The skill
fits the covariance matrix on the historical window, simulates N
correlated return trajectories forward, and reports the distribution
of outcomes.

Companion to `position-sizer` and to `risk-report --mc`. Same math
underneath: shrunk correlation × per-name vols → covariance →
Cholesky-factored path simulation. The three tools differ in framing:

- `position-sizer` produces target weights under a target vol.
- `risk-report` includes MC as one lens alongside historical VaR,
  drawdown, stress days.
- `mc-portfolio-simulator` is the standalone MC lens: you already
  have weights, you want the P&L distribution.

## When to invoke

- "Given my proposed weights, what's the 5th percentile 60-day
  outcome?"
- Comparing two candidate books by tail severity
- Answering "how bad can this get" for a small book without needing
  full risk-report output
- The user says "monte carlo my book", "simulate this portfolio",
  "P(loss > 10%)", "forward P&L distribution"

Not for: predicting the direction (MC doesn't pick winners; it fans
the future out). Not for options portfolios (payoffs are non-linear;
this simulates linear returns).

## What you need

- A book: `--positions T=w,T=w,...`
- `MASSIVE_API_KEY` exported
- Stocks Basic plan minimum

Optional:

- `--simulation-days` (default 60): forward horizon in trading days.
- `--n-paths` (default 10000): Monte Carlo path count.
- `--tail {normal, student_t}` (default normal): innovation
  distribution. student_t gives fatter tails.
- `--tail-df` (default 4): student-t degrees of freedom.
- `--lookback-days` (default 252): historical window for covariance.
- `--vol {realized, ewma}` (default realized): per-name vol estimator.
- `--ewma-lambda` (default 0.94): EWMA decay when vol=ewma.
- `--shrinkage` (default 0.05): correlation shrinkage toward identity.
- `--seed` (default 42): rng seed for reproducibility.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
`cumulative_return_distribution` with mean, std, and p5/p10/p25/p50/p75/p90/p95.
`max_drawdown_distribution` with p5/p10/p25/p50/p75 (all negative).
`path_var_by_confidence` at 95/99. `loss_probabilities` at 5/10/20/30%
and `gain_probabilities` at 5/10/20%. Per-ticker annualized vols and
the exact weight vector used (may exclude tickers with insufficient
history).

**Layer 2: rendered note**. Composition table, cumulative-return
percentile block, path max-drawdown block, probability grid, one-line
Take. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily aggs** for each ticker over `lookback_days * 1.6 + 14`
   calendar days.
2. **Align to common dates** across the book.
3. **Fit per-name vol** using `realized` or `ewma` on the aligned window.
4. **Correlation matrix + shrinkage** toward identity for numerical
   PD safety.
5. **Covariance matrix** from vols × correlation.
6. **Simulate paths** via `simulate_correlated_paths`:
   Cholesky-factored multivariate normal (or student-t via
   `sqrt(df/chi2(df))` scaling), one row per path per day. Mean of
   each daily-return distribution is the historical daily mean.
7. **Portfolio P&L per path** = sum over days of (weight vector ·
   per-name daily return). Path NAV = cumulative product of
   exp(daily returns).
8. **Distribution stats**: percentile summary on cumulative returns
   and path max-drawdowns. Path VaR at each confidence is
   `-quantile(cum_ret, 1-c)`. Expected shortfall averages the tail.
9. **Probability grid**: fraction of paths crossing each loss/gain
   threshold.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and daily aggs.

## Output mode: note

Narrative note with a percentile grid. A single portfolio simulation
produces a handful of numbers per bucket; a note reads better than
a wide table.

## Endpoints used

- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true` per
  ticker. One call per ticker per run.

## Doesn't handle (yet)

- **Options / non-linear payoffs.** Linear returns only.
- **Regime shifts.** Simulates from the fitted covariance; the
  regime is what the window captured.
- **Time-varying correlations.** Constant cov over the horizon.
  GARCH-DCC would improve this at the cost of much more machinery.
- **Path-dependent objectives.** Reports max drawdown per path but
  not path-dependent utility functions (constant proportional
  drawdown, etc.).
- **Explicit jump processes.** student_t fattens the marginals; a
  Merton-style jump-diffusion would add discrete crash events.

These are clean PR extensions. Output schema is forward-compatible.
