---
name: prediction-market-monitor
description: Pull Kalshi prediction market prices for Fed decisions, CPI, GDP, NFP, and other macro / market events. Report implied probability per outcome, aggregate cross-strike distribution when the series is a laddered strike set (like KXFED-27APR-T4.25, T4.00, T3.75...), expected value, modal outcome, and open interest. Prediction markets now clear enough volume post-2024 to reflect a real market-implied policy path, often diverging from surveyed economist consensus. Uses Kalshi's public read-only API; no authentication required.
---

# prediction-market-monitor

You pass a Kalshi series or shortcut. The skill pulls open markets,
groups by event, and reports implied probabilities. When the event
is a laddered strike-type (Fed funds level, CPI reading), it derives
the cross-strike probability distribution from adjacent-threshold
differences and reports the modal outcome and expected value.

Motivated by 2025-26 growth of prediction markets as leading
indicators post-2024 election validation. Kalshi Fed-decision
contracts now trade meaningful volume; contract prices often reflect
policy expectations before consensus surveys catch up.

## When to invoke

- "What's the Kalshi-implied Fed decision at the next meeting?"
- Comparing market-implied CPI to Bloomberg / Reuters consensus
- Cross-referencing macro-print bets against your positioning
- The user says "Kalshi", "prediction market", "implied Fed", "fed
  funds futures alternative"

## What you need

- Internet access (Kalshi's public API; no Massive key needed)

Optional:

- `--series` (shortcut like `fed`, `cpi`, `nfp`, `gdp`, or a raw
  Kalshi series ticker like `KXFED`, `KXCPI`)
- `--keyword` (client-side filter on title / ticker)
- `--event-ticker` (pin a specific event, e.g. `KXFED-27APR`)
- `--max-events` (default 5)

## What you get back

**Layer 1: JSON**. Per event: `event_ticker`, `title`, `close_time`,
`markets` (each with `implied_probability`, bid, ask, last, volume,
open interest, floor_strike). When laddered:
`implied_distribution.buckets` with `p_in_bucket` and
`cumulative_p_above_lower`, plus `modal_bucket` and
`expected_value`.

**Layer 2: rendered note**. Per event: title + close time, modal
outcome + expected value line, bucket distribution with ASCII bars.

## How it works

1. Query Kalshi `/trade-api/v2/markets` with the series filter.
   Paginate up to 3 pages (600 markets max).
2. Group markets by `event_ticker`. A single event ("KXFED-27APR")
   typically contains 15-20 laddered strikes.
3. For each event, sort by `floor_strike` and derive the implied
   distribution: `P(rate in [lower, upper))` = `P(above lower)` -
   `P(above upper)`.
4. Report modal outcome (highest-probability bucket) and expected
   value (probability-weighted midpoint).

## Series shortcuts

- `fed`: KXFED (fed funds level after meeting)
- `fed_decision`: KXFEDDECISION (rate change at meeting)
- `cpi`: KXCPI (m/m)
- `core_cpi`: KXCORECPI
- `cpi_yoy`: KXCPIYOY
- `nfp`: KXNFP
- `unemployment`: KXUNEMP
- `gdp`: KXGDP
- `jobless_claims`: KXICSA
- `recession`: KXRECESSIONYEAR
- `spx_close`: KXSPX
- `btc_close`: KXBTCD

## Foundations used

- None. Kalshi public API only.

## Endpoints used

- `GET https://api.elections.kalshi.com/trade-api/v2/markets`
  Public read-only, no auth.

## Doesn't handle (yet)

- **Polymarket integration.** Kalshi only. Polymarket has more
  political / cultural markets; Kalshi has more macro. Adding a
  `--source polymarket` toggle would be a clean extension.
- **Time series of implied probability.** Snapshot only. A rolling
  history would show when the market moved.
- **Cross-reference to survey consensus.** Would need a data
  partnership with Bloomberg / Reuters or an FOMC dot-plot lookup.
- **CFTC-regulated futures cross-check.** SOFR/Fed funds futures
  implied path from CME data would be a nice comparator.

These are clean PR extensions.
