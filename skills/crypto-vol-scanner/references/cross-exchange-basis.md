# Cross-exchange basis

The skill surfaces the max-minus-min of the most-recent per-exchange
price as a basis-points spread. This is the closest the spot REST
surface gets to surfacing an arb-style anomaly.

## What Massive exposes

`GET /v3/trades/{X:BASEUSD}?limit=200&order=desc` returns the most-
recent 200 tick-level trades for the ticker. Each trade carries an
`exchange` field (integer). Massive carries five crypto exchanges
under the Currencies product:

| Exchange ID | Name      |
|-------------|-----------|
| 1           | Coinbase  |
| 2           | Bitfinex  |
| 6           | Bitstamp  |
| 10          | Binance   |
| 23          | Kraken    |

The bulk snapshot endpoint (`/v2/snapshot/...`) does NOT return
per-exchange data; its `lastTrade` field is one trade from one
exchange (whichever printed most recently). So the cross-exchange
basis signal requires the second `/v3/trades/...` call per ticker.

## The signal

For each exchange present in the most-recent 200 trades, take that
exchange's most-recent print. Compute:

```
mid    = (max(prices) + min(prices)) / 2
basis_bps = (max(prices) - min(prices)) / mid * 10000
```

Threshold for flagging: `basis_bps > 20`. This is a defensible cut
because:

- The intra-exchange bid-ask spread on a tight pair like BTC-USD is
  typically 1-3bps on Coinbase, 1-2bps on Binance
- "Normal" cross-exchange divergence is 5-15bps, mostly driven by
  fee-tier differences and the millisecond-scale lag between exchanges
- Persistent basis above 20bps means there's a real bid-ask or
  inventory imbalance that an arber should be closing

## Caveats

**Liquidity tiers.** Per-exchange price comparison is only meaningful
when each exchange has decent recent volume on the pair. The skill
records `trade_count` per exchange in the JSON and trusts the
most-recent price even if only a handful of trades fired; that's a
known degradation for the long tail (DOT, AVAX, LINK frequently have
< 5 Bitstamp / Bitfinex trades in the last 200-trade window).

**Wrong-side liquidity.** A 25bps basis between Coinbase and Binance
on BTC sounds like an arb, but it usually isn't free money:
- The capital-efficiency cost of running the inventory on both sides
- The withdrawal time when one side runs dry (BTC withdrawals between
  exchanges are 15-30 minutes; ETH is 2-5)
- Stablecoin venue risk (USDT vs USD pairs aren't the same instrument)

The skill surfaces the basis as an anomaly signal, not an arb
recommendation. Quant desks running cross-exchange MM already know
the basis; surfacing it for a discretionary trader is about
information ("Coinbase is heavy bid right now" or "Binance flow has
diverged from US venues").

**Stale ticks.** If a small exchange's last print is more than a few
minutes old, the basis comparison drifts. The skill currently uses
the most-recent print regardless of staleness; a v2 refinement would
require all per-exchange prints to be within e.g. 30 seconds of each
other.

## What to NOT do

- Don't quote basis when only one exchange is in the recent trade
  set. Report `basis_bps: null` and skip the cross-exchange signal.
- Don't compare across quote currencies (e.g. Binance BTC-USDT vs
  Coinbase BTC-USD). The skill only walks USD pairs; the USDT basis
  is informative but is a different signal (USDT depeg, exchange-
  specific stablecoin liquidity), tracked separately in a v2.

## What the rendered output looks like

```
BTC-USD  CROSS-EXCHANGE  Coinbase $63,420 · Binance $63,580 (25bps)
$63,500 · 24h move +0.8% (0.7σ) · vol $24B (1.1x avg)
↳ basis widened from 8bps prior 24h; small but persistent
```

The first line tags the signal and names the two exchanges with the
largest spread. The third line (↳) is the trader context: how did the
basis change vs the prior window, and is it persistent.
