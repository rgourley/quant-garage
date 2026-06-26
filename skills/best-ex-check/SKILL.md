---
name: best-ex-check
description: Transaction cost analysis on an executed-fills CSV. For each fill, pulls the NBBO at trade time, computes slippage versus the inside quote and session VWAP, and flags fills that crossed the spread, traded off-NBBO, paid through a wide spread, or showed adverse selection in the 30 seconds after the print. Exception-report mode: only flagged fills surface. Use when an execution desk or PM hands over a fill log and asks "did we get good fills today."
---

# best-ex-check

You hand over a fill log. The skill walks every line against the NBBO
at the fill timestamp, computes slippage in basis points, and emits an
exception report listing only the fills that deserve scrutiny.

This is the TCA workflow that buy-side compliance and execution desks
run after every session. The output answers two questions the regulator
asks (off-NBBO prints, wide-spread fills) and two questions the PM asks
(VWAP slippage, adverse selection). Same data, different audiences.

## When to invoke

- Execution desk says "run TCA on yesterday's fills" or "best-ex check
  this CSV"
- A PM asks "did we get good fills" or "what's our slippage versus
  arrival price"
- Compliance review of a month's executions for Reg NMS off-NBBO
  prints
- A broker performance review where you're comparing implementation
  shortfall across venues

## What you need

- A fill CSV with columns: `ticker`, `side` (BUY/SELL), `qty`, `price`,
  `timestamp` (ISO-8601 with timezone, Eastern recommended)
- `MASSIVE_API_KEY` exported. Stocks Developer or higher unlocks Tier A
  (full historical NBBO). Stocks Starter falls back to Tier B (1-second
  aggregate band as NBBO proxy).

## Tiers

### Tier A: full NBBO tick history

`GET /v3/quotes/{ticker}` returns every NBBO update at microsecond
precision. The skill pulls the inside quote at the fill timestamp,
flags crossed-spread fills against the exact inside, and detects
off-NBBO prints precisely. This is the default path on Developer+
tier keys.

Verified 2026-06-25 on a Stocks Business + Options Business + Benzinga
key: the endpoint returns full NBBO ticks. This contrasts with the
WebSocket `Q` channel, which returns `not_authorized` on the same key
(see portfolio-mark's `references/live-vs-delayed.md`). REST quote
history and live quote streaming are entitled separately.

### Tier B: 1-second aggregate band as NBBO proxy

When `/v3/quotes` returns 403, the skill walks
`GET /v2/aggs/ticker/{ticker}/range/1/second/{from}/{to}` for a
±2-second window around each fill. The "reference NBBO" at time T
becomes `low` of the [T, T+1s] bar as the proxy bid, `high` of the
same bar as the proxy ask. Defensible but lossy: a quote that updated
mid-second is invisible; a violent intra-second move is squashed into
the bar high/low.

See [`references/nbbo-proxy-via-aggregates.md`](./references/nbbo-proxy-via-aggregates.md)
for the precision tradeoffs and what gets lost.

## What you get back

Two output layers.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per flagged fill: ticker, side, qty, price, timestamp, slippage_bps,
spread_bps_at_fill, vwap_slippage_bps, reasons[], adverse_selection_bps,
suggested_next_action. Per run: scan_params, fills_checked, tier,
quote_source, summary (counts by reason, total implementation
shortfall in dollars). UI dashboards and downstream agents consume
this.

**Layer 2: rendered exception report**. Header with the run metadata.
One BREAK block per flagged fill. Summary block at the bottom. See
[`references/rendering.md`](./references/rendering.md) for the full
format. Claude Code users read this.

A short example:

```
TCA: 18 fills checked · 6 BREAKS flagged · run 2026-06-25 15:32 UTC

BREAK 1: AAPL BUY 1,000 @ $300.85 · 2026-06-23 10:14:18 ET
  Slippage:    +18.0 bps vs reference ask $300.31 at 10:14:18
  Spread:      $300.25 × $300.31 (2 bps inside, normal)
  VWAP slip:   +18.4 bps vs session VWAP $296.88
  Reasons:     crossed_spread, high_vwap_slippage
  Adverse:     +4.2 bps within 30s of fill (mild adverse)
  Suggest:     Investigate venue routing; price improvement opportunity missed
```

## How it works

1. **Probe quote-data availability.** Call `/v3/quotes/{ticker}?limit=1`
   on the first ticker in the CSV. 200 means Tier A; 403 means Tier B.
   Print the tier in the report header so the operator sees the
   confidence level.
2. **For each fill**, pull the reference NBBO at the fill timestamp:
   - Tier A: `/v3/quotes/{ticker}` with `timestamp.gte`/`timestamp.lte`
     wrapping the fill time. Take the most recent quote before the
     fill timestamp.
   - Tier B: `/v2/aggs/ticker/{ticker}/range/1/second/{from}/{to}` for
     the second-bar straddling the fill. Reference bid = bar low,
     reference ask = bar high.
3. **Compute slippage** per [`references/slippage-methodology.md`](./references/slippage-methodology.md):
   `bps = (fill_price - reference_price) / reference_price × 10000`.
   Signed by side: positive on a BUY means paid more than reference
   (bad); negative on a SELL means sold below reference (bad).
4. **Pull session VWAP** from
   `/v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}`, compute
   weighted average across all minutes up to the fill timestamp. The
   fill's VWAP slippage is the same bp formula, sign-adjusted.
5. **Check adverse selection** by pulling 30 seconds of post-fill
   second-aggregates and measuring price drift. A BUY followed by
   price decline is adverse; a SELL followed by price rise is adverse.
   See [`references/adverse-selection.md`](./references/adverse-selection.md).
6. **Apply flag categories** per [`references/flag-categories.md`](./references/flag-categories.md).
   A fill that hits zero categories is dropped. A fill that hits one
   or more becomes a BREAK in the exception report.
7. **Emit JSON and rendered output.** Only flagged fills appear.
   Summary block shows counts by category and total estimated
   implementation shortfall in dollars.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate limiting, and timestamp handling

## Endpoints used

- `GET /v3/quotes/{ticker}` (Tier A; primary)
- `GET /v2/aggs/ticker/{ticker}/range/1/second/{from}/{to}` (Tier B
  primary; also used for adverse-selection check on both tiers)
- `GET /v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}` (session
  VWAP)

## Example

```bash
# Fill log with 18 trades from a session
cat examples/sample-fills.csv

# Invoke from Claude Code
# > /best-ex-check examples/sample-fills.csv
```

The skill processes fills in order and emits flagged ones immediately,
so a long fill log streams findings instead of waiting for completion.

## Doesn't handle (yet)

- Options fills. OCC contract NBBO lives at
  `/v3/quotes/O:{occ_ticker}`; the endpoint shape is the same but the
  contract-specific liquidity model and 1-second-aggs-for-options
  fallback are different. A v2 of this skill will add an
  options-aware path.
- Block-trade carveouts. A 100,000-share block fill on a $50 stock is
  expected to print outside the NBBO and shouldn't be flagged as
  `off_nbbo` without context. v1 flags it as a BREAK with reason
  `off_nbbo`; the operator overrides on review.
- Average-price fills (VWAP execution, TWAP execution, ISO orders).
  These are best evaluated against the parent order's benchmark, not
  the inside quote at the child-fill print. v1 evaluates every fill
  as an arrival-price comparison; an extended schema can add
  `parent_strategy` to the input CSV and switch the benchmark.
- Cross-venue dark-pool prints. Flagged as `off_nbbo` in v1 (which is
  correct from a Reg NMS view), but the operator may want a separate
  `dark_print` reason. Add to the flag categories file if needed.

Add these in a PR if you need them. The slippage and adverse-selection
math carry over.
