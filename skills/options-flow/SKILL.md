---
name: options-flow
description: Surface unusual options activity across a watchlist as a Cheddar Flow / FlowAlgo-style stream. Each notable print rendered as a three-line block with kind (sweep vs block), premium, volume vs avg, volume vs OI (opening vs closing), price relative to NBBO, and inferred direction. Use when a trader is scanning for unusual flow, monitoring smart money, or hunting for actionable setups. Real-time on Options Business; ~15-min delayed on Options Developer.
---

# options-flow

You hand over a watchlist. The skill scans each name's options chain,
filters for unusual activity (high volume vs 30-day avg, volume above
open interest, premium thresholds), classifies each print as a sweep or
block, infers direction from where the trade printed in the NBBO, and
emits a Cheddar Flow / FlowAlgo-style stream of the top N most actionable
prints.

This is the "what's smart money doing right now" workflow. Unlike a chain
viewer or screener, options-flow ranks prints by signal quality rather
than raw volume, ships the methodology with the output, and emits both
JSON and human-readable formats from one analysis.

## When to invoke

- A trader is starting their session and wants the day's unusual flow
- A PM is checking whether options activity confirms a thesis
- The user says "what's the flow on NVDA today", "any unusual options
  activity in TSLA", or "scan flow on my watchlist"
- A discretionary trader is hunting for sympathy plays off a major print

## What you need

- A watchlist of tickers (default: AAPL, NVDA, TSLA, AMD, SPY)
- `MASSIVE_API_KEY` exported in the environment
- Options Developer plan minimum (Options Business for real-time)

The skill runs at three fidelity tiers. The chosen tier is flagged in
the output JSON as `tier`.

- **Tier A (real-time):** Options Business + Stocks Business. Tape is
  live, sweeps detected within seconds, IV and OI fresh. The output
  matches what Cheddar Flow / FlowAlgo show their subscribers.
- **Tier B (15-min delayed):** Options Developer + Stocks Starter. Same
  methodology, same per-print fields, but the prints are at least 15
  minutes old. Useful for end-of-day review or postmortems.
- **Tier C (free Basic):** Documented but not actively supported. Free
  Basic doesn't include options snapshot or trades; the skill warns and
  exits.

## What you get back

The skill ships two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-print fields include the OCC contract ID, kind (sweep/block/other),
premium, volume, volume vs 30-day avg, volume vs OI (signals
opening/closing), price vs NBBO (above_ask / at_mid / below_bid),
inferred direction (bullish/bearish/neutral), spot at print, IV at
print, and the contributing trades. UIs, alert pipelines, and downstream
agents consume this.

**Layer 2: rendered stream** in Cheddar Flow style. See
[`references/rendering.md`](./references/rendering.md) for the format
rules. Three lines per print plus optional `↳` continuation lines for
context (clustering, prior prints, dealer notes). Compact, scanable,
key:value pairs. Claude Code users read this.

## How it works

1. For each ticker in the watchlist, pull the options chain snapshot
   filtered to expiries within the next ~60 days and strikes within ±10%
   of spot. This caps the chain at the slice where actionable flow lives.
2. For each contract, compute the unusual activity score per
   [`references/unusual-activity-detection.md`](./references/unusual-activity-detection.md):
   volume / 30-day avg, volume / OI (signals opening vs closing interest),
   premium dollar value, and percentage of chain volume.
3. Pull recent trades for the top-ranked contracts. Classify each as
   sweep or block per [`references/sweep-vs-block.md`](./references/sweep-vs-block.md):
   the Massive trade conditions field carries condition `219`
   (Intermarket Sweep Order) when the print swept multiple exchanges.
4. Infer direction per [`references/directional-inference.md`](./references/directional-inference.md):
   compare trade price to the NBBO at the time of print (above ask =
   aggressive buy, below bid = aggressive sell, at mid = negotiated).
   Apply the call/put overlay for bullish vs bearish read.
5. Distinguish opening from closing per
   [`references/opening-vs-closing.md`](./references/opening-vs-closing.md):
   trade volume materially above OI = new interest opening; under OI =
   likely closing existing positions.
6. Rank all qualifying prints by score and emit the top N (default 20).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, rate
  limiting, and the best-price fallback chain for spot

## Output mode: stream

Stream mode is the format Cheddar Flow / FlowAlgo / Unusual Whales use
for live flow. Each print is a self-contained block; the reader scans
top to bottom and stops when they see one they want to act on.
[`references/rendering.md`](./references/rendering.md) is the canonical
format reference for any future stream-mode skill (news-scanner,
crypto-vol-scanner).

## Endpoints used

- `GET /v3/snapshot/options/{ticker}`: paginated options chain with
  per-contract day volume, OI, IV, greeks, last quote (NBBO).
- `GET /v3/trades/{occ_ticker}`: tick-level trades for a contract.
  Returns size, price, conditions array (219 = ISO sweep), and
  exchange. Used to classify sweep vs block and to compare against
  NBBO. Real-time on Options Business; 15-min delayed on Options
  Developer.
- `GET /v2/aggs/ticker/{occ_ticker}/range/1/day/{from}/{to}`: per-day
  volume aggregates for the contract's recent history, used to compute
  30-day average volume.
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: spot
  price snapshot, with the best-price fallback chain.

## Doesn't handle (yet)

- Multi-leg detection (spreads, condors, butterflies). Massive's trade
  feed marks them via conditions 232-240, but constructing the underlying
  strategy requires linking the legs by sequence_number which the v1
  skill doesn't attempt.
- Dealer positioning / GEX. The skill documents the methodology in
  [`references/dealer-positioning.md`](./references/dealer-positioning.md)
  but doesn't compute it. v2 candidate.
- Real-time WebSocket streaming. v1 is REST-polled. Use the
  `massive-websockets` foundation for live stream interop in a future
  variant of this skill.
- Dark pool prints on the underlying. Surfacing dark prints alongside
  options flow is a known complement; left for a separate skill.

These are clean PR extensions and welcome contributions.
