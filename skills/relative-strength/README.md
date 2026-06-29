# relative-strength

You have a watchlist. You want to know which names have been leading
SPY (or whatever benchmark) over the last week, month, quarter, and
half-year, and whether the leadership is accelerating or rolling over.
This skill ranks them and labels each one.

For universe-wide momentum studies (top 500, IC, decile spreads), use
[`factor-research`](../factor-research). This skill is the lightweight
watchlist ranker.

## Quick start

```bash
python3 examples/run-relative-strength.py \
  --watchlist NVDA,AMD,MU,INTC,QCOM,AVGO,TXN,KLAC,AMAT,LRCX \
  --benchmark SPY \
  --windows 5,20,60,120
```

Add `--include-sectors` to drop the 11 SPDR sector ETFs into the same
ranking for sector-leadership context.

## What you get back

```
Relative Strength vs SPY — 2026-06-29
Watchlist: 10 tickers · Windows: 5/20/60/120 days

Ticker     5d RS    20d RS    60d RS   120d RS   Trend              Comp %ile
─────────────────────────────────────────────────────────────────────────────
NVDA      +142bp    +380bp    +720bp   +1850bp   stable_leader            92
AVGO       +95bp    +210bp    +480bp   +1020bp   stable_leader            85
AMD        +60bp    +180bp    +320bp    +680bp   improving                71
...
INTC       -85bp    -290bp    -540bp   -1100bp   stable_laggard            8

Leaders:  NVDA, AVGO, AMD
Laggards: INTC, QCOM, TXN
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the table in Claude
Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`methodology.md`](./references/methodology.md) — why basis points,
  why composite percentile, how trend labels are assigned
- [`rendering.md`](./references/rendering.md) — table sort rules,
  leader / laggard footer

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST. A watchlist of 10 names
plus benchmark is 11 calls per run; with `--include-sectors` it's 22.
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md). That
file is what Claude reads to decide when and how to invoke this tool.
