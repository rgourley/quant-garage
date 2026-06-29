# Indicators

What each indicator measures, its standard interpretation, and the
edge cases honest analysts call out. All math lives in
`lib.quant_garage.technicals` so a methodology change in one place
propagates to every skill that reads it.

## RSI (14)

Wilder's Relative Strength Index. Ratio of average gain to average
loss over the lookback window, mapped to a 0-100 scale.

```
RS  = avg_gain(14) / avg_loss(14)
RSI = 100 - 100 / (1 + RS)
```

Buckets the skill uses:

- `< 30` → oversold
- `30-45` → weak
- `45-55` → neutral
- `55-70` → firm
- `> 70` → overbought

What it actually measures: the *speed* of recent price changes
relative to themselves, not their *direction*. RSI = 80 in a strong
uptrend is normal; RSI = 80 in a flat tape is genuinely overbought.

What NOT to read into it:

- RSI > 70 is not a sell signal. In trending tape, RSI can hold > 70
  for weeks. The "overbought" label is a momentum description, not a
  reversal call. The composite trend regime is the right place to
  decide whether the trend backs up the read.
- RSI < 30 in a strong downtrend is the *expected* reading, not a
  contrarian buy. Same logic.
- The standard 14-period window is convention, not law. Shorter (e.g.,
  2) makes the indicator hyper-reactive; longer (e.g., 30) smooths
  the read at the cost of lag. This skill uses 14.

## MACD (12, 26, 9)

Moving Average Convergence/Divergence. Standard textbook:

```
MACD line  = EMA(12) - EMA(26)
Signal     = EMA(9) of the MACD line
Histogram  = MACD line - Signal
```

Three readings:

- **Sign of the MACD line.** Positive = the fast EMA is above the
  slow EMA → short-term price > medium-term price → momentum is up.
- **Cross status.** `bullish_cross` / `bearish_cross` when the MACD
  line crossed the signal in the last bar; `holding_above` /
  `holding_below` otherwise. The cross is the standard entry trigger
  in textbook usage, but it's lagging — you're buying *after* the
  short-term has already overtaken the longer.
- **Histogram trajectory.** The histogram contracts before a cross
  (momentum decelerating) and expands after one (momentum building).
  The skill emits the histogram value; trajectory analysis is up to
  the caller.

What NOT to read into it:

- A positive MACD line in a downtrend is a *bounce*, not a reversal.
  Always cross-check against the longer MA structure.
- The 12/26/9 parameterization is convention. Faster (5/13/4) is more
  reactive, slower (19/39/9) smoother. This skill uses textbook.

## Bollinger Bands (20, 2σ)

A 20-period SMA with bands at ±2 rolling-standard-deviations.

```
mid     = SMA(20)
upper   = mid + 2 * stdev(20)
lower   = mid - 2 * stdev(20)
```

The bands quantify *normal* range. Under a normal distribution
assumption, ~95% of observations fall within ±2σ. Markets are not
normally distributed; in trending tape, price walks the upper or
lower band for extended stretches.

Position labels the skill emits:

- `above_upper` (price > upper)
- `near_upper` (top 20% of band width)
- `mid_range` (middle 60%)
- `near_lower` (bottom 20%)
- `below_lower` (price < lower)

Band-width percentile is exposed as `pct_of_band_width` for callers
that want the raw number.

What NOT to read into it:

- Touching the upper band is not a sell signal. In trending tape it's
  a continuation signal. Pair with trend regime.
- "Bollinger squeeze" (narrowing bands → vol contraction) is a real
  setup, but this skill doesn't surface squeeze regime. ATR percent
  is a coarser version of the same idea.

## ATR (14)

Wilder's Average True Range. The 14-period exponentially smoothed
mean of true range, where true range = max(H-L, |H - prev_C|,
|L - prev_C|). Always non-negative.

Reported two ways:

- `atr_14` in dollars
- `atr_pct_of_price` = `atr_14 / current_price`

The percentage form is the comparable number across tickers. A $5
ATR on a $50 stock (10%) is dramatically more volatile than a $5 ATR
on a $500 stock (1%).

Buckets the rendered output uses:

- `> 5%` → elevated (sizing should reflect)
- `2%-5%` → normal
- `< 2%` → quiet

What NOT to read into it:

- ATR is backward-looking. It does not anticipate earnings prints,
  FDA decisions, M&A, or macro releases. The `tier_caveats` array
  flags this on every run.
- ATR is direction-agnostic. A name with 6% ATR could be ranging
  violently or trending hard.

## SMAs and EMAs (20, 50, 200)

Simple and exponential moving averages over standard windows. The
skill emits all five (`sma_20`, `sma_50`, `sma_200`, `ema_20`,
`ema_50`) plus the price-vs-MA percent differences. The trend-regime
classifier reads the stacked structure of `sma_20 / sma_50 / sma_200`;
see [`trend-regime.md`](./trend-regime.md) for why.

EMAs are reactive (weights recent observations more heavily); SMAs
are smooth. EMA(20) crossing SMA(50) is a common short-term swing
signal but the skill doesn't surface that cross explicitly — it's
inferable from the published values.

## Why these indicators, not others

The four (RSI, MACD, Bollinger, ATR) plus the MAs are the standard
sell-side technical block. Every technical analyst learns these
first; every charting platform exposes them by default; every
methodology textbook covers them. The skill is opinionated about
*not* layering in less-standard indicators (Ichimoku, Williams %R,
Stochastic) — they tell roughly the same story with more parameter
choices. If a caller wants additional indicators, the right fork is
to add them to `lib.quant_garage.technicals` and surface them here.

## Honest limits

- All indicators are derived from the same closes. They are not
  independent signals. RSI, MACD, and the MAs all agree most of the
  time because they're all reading the same series.
- Trending vs ranging is a separate axis from the indicators
  themselves. The trend-regime label is the skill's attempt at that
  axis, but it's still indicator-driven.
- Single-timeframe (daily). The chart looks different on a weekly
  view. The skill flags this implicitly via the "single-name snapshot"
  caveat; explicit multi-timeframe support is queued.
