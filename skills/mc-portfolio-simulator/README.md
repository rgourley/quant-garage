# mc-portfolio-simulator

Standalone Monte Carlo forward P&L simulator. Give it a book and a
horizon; get back the distribution of outcomes, tail scenarios, path
max-drawdown, and probability of loss / gain at 5/10/20/30% thresholds.

Companion to `position-sizer` (which produces target weights) and
`risk-report --mc` (which includes MC alongside historical VaR).

## Quick start

### Python library

```python
from quant_garage.skills.mc_portfolio_simulator import run, render
payload = run(
    "NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25",
    simulation_days=60,
    n_paths=10_000,
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-mc-portfolio-simulator.py \
  --positions NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25 \
  --simulation-days 60 --n-paths 10000
```

Add `--tail student_t --tail-df 4` for fatter tails.

## What you get back

```
MC Portfolio Simulator: 4 names · 60d horizon · 10,000 paths
Lookback 252d (250 obs) · realized vol · normal

Portfolio composition
  NVDA     weight  25.0%  σ(annual)  35.4%
  ...

Cumulative return over 60d
  Mean +5.7% · σ 11.5%
  p5 -12.7% p10 -9.0% p25 -2.0% p50 +5.7%
  p75 +13.3% p90 +20.4% p95 +25.0%

Path max drawdown
  Median -8.2% · p25 (typical bad) -12.0% · p10 (bad case) -16.4% · p5 (tail case) -18.7%

Probability
  Loss > 5%: 24.0%   Loss > 10%:  9.8%   Loss > 20%:  0.7%   Loss > 30%:  0.0%
  Gain > 5%: 55.2%   Gain > 10%: 30.7%   Gain > 20%:  7.8%

Take: Median 60d outcome is +5.7%, tail (p5) is -12.7%.
Worst-case path drawdown (p10) is -16.4%.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
