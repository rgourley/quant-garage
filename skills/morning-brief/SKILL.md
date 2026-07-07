---
name: morning-brief
description: 60-second daily open briefing. Composes market-regime + macro-event-calendar (today+tomorrow) + news-scanner (last N per watchlist ticker). Lighter and shorter-horizon than weekly-brief. Use daily at the open, or when the operator wants a quick "what happened overnight and what's on today."
---

# morning-brief

Daily open briefing. Runs market-regime, today+tomorrow's macro
calendar, and last-N news per watchlist ticker. Answers "what's the
tape today and what's the news I missed."

## When to invoke

- Daily cadence (cron at market open with your watchlist)
- User says "morning brief", "what's happening today", "overnight
  news"
- Sharp counterpart to weekly-brief (which is 7d) — this is 1-2d

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Headline: regime + today's macro events + top-5 news items across
watchlist.

**Layer 2 rendered brief**. See
[`references/rendering.md`](./references/rendering.md).

## How it works

Pure composition:
1. market-regime
2. macro-event-calendar (window_days=2)
3. news-scanner (last_n per ticker, watchlist)

Watchlist is optional. Without it, morning-brief runs macro-only.
