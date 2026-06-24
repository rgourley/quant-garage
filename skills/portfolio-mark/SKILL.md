---
name: portfolio-mark
description: Mark a book of positions to current fair value and flag any name where the mark is low-confidence (stale, wide-spread, illiquid). Two modes: delayed REST snapshots for EOD reporting, live WebSocket stream for intraday risk. Emits a marked-positions table plus an exception block per flagged mark. Use when an operator hands over a CSV and asks "what's this book worth right now" or "which marks am I not sure about."
---

# portfolio-mark

You hand over a position CSV. The skill marks every line to current
fair value, computes book value and (if cost basis is in the input)
unrealized P&L, and flags any mark where confidence is medium or low
so the operator can override before the number lands in a report.

This is the "what is this book worth right now" workflow. Pricing
services do it; this skill does the same thing without the seat fee
and with the per-position confidence rating that internal pricing
desks usually leave implicit.

## When to invoke

- Operator says "mark this book" or "what's this portfolio worth right
  now" or hands over a CSV asking for current value
- A risk team needs intraday marks for a Greek aggregation or var run
- An ops team is generating an EOD statement and wants to flag thin
  names before publishing the NAV
- A PM is wiring up a live dashboard and needs a streaming mark per
  symbol

## Modes

### Delayed mode (default)

REST snapshot per position. Walks the fallback chain
(`snapshot.last.price` → `lastTrade.p` → `min.c` → `day.c` →
`prevDay.c`) and emits the timestamp of whichever field won. Works on
any paid Stocks plan; on Free Basic the rate limit caps batch size to
~5 positions per minute but the methodology is identical.

Use this for end-of-day reporting, weekly statements, and any context
where a 15-minute lag on the underlying is acceptable. Cheaper
operationally; no socket to manage; one report per run.

### Live mode

WebSocket subscribes to one or more channels for every position.
Listens for `--listen` seconds (default 30), accumulates the most
recent mark per symbol, and emits the same marked-positions table
plus an optional "live tape" trailer showing the last few ticks per
symbol.

Live mode reads from the **business cluster**
(`wss://business.polygon.io/stocks`) and prefers, in order:

1. `FMV.{ticker}` (Business-tier Fair Market Value stream)
2. `AM.{ticker}` (per-minute aggregates; available on Stocks Business
   without the real-time addendum)
3. `T.{ticker}` (tick trades; requires Stocks Advanced + signed
   real-time agreement)

If `T.{ticker}` returns `not authorized`, the skill falls back to
`AM.{ticker}` automatically and notes the downgrade in the rendered
output. See [`references/live-vs-delayed.md`](./references/live-vs-delayed.md)
for the tier matrix and which channels each plan actually delivers
(the published Massive docs and the lived entitlement behavior diverge,
see notes there).

## What you need

- A position CSV with at minimum `ticker,shares`. Optional columns:
  `cost_basis` (per-share, for P&L), `as_of_date` (informational).
- `MASSIVE_API_KEY` exported. Delayed mode runs on Stocks Starter or
  higher (Stocks Basic works with rate-limit pain). Live mode needs
  Stocks Advanced for `T.{ticker}` ticks, or Stocks Business for the
  `AM.{ticker}` and `FMV.{ticker}` fallback channels.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-position fields: mark, mark_source (which step in the fallback
chain won), confidence (high/medium/low), as_of timestamp, bid/ask if
available, spread in basis points, and (if cost basis given) unrealized
P&L. Per-flagged-mark fields: reason codes, detail text, source
endpoint. UIs and downstream agents consume this.

**Layer 2: rendered hybrid output**. A marked-positions table at the
top, a FLAGGED exception block at the bottom for any mark that wasn't
high-confidence. Optional "Live tape" trailer in live mode. See
[`references/rendering.md`](./references/rendering.md) for the format
rules. Claude Code users read this.

## How it works (delayed mode)

1. Read every row of the CSV. Group by symbol; warn on duplicates but
   keep them separate (different lots).
2. For each unique symbol, GET the snapshot endpoint. Walk the
   fallback chain per
   [`references/snapshot-fallback-chain.md`](./references/snapshot-fallback-chain.md)
   and record which step produced the mark.
3. Compute confidence per
   [`references/confidence-scoring.md`](./references/confidence-scoring.md):
   recency of the chosen mark, bid-ask spread in bps, and average daily
   volume. High = top-decile ADV and last trade within 60s and spread
   <10bps; Medium = mid ADV and trade within 5min and spread 10-50bps;
   Low = anything thinner or staler.
4. Compute book value (sum of `shares * mark`). If cost basis is in
   the input, compute unrealized P&L per
   [`references/book-value-and-pnl.md`](./references/book-value-and-pnl.md).
5. Emit JSON and rendered markdown. Anything below `high` confidence
   appears in the FLAGGED block.

## How it works (live mode)

1. Open one WebSocket to the business cluster.
2. Auth, then subscribe to the preferred channel for every symbol
   in the book. If `T.{ticker}` returns `not authorized`, resubscribe
   to `AM.{ticker}` and note the downgrade.
3. Listen for `--listen` seconds. Maintain per-symbol state: last
   mark, last update timestamp, trade count, recent ticks for the tape.
   See [`references/websocket-mark-updates.md`](./references/websocket-mark-updates.md)
   for the message-handling pattern (move work off the receive thread,
   resubscribe on reconnect, backpressure-aware drain loop).
4. On disconnect within the listen window, resubscribe to the full
   set per the
   [`massive-websockets`](../massive-websockets) foundation. Log the
   gap and continue.
5. At the end of the window, emit the same marked-positions table.
   For symbols that received zero ticks during the window, fall back
   to a one-shot REST snapshot (with `mark_source: "snapshot.*"` so
   the operator sees which symbols never streamed).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate limiting, and the snapshot fallback chain
- [`massive-websockets`](../massive-websockets) for the live socket
  flow (auth, subscribe, reconnect, backpressure)

This is the first skill in the suite that exercises the
`massive-websockets` foundation end-to-end. Any gotchas discovered
during the build are added to that foundation's SKILL.md.

## Endpoints used

Delayed mode:

- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: mark,
  bid/ask, recent OHLC. The single REST call per symbol.
- `GET /v3/reference/tickers/{ticker}`: optional, for ADV lookup when
  the snapshot doesn't carry recent daily volume.

Live mode (in addition to the REST snapshot for fallback):

- `wss://business.polygon.io/stocks` channels: `FMV.{ticker}`,
  `AM.{ticker}`, `T.{ticker}` (subscribe-time fallback).

## Doesn't handle (yet)

- Options OCC marks (the skill is equity-only for v1; options chains
  use a different snapshot endpoint and a different live channel under
  `wss://socket.polygon.io/options`. Extending to options is a clean
  follow-up; the fallback chain and confidence model carry over.)
- FX-denominated positions. The mark is in USD; non-USD positions
  need an FX overlay outside the scope of v1.
- Long/short with separate margin treatment. The skill flips the sign
  on `unrealized_pnl_usd` for negative-share positions but doesn't
  compute margin requirements.
- Multi-account roll-up. One CSV in, one report out.

Add these in a PR if you need them. The patterns are clean extensions
of the existing chain + confidence logic.
