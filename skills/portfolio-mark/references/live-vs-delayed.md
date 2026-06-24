## Live vs delayed mode

When to use each, what each costs, and what each gives up.

## Delayed mode (REST snapshot)

**Use for:** EOD reporting, weekly statements, monthly NAV runs, any
context where a 15-minute lag on each mark is acceptable. Also the
right choice on Free Basic when WebSocket isn't on the plan.

**Pros:** simple operationally (one HTTP call per symbol), works on
any paid Stocks plan, no socket to keep alive, deterministic output
("the mark at this moment was X"), low ongoing cost.

**Cons:** 15-minute lag on Stocks Starter/Developer (real-time only
on Stocks Advanced). The mark you ship is already stale by the time
the operator reads it; not appropriate for intraday risk or any
hedging decision.

**Run cost:** one snapshot call per unique symbol. A 50-position book
is 50 calls; on any paid tier that's sub-second total. On Free Basic
the 5/min rate cap stretches it to 10 minutes.

## Live mode (WebSocket stream)

**Use for:** intraday risk dashboards, live Greek aggregation, any
context where the mark you ship should be seconds old, not minutes.
The dashboard refreshing as the market moves.

**Pros:** sub-second updates on the active channels. One connection
serves the whole book. The "live tape" trailer surfaces flow
context the delayed mode can't show.

**Cons:** operationally heavier (socket reconnects, message-rate
spikes at the open, backpressure when subscriptions are wide). Costs
more in plan tier; needs at minimum Stocks Business for the AM/FMV
channels or Stocks Advanced for the T tick stream.

**Run cost:** one WebSocket connection for `--listen` seconds. The
foundation skill ([`massive-websockets`](../../massive-websockets))
covers the reconnect strategy and the message-buffering pattern.

## Channel matrix (verified 2026-06-23 on a Stocks Business +
Options Business + Benzinga key)

The published Massive channel matrix and the lived entitlement
behavior diverge. Here's what actually works:

| Channel | What | Endpoint | Entitlement reality |
|---|---|---|---|
| `T.{ticker}` | Tick trades | `wss://business.polygon.io/stocks` | "not authorized" on Stocks Business despite docs. Requires Stocks Advanced AND a signed real-time data agreement in the Massive dashboard. |
| `Q.{ticker}` | NBBO quote ticks | same | Same as T: not authorized on Business until the agreement is signed. |
| `A.{ticker}` | Per-second aggregates | same | Same as T. |
| `AM.{ticker}` | Per-minute aggregates | same | Works on Stocks Business immediately. No agreement needed. |
| `FMV.{ticker}` | Fair Market Value stream | same | Works on Stocks Business immediately. Business-tier proprietary metric. |

What this means for the skill: the channel preference order is
`FMV → AM → T`, not the other way around, on a Business key. The skill
attempts `T.{ticker}` first when invoked on a tier that should have
it, and on the `not_authorized` status message resubscribes to
`AM.{ticker}` automatically and adds `stream_downgrade` to the
caveats array.

## Reference time and confidence interact with mode

The confidence model in
[`confidence-scoring.md`](./confidence-scoring.md) compares each
position's `as_of` to a reference time. In delayed mode the reference
is `marked_at` (when the run started). In live mode the reference is
the end of the listen window. A position that received its last tick
40 seconds before the end of a 30-second listen window would be
high-confidence (the tick was during the window) but the same 40-
second gap in delayed mode would already be ineligible for high
confidence because high requires within 60s.

## When to combine

Some operators run delayed-mode daily and live-mode hourly during
trading hours. The output schema is identical so a single dashboard
can ingest both; the `mode` field tells the UI which icon to show.

## Market-closed live runs

A live run during a closed market period (overnight, weekend) connects
and authenticates fine, subscribes successfully, and then receives
zero data messages. The skill detects this via `tick_count == 0`
across the entire book, falls back to one REST snapshot per symbol,
and adds `no_ticks_in_window` to the caveats. The output renders
identically to a delayed run but the `mode` field still says "live"
so the operator knows the socket was tested.

This is useful for validating the WebSocket plumbing end-to-end
outside of market hours.
