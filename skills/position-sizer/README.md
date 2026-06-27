# position-sizer

The PM has the names already. NVDA, AMZN, GOOGL, META, all going in
the book. The next question is how much of each. Run the tool and
get four canonical sizing methods side-by-side: vol-target (high-vol
names cut), fractional Kelly (high-edge-per-variance names favored),
risk parity (every name contributes equally to portfolio variance),
equal weight. The numbers usually disagree; that's the point. Pick
the column whose worldview matches your conviction.

This is descriptive math, not alpha. The script doesn't pick names
and doesn't predict returns. You bring the basket and (for Kelly)
the edges.

## Quick start

```bash
python3 examples/run-position-sizer.py \
  --tickers NVDA,AMZN,GOOGL,META \
  --target-vol 0.12 \
  --max-weight 0.30 \
  --kelly-edges NVDA=0.15,AMZN=0.10,GOOGL=0.08,META=0.12
```

## Sample output

```
Position sizes — AMZN, GOOGL, META, NVDA
Target vol 12% · Lookback 252d · Max weight 30% · Leverage cap 1.0x

Ticker     σ(annual)    Vol-Target   Kelly(0.25)   Risk-Parity   Equal-Wt
--------------------------------------------------------------------------
AMZN          27.9%        20.7%        30.0%         30.0%        25.0%
GOOGL         30.1%        19.4%        23.2%         27.9%        25.0%
META          40.0%        14.6%        22.3%         21.7%        25.0%
NVDA          41.0%        14.3%        24.5%         20.3%        25.0%
--------------------------------------------------------------------------
Σ |w|                      69.0%       100.0%        100.0%       100.0%
Port vol                   12.0%        17.8%         17.4%        18.2%
Binding               target_vol   max_weight    max_weight         none

Take: Vol-target tilts away from NVDA (σ 41%) and toward AMZN (σ 28%);
keeps high-vol names from dominating the book. Kelly shifts toward
the highest edge per variance. Risk-parity equalizes each name's
contribution to portfolio variance. Equal-weight ignores risk
entirely. Pick the method that matches your conviction model.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`vol-target.md`](./references/vol-target.md) — inverse-vol weights,
  scaling to target portfolio vol, when the binding constraints matter
- [`kelly.md`](./references/kelly.md) — fractional Kelly derivation
  (matrix form), why edges are an input not an output, why 0.25
  scaling is the convention
- [`risk-parity.md`](./references/risk-parity.md) — ERC math, the
  iterative fixed-point algorithm, convergence behavior, identity
  shrinkage

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST. The skill only pulls
daily aggregates, which are Tier B data; free Basic runs it too,
just slowly. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
