---
name: technical-briefing
description: Single-name technical briefing — composite trend regime, RSI momentum, MACD cross, key MAs (20/50/200), Bollinger position, ATR as % of price, ADV-bucketed liquidity context — in a sell-side-quality block. Use when the user asks "what does the chart say on NVDA right now?" or "give me a technical read on TSLA." Doesn't predict direction; reads textbook indicators and labels the regime. Requires Stocks Starter.
---

# technical-briefing

One ticker in, one analyst-grade technical read out. The most-asked
first question on a single name — "what does the chart look like
right now?" — answered with the standard indicator suite (Wilder
RSI 14, MACD 12/26/9, Bollinger 20/2σ, ATR 14, SMAs 20/50/200) and
a composite trend-regime label that combines them honestly.

This is NOT a prediction. The script reads textbook indicators on the
last 252 trading days, labels the trend regime (`bullish_strong`,
`bullish_weak`, `bearish_strong`, `bearish_weak`, `neutral`) with
explicit reasons, and writes a Take from the actual readings. No
hidden alpha, no proprietary model.

## When to invoke

- Active trader asking "what's the technical setup on NVDA?"
- Junior analyst building the technicals slide of a single-name brief
- PM sanity-checking a chart-driven entry against the indicator stack
- Anyone who'd otherwise eyeball a TradingView chart for 30 seconds

## What you need

- One US ticker
- `MASSIVE_API_KEY` exported

Optional:

- `--lookback-days` (default 252; minimum 60)

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Trend regime + reasons, RSI latest + 5-day average + bucket read,
MACD line/signal/histogram + cross status, every MA value + price-vs-
MA percent, Bollinger upper/mid/lower + position label + band-width
percentile, ATR + ATR/price percent, 30-day ADV + bucket + current
spread bps when available.

**Layer 2: rendered briefing block.** Sell-side morning-note voice.
Header (ticker + as-of + price + liquidity), trend regime section,
momentum section, MACD section, Bollinger section, volatility section,
the Take, and the caveats. The Take is computed adaptively from the
actual readings — never a hardcoded narrative.

## How it works

1. **Pull daily aggs** for the lookback window via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
   Overshoots calendar days by 1.6x to cover weekends/holidays, then
   trims to the last `lookback_days` sessions.
2. **Pull a snapshot** via
   `/v2/snapshot/locale/us/markets/stocks/tickers/{T}` for current
   bid/ask/spread. Failure here is non-fatal — spread is optional.
3. **Compute indicators** with the shared `lib.quant_garage.technicals`
   helpers. No reimplementation: `sma`, `ema`, `rsi`, `macd`,
   `bollinger`, `atr` all come from one place so a methodology change
   in one helper updates every skill that reads them.
4. **Classify the trend regime** as a composite read of stacked-MA
   structure, MACD sign, and RSI bucket. See
   [`references/trend-regime.md`](./references/trend-regime.md).
5. **Classify momentum** from the RSI bucket (oversold / weak /
   neutral / firm / overbought).
6. **Classify Bollinger position** by the price's percentile within
   the band (above_upper, near_upper, mid_range, near_lower,
   below_lower).
7. **Bucket liquidity** by 30-day average dollar volume (thin <$1M,
   medium <$50M, liquid <$500M, mega ≥$500M).
8. **Build the Take adaptively.** A small `(regime, momentum)` map
   provides the closing sentence; the rest reads the actual numbers.
   See [`references/rendering.md`](./references/rendering.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth and
  the snapshot-resolution conventions.

## Output mode: note

A sell-side morning-note block is the right canvas for "tell me what
the chart says on this name." Section headers, indicator values, a
two-sentence read per indicator, the Take. No table — one ticker
doesn't warrant rows.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily aggregates over the lookback window. One call per run.
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`
  Current bid/ask for the spread read. One call per run. Optional.

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- **Intraday timeframes.** Daily bars only. A 5m / 15m / 1h variant
  would slot in cleanly: same indicator code, swap the aggregate
  resolution.
- **Multi-timeframe alignment.** The trend regime is a single-
  timeframe read. A higher-timeframe overlay (weekly trend + daily
  momentum) is the natural v2.
- **Event-aware vol.** ATR is backward-looking. It does not anticipate
  earnings prints, FDA decisions, or macro releases. Flagged in
  `tier_caveats`; pair with `earnings-drilldown` for catalyst-aware
  vol.
- **Pattern detection.** No flags / triangles / heads-and-shoulders.
  Standard indicator-only read. Pattern recognition is a separate
  problem; the methodology references explain why we don't pretend
  otherwise.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
