---
name: crypto-vol-scanner
description: Surface 24h crypto volatility and microstructure anomalies across a universe (default top 10) as a Bloomberg crypto desk / Cheddar-Flow-for-crypto-style stream. Per-name: realized vol spike (vs 30d distribution), volume anomaly (vs 30d avg), cross-exchange basis (max bid-ask divergence across Coinbase / Binance / Kraken / Bitstamp / Bitfinex), and 24h move z-score. Ranked by composite impact. Real-time on Currencies Business; spot-snapshot accurate within seconds across all paid tiers.
---

# crypto-vol-scanner

You hand over a crypto universe and a window. The skill pulls each name's
current snapshot, last 200 ticks across exchanges, and 30 days of hourly
+ daily aggregates, computes realized vol against the trailing 30-day
distribution, flags volume anomalies, surfaces cross-exchange basis when
prints disagree, z-scores the 24h move against the trailing daily
return distribution, ranks every name by a composite impact score, and
emits a stream of the most notable events.

This is the workflow a crypto desk runs at the open. Ten names, four
signal types, ranked by what actually moved or is acting weird right
now. Unlike a CoinGecko screener or TradingView heatmap, crypto-vol-
scanner ranks by statistical context (percentiles, z-scores, multipliers
against trailing distributions) rather than absolute move size, and
ships the methodology alongside the output.

First crypto skill in the suite. Validates a third asset class beyond
stocks and options.

## When to invoke

- A crypto trader is starting their session and wants the universe's
  vol + volume + basis state
- A PM is asking "what's the action in crypto today"
- The user says "scan crypto vol", "any crypto anomalies", "what's
  weird in crypto right now"
- A discretionary trader is hunting for setups: vol spikes that precede
  trend resumption, persistent cross-exchange basis that signals
  exchange-specific flow

## What you need

- A crypto universe (default: BTC, ETH, SOL, XRP, ADA, DOGE, AVAX,
  LINK, DOT, POL). POL is the new ticker for the former MATIC; the
  skill auto-substitutes
- `MASSIVE_API_KEY` exported in the environment
- Crypto Starter or higher (Currencies Business covers it). All paid
  tiers return real-time spot and tick-level trades

The skill runs at two fidelity tiers.

- **Tier A (Currencies Business or Crypto Developer):** Real-time
  spot, tick-level trades for cross-exchange basis, full 30d hourly
  and daily aggregate history. Sub-second updates on the snapshot.
  This is the default tier.
- **Tier B (Crypto Starter):** Same data shapes, but trade history
  may be 15-min delayed depending on plan specifics. The methodology
  is identical; only the snapshot freshness differs. Volume and vol
  numbers are computed from historical aggregates, which are stable.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-event fields: ticker, base_currency, quote_currency, signal_type
(vol_spike | volume_anomaly | cross_exchange | tail_move | quiet |
combined), realized_vol_pct, vol_percentile_ttm, vol_vs_avg_ratio,
volume_24h_usd, volume_vs_avg_ratio, move_24h_pct, move_zscore,
basis_bps (when cross-exchange), exchanges_compared, context_line.
UIs, alert pipelines, and downstream agents consume this.

**Layer 2: rendered stream** in Bloomberg crypto desk / Cheddar-Flow-
for-crypto style. Three lines per event, optional `↳` continuation
line. Format rules in [`references/rendering.md`](./references/rendering.md).
Compact, scanable, key:value pairs. Claude Code users read this.

## How it works

1. For each ticker in the universe, pull the bulk snapshot via
   `/v2/snapshot/locale/global/markets/crypto/tickers?tickers=X:BTCUSD,X:ETHUSD,...`.
   The `prevDay` block is the "last 24h" reference (last completed UTC
   day); `lastTrade.p` is current spot. POL is auto-substituted for the
   deprecated MATIC ticker.
2. For each ticker, pull `/v2/aggs/ticker/{X:BASEUSD}/range/1/day/{from}/{to}`
   over the trailing TTM. Used for the 30-day daily-return distribution
   and the trailing 30d daily-volume baseline. See
   [`references/move-zscore.md`](./references/move-zscore.md).
3. For each ticker, pull `/v2/aggs/ticker/{X:BASEUSD}/range/1/hour/{from}/{to}`
   over the trailing 32 days. Used to compute current 24h realized vol
   (close-to-close log returns, annualized × sqrt(365 × 24)) and to
   build the trailing 30-day rolling-24h realized-vol distribution.
   Methodology in [`references/realized-vol-methodology.md`](./references/realized-vol-methodology.md).
4. For each ticker, compute volume anomaly per
   [`references/volume-anomalies.md`](./references/volume-anomalies.md):
   `prevDay.v * prevDay.vw` (USD) vs trailing 30d daily-volume average
   from the daily aggs. Threshold for flagging: > 2x.
5. For each ticker, pull `/v3/trades/{X:BASEUSD}?limit=200&order=desc`
   and group by exchange (Coinbase=1, Bitfinex=2, Bitstamp=6,
   Binance=10, Kraken=23). Compute the max minus min of the per-exchange
   most-recent price as the cross-exchange basis in bps. Methodology
   and caveats in [`references/cross-exchange-basis.md`](./references/cross-exchange-basis.md).
6. Compose per-ticker signals. Tag the dominant signal type
   (`vol_spike`, `volume_anomaly`, `cross_exchange`, `tail_move`,
   `quiet`, or `combined` when multiple fire). Rank by composite
   impact = max(vol_zscore, volume_zscore, abs(move_zscore),
   basis_bps_zscore) per [`references/signal-ranking.md`](./references/signal-ranking.md).
   Emit the top N (default 15).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, pagination, and the best-price fallback chain
  for spot

## Output mode: stream

Stream mode is the format Bloomberg's crypto desk pages, Cheddar Flow,
and Coinbase Institutional desk reports converge on for "here's what's
weird right now": per-event blocks, ticker-tagged, signal-typed,
compact key:value pairs, scanable top to bottom. Inherited from
[`options-flow/references/rendering.md`](../options-flow/references/rendering.md),
adapted for crypto signals per [`references/rendering.md`](./references/rendering.md).

## Endpoints used

- `GET /v2/snapshot/locale/global/markets/crypto/tickers?tickers=...`:
  Bulk snapshot. Returns `lastTrade.p`, `day.{o,h,l,c,v,vw}`,
  `prevDay.{o,h,l,c,v,vw}`, `min.*`, `fmv`, and `todaysChangePerc`
  per ticker. The `prevDay` block is the canonical "last 24h" reference
  because `day.*` is the current incomplete UTC day.
- `GET /v2/aggs/ticker/{X:BASEUSD}/range/1/day/{from}/{to}`: daily OHLCV
  aggregates. Used for the trailing 30d daily-volume baseline and the
  daily-return distribution for the 24h move z-score.
- `GET /v2/aggs/ticker/{X:BASEUSD}/range/1/hour/{from}/{to}`: hourly
  OHLCV aggregates. Used for current 24h realized vol and the
  rolling-24h realized-vol distribution.
- `GET /v3/trades/{X:BASEUSD}?limit=200&order=desc`: most-recent
  tick-level trades, including the `exchange` field. Used to compute
  per-exchange basis. Five paid crypto exchanges: Coinbase (1),
  Bitfinex (2), Bitstamp (6), Binance (10), Kraken (23).

## Doesn't handle (yet)

- Perpetual funding rates. Funding rate divergence and basis trade
  setups are the bread and butter of crypto desks, but Massive's
  REST surface doesn't expose perp markets in this product. Spot only.
- WebSocket streaming. v1 is REST-polled. The `massive-websockets`
  foundation covers the live-stream pattern for a future variant of
  this skill.
- Cross-quote-currency basis. The skill compares USD pairs only; the
  BTC-USDT vs BTC-USD basis (which is informative about USDT depeg
  risk and exchange-specific flow) is a clean v2.
- Derivatives implied vol (e.g. Deribit DVOL). Massive doesn't carry
  Deribit options surface; realized vol is the only vol metric the
  skill produces.
- Sentiment / social flow. DOGE-style social-driven anomalies surface
  in this skill as volume + vol spikes, but the skill doesn't attribute
  to a source. Pair with `news-scanner` on the crypto-news tickers (BTC,
  ETH) for a complete picture.

These are clean PR extensions and welcome contributions.
