# Rendering: news-scanner

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in stream mode. Stream mode is
the format Bloomberg's news tape, Benzinga Pro's feed, and Reuters
Eikon's incoming events panel converge on: per-event blocks,
ticker/time/source header, then a key:value details line, then
optional context. Scanable top to bottom.

The base format follows
[`options-flow/references/rendering.md`](../../options-flow/references/rendering.md).
This document covers the news-specific adaptations.

## Mode: stream

Each notable event is a self-contained 3-line block plus optional `↳`
continuation lines for context. The reader scans the file top to
bottom and stops when they see one they want to act on. No prose, no
intros. The data is the output.

## Header

One line at the top, then a blank line before the first event:

```
{event_count} events surfaced from {ticker_count} tickers · window: last {window_label} · run {YYYY-MM-DD HH:MM} UTC
```

When tier B (keyword sentiment fallback), append a caveat line:

```
Note: keyword sentiment scorer in use (Benzinga insights not available). Reaction window: 5-min aggs.
```

## Per-event block

Three lines per event, no leading bullet, separated by a blank line:

```
{TICKER}  {publish_time_et}  {SOURCE}
HEADLINE: {headline}
SENTIMENT: {±score} · NOVELTY: {band} · REACTION: {±pct}% ({window_label}) · {volume_anomaly}x baseline vol
```

Where:

- `{TICKER}` is the underlying, uppercase, space-padded to 4 chars on
  the left for column alignment (NVDA, TSLA, AAPL align; SPY gets a
  trailing space)
- `{publish_time_et}` is `YYYY-MM-DD HH:MM ET`
- `{SOURCE}` is the publisher name as Benzinga returns it: `Reuters`,
  `Bloomberg`, `Benzinga`, `CNBC`, `The Motley Fool`, etc. Don't
  uppercase; preserve publisher capitalization.
- `{headline}` is the article title. Truncate to 90 chars; append `…`
  when truncated. Headlines are part of the data, not formatting;
  don't paraphrase.
- `{±score}` is the sentiment score with explicit sign and 2 decimal
  places: `+0.72`, `-0.55`, `+0.00`. Score is always shown even when
  zero; the sign is the signal.
- `{band}` is the novelty band: `high`, `medium`, `low`. Lowercase.
- `{±pct}` is the reaction percentage with explicit sign, 1 decimal
  place: `+1.8%`, `-2.1%`, `+0.4%`. When reaction is null (publish
  inside an off-hours window with no post-open data yet), render the
  full clause as `REACTION: pending overnight`.
- `{window_label}` is the reaction window length: `47min`, `1h 7min`,
  `3h`, `overnight`.
- `{volume_anomaly}` is the multiple of baseline per-minute volume,
  one decimal: `8.4x`, `1.1x`. When unavailable (no baseline), render
  the full clause as `· baseline vol n/a`.

## Continuation lines (↳)

Use `↳` (U+21B3) to attach a context line to the event above. Indent 0
spaces; the arrow is the indicator. Maximum one continuation line per
event in v1 (multiple lines for one event become a wall of `↳` that
hurts scanability).

Two patterns produce a continuation line, in priority order:

1. **Divergence flag (highest priority).** When the divergence flag is
   non-`none`, render:

   ```
   ↳ DIVERGENCE: positive sentiment, -1.8% reaction. Likely priced in.
   ↳ DIVERGENCE: negative sentiment, +0.9% reaction. Tape says "not as bad."
   ```

2. **Novelty context.** When divergence is `none` and the
   `context_line` field is populated (typically paraphrasing the
   nearest-prior article from novelty detection), render it:

   ```
   ↳ first India-specific partnership; prior coverage speculative
   ↳ third China price cut this year; margin pressure narrative compounding
   ↳ matches Friday's whisper-number leak
   ```

Skip the continuation entirely when neither applies. Don't pad with
"↳ no context"; absence is the signal.

## Section grouping

Sort events by `impact_score` descending. No section dividers. Group
is implicit via sort order: the highest-impact events surface first.

If a single ticker has multiple qualifying events, render each as a
separate block (don't collapse). Same-story duplicates should already
be deduplicated upstream into one event with `related_event_ids`.

## Footer

One line summarizing scope, then a blank line, then any skipped
tickers on a single line:

```
End of stream. {n} events across {m} tickers. {p} tickers skipped: {SKIP1}, {SKIP2}.
```

If no tickers were skipped, omit the skipped line.

When the user invoked with `--full`, append the JSON summary block.
Default is to omit; the JSON has it.

## Worked example

Given four sample events (NVDA partnership, TSLA price cut, AAPL
services rumor, SPY Powell remarks) the rendered output reads:

```
12 events surfaced from 5 tickers · window: last 24h · run 2026-06-23 19:30 UTC

NVDA  2026-06-23 14:32 ET  Reuters
HEADLINE: Nvidia, Foxconn announce India fab partnership for AI chips
SENTIMENT: +0.72 · NOVELTY: high · REACTION: +1.8% (47min) · 8.4x baseline vol
↳ first India-specific partnership; prior coverage speculative

TSLA  2026-06-23 14:12 ET  Bloomberg
HEADLINE: Tesla cuts Model Y prices in China by 6% as competition intensifies
SENTIMENT: -0.55 · NOVELTY: high · REACTION: -2.1% (1h 7min) · 4.2x baseline vol
↳ third China price cut this year; margin pressure narrative compounding

AAPL  2026-06-23 10:48 ET  CNBC
HEADLINE: Apple's Services revenue tops $26B in fiscal Q3 (rumor mill ahead of print)
SENTIMENT: +0.34 · NOVELTY: medium · REACTION: +0.9% (3h) · 2.1x baseline vol
↳ rumored leak; consistent with prior whisper, modest reaction

SPY   2026-06-23 09:14 ET  Federal Reserve
HEADLINE: Powell signals patient stance on rates in Jackson Hole prep remarks
SENTIMENT: +0.32 · NOVELTY: low · REACTION: +0.4% (5h 18min) · 1.1x baseline vol
↳ consistent with prior FOMC dot plot; market priced in

End of stream. 4 events across 4 tickers.
```

## What UI devs do instead

A custom UI consumes the JSON payload directly. A news feed UI
typically shows each event as a card with the headline at the top,
sentiment as a colored chip, reaction as a sparkline overlay, and
novelty as a small badge. The stream format is the Claude Code
default; UIs build their own visual layer from the same JSON.

## Why this format

Bloomberg's news tape, Benzinga Pro, Reuters Eikon, and CNBC's
fast-money desk all use the same shape because:

- The first line is enough to know which ticker and where the news
  came from (publisher trust is one of the strongest filters analysts
  apply)
- The second line is the headline itself; rewriting it loses precision
- The third line is what makes news-scanner different from a news
  terminal: it's the market's response, with the volume confirmation
  and the novelty band

Traders skim line 1 (do I care about this ticker / source), read
line 2 (what's the actual news), and only consume line 3 on events
where line 1+2 already grabbed them. That's why line 3 is the
densest: it earns the attention by being the discriminator.

## What to NOT do

- Don't editorialize. The headline is the data; the skill renders it
  verbatim.
- Don't add emoji. Bloomberg doesn't; Benzinga Pro doesn't.
- Don't use em-dashes. House rule across the suite. Colons,
  parentheses, periods.
- Don't put the URL in the rendered output. It's in the JSON for
  click-through; the rendered stream stays scanable.
