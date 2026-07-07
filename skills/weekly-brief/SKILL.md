---
name: weekly-brief
description: Sunday-night briefing for the week ahead. Composes market-regime + sector-rotation-signal + macro-event-calendar (7d) + earnings-blackout (7d) into a watchlist-focused prep brief. Different from portfolio-review, which is position-focused; this one asks "what's happening this week across the names I care about." Use when the operator wants recurring weekly context, not on-demand book review.
---

# weekly-brief

You hand over a watchlist. The skill runs the four macro/calendar
sub-skills and stitches them into a briefing sized for the week
ahead. Watchlist-focused, not position-focused.

## When to invoke

- "Weekly brief", "what's on the calendar this week", "prep for the
  week", "Sunday briefing"
- Recurring cadence use: cron this every Sunday night with your
  watchlist
- Different from portfolio-review: this is watchlist context, not
  portfolio decisions

## What you need

- `MASSIVE_API_KEY` (Stocks Basic minimum — all sub-skills run on free)

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Headline block distills the regime, rotation theme, this-week's
top-3 macro events, and this-week's earnings prints. Full sections
below.

**Layer 2 rendered brief** with the same shape as portfolio-review:
headline block + 4 titled sections. See
[`references/rendering.md`](./references/rendering.md).

## How it works

Pure composition. Runs sub-skills in order:
1. market-regime
2. sector-rotation-signal (30d default)
3. macro-event-calendar (window_days=7 by default)
4. earnings-blackout (watchlist, same window)

Shares a single MassiveClient. Failures in any single section leave
its `sections[<name>]` null and appear in the errors block.

## Endpoints used

- Union of the 4 sub-skills' endpoints (daily-aggs for SPY + sector
  ETFs, EDGAR for earnings fallback, no options)

## Doesn't handle (yet)

- **No positions.** This is watchlist-focused. For book-level review
  use portfolio-review.
- **No news scan.** For a daily open with fresh news, use
  morning-brief.
