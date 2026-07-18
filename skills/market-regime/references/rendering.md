# Rendering: market-regime

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for human consumption as a
PM-facing morning briefing.

The format is one header, four stanzas (one per evidence block), one
"Take" line, and an optional caveats footer. Each block renders in
the order the eye should read them: SPY first (the headline tape),
then VIX (risk gauge), then breadth (participation), then leadership
(rotation). The take closes with what to watch for a regime change.

## Header

Two lines, blank line after:

```
Market Regime — {as_of}
{LABEL_UPPERCASE}
```

`{as_of}` is `YYYY-MM-DD`. `{LABEL_UPPERCASE}` is the
`composite_regime.label` in ALL CAPS so it scans at the top of the
briefing.

## SPY stanza

Two lines, blank line after:

```
{benchmark}: ${price} ({r1d} today, {r5d} 5d, {r20d} 20d) — {trend}
  {ma_summary}
```

Where:
- `${price}` is `current_price` to 2 decimals
- `{r1d}` / `{r5d}` / `{r20d}` are signed percent changes (`+0.23%`,
  `-1.5%`); 1-day uses 2 decimals, 5-day and 20-day use 1 decimal
- `{trend}` is the trend bucket (`uptrend_strong`, etc.)
- `{ma_summary}`:
  - `Above 20/50/200-day MAs` if above all three
  - `Above {X/Y}-day MA(s); below the rest` if above some
  - `Below 20/50/200-day MAs` if below all three

When SPY data is insufficient, render `{benchmark}: insufficient
history for trend computation` and skip the second line.

## Volatility stanza

Two lines, blank line after:

```
{metric_label}: {current} ({rank}th %ile of trailing year) — {state}
  20-day avg {avg}. {stress_note}
```

Where:
- `{metric_label}` is `vix_state.metric_label`: `VIX` when the provider
  carries VIX (`kind`=implied), or `Volatility (realized 20d,
  annualized)` when the realized-vol proxy is used (`kind`=realized).
  Never label the proxy as `VIX`.
- `{current}` is `vix_state.current` to 1 decimal
- `{rank}` is `percentile_rank` as an integer
- `{state}` is the state bucket (`quiet`, `normal`, `elevated`, `stressed`)
- `{avg}` is `avg_20d` to 1 decimal
- `{stress_note}` is `No stress signal.` for quiet/normal, or
  `Stress signal active.` for elevated/stressed

When VIX is unavailable, the tool falls back to a realized-vol proxy
(annualized realized volatility of the benchmark's own closes,
percentile-ranked vs the trailing year). This is realized
(backward-looking), not VIX's implied (forward-looking) reading, so the
`{state}` bucket is set by percentile rank, not VIX's absolute levels,
and a tier_caveat says so. Only when VIX is unavailable AND benchmark
history is too short for the proxy do we render `Volatility: data
unavailable; regime read computed without volatility component` and skip
the second line.

## Breadth stanza

Two lines, blank line after:

```
Breadth (sector ETF proxy): {n50} of {ntot} above 50-day MA ({pct50}%)
  {n200} of {ntot} above 200-day ({pct200}%). {Read}.
```

Where:
- `{n50}` / `{n200}` are integer counts
- `{ntot}` is `n_sector_etfs` (typically 11)
- `{pct50}` / `{pct200}` are integer percents
- `{Read}` is the read label, capitalized (`Broad participation`,
  `Mixed participation`, etc.)

When breadth data is insufficient, render `Breadth: insufficient data
(need >=200-day history per sector ETF)` and skip the second line.

## Leadership stanza

Three lines, blank line after:

```
Sector leadership (20-day RS vs SPY):
  Leaders:  {T1} {bps1}  ·  {T2} {bps2}  ·  {T3} {bps3}
  Laggards: {T1} {bps1}  ·  {T2} {bps2}  ·  {T3} {bps3}
```

Where:
- `{T1}` / `{T2}` / `{T3}` are sector ETF tickers
- `{bpsN}` is the RS delta in basis points with sign, no decimals:
  `+215bp`, `-187bp`
- Leaders are sorted by `rs_20d_bps` descending; laggards are sorted
  ascending (worst-first) so the worst sector is on the left

When leadership data is missing, render `Sector leadership:
insufficient data`.

## Take

One line at the end:

```
Take: {adaptive_take}
```

The take is keyed off the actual readings, not hardcoded per label.
The rules:

- For `risk_on`: lead with "Risk-on regime", summarize the supporting
  blocks (SPY uptrend + breadth + growth leadership + VIX), end with
  "Watch for VIX > 22 or breadth dropping below 50% as the first sign
  of regime change."

- For `risk_off`: lead with "Risk-off regime", summarize the
  supporting blocks (SPY downtrend + thin breadth + defensive
  leadership + VIX), end with "Watch for VIX retreat below 22 or
  growth sectors returning to the top of the RS table for a regime
  turn."

- For `mixed_risk_on`: lead with "Mixed risk-on", explicitly name the
  gap blocks ("breadth has thinned", "VIX has lifted to elevated"),
  end with "Treat as constructive but reduce trust until breadth and
  VIX confirm."

- For `mixed_risk_off`: lead with "Mixed risk-off", explicitly name
  the offsetting positives ("breadth still above 50%", "VIX normal"),
  end with "Treat as a defensive lean but watch for a confirmed bottom
  (breadth turning + VIX rolling over)."

- For `neutral`: lead with "Neutral regime", note SPY in range, end
  with "Wait for confirmation across two or more blocks before sizing
  up directional exposure."

The point of "adaptive" is that the take reflects what the data
actually says today, not a template that always reads the same. A
mixed_risk_on day where the only gap is rising VIX reads differently
from one where the gap is narrowing breadth.

## Caveats footer

Only render when `tier_caveats[]` is non-empty:

```
Caveats:
  - {caveat_1}
  - {caveat_2}
```

Always includes the sector-ETF breadth proxy caveat. Adds the
VIX-unavailable caveat when both `VIX` and `I:VIX` failed to resolve.

## Worked example: risk_on tape

```
Market Regime — 2026-06-29
RISK_ON

SPY: $555.20 (+0.23% today, +1.2% 5d, +4.5% 20d) — uptrend_strong
  Above 20/50/200-day MAs

VIX: 18.4 (42nd %ile of trailing year) — normal
  20-day avg 17.9. No stress signal.

Breadth (sector ETF proxy): 8 of 11 above 50-day MA (73%)
  10 of 11 above 200-day (91%). Broad participation.

Sector leadership (20-day RS vs SPY):
  Leaders:  XLK +215bp  ·  XLC +142bp  ·  XLY +98bp
  Laggards: XLE -187bp  ·  XLP -94bp   ·  XLU -53bp

Take: Risk-on regime. SPY uptrend with broad participation and growth
sector leadership; VIX at the median signals no immediate fear. Watch
for VIX > 22 or breadth dropping below 50% as the first sign of
regime change.

Caveats:
  - Breadth computed from 11 sector ETFs as a proxy; not the full advance/decline line. Sufficient for regime read, not for fine-grain breadth analysis.
```

## What UI devs do instead

A custom UI consumes the JSON payload directly: regime label as a
prominent chip (color-coded by label), each block as its own card with
a sparkline, leaders/laggards as a horizontal bar chart of RS deltas.
The rendered briefing is the Claude Code default; UIs build their own
visual layer from the same JSON, and downstream agents key off
`composite_regime.label` to qualify their own reads.

## Why this format

Morning briefings are a real workflow on every buy-side trading desk.
The structure (headline label, evidence blocks, take, watch points)
matches what a strategist memo or a Bloomberg morning note already
uses. Match the format; don't invent a new one. Any future
context-block skill (macro-overlay, intraday-regime, sentiment) should
follow the same five-section layout so the operator's eye learns one
template and reuses it across the suite.
