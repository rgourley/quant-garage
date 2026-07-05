# portfolio-rebalancer

Decision layer on top of risk-report. Takes positions with weights,
computes per-name variance shares, and outputs specific trade tickets
to bring the book under a variance-share cap while respecting weight
and churn caps.

## Quick start

```bash
python3 examples/run-portfolio-rebalancer.py \
  --positions "JEPI=0.305,ALLO=0.183,BRK.B=0.163,GLD=0.145,SOFI=0.070" \
  --book-value 650000 --format render
```

## What you get back

```
Portfolio Rebalance Recommendation — 2026-07-02
Book value: $650,000 · 5 positions · caps: variance-share <= 25.0%, weight <= 15.0%, churn <= 10.0%

Before / After:
  Vol (ann):              21.3% -> 15.5%
  Top-3 variance share:   82.5% -> 65.6%
  Max single-name var:    66.3% (ALLO) -> 30.0% (ALLO)
  Herfindahl:             0.181 -> 0.194
  Actual churn:           10.1%

Recommended trades (19):

Ticker  Action        Dollar     Δ wt              Weight           Var Share
ALLO      SELL      -$65,748   -10.1%       18.3% -> 8.2%      66.3% -> 30.0%
JEPI       BUY       $24,572    +3.8%      30.5% -> 34.3%       5.3% -> 10.7%
BRK.B      BUY       $13,132    +2.0%      16.3% -> 18.3%        1.9% -> 3.6%
...

STATUS: All variance shares within cap after rebalance.
```

## Methodology

Iterative solver: sqrt-scale trim on over-cap names, proportional
redistribution to under-cap names, max-weight clipping with a second
redistribution pass, renormalization to preserve gross. Churn cap
applied as a proportional scale on the final delta vector.

## Caveats

- Not tax-aware. Selling appreciated positions incurs capital gains.
- Not liquidity-aware. Verify with slippage-cost for illiquid names.
- Descriptive against a risk cap, not return-maximizing.

## Plan requirement

Stocks Starter — one daily-aggs call per position + benchmark. See
top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
