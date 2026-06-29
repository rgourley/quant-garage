# Trend regime

The composite label that turns five indicator readings (SMA 20 / 50 /
200, MACD sign, RSI bucket) into one of five buckets the rest of the
output keys off.

## The five buckets

- **`bullish_strong`** — price > SMA(20) > SMA(50) > SMA(200), MACD
  positive, RSI ≥ 50. Stacked uptrend with momentum behind it.
- **`bullish_weak`** — price is above SMA(50) but below SMA(20)
  (pullback inside an uptrend), OR price is above SMA(200) but below
  SMA(50) (broader trend intact, medium-term broken), OR RSI < 50 in
  an otherwise constructive tape.
- **`bearish_strong`** — price < SMA(20) < SMA(50) < SMA(200), MACD
  negative, RSI ≤ 50. Stacked downtrend, momentum confirms.
- **`bearish_weak`** — price below SMA(50) but above SMA(200), OR
  price below SMA(200) with RSI > 50 (countertrend bid in a broken
  tape).
- **`neutral`** — none of the above; mixed signals. The skill returns
  a "mixed signals" reason and the Take defers to "no edge from
  technicals; wait for confirmation."

The `trend.reasons` array surfaces which signals fired so the read is
auditable. A caller seeing `bullish_weak` knows whether it's a
pullback inside an uptrend (one reason) or a broken-50-day-but-200-
day-intact tape (different reason).

## Why SMA(20), SMA(50), SMA(200) as the anchors

These three are the consensus trend anchors in technical analysis:

- **SMA(20)** ≈ one trading month. Short-term price-vs-recent.
- **SMA(50)** ≈ one quarter. Medium-term, the line every desk watches
  on every chart.
- **SMA(200)** ≈ one trading year. The long-term trend line; the
  "golden cross" (SMA 50 over SMA 200) and "death cross" (SMA 50
  under SMA 200) are the textbook regime-shift signals.

Their stacked structure (20 > 50 > 200 in an uptrend, 200 > 50 > 20
in a downtrend) is the cleanest visual representation of trend
agreement across timeframes. When all three agree AND price sits on
the correct side of the 20, you have unambiguous trend; anything else
is some degree of weakening or recovery.

We do NOT use the EMAs as trend anchors even though they're computed
for display. EMAs respond faster, which makes them better for entry
timing and worse for regime classification — the whole point of a
regime label is that it doesn't flip on every wiggle.

## Why MACD is the cross signal, not a regime signal

MACD is a momentum oscillator built on EMAs. It moves faster than
the SMA stack. A negative MACD inside a `bullish_strong` SMA stack
means short-term momentum has cooled but the trend is intact — that
combination is rare under the strict bullish_strong rule (we require
MACD > 0 there) so it lands in `bullish_weak` instead.

MACD's `cross_status` is exposed separately so callers can read the
trigger (`bullish_cross`, `bearish_cross`, `holding_above`,
`holding_below`) without it dominating the regime label.

## Why RSI is a confirmation, not a contrarian signal

The natural urge with RSI is to read > 70 as "sell" and < 30 as
"buy." In a strong trend, both are wrong. RSI > 70 in
`bullish_strong` regime is *confirmation* of the trend; the
overbought label is descriptive, not prescriptive.

The Take handles this by keying off `(regime, momentum)` jointly:

- `(bullish_strong, overbought)` → "Trend intact but momentum
  stretched; mean-revert risk on shorter timeframes."
- `(bearish_strong, oversold)` → "Trend is down; oversold readings
  here are noise, not reversal."

The closing sentence carries the conditional read so the user sees
the right interpretation for the regime, not a generic "RSI 80 means
sell."

## Edge cases

- **Insufficient bars for SMA(200).** When the lookback yields fewer
  than 200 bars (e.g., a recent IPO), SMA(200) is null. The regime
  classifier falls back to `neutral` with the reason "insufficient
  history for full trend read." A `tier_caveats` entry surfaces the
  shortfall.
- **Stacked MAs but MACD negative.** Lands in `bullish_weak`; the
  reasons array calls it out. This is the classic "pullback inside an
  uptrend" tape — the MAs agree but the short-term momentum has cooled.
- **Mixed signals.** When the rules above don't all fire one way, the
  regime is `neutral` and the reasons array reports "mixed signals;
  no clean stacked-MA + momentum agreement." The Take then defers to
  "wait for confirmation."

## What the regime is NOT

- Not a prediction. A `bullish_strong` regime tells the caller the
  trend was up at the end of the lookback; it does not say the next
  bar will be up.
- Not a recommendation. The skill never says "buy" or "sell" — it
  labels the tape and explains the read.
- Not a multi-timeframe view. This is a daily-timeframe label. The
  same name can be `bullish_strong` on daily and `bearish_weak` on
  weekly; the skill flags that the daily view is a single-timeframe
  read.
