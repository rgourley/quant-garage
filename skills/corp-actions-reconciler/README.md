# corp-actions-reconciler

An ops desk inherits a position file from 2024. Did the share counts
get adjusted for AAPL's 4-for-1 split? GOOGL's 20-for-1? NVDA's
10-for-1? Run the tool. The exception report lists every position
whose recorded share count doesn't match the expected post-split
share count, with the source endpoint and verified-at timestamp on
every flag.

## Quick start

```bash
python3 examples/run-corp-actions.py examples/sample-positions.csv
```

## What you get back

```
4 BREAKS found across 5 positions checked. Clean: MSFT

BREAK 1: AAPL
  Recorded:    100 shares as of 2020-08-01
  Action:      4-for-1 split, ex-date 2020-08-31
  Expected:    400 shares · Delta: +300 (under-allocated)
  Basis:       $37.50/sh (was $150.00/sh)
  Source:      api.massive.com/v3/reference/splits?ticker=AAPL

BREAK 2: GOOGL · 50 → 1000 (20-for-1 split, 2022-07-18)
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`splits-methodology.md`](./references/splits-methodology.md) — forward and reverse split math
- [`dividends-methodology.md`](./references/dividends-methodology.md) — when cash dividends move the file vs informational
- [`spinoffs-methodology.md`](./references/spinoffs-methodology.md) — FMV cost-basis allocation and the spinoffs.json override pattern
- [`edge-cases.md`](./references/edge-cases.md) — fractional shares, ratio changes, the 5% that cause real ops breaks
- [`rendering.md`](./references/rendering.md) — exception-report format

## Plan requirement

Runs on free Stocks Basic. Above ~50 positions the 5-call/min limit
slows the run but doesn't block it. Any paid tier removes the limit.
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
