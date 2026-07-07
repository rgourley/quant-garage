# backtest-data-prep

You're building a momentum backtest. You need a 4-year OHLCV dataset
that's properly split-adjusted, survivorship-clean, and free of
look-ahead bias. The tool emits a parquet file (1,003 trading days x
99 tickers in the standard run), a manifest documenting every
corporate action applied, and an edge-cases log noting any IPOs,
delistings, or symbol changes inside the window. Drop the parquet
into pandas and start backtesting.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.backtest_data_prep import run, render
payload = run(
    universe="top100",
    window=("2022-06-25", "2026-06-25"),
    out_dir="./backtest-data/",
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-backtest-data-prep.py --window 4y --seed top100
```

### Claude Code / LLM tool use

Discovered at `skills/backtest-data-prep/`. In a Claude Code
session, ask "build a clean 4-year OHLCV dataset for the top 100
US stocks". Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
Backtest dataset · 2022-06-25 → 2026-06-25 · 1003 trading days
Universe: top 100 by current mcap (99 after CS filter) · survivorship: biased

Files written
- ohlcv.parquet (98,140 rows, 12 cols, 3.9 MB)
- manifest.md, edge-cases.log (3 partial-coverage anomalies)

Corp actions: 18 splits (NVDA 10:1, GOOGL 20:1, TSLA 3:1, ...), 1,255 dividends
Schema: date, ticker, open, high, low, close, vwap, volume,
        transactions, adj_factor_cumulative, sic_code, sector

Take: Point-in-time clean for OHLCV and corp actions. Fundamentals not included.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`survivorship-handling.md`](./references/survivorship-handling.md) — stricter than `universe-builder`; backtests live or die on this
- [`corporate-action-adjustment.md`](./references/corporate-action-adjustment.md) — price-only vs total-return; v1 ships price-only
- [`calendar-alignment.md`](./references/calendar-alignment.md) — trading days, holidays, half-days, missing-row rules
- [`point-in-time-fundamentals.md`](./references/point-in-time-fundamentals.md) — look-ahead bias trap on fundamental joins
- [`output-formats.md`](./references/output-formats.md) — parquet schema and consumer pattern
- [`rendering.md`](./references/rendering.md) — dataset mode (operator-readable companion to the on-disk artifact)

## Plan requirement

Stocks Starter ($29/mo) with flat-files entitlement runs Tier A.
Same plan without flat-files runs Tier B via the bulk grouped-aggs
REST fallback (same output, slower wall-clock). See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
