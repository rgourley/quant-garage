# risk-report

You have the book already. NVDA, AMZN, GOOGL, META, 25% each. The
next questions are: how volatile has this book been, how does it
co-move with the market, how bad does the tail get, where did the
last drawdown come from, and which positions are doing the heavy
lifting on the variance budget. Run the tool and get the full
picture in one report.

This is descriptive risk math, not predictive. The script doesn't
forecast returns; it tells you what the last N days of history say
about a book like the one you have.

Pairs naturally with `portfolio-mark`. That skill answers "what is
this book worth right now?" risk-report answers "what could happen
to that value?"

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.risk_report import run, render
payload = run(
    positions="NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25",
    lookback_days=252,
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-risk-report.py \
  --positions NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25 \
  --benchmark SPY \
  --lookback-days 252 \
  --var-confidence 0.95,0.99 \
  --stress-n 5
```

Or pass a book JSON:

```bash
python3 examples/run-risk-report.py --book examples/sample-book.json
```

### Claude Code / LLM tool use

Discovered at `skills/risk-report/`. In a Claude Code session, ask
"run a risk report on my book" and pass your positions — Claude
returns VaR, ES, drawdown, and the variance budget. Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

The JSON supports either `weight` per position or `shares` + `price`
(in which case weights are computed from value share). See
[`examples/sample-book.json`](./examples/sample-book.json).

## Sample output

```
Risk Report — NVDA, AMZN, GOOGL, META (gross 100.0%)
Lookback 252d · Benchmark SPY · As of 2026-06-27

Portfolio statistics:
  Annualized vol         21.5%
  Annualized return      +18.0%
  Sharpe (naive)          0.84
  Beta vs SPY             1.32
  Tracking error         12.0%
  R² vs SPY              0.76

Value at Risk (1-day):
                         95%    99%
  Historical            -2.2%  -3.8%
  Parametric            -2.0%  -2.9%
  Expected shortfall    -3.1%  -5.2%   (historical, mean loss beyond VaR)

Max drawdown (252d): -18.0% from 2026-01-15 to 2026-04-08 (60 days, not recovered).

Worst 5 historical days for current book:
  2026-04-04   -5.5%   (SPY -4.1%)   NVDA -2.4pp · META -1.1pp · GOOGL -1.1pp · AMZN -0.9pp
  ...

Position contribution to portfolio variance:
  NVDA      42.3%   (weight 25.0%, vol 48.5%)
  META      21.1%   (weight 25.0%, vol 40.1%)
  GOOGL     19.4%   (weight 25.0%, vol 31.2%)
  AMZN      17.2%   (weight 25.0%, vol 28.7%)

Concentration: top 5 = 100%, Herfindahl 0.25 (effective N = 4.0)

Take: Book runs hot to SPY (beta 1.32). NVDA dominates risk (42% of
variance) despite sitting at 25% weight. Consider whether NVDA's
risk share matches your conviction in NVDA specifically.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`var-and-es.md`](./references/var-and-es.md) — historical vs
  parametric VaR, why Expected Shortfall is the more honest tail
  measure, sample-size requirements
- [`max-drawdown.md`](./references/max-drawdown.md) — peak-to-trough
  math, duration interpretation, recovery semantics
- [`stress-scenarios.md`](./references/stress-scenarios.md) — worst-N
  methodology, why empirical history beats Monte Carlo for "what hurt
  this book before"
- [`concentration.md`](./references/concentration.md) — Herfindahl,
  effective N, top-K, why these matter alongside vol

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST. The skill only pulls
daily aggregates, which are Tier B data; free Basic runs it too,
just slowly. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
