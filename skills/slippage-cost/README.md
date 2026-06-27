# slippage-cost

You hand the tool yesterday's executed fills. It pulls the
microsecond NBBO at each trade time, computes slippage vs the
inside, and flags fills that crossed the spread, printed off-NBBO,
hit a wide spread moment, or showed adverse selection in the 30
seconds after fill. The exception report is short by design: only
the broken stuff surfaces.

This is NOT true Implementation Shortfall. IS compares each fill
against the decision-time (arrival) benchmark price; this skill
compares against NBBO at fill time. The input CSV doesn't carry an
arrival timestamp, so arrival-price IS isn't computable here. Use
this when you want to know "how much did we leak vs the inside,"
not "did execution match the PM's decision price."

## Quick start

```bash
python3 examples/run-slippage-cost.py examples/sample-fills.csv
```

## What you get back

```
16 of 20 fills flagged · $4,812 implementation shortfall

BREAK 1: AAPL BUY 1,000 @ $299.64 · 10:14:18 ET
  Slippage:  -1.3 bps vs reference ask $299.68
  VWAP slip: +38.8 bps vs session VWAP $298.48
  Reasons:   high_vwap_slippage
  Suggest:   Fill timing diverged from VWAP; review parent-order strategy

BREAK 3: NVDA BUY 2,000 @ $201.85 · 14:08:42 ET
  Slippage:  +13.4 bps vs reference ask $201.58
  Reasons:   crossed_spread, adverse_selection (+12.4 bps within 30s)
  Suggest:   Paid up into adverse flow; classic toxic-fill pattern
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`flag-categories.md`](./references/flag-categories.md) — five flag types, thresholds, one-line read on each
- [`slippage-methodology.md`](./references/slippage-methodology.md) — bps math signed by side, "positive is bad" convention
- [`nbbo-proxy-via-aggregates.md`](./references/nbbo-proxy-via-aggregates.md) — Tier B fallback when quotes aren't entitled
- [`adverse-selection.md`](./references/adverse-selection.md) — 30-second-after-fill drift as toxic-flow signal
- [`rendering.md`](./references/rendering.md) — exception-report (only broken fills surface)

## Plan requirement

Stocks Developer or higher runs Tier A with microsecond NBBO ticks
via `/v3/quotes`. Stocks Starter ($29/mo) auto-downgrades to Tier B
using 1-second aggregate bands as the NBBO proxy. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
