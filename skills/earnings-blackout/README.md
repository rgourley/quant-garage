# earnings-blackout

Pre-trade hygiene scanner. Hand it a watchlist; it returns which names
have earnings in the next N days, which just printed, which are clear.

## Quick start

```bash
python3 examples/run-earnings-blackout.py \
  --watchlist NVDA,TSLA,AMZN,GOOGL,META,AAPL,MSFT \
  --window-days 7
```

## What you get

```
Earnings Blackout Scan — 2026-06-29
Watchlist: 7 tickers · Forward window 7 days · Past window 3 days

BLACKOUT IMMINENT (0-3 days forward):
  NVDA   Wed 2026-07-02 AMC  (3 days)  consensus EPS $0.78, rev $32.5B

BLACKOUT SOON (4-7 days forward):
  TSLA   Fri 2026-07-04 BMO  (5 days)  consensus EPS $0.55, rev $26.0B

JUST PRINTED (within past 3 days):
  (none)

CLEAR (no earnings in window):
  AMZN, GOOGL, META, MSFT

UNRESOLVED:
  AAPL   resolver returned no events (source attempted: edgar_8k)
```

Every run also emits canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Resolver

Two-tier, mirroring `event-study`:

1. **Tier A**: `/benzinga/v1/earnings` for forward dates + consensus EPS
2. **Tier B**: SEC EDGAR 8-K filings (items 2.02 / 7.01 / 8.01) for
   past prints, free, no add-on required

CIK lookup chain: Massive `/v3/reference/tickers/{T}` primary, SEC
canonical `company_tickers.json` fallback.

## Status buckets

`blackout_imminent` (0-3d fwd) · `blackout_soon` (4-7d fwd) ·
`blackout_extended` (8d+ fwd) · `just_printed` (0-3d past) ·
`recent_print` (4-7d past) · `clear` (no earnings in window) ·
`unresolved` (resolver returned nothing).

## Methodology

- [`references/methodology.md`](./references/methodology.md) — the
  two-tier resolver, 8-K item code interpretation, CIK chain
- [`references/rendering.md`](./references/rendering.md) — exception-
  report grouping rules
