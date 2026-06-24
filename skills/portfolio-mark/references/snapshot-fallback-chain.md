## Snapshot fallback chain

The canonical price-resolution chain when marking a position from a
single REST snapshot. Walk it in order and stop at the first non-null
field. Always emit `mark_source` so the caller sees which step won
and the corresponding `as_of` timestamp so they know how stale it is.

This is the same chain documented in
[`massive-api-patterns`](../../massive-api-patterns); portfolio-mark
adds the per-step staleness consequences for the confidence model.

## The chain

For a snapshot returned by
`GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`:

| Step | Field path | What it is | Typical lag (Stocks Starter) |
|---|---|---|---|
| 1 | `snapshot.ticker.lastTrade.p` | Last reported trade price | 15 min |
| 2 | `snapshot.ticker.lastTrade.p` (older snapshot endpoints) | Same field, legacy name | 15 min |
| 3 | `snapshot.ticker.min.c` | Close of the current minute aggregate | up to 1 min after the snapshot timestamp |
| 4 | `snapshot.ticker.day.c` | Today's last printed price | up to 15 min |
| 5 | `snapshot.ticker.prevDay.c` | Yesterday's close | up to ~24h on the first run of the day before the open print |

In the JSON output, `mark_source` uses the form `snapshot.last.price`
for step 1, `snapshot.lastTrade.p` for step 2, and so on, matching the
shorthand the foundation skill uses.

## When each field is null

- `lastTrade.p` is null for symbols that haven't traded yet today
  (pre-market for stocks that don't trade pre-market) and for
  delisted/halted names. On Stocks Starter, this field is the
  delayed last trade, not the live one.
- `min.c` is null in the first few seconds of a new minute before the
  aggregate seals, and for the entire pre-market window if the ticker
  doesn't trade pre-market.
- `day.c` is null before the first print of the day (early pre-market,
  market-closed weekend morning).
- `prevDay.c` should always be populated for any live ticker. If it's
  null, you're looking at a brand-new IPO, a recently relisted symbol,
  or a symbol that doesn't exist. The skill treats this as an error
  and reports `mark_price: null` for that position with confidence
  `low`.

## Why the chain isn't "just use lastTrade"

Stocks Starter and Developer return delayed prints in `lastTrade`. On
a thin or halted name, `lastTrade` can be hours stale while `prevDay.c`
is the operator's actual valuation reference. Walking the chain and
emitting the source lets the operator see "this mark came from
yesterday's close, not from a recent print" before the number lands in
a NAV.

Even on Stocks Advanced (real-time `lastTrade`), the chain remains
useful for halted names and overnight runs where the snapshot is
queried while the market is closed.

## Live mode interaction

In live mode, the WebSocket produces marks first; the REST chain only
runs as a backfill for symbols that received zero ticks during the
listen window. When the snapshot backfills, the resulting position
carries `mark_source: snapshot.*` (the specific step that won) and
the live-mode caveat "no ticks in listen window" is added to the
flagged reason codes if the snapshot also produced a less-than-high
confidence.

## Implementation note

Always emit the timestamp of whichever field won as `as_of_utc` and
its Eastern-time counterpart as `as_of_et`. Massive returns most
timestamps in nanoseconds (e.g. `lastTrade.t`, `lastTrade.f`); the
minute aggregate uses `.t` in milliseconds. Convert to UTC at the
boundary and never propagate nanoseconds past the source code.
