# Rendering: options-flow

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in stream mode. Stream mode is the
format Cheddar Flow / FlowAlgo / Unusual Whales use for live flow:
per-print blocks, compact, key:value pairs, scanable top to bottom.

This is the canonical reference for any stream-mode skill in the suite
(news-scanner, crypto-vol-scanner). Match this format.

## Mode: stream

Each notable print is a self-contained block of 3 lines plus optional
`↳` continuation lines for context. The reader scans the file top to
bottom and stops when they see one they want to act on. No prose, no
intros. The data is the output.

## Header

One line at the top, then a blank line before the first print:

```
{ticker_count} tickers scanned · {total_prints} prints surfaced · run {YYYY-MM-DD HH:MM} UTC · Tier {tier}
```

When tier < A, append the most important caveat on a second line:

```
Note: 15-min delayed tape (Options Developer). Latest prints from {timestamp}.
```

## Per-print block

Three lines per print, no leading bullet, separated by a blank line:

```
{TICKER}  {expiry}  ${strike}{C|P}  {KIND}  @ ${price}
{volume} vol · ${premium_short} prem · {ratio}x avg · {nbbo_tag} · {DIRECTION}
spot ${spot} · OI {oi} ({position_signal}) · IV {iv}%
```

Where:

- `{TICKER}` is the underlying, uppercase, space-padded to 4 chars on
  the left for column alignment (TSLA, NVDA, AAPL all align; SPY gets
  trailing space)
- `{expiry}` is `YYYY-MM-DD`
- `${strike}` is the strike with no decimal if integer, one decimal
  otherwise. Examples: `$310C`, `$7.5P`
- `{KIND}` is uppercase: `SWEEP`, `BLOCK`, or `OTHER`
- `${price}` is the volume-weighted average price for sweeps, the block
  print price for blocks, or the day VWAP for `other`
- `{volume}` is comma-separated thousands (`2,840`)
- `${premium_short}` is humanized: `$1.4M`, `$320K`, `$2.1B`. Round to
  one decimal place. The methodology pages threshold premium at $100K
  default, so K-scale is rare in the rendered output.
- `{ratio}` is `vol/30d-avg` with one decimal (`14.2x`)
- `{nbbo_tag}` is one of: `ASK side`, `MID`, `BID side`, `ABOVE ASK`,
  `BELOW BID`. Maps from the schema's `price_vs_nbbo`:
  - `above_ask` → `ABOVE ASK`
  - `at_ask` → `ASK side`
  - `at_mid` → `MID`
  - `at_bid` → `BID side`
  - `below_bid` → `BELOW BID`
  - `unknown` → omit this field and the surrounding ` · `
- `{DIRECTION}` is uppercase tag: `BULLISH`, `BEARISH`, `NEUTRAL`. Omit
  if `unknown`.
- `{position_signal}` is the OI signal: `opening`, `closing`, or `mixed`.
  Lowercase, in parens after the OI number.
- `{iv}` is annualized IV percentage with no decimals (`62`).

The methodology should NOT use directional words ("bullish/bearish") in
its top-level take, but they're appropriate here because per-print flow
tags are how traders read flow on a screen. A call sweep above ask is
flagged BULLISH at the print level; the aggregated take stays neutral.

## Continuation lines (↳)

Use `↳` (U+21B3) to attach a context line to the print above. Indent 0
spaces; the arrow is the indicator. One continuation per insight; if you
have 2 insights, use 2 arrows.

Common patterns:

```
↳ same strike: 1,420 yesterday → clustering
↳ matches Friday's $1.2M sweep
↳ near gamma flip ($300)
↳ unusual for {expiry} weekly (avg <100 vol)
```

Continuation lines are optional. Skip them entirely if no context to
add. Don't pad blocks with "↳ no related prints"; absence is the signal.

## Section grouping

Sort prints by `score` descending. No section dividers. Group is implicit
via sort order: the most unusual prints surface first.

If a single ticker has multiple qualifying prints, render each as a
separate block (don't collapse). Same expiry / strike duplicates should
already be deduplicated upstream into one print with continuation lines.

## Footer

One line summarizing scope, then a blank line, then any skipped tickers
on a single line:

```
End of stream. {n} prints across {m} tickers. {p} tickers skipped: {SKIP1}, {SKIP2}.
```

If no tickers were skipped, omit the skipped line.

If the user invoked with `--full` or equivalent, append the full sources
block from the JSON. Default is to omit; the JSON has them.

## Worked example

Given three sample prints (TSLA call sweep, NVDA call block, AAPL put
sweep) the rendered output reads:

```
5 tickers scanned · 3 prints surfaced · run 2026-06-23 18:45 UTC · Tier A

TSLA  2026-07-18  $310C  SWEEP  @ $4.85
2,840 vol · $1.4M prem · 14.2x avg · ASK side · BULLISH
spot $302.10 · OI 1,890 (opening) · IV 62%
↳ same strike: 1,420 yesterday → clustering

NVDA  2026-07-18  $210C  BLOCK  @ $3.20
5,200 vol · $1.7M prem · 8.1x avg · MID · NEUTRAL
spot $202.55 · OI 12,400 (closing) · IV 58%

AAPL  2026-08-15  $290P  SWEEP  @ $5.40
1,890 vol · $1.0M prem · 5.7x avg · BID side · BEARISH
spot $298.20 · OI 540 (opening) · IV 27%

End of stream. 3 prints across 3 tickers.
```

## What UI devs do instead

A custom UI consumes the JSON payload directly. A flow dashboard
typically shows each print as a row in a sortable table or a card with
icons for kind / direction, with click-through to the underlying chain
and the contributing trades. The stream format is the Claude Code
default; UIs build their own visual layer from the same JSON.

## Why this format

Cheddar Flow, FlowAlgo, and Unusual Whales all converged on the
three-line block because:

- The first line is enough to know what was traded (ticker, contract,
  kind, price)
- The second line is enough to know if it's worth caring about (volume,
  dollars, ratio, side, direction)
- The third line is the context for the read (spot, OI position, IV)

Traders skim line 1, decide whether to keep reading, and only consume
lines 2-3 on the prints that pass the first filter. Stream mode in this
suite follows the same logic; any future stream-mode skill should
preserve it.
