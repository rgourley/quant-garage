# Rendering: crypto-vol-scanner

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in stream mode. Stream mode is
the format Bloomberg's crypto desk page, Cheddar Flow's intraday
panel, and Coinbase Institutional's morning report converge on:
per-event blocks, ticker-tagged, signal-typed, key:value pairs,
scanable top to bottom.

The base format follows
[`options-flow/references/rendering.md`](../../options-flow/references/rendering.md)
and [`news-scanner/references/rendering.md`](../../news-scanner/references/rendering.md).
This document covers the crypto-specific adaptations.

## Mode: stream

Each event is a self-contained 3-line block plus optional `↳`
continuation line for context. The reader scans top to bottom and
stops when they see one they want to act on. No prose, no intros. The
data is the output.

## Header

One line at the top, then a blank line before the first event:

```
{event_count} events surfaced from {universe_size} names · window: last {window_label} · run {YYYY-MM-DD HH:MM} UTC
```

When tier B (delayed tick freshness), append a caveat line:

```
Note: cross-exchange basis on Crypto Starter may reflect 15-min-delayed prints.
```

## Per-event block

Three lines per event, no leading bullet, separated by a blank line.
The format varies slightly by signal type. The first line is always:

```
{TICKER-PAIR}  {SIGNAL_TAG}  {signal_summary}
```

Where:

- `{TICKER-PAIR}` is `BTC-USD`, `ETH-USD`, etc. (hyphen, uppercase).
  No padding; the ticker length varies (DOGE-USD is 8 chars, BTC-USD
  is 7) and forcing alignment hurts readability with the signal tag
  that follows.
- `{SIGNAL_TAG}` is one of: `VOL SPIKE`, `VOLUME ANOMALY`,
  `CROSS-EXCHANGE`, `TAIL MOVE`, `QUIET`, `COMBINED`. Always uppercase,
  spelled out (no abbreviations).
- `{signal_summary}` is signal-specific. See the four templates below.

### Signal-specific summaries

**VOL SPIKE:**
```
realized 24h: {rv}% ({percentile}th %ile TTM, {ratio}x avg)
```

**VOLUME ANOMALY:**
```
24h ${volume_short} ({ratio}x trailing 30d avg)
```

**CROSS-EXCHANGE:**
```
{high_exchange} ${high_price} · {low_exchange} ${low_price} ({bps}bps)
```

**TAIL MOVE:**
```
{±pct}% 24h ({zσ}σ vs 30d)
```

**QUIET:**
```
realized 24h: {rv}% ({percentile}th %ile TTM)
```

**COMBINED:** use the highest-impact signal's summary, then list the
secondary signals on the second metrics line.

## The second line: metrics

Always three or four fields, separated by ` · `:

```
${spot} · 24h move {±pct}% ({zσ}σ) · 24h vol ${volume_short} ({ratio}x avg) · realized vol {rv}%
```

Where:

- `${spot}` is the current price. Crypto uses dynamic precision:
  4 decimals for sub-$1 (`$0.2180`), 2 decimals for $1-$1000 (`$152.30`),
  no decimals with thousands separator for $1000+ (`$63,500`).
- `{±pct}` is the 24h move percentage with explicit sign, 1 decimal
  (`+6.2%`, `-2.1%`).
- `{zσ}` is the z-score absolute value, 1 decimal, with sigma symbol
  (`2.1σ`). Sign is implicit in `{±pct}` directly above.
- `${volume_short}` is humanized: `$2.8B`, `$340M`, `$24B`. Round to
  one decimal. K-scale never appears (no crypto in the top 10 trades
  less than $1M/day).
- `{ratio}` is volume vs 30d avg, one decimal (`3.1x`).
- `{rv}` is annualized realized vol percentage with no decimals
  (`87`).

When a metric is unavailable (e.g. realized vol couldn't be computed
due to sparse hourly aggs), omit just that field and its surrounding
` · `; don't render `n/a`.

## The third line: context

Always shows the per-event signals when COMBINED, otherwise an
optional `↳` continuation line:

For COMBINED events, the third line is the secondary metric:
```
{primary signal summary on line 1} · also {secondary signal: e.g. volume 3.1x avg}
```

For non-combined events with notable context, use `↳`:

```
↳ basis widened from 8bps prior 24h; small but persistent
↳ social-driven flow; vol AND volume both 95th+%ile
↳ unusually quiet; calm-before-storm watch
↳ third 2σ+ move this week; momentum regime
```

Skip the continuation entirely when no context to add. Don't pad with
"↳ no context."

## Continuation lines (↳)

Use `↳` (U+21B3) to attach a context line. Indent 0 spaces; the arrow
is the indicator. Maximum one continuation per event in v1.

The context line should add information not visible in the first
three lines: regime context, prior-window comparison, social/news
attribution where defensible, or a flag like "calm-before-storm."

## Section grouping

Sort events by `composite_score` descending. No section dividers.
Group is implicit via sort order: most-notable events surface first,
quiet events sort to the bottom.

If the universe is small (e.g. 10 names) and N >= 10, every name
appears. That's a feature: in a quiet regime, the quiet names ARE the
signal.

## Footer

One line summarizing scope, then a blank line:

```
End of stream. {n} events across {universe_size} names. Universe median RV: {median_rv}%.
```

The universe median RV is the "what regime are we in" summary number.
Useful for the reader to anchor: "the median name is at 45% RV, so
SOL at 87% is genuinely elevated."

If tickers were skipped (e.g. snapshot returned null), append:

```
Skipped: {SKIP1}, {SKIP2} (reason: {reason}).
```

## Worked example

Given four sample events from a top-10 scan, the rendered output
reads:

```
8 events surfaced from 10 names · window: last 24h · run 2026-06-23 21:50 UTC

SOL-USD  VOL SPIKE  realized 24h: 87% (98th %ile TTM, 2.4x avg)
$152.30 · 24h move +6.2% (2.1σ) · 24h vol $2.8B (3.1x avg) · realized vol 87%
↳ 30d realized 36%, current ~2.4x baseline; positioning skewed

DOGE-USD  COMBINED  realized 24h: 124% (96th %ile TTM, 3.8x avg) · also volume 4.7x avg
$0.2180 · 24h move +18.4% (3.8σ) · 24h vol $1.2B (4.7x avg) · realized vol 124%
↳ social-driven flow; vol AND volume both 95th+%ile

BTC-USD  CROSS-EXCHANGE  Coinbase $63,420 · Binance $63,580 (25bps)
$63,500 · 24h move +0.8% (0.7σ) · 24h vol $24B (1.1x avg) · realized vol 41%
↳ basis widened from 8bps prior 24h; small but persistent

ETH-USD  QUIET  realized 24h: 28% (22nd %ile TTM)
$3,420 · 24h move -0.4% (0.3σ) · 24h vol $9.2B (0.9x avg) · realized vol 28%
↳ unusually quiet; calm-before-storm watch

End of stream. 4 events across 10 names. Universe median RV: 47%.
```

## What UI devs do instead

A custom UI consumes the JSON payload directly. A crypto monitor
typically shows each event as a card with the signal type as a colored
badge (red for vol spike, blue for cross-exchange, gray for quiet),
the spot as a large number, and the four metric ratios as sparkline
bars. The stream format is the Claude Code default; UIs build their
own visual layer from the same JSON.

## Why this format

Bloomberg's crypto desk page, Cheddar Flow's intraday panel, and the
Coinbase Institutional desk note converge on the same shape because:

- Line 1 identifies the name and what's weird about it (vol spike,
  basis, etc.) in 8 words or less
- Line 2 is the discriminator: spot, move size, move z-score, volume
  context, vol context (in one row)
- Line 3 (optional ↳) is the trader's read: what's the regime, what
  changed, what's the watch

Traders skim line 1 (do I care about this name and signal type), read
line 2 (is the magnitude big enough to act on), and only consume the
↳ when the first two earned their attention.

## What to NOT do

- Don't add emoji. Bloomberg doesn't; the desk doesn't.
- Don't use em-dashes. House rule. Colons, parentheses, periods.
- Don't editorialize on direction in the context line ("BTC looks
  ready to break out"). The skill surfaces statistical context; the
  trader makes the call.
- Don't quote the exchange-ID integer (e.g. "exchange 10"). Render
  the name (`Binance`). The ID is in the JSON for downstream tools.
