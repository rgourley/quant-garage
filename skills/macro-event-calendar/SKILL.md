---
name: macro-event-calendar
description: Forward calendar of the macro releases that reprice the whole book (FOMC, CPI, PPI, NFP, ISM manufacturing/services, GDP, PCE, JOLTS, jobless claims, retail sales, Consumer Confidence, Michigan Sentiment). Each event ships with release date/time, an impact tier, and the historical mean absolute 1-day SPY move on that release type. Sibling to earnings-blackout: earnings-blackout is single-name and this is macro-level. Use when running a portfolio review, sizing around a print, or answering "what's on the calendar this month."
---

# macro-event-calendar

You hand over a forward window (default 30 days) and get back the
macro release schedule with historical reaction stats per event type.

Sibling to earnings-blackout. earnings-blackout covers single-name
prints; this covers the macro releases that move the whole tape.
Every portfolio review should run both.

## When to invoke

- Portfolio review workflow: after earnings-blackout, run this for
  the macro-side of the calendar
- Pre-trade planning: "what's the next FOMC / CPI / NFP" before
  sizing a position
- The user says "macro calendar", "FOMC when", "CPI print", "what
  data is out this week"

## What you need

- `MASSIVE_API_KEY` for SPY historical reactions (Stocks Basic is
  enough; the tool is one range aggs call for SPY over the history
  window)

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Per-event: date, release time ET, impact tier, historical mean /
median / p90 |1-day SPY move|, sample size. Plus a `crowded_days`
block flagging dates with 2+ events.

**Layer 2 rendered table** sorted by date. Impact tier rendered as
1-4 stars. Pattern-derived dates marked with `~` so users know to
verify against the official calendar. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Generate scheduled dates** for each event type over the forward
   window via pattern rules (NFP = 1st Friday, CPI = 2nd Wednesday,
   ISM Mfg = 1st business day, etc.). FOMC dates are hardcoded from
   the official published schedule.
2. **Fetch SPY history** for the `history_days` lookback (default
   730 = 2 years).
3. **For each event type, compute historical stats**: generate the
   same pattern dates over history, look up SPY's |1-day move| on
   each matched date, aggregate to mean / median / p90.
4. **Flag crowded days** where 2+ events land on the same date.

## Endpoints used

- `GET /v2/aggs/ticker/SPY/range/1/day/{from}/{to}` (one call for
  the history window)

## Doesn't handle (yet)

- **No prior / consensus values.** Add via FRED integration for a
  future release.
- **Pattern-derived dates approximate the real release dates.**
  BLS/BEA/ISM release dates vary +/- 1-2 days from the pattern; the
  tool flags these with `~`.
- **Regime-conditional reactions.** Historical stats are
  unconditional; CPI moves the tape harder in inflation regimes than
  in disinflation. A conditioned version is a clean extension.
- **FOMC schedule is hardcoded for 2026.** Regenerate at year-end.
