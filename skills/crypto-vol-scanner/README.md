# crypto-vol-scanner

You watch BTC/ETH/SOL plus a handful of alts. The tool surfaces vol
spikes (vs trailing 30d distribution), volume anomalies, cross-
exchange basis (Coinbase vs Bitfinex vs Bitstamp vs Binance vs
Kraken), and 24h move z-scores. Output is a stream of the top events,
with a one-line read at the bottom on the broader regime (this
week's read: "quiet regime, BTC realized vol at 30% sitting in the
25th percentile of trailing year, setup-watch day not entry day").

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.crypto_vol_scanner import run, render
payload = run(watchlist="BTC-USD,ETH-USD,SOL-USD,AVAX-USD")
print(render(payload))
```

### CLI

```bash
python3 examples/run-crypto-vol-scanner.py
```

### Claude Code / LLM tool use

Discovered at `skills/crypto-vol-scanner/`. In a Claude Code
session, ask "any vol spikes across BTC/ETH/SOL right now" or
"scan my alt watchlist for cross-exchange basis". Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
10 events surfaced from 10 names · window: last 24h

AVAX-USD  CROSS-EXCHANGE  Bitfinex $6.45 · Bitstamp $6.43 (31bps)
$6.43 · 24h move -0.7% · 24h vol $9M (1.2x avg) · realized vol 58%

BTC-USD  QUIET  realized 24h: 30% (25th %ile TTM)
$62,806 · 24h move +0.2% · 24h vol $778M (0.9x avg)

Take: AVAX leads with cross-exchange basis on 28th-%ile RV.
Universe median RV 53% (normal regime).
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`realized-vol-methodology.md`](./references/realized-vol-methodology.md) — 24h window vs trailing 30d distribution
- [`move-zscore.md`](./references/move-zscore.md) — 24h returns normalized against trailing 30d
- [`volume-anomalies.md`](./references/volume-anomalies.md) — 2x trailing-30d USD volume threshold
- [`cross-exchange-basis.md`](./references/cross-exchange-basis.md) — max-minus-min per-exchange price across five venues
- [`signal-ranking.md`](./references/signal-ranking.md) — composite scoring across the four signal types
- [`rendering.md`](./references/rendering.md) — stream-mode inherited from `options-flow`, adapted for crypto

## Plan requirement

Crypto Starter ($29/mo) runs daily and hourly aggs end-to-end.
Crypto Developer adds tick-level trades and sub-second cross-exchange
basis. Perp funding and derivatives IV are not exposed by Massive
REST. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
