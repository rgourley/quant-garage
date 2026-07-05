---
name: portfolio-rebalancer
description: Decision layer on top of risk-report. Takes positions with weights and outputs specific trade tickets to bring every name under a variance-share cap while respecting weight and churn limits. Turns "ALLO carries 66% of portfolio variance at 18% weight" into "sell $65k of ALLO, redistribute, portfolio vol drops from 21% to 15%." Not tax-aware, not liquidity-aware in v1 — honest about both. Use when the operator asks "so what should I change?" after a risk-report.
---

# portfolio-rebalancer

You hand over a positions map plus book value, a per-name variance-
share cap, a per-name weight cap, and a max churn per rebalance. The
skill returns a specific trade-ticket list with dollar amounts,
weight deltas, and before/after variance-share readings.

risk-report tells you which name is driving the risk. This skill
tells you what to trim, by how much, and where to redistribute.

## When to invoke

- After a risk-report run flags a name with variance share far
  disproportionate to its weight
- Portfolio-review workflow, decision-support step
- The user says "rebalance", "trim my winners", "cut variance",
  "what should I sell", "reduce concentration"
- Any time the operator wants an actionable answer, not a report

## What you need

- `MASSIVE_API_KEY` — Stocks Starter for daily aggs on every name +
  benchmark

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Per-name trade tickets sorted by absolute dollar amount, plus
portfolio-level before/after summary (vol, top-3 variance share,
Herfindahl, max variance share), constraint-satisfaction status.

**Layer 2 rendered table**. Before/after summary block, then a table
of trades, then a status line. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Parse positions** from either a comma-separated string
   (`TICKER=WEIGHT`) or a book JSON file. Same shape as risk-report
   for consistency.
2. **Pull daily aggs** for every position + benchmark over the
   `lookback_days` window (default 252).
3. **Compute covariance**: per-name annualized vol, shrinkage-
   adjusted correlation, covariance matrix. Same machinery as
   risk-report.
4. **Compute current variance shares** via `w_i * (Σw)_i / total`.
5. **Solve iteratively**:
   - For every over-cap name, trim by `sqrt(target/current)` since
     variance share scales roughly quadratically with weight.
   - Redistribute freed weight to under-cap names in proportion to
     their current weight.
   - Enforce max_weight cap after distribution; clip and re-
     redistribute if needed.
   - Renormalize to preserve gross exposure.
   - Iterate to convergence or max_iter.
6. **Apply churn cap**: if the target rebalance exceeds `max_churn`
   one-way turnover, scale the delta vector down proportionally
   until it fits. Emit a status flag when this happens.
7. **Emit trade tickets**: delta_weight * book_value per name, drop
   trades below `min_trade_dollar`.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` for every
  position + benchmark

## Doesn't handle (yet)

- **Not tax-aware.** Selling appreciated positions incurs capital
  gains; the tool ignores this. Apply the trade list through a tax-
  lot-aware execution layer if lots matter.
- **Not liquidity-aware.** Dollar amounts do not consider ADV, spread,
  or market impact. Verify with slippage-cost before executing large
  trades in illiquid names.
- **Descriptive against a risk cap, not return-maximizing.** The tool
  does not use forward return estimates. It solves for a specified
  risk-share target only.
- **Covariance is estimated with shrinkage but still relies on the
  last N trading days.** Regime shifts can change covariance faster
  than the estimator adapts.
- **Single-asset-class only.** Multi-asset books (equities + fixed
  income + crypto) need the correlation panel to align across asset
  types — not handled in v1.
