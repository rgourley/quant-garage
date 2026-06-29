# Rendering

The technical-briefing output mode is `note`: a sell-side morning-
note block with one section per indicator family and a Take at the
bottom. Single ticker, single canvas; no table is warranted.

## Layout

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

## Per-section rules

### Header (two lines)

- Line 1: `{TICKER} Technical Briefing — {as_of date}`
- Line 2: `Price ${price} · {lookback}-day lookback · Liquidity:
  {bucket} ({adv}) [, spread {bps} bps]`

The spread suffix appears only when the snapshot provided bid/ask.

### Trend regime section

- Header: `Trend regime: {LABEL}` where LABEL is the bucket in
  sentence-case, e.g., `BULLISH (weak)`.
- Detail line: above/below for SMA(200), SMA(50), SMA(20) in that
  order so the eye reads long-term → short-term.
- Read line: the first reason from the `trend.reasons` array (the
  load-bearing one). Additional reasons live in the JSON.

### Momentum section

- Header: `Momentum (RSI 14): {value} → {BUCKET}`.
- Sub-line: 5-day RSI average.

### MACD section

- Header: `MACD (12/26/9): {line:+.2f} line vs {signal:+.2f} signal
  → {cross_status text}`. `holding_above` renders as "holding above",
  `bullish_cross` as "bullish cross", etc.
- Sub-line: `Histogram {value:+.2f}`.

### Bollinger section

- Header: `Bollinger (20, 2σ): ${price} at {pct}% of band width`
  where `pct` is the price's percentile within the band, clamped to
  [0%, 100%] for display.
- Detail line: `Upper · Mid · Lower` values.
- Read line: position label in plain English ("near lower band",
  "above upper band", etc.).

### Volatility section

- Header: `Volatility (ATR 14): ${atr} ({pct}% of price)`.
- Sub-line: one of `Elevated — sizing should reflect` (> 5%),
  `Normal` (2-5%), `Quiet` (< 2%).

### Take

One paragraph, three or four sentences, computed adaptively:

1. **Lead**: regime label in plain English (`"{ticker} looks
   constructive."` / `"looks mixed."` / `"looks weak."` / `"looks
   soft."` / `"looks rangebound."`).
2. **Momentum + MA structure sentence**: `"RSI {value} {bucket
   word}, price {ma phrase}."` The MA phrase is conditional:
   - price < SMA(20) but > SMA(50) → "below the 20-day EMA but
     holding above the 50-day SMA"
   - price < SMA(50) → "below the 50-day SMA"
   - price > SMA(20) → "above the 20-day EMA"
   - otherwise → "hugging the 20-day EMA"
3. **ATR sentence**: `"ATR elevated at ~X% of price."` (> 5%) /
   `"ATR normal at ~X% of price."` (2-5%) / `"ATR quiet at ~X% of
   price."` (< 2%).
4. **Closing sentence**: pulled from the `(regime, momentum)` map
   below. Falls back to `"Mixed setup; no edge from technicals
   alone."` for any pair not in the table.

### Adaptive-take map

The closing sentence is keyed on `(regime, momentum)`:

| Regime | Momentum | Closing sentence |
|---|---|---|
| bullish_strong | firm | Trend and momentum aligned; respect the setup. |
| bullish_strong | overbought | Trend intact but momentum stretched; mean-revert risk on shorter timeframes. |
| bullish_strong | neutral | Trend strong; momentum cooling but not broken. |
| bullish_weak | weak | Pullback inside an uptrend, not a clean momentum breakout. |
| bullish_weak | neutral | Constructive tape losing thrust; wait for momentum to confirm. |
| bullish_weak | oversold | Pullback in an uptrend hitting oversold; classic dip-buy setup if the higher-timeframe trend holds. |
| bullish_weak | firm | Above the 50-day with firm momentum; trend repairing. |
| bearish_strong | oversold | Trend is down; oversold readings here are noise, not reversal. |
| bearish_strong | weak | Downtrend intact; momentum has room to deteriorate further. |
| bearish_strong | neutral | Downtrend intact; counter-trend bounce attempt without confirmation. |
| bearish_weak | firm | Countertrend bid in a tape still below the 50-day; treat as a bounce, not a reversal. |
| bearish_weak | neutral | Below the 50-day; momentum lifting but trend not repaired. |
| bearish_weak | overbought | Stretched countertrend bounce; reversion risk into resistance. |
| neutral | neutral | No edge from technicals; wait for confirmation. |
| neutral | firm | Momentum building inside a range; watch the upper edge. |
| neutral | weak | Momentum fading inside a range; watch the lower edge. |
| neutral | overbought | Range top with stretched momentum; reversion-friendly. |
| neutral | oversold | Range bottom with stretched momentum; reversion-friendly. |

The map is intentionally small. Eighteen entries cover the joint-
distribution mass; the fallback handles the corner cases. The
sentences are calibrated to the conditional interpretation that
matches the regime, not a generic "RSI 80 means sell."

### Caveats footer

Bullet list pulled from the JSON's `tier_caveats` array. Always-on
caveats:

- "Single-name snapshot; pair with universe-builder or factor-
  research for market context"
- "ATR-based vol does not anticipate event-driven jumps (earnings,
  FDA, macro print)"

Conditional caveats:

- "SMA(200) unavailable: only {n} bars; long-term trend label is
  approximate" — when fewer than 200 bars
- "Live spread unavailable from snapshot; liquidity read uses ADV
  only" — when the snapshot doesn't provide bid/ask

## What the renderer does NOT do

- No color codes / ANSI escapes. The output should look correct in
  plain text, in markdown code fences, and in a developer's terminal.
- No charts. Numbers carry the read; charts would compete with the
  text without adding signal at the rendering layer.
- No verdict, no recommendation. The Take describes the tape; it
  doesn't say "buy" or "sell."

## Output file

The script writes `examples/technical-briefing-output.md` with a
two-layer structure inherited from the rest of the repo:

- Layer 1: canonical JSON payload in a fenced code block
- Layer 2: rendered note in a fenced code block

Both layers are gitignored (test artifact, not methodology). The
methodology lives in `references/`.
