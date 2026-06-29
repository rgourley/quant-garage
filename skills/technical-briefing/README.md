# technical-briefing

The first question on a single name. "What does the chart say on NVDA
right now?" Run the tool and get the composite trend regime, RSI
momentum read, MACD cross status, key MAs (20 / 50 / 200), Bollinger
position, ATR as % of price, and the liquidity bucket. Sell-side
morning-note voice, all live data, cited to the API calls underneath.

Not a prediction. The script reads textbook indicators (Wilder RSI,
MACD 12/26/9, Bollinger 20/2σ, ATR 14) on the last 252 sessions and
labels the regime honestly. The Take is computed from the actual
readings, not a hardcoded narrative.

## Quick start

```bash
python3 examples/run-technical-briefing.py \
  --ticker NVDA \
  --lookback-days 252 \
  --format render
```

## Sample output

```
NVDA Technical Briefing — 2026-06-29
Price $134.21 · 252-day lookback · Liquidity: mega ($12.4B ADV), spread 1.2 bps

Trend regime: BULLISH (weak)
  Above 200-day SMA ($128.45), below 50-day ($138.10), below 20-day ($142.30)
  Read: price > SMA(200) 128.45 but < SMA(50) 138.10

Momentum (RSI 14): 41.2 → WEAK
  5-day RSI avg: 43.8

MACD (12/26/9): -0.83 line vs -0.65 signal → holding below
  Histogram -0.18

Bollinger (20, 2σ): $134.21 at 22% of band width
  Upper $148.20 · Mid $142.30 · Lower $136.40
  Read: near lower band

Volatility (ATR 14): $8.52 (6.4% of price)
  Elevated — sizing should reflect

Take: NVDA looks mixed. RSI 41 weak, price below the 20-day EMA but
holding above the 50-day SMA. ATR elevated at ~6.4% of price.
Pullback inside an uptrend, not a clean momentum breakout.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`indicators.md`](./references/indicators.md) — what RSI, MACD,
  Bollinger, and ATR mean methodologically, what NOT to read into them
- [`trend-regime.md`](./references/trend-regime.md) — the composite
  regime taxonomy, why we use SMA(20/50/200) as the trend anchors,
  why MACD is the cross signal
- [`rendering.md`](./references/rendering.md) — render-block voice and
  the adaptive-take map

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST. The skill only pulls
daily aggregates plus one snapshot, which are Tier B data; free Basic
runs it too, just slower. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
