---
name: sector-rotation-signal
description: Change-detection layer on top of market-regime. market-regime reports current sector leadership as a snapshot; this skill tracks how the leadership order has changed over a rotation window (default 30 days) and flags sectors moving up or down the ranks. Categorizes moves into growth / value-cyclical / defensive / rate-sensitive buckets and generates a plain-English theme read. Use when the daily regime hasn't moved but the composition of leadership is shifting — the actual tradeable signal.
---

# sector-rotation-signal

You hand over a rotation window (default 30 days) and get back the
current 20-day RS rank per sector, the rank change over the window,
and a rotation classification (rotating in, stable, rotating out).
Bundled with a plain-English theme read.

Rank change is the leading signal. The tape already prices absolute
strength; leadership rotation is what tells you the composition of
strength is shifting.

## When to invoke

- The user asks "what sectors are rotating", "is the market rotating",
  "growth vs value right now"
- market-regime shows the same label two days in a row but you want
  to know if the underlying composition is changing
- Portfolio-review workflow, after market-regime — regime says WHAT,
  rotation-signal says WHERE-IT'S-MOVING
- A single-name research question wants sector context: "is my
  Tech position bucking the sector rotation"

## What you need

- `MASSIVE_API_KEY` (Stocks Starter). One daily-aggs call per SPDR
  sector ETF + SPY (12 calls total).

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Per-sector: current 20-day RS rank, rank in the reference date,
rank_delta, 20-day RS in basis points, secondary RS window, rotation
classification, category tags.

**Layer 2 rendered table** sorted by current rank. Rotation column
uses arrows (`↑`, `↑↑`, `↓`, `↓↓`, `stable`) so the visual read is
immediate. Theme line above the table. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Fetch SPY + 11 sector ETFs** over `lookback_days` (default 252)
   using the same helpers as market-regime.
2. **Compute RS ranks on two reference days**: `now` (latest trading
   day) and `then` (latest trading day <= today - rotation_window).
   RS is `sector_return - spy_return` over the primary RS window
   (default 20 days), reported in basis points.
3. **Compute rank_delta** = rank_then - rank_now (positive means the
   sector moved UP the leadership order, i.e. rank number went DOWN).
4. **Classify each sector**: rotating_in_strong (delta >= +3),
   rotating_in (>= +2), stable (|delta| <= 1), rotating_out (<= -2),
   rotating_out_strong (<= -3).
5. **Generate theme read** based on which category buckets (growth,
   defensive, value-cyclical, rate-sensitive) are receiving inflows
   vs outflows.

## Category tags

- **growth**: XLK, XLY, XLC
- **defensive**: XLP, XLU, XLV
- **value_cyclical**: XLE, XLI, XLB, XLF
- **rate_sensitive**: XLRE, XLU, XLF (overlap intentional)

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` for SPY +
  11 sector ETFs

## Doesn't handle (yet)

- **A 1-position rank move is inside the noise floor.** Deliberately
  classified as `stable`. Users who want fine-grain rotation should
  use a longer rotation window.
- **SPDR sector ETFs are a proxy for the full sector universe.**
  Real-world sector performance can diverge from the ETF (especially
  in Energy, where XLE is oil-major heavy).
- **RS is past-return, not predictive.** Rotation captures a shift
  in what's working, not what will work next.
- **Rotation reads are heuristic.** Real regime classification
  belongs in a dedicated macro tool; this is a change-detection
  surface, not a regime call.
