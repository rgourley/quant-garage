# portfolio-macro-scenario

`risk-report` and `portfolio-review` are descriptive of the past.
This one is prescriptive under a scenario you name. Given your book,
what happens if rates keep rising, the dollar keeps rallying, oil
spikes, or gold falls?

## Quick start

### Python library

```python
from quant_garage.skills.portfolio_macro_scenario import run, render
payload = run(
    book_path="examples/sample-book.csv",
    rates_bp=50,      # +50bp parallel shock
    dxy_pct=2,        # +2% dollar
    oil_pct=10,       # +10% oil
    gld_pct=-5,       # -5% gold
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-portfolio-macro-scenario.py \
  --book examples/sample-book.csv \
  --rates-bp 50 --dxy-pct 2 --oil-pct 10 --gld-pct -5
```

### Claude Code

Discovered at `skills/portfolio-macro-scenario/`. Ask "shock my book
by +50bp rates and +2% dollar" and the tool runs the regression and
returns book-level P&L with dominant contributors.

## Method

1. Pull daily aggs for every position ticker plus TLT, UUP, USO, GLD.
2. Per-position multivariate OLS regression of daily returns on the
   four factor returns (numpy.linalg.lstsq with an intercept).
3. Translate shocks: DXY/oil/gold as direct ETF returns; rates via
   an effective duration of ~17 years on TLT.
4. Position P&L = position_value * sum(beta * shock).
5. Book P&L with a rough +/-1.64 sigma band (independence assumed).
6. Rank positions by absolute contribution, factors by aggregate.

## What you get back

A sensitivity table (position x factor beta, R^2, expected return,
per-position P&L), book P&L with a 90% band, dominant positions,
dominant factors, and a one-line take. Full JSON contract in
[`output-schema.json`](./output-schema.json).

## Runs on

Stocks Starter (unlimited REST) or Free Basic with `--sleep 13` for
larger books. Details in [`requires.yml`](./requires.yml).
