---
name: stock-one-pager
description: Beginner-friendly single-name snapshot. Composes technical-briefing + earnings-blackout + market-regime into a plain-language card. The retail-tier answer to "what should I know before buying this thing I saw on social." Zero jargon. Every claim gated by what the data actually says. Use when a first-time trader (or a friend) asks about a specific stock, or when the operator wants a low-glossary read before doing deeper work.
---

# stock-one-pager

You hand over a ticker. The skill returns a beginner-friendly card:
plain-language trend read, valuation-in-english, recent-range position,
key support/resistance levels, next catalyst, market context, and a
short honest "what could go wrong."

This is a RETAIL-TIER composed skill. Design bar: a first-time trader
understands the output without a glossary, and the tool never implies
certainty the data doesn't support. Every claim is gated to what
technical-briefing + earnings-blackout + market-regime actually say.

## When to invoke

- A retail-tier ask: "what should I know about NVDA before I buy?"
- Media / newsletter blurbs — the tool writes at reading-level
  appropriate for someone who doesn't speak sell-side
- Before running heavier analyst-tier skills, use this to get a
  plain-language anchor on the name
- The user says "one-pager", "snapshot", "brief", "what's the deal
  with X"

## What you need

- `MASSIVE_API_KEY` (Stocks Starter minimum, same as the underlying
  technical-briefing + market-regime + earnings-blackout)

## What you get back

**Layer 1 canonical JSON** matching [`output-schema.json`](./output-schema.json).
Plain-language fields (`trend_plain`, `market_context_plain`, etc.)
alongside a `components` block that surfaces the raw component
readings for callers who want to layer more on top.

**Layer 2 rendered card**. Header + 6 short blocks. No em-dashes, no
jargon. See [`references/rendering.md`](./references/rendering.md).

## How it works

Pure composition. Calls `run()` on three sub-skills, translates each
into plain language via lookup tables, threads the readings into a
"what could go wrong" list.

1. `technical-briefing.run(ticker)` for the chart read.
2. `earnings-blackout.run([ticker], window_days=30, include_past_days=30)`
   for the next-catalyst hint.
3. `market-regime.run()` for macro context (optional; skip via
   `include_market_context=False` when running in a tight loop over
   many tickers).

## Foundations used

- `massive-api-patterns` (via the sub-skills)
- No direct API calls of its own

## Doesn't handle (yet)

- **No news / catalyst detection beyond earnings.** Product launches,
  FDA decisions, regulatory events don't show up. Add news-scanner
  to the chain if you want a richer catalyst read.
- **No valuation lens.** This is a retail-tier read; adding
  valuation-sanity-check would push it toward analyst-tier and mix
  the audience. If a user needs valuation, invoke that skill
  separately.
- **No sentiment / positioning read.** No options-flow, no short-
  interest. Same rationale.
