---
name: vs-benchmark-audit
description: Take a book (weights per ticker), compute the daily portfolio return series, and run the full tearsheet with deflated Sharpe correction (Bailey & Lopez de Prado) plus rolling IC vs benchmark. Emits a verdict (real_alpha / possibly_alpha / essentially_beta / underperforming / no_edge_evident) based on DSR significance, alpha annualized, and beta. Answers "is this book actually alpha, honestly?" Requires Stocks Basic.
---

# vs-benchmark-audit

You hand over a book (weights per ticker) and a benchmark (default
SPY). The skill pulls daily bars, computes the portfolio return
series, and runs the full performance tearsheet with the deflated
Sharpe correction, plus a rolling 63-day IC vs the benchmark.

Answers **"is this book actually alpha, honestly?"** — with a
verdict that separates real alpha from beta from noise.

## When to invoke

- Post-quarter review: did my strategy add anything above beta?
- Investment committee prep on a candidate manager or strategy
- Auditing a historical backtest with proper DSR correction
- The user says "vs benchmark", "alpha vs beta", "is this real"

## What you need

- Positions (`--positions T=w,T=w,...`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--benchmark` (default SPY)
- `--lookback-days` (default 504, 2 years)
- `--ic-window` (default 63, one quarter)
- `--n-trials-dsr` (default 1): multiple-testing correction for
  Deflated Sharpe. Pass N if this book was picked from N candidates
  during search.

## What you get back

**Layer 1: JSON**. Full tearsheet (CAGR, Sharpe, DSR, Sortino,
Calmar, max DD, ulcer, tail ratio, profit factor, hit rate, beta,
alpha, tracking error) plus rolling IC mean and std vs benchmark.
Top-level `verdict`.

**Layer 2: rendered note**. Header verdict + return stats block +
vs-benchmark block + Take.

## How it works

1. Pull daily bars for each position and the benchmark.
2. Align to common dates.
3. Compute daily portfolio returns (weighted sum of position returns,
   renormalized to abs-weights = 1).
4. Run `quant_garage.performance.tearsheet` with `benchmark` kwarg
   populated so beta / alpha / tracking error come through.
5. Compute rolling `ic_window`-day Pearson IC of portfolio vs
   benchmark returns for a time-varying correlation lens.
6. Emit verdict:
   - `real_alpha`: DSR significant at 5% AND alpha > 2% annualized
   - `possibly_alpha`: alpha > 0 AND Sharpe > 0.5 but DSR not sig
   - `essentially_beta`: beta > 0.8 AND |alpha| < 2%
   - `underperforming`: annualized return < 0
   - `no_edge_evident`: everything else

## Foundations used

- `quant_garage.performance.tearsheet`
- `quant_garage.backtest.rolling_ic_series`
- `massive-api-patterns`
