---
name: macro-basket
description: Cross-asset macro context. Pulls a fixed basket of liquid macro ETFs (TLT, IEF, SHY, HYG, LQD, TIP, BND, GLD, SLV, UUP, DBC), ranks each by relative strength versus SPY across 5/20/60/120-day windows, and derives the cross-asset signals a macro desk watches: rates direction (TLT), curve shape (SHY vs TLT, bull/bear steepening/flattening), real yields (TIP vs IEF), credit stress (HYG vs LQD), dollar direction (UUP), commodity carry (DBC), gold/silver ratio, and gold-vs-dollar beta. The take is a one-sentence macro summary. Use when the question is "what's happening in rates / credit / the dollar / gold / commodities" (the piece market-regime does not cover). Runs on any stocks tier. Requires Stocks Starter (Free Basic works with --sleep 13).
---

# macro-basket

market-regime tells you what equities are doing. macro-basket tells you
what everything else is doing: rates, credit, the dollar, gold, and broad
commodities. Together they are the full risk picture for a research
session.

You run it and get each macro ETF ranked by relative strength versus SPY,
plus a block of derived cross-asset signals stated the way a macro desk
states them: rates easing or tightening, the curve bull-steepening or
bear-flattening, credit tight or widening, the dollar strong or weak,
commodity carry on or off. The take line is the one-sentence summary you
would put at the top of a macro note.

This is not a rates model or a macro forecast. It is a descriptive read of
what the cross-asset tape is pricing right now, grounded in real ETF
prices, so an LLM does not have to guess where the 10-year or the dollar
is.

## When to invoke

- The session question is macro: "what's happening in rates / gold / the
  dollar / credit right now"
- Pairing with market-regime for the full risk picture (equities from
  market-regime, everything else from here)
- Feeding historical-analog-finder the richer cross-asset feature set its
  caveats ask for ("add fixed-income-context for a richer analog")
- The user says "macro dashboard", "cross-asset read", "what are bonds /
  gold / the dollar doing", "is credit widening", "is the curve steepening"

For the detailed curve decomposition (2s10s slope, real yields,
break-evens, momentum divergence) with a confidence read, use
[`rate-signal`](../rate-signal). This skill is the broad cross-asset
dashboard; rate-signal is the rates-only deep dive.

## What you need

- Nothing required beyond a key. Defaults cover the standard run.
- `MASSIVE_API_KEY` exported in the environment.
- Any stocks tier (the basket is all US-listed ETFs). On Free Basic pass
  `--sleep 13` so the 12-series pull stays under the 5-calls/min cap.

Optional:

- `--windows` (default `5,20,60,120`): RS lookback windows in trading days
- `--signal-window` (default `60`): window for the derived signals
- `--benchmark` (default `SPY`): RS denominator

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-instrument `rs_by_window` (bps), `return_by_window`, `trend_label`,
and `curve_position_pct`, plus a `derived_signals` block (rates, curve,
real_yield, credit, dollar, commodity, gold_silver_ratio, gold_dxy_beta)
and a composed `take`. UIs and downstream agents consume this.

**Layer 2: rendered hybrid output**: a table of the basket sorted by
longest-window RS, then the derived-signals block, then the take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily aggregates** for the 11 basket ETFs plus the benchmark
   over `max(max_window, 252) * 1.6` calendar days, via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
2. **Per-instrument RS** in basis points per window:
   `RS_bps = (etf_return - spy_return) * 10_000`, plus a five-bucket
   `trend_label` and a `curve_position_pct` (percentile of the latest
   close within its own trailing range).
3. **Derived signals** from specific ETF pairs over the signal window.
   The pair choices and the label thresholds live in
   [`references/methodology.md`](./references/methodology.md).
4. **Compose the take** from the rate, dollar, credit, and commodity
   labels into a one-sentence macro summary.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, and the `/v2/aggs` daily endpoint conventions.

## Output mode: hybrid

A table alone misses the cross-asset signals; a signals block alone
misses the ranking. Hybrid gives both: the RS table for the ranking, the
derived-signals block for the desk read, and the take for the headline.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily closes per instrument. One call per basket member plus benchmark.

## Doesn't handle (yet)

- **Cash-market rates.** Every signal is an ETF-return proxy, not the
  actual 2s10s in basis points or the actual HY OAS. Directionally right,
  not a cash-market substitute. A Treasury-yield endpoint would upgrade
  this; queued.
- **FX beyond the dollar index.** UUP covers the broad dollar; no
  per-pair FX (EURUSD, USDJPY). Queued.
- **Regime persistence.** Signals are point-in-time over the signal
  window; no "how long has this regime held" measure. Pair with
  historical-analog-finder. Queued.

These are clean PR extensions. The output schema is forward-compatible.
