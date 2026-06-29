---
name: earnings-blackout
description: Lightweight watchlist scanner. Takes a comma-separated list of tickers plus a forward window and returns each ticker's earnings status (blackout_imminent, blackout_soon, blackout_extended, just_printed, recent_print, clear, unresolved) with the next/most-recent print date and consensus EPS where available. Use before a trading day to spot which positions are about to print. Earnings-drilldown is the deep dive; this is the batch scan.
---

# earnings-blackout

You have a watchlist of 20 names. Which print this week? Which printed
yesterday and might gap on follow-up news? Which are clear to trade
without earnings-event risk? Run the scanner. One pass per ticker over
Benzinga (or SEC EDGAR as fallback), one classification per ticker, one
grouped exception report at the end.

This is the pre-trade hygiene check, not a full preview. For a single
name's full sell-side preview (implied move, beat/miss history, peer
reaction, drift), use [`earnings-drilldown`](../earnings-drilldown/).
For the per-event windowing study, use [`event-study`](../event-study/).

## When to invoke

- Morning watchlist scan before the market opens
- Position-sizing check: which names in the book have earnings risk
  in the next N days
- Post-mortem: which name in the watchlist already printed and might
  have residual gap risk

## What you need

- A watchlist (`--watchlist NVDA,TSLA,AMZN,GOOGL,META,AAPL,MSFT`)
- `MASSIVE_API_KEY` exported in the environment
- Optional: Benzinga earnings add-on for forward dates + consensus EPS
  (Tier A). Without it, the skill falls back to SEC EDGAR 8-K filings
  (Tier B, past-only).

## Quick start

```bash
python3 examples/run-earnings-blackout.py \
  --watchlist NVDA,TSLA,AMZN,GOOGL,META,AAPL,MSFT \
  --window-days 7
```

## Status buckets

Seven, returned in `results[].status`:

- `blackout_imminent` — earnings 0-3 days forward. Position-sizing
  decisions need to be locked in NOW.
- `blackout_soon` — earnings 4-7 days forward. Watching for IV ramp,
  positioning unwind.
- `blackout_extended` — earnings 8+ days forward. Only surfaces when
  `--window-days > 7`.
- `just_printed` — earnings 0-3 days past. Watch for analyst-day
  follow-on, guidance digestion, gap fill.
- `recent_print` — earnings 4-7 days past. Less relevant, ranked low
  in render.
- `clear` — no earnings in either the forward or past window.
- `unresolved` — the resolver returned nothing from Benzinga AND the
  SEC EDGAR fallback. Could mean the ticker has no 8-K with items
  2.02 / 7.01 / 8.01 in the window, or that the CIK lookup failed.
  Surfaced explicitly rather than silently dropped.

## Tiers

- **Tier A (Benzinga):** True forward calendar dates, consensus EPS,
  consensus revenue, expected release time (BMO/AMC/DMH).
- **Tier B (SEC EDGAR only):** Past prints only. No forward calendar
  (EDGAR is filing-based). No consensus EPS. Surfaces `item_code` and
  `signal_strength` (strong=2.02, soft=7.01/8.01) so consumers can
  weight conservatively.

The skill returns `tier: "A"` if any ticker resolved via Benzinga.
`tier_caveats[]` lists what the user is missing.

## Output

Always emits canonical JSON matching
[`output-schema.json`](./output-schema.json) and a rendered exception
report grouped by status (imminent first). See
[`references/rendering.md`](./references/rendering.md) for the rules
and [`references/methodology.md`](./references/methodology.md) for the
two-tier resolver chain.

## Reading

- [`references/methodology.md`](./references/methodology.md) — Benzinga
  primary, SEC EDGAR 8-K fallback, item-code interpretation, CIK chain
- [`references/rendering.md`](./references/rendering.md) — exception-
  report grouping rules
