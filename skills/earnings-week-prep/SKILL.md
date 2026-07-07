---
name: earnings-week-prep
description: Sunday-night prep for the week's earnings prints. Runs earnings-blackout across the watchlist to find who prints in the window, then earnings-drilldown + technical-briefing per imminent print (capped to top_n_drilldown for cost control). Use when the operator has multiple names printing in a single week and wants a one-shot sizing / positioning briefing on each.
---

# earnings-week-prep

You hand over a watchlist. The skill finds who prints in the forward
window (default 7d), then runs full drilldown + technical briefing
per imminent print. Capped to top N by proximity for cost control.

## When to invoke

- Sunday night before a heavy earnings week
- Multiple names in the book printing over the next 5-10 days
- User says "prep for earnings week", "who's printing this week",
  "drilldown my earnings names"

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Watchlist earnings-blackout scan + per-print drilldown + technical
readings.

**Layer 2 rendered brief** listing prints, then a per-name block for
each. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. earnings-blackout on the watchlist (window_days=7 by default)
2. Sort imminent prints by days-out
3. For top N (default 5): earnings-drilldown + technical-briefing

## Cost note

earnings-drilldown is heavy (SEC EDGAR walk + Massive financials +
optional options chain). Cap `top_n_drilldown` to keep the API
budget honest.
