---
name: commodity-cycle
description: Single-commodity macro read. Answers "is this commodity in a winning or losing macro setup right now" and names the macro driver that dominates it. Pulls one commodity ETF (default GLD; accepts SLV, USO, DBC, or any commodity ETF) plus the macro context it needs (UUP for the dollar, TIP and IEF for a real-yield proxy, and GDX/SLV for gold's miner and silver co-move set) and computes the drivers that push the commodity around: rolling DXY correlation, real-yield correlation (vs the TIP-minus-IEF spread), miner divergence (gold only), silver co-movement (gold only), and a momentum quintile. The take reads constructive / neutral / headwind and names the dominant variable. It would have flagged a gold drawdown two weeks early via the dollar and real-yield readings. Use when the question is about one commodity specifically ("is gold in trouble", "what's driving crude", "is the dollar the problem for gold"). Runs on any stocks tier (Free Basic works with --sleep 13).
---

# commodity-cycle

macro-basket paints the whole cross-asset tape. commodity-cycle zooms in on
a single commodity and answers one question: is it in a winning or losing
macro setup right now, and which driver dominates it.

You give it a commodity ETF (default GLD) and it pulls the macro context
that commodity actually responds to: the dollar (UUP), a real-yield proxy
(TIP minus IEF), and for gold the miner and silver co-move set (GDX, SLV).
It reads the rolling correlations of the commodity to each driver, folds in
its own momentum, and states a one-line take: constructive, neutral, or
headwind, naming the dominant macro variable.

This is descriptive, not a forecast. It grounds the read in real ETF
prices so an LLM does not have to guess whether the dollar or real yields
are the thing hurting gold. It would have flagged a gold drawdown two weeks
early: a strengthening dollar plus rising real yields against a commodity
that is inversely correlated to both is a headwind before price confirms.

## When to invoke

- The session question is about one commodity specifically: "is gold in
  trouble", "what's driving crude", "is the dollar the problem for gold",
  "is silver set up well"
- You want the dominant macro driver named, not just a basket ranking
- Confirming or explaining a commodity move: "gold is down, is it the
  dollar or real yields"
- The user says "commodity setup", "gold cycle", "is this a headwind for
  gold", "why is gold lagging its miners"

For the full cross-asset dashboard (rates, credit, the dollar, gold, and
broad commodities ranked together), use
[`macro-basket`](../macro-basket). commodity-cycle is the single-name
drill-down; macro-basket is the whole tape. For the equity side use
[`market-regime`](../market-regime).

## What you need

- Nothing required beyond a key. Defaults cover the standard gold run.
- `MASSIVE_API_KEY` exported in the environment.
- Any stocks tier (all instruments are US-listed ETFs). On Free Basic pass
  `--sleep 13` so the pull stays under the 5-calls/min cap.

Optional:

- `--ticker` (default `GLD`): target commodity ETF (GLD/SLV/USO/DBC or any)
- `--window` (default `60`): lookback in trading days for returns and
  rolling correlations
- `--sleep` (default `0`): seconds between calls for Free Basic

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
A `signals` block (dxy_correlation, real_yield_correlation,
momentum_quintile, plus miner_divergence and silver_comovement for gold or
broad_commodity_comovement for others), a `setup` label, and the composed
`take`. UIs and downstream agents consume this.

**Layer 2: rendered note**: the setup label, a drivers block, then the
take. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily aggregates** for the target commodity plus UUP, TIP, IEF,
   and the co-move partners (GDX and SLV for gold, DBC for others) over
   `max(window, 252) * 1.6` calendar days, via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
2. **Rolling correlations** over the window: commodity vs UUP (the dollar),
   and commodity vs the TIP-minus-IEF daily return spread (real yields).
3. **Gold-specific reads** (skipped gracefully otherwise): GLD vs GDX
   relative return (miner divergence) and rolling GLD vs SLV correlation
   (silver co-movement).
4. **Momentum quintile**: the commodity's own window return ranked into a
   quintile 1..5 against its trailing-year window returns.
5. **Compose the take**: score each driver by its directional effect
   (correlation times the driver's own move) plus momentum and miner
   confirmation into constructive / neutral / headwind, naming the dominant
   variable. Methodology in
   [`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, and the `/v2/aggs` daily endpoint conventions.

## Output mode: note

The deliverable is a single-commodity read with a headline setup and a
short drivers block. A note (setup label, drivers, take) fits it better
than a wide table; the ranking view is macro-basket's job.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily closes per instrument. One call for the target plus the macro
  context and co-move set.

## Doesn't handle (yet)

- **Cash-market rates.** The real-yield read is the TIP-minus-IEF ETF
  return spread, not the actual real yield in basis points. Directionally
  right, not a cash-market substitute.
- **FX beyond the dollar index.** UUP covers the broad dollar; no per-pair
  FX. Queued.
- **Non-gold miner/co-move sets.** Only gold gets the miner (GDX) and
  silver co-move reads; other commodities fall back to a broad-commodity
  (DBC) correlation. Oil-services or ags co-move sets are queued.

These are clean PR extensions. The output schema is forward-compatible.
