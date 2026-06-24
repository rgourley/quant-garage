# Rendering: portfolio-mark

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for human consumption in
hybrid mode: a marked-positions table at the top, an exception block
at the bottom for any non-high-confidence marks, and an optional
"Live tape" trailer in live mode.

This is the canonical rendering for any future hybrid-mode skill in
the suite. Hybrid mode pairs a list of normal items (the table) with
an exception report (the FLAGGED block) so the reader scans the
overview, drills into the few items that matter, and stops. Match
this format.

## Mode: hybrid

Two sections always; a third in live mode when ticks were received.

## Header

One line at the top, then a blank line before the table:

```
Book marked: {marked_at_utc} · {n_positions} positions · Tier: {tier} ({mode})
```

Where:
- `{marked_at_utc}` is `YYYY-MM-DD HH:MM:SS UTC`
- `{tier}` is `A` (live) or `B` (delayed)
- `{mode}` is `live` or `delayed`

When any caveat is set (e.g. stream downgrade or no-ticks-in-window),
append a second header line:

```
Note: {first_caveat}
```

If there are multiple caveats, render only the first on the header
and roll the rest into the FLAGGED block as `stream_downgrade` /
`no_ticks_in_window` reasons.

## Marked table

```
Marked
| Ticker | Shares | Mark      | Source     | Confidence | As-of (ET)  |
|--------|--------|-----------|------------|------------|-------------|
```

Column rules:

- **Ticker:** left-aligned, no padding constraint.
- **Shares:** right-aligned. Comma-separated thousands. Negative for
  shorts: `(100)` in parentheses, not `-100`. (Brokerage convention.)
- **Mark:** right-aligned. `$XXX.XX` to 2 dp.
- **Source:** the `mark_source` shortened:
  - `stream.T` → `live_trade`
  - `stream.AM` → `live_minute`
  - `stream.FMV` → `live_fmv`
  - `snapshot.last.price` → `last_trade`
  - `snapshot.lastTrade.p` → `last_trade`
  - `snapshot.min.c` → `minute_close`
  - `snapshot.day.c` → `day_close`
  - `snapshot.prevDay.c` → `prev_close`
- **Confidence:** lowercase: `high`, `medium`, `low`.
- **As-of (ET):** `HH:MM:SS` if the mark is from today; `YYYY-MM-DD
  HH:MM:SS` if older.

If the input CSV had a `cost_basis` column, append two columns to
the right:

```
| Cost  | Unrealized P&L  |
|-------|-----------------|
```

P&L renders as `+$X,XXX.XX` or `-$X,XXX.XX` (sign always shown,
comma thousands).

## Sub-header line under the table

A single line summarizing book-level numbers, blank line before
FLAGGED:

```
Book value: ${book_value_usd:,.2f} · Last update lag: {n}s
```

If unrealized P&L was computed (cost basis present), include it:

```
Book value: ${book_value_usd:,.2f} · Unrealized P&L: ±${pnl:,.2f} · Last update lag: {n}s
```

Lag is `(reference_time - max(as_of)) in seconds`. Format: `14s`,
`2m 14s`, `1h 12m`. Drop the leading zeroes.

## FLAGGED block

Only present when `flagged[]` is non-empty. Blank line before, then:

```
FLAGGED ({count})
{TICKER} · ${mark:,.2f} · {confidence} confidence
  - {detail_text[0]}
  - {detail_text[1]}
  ...
  - Source: {source_endpoint_shortened} · Verified: {as_of_et}
```

`source_endpoint_shortened` strips the host:
- `https://api.polygon.io/v2/snapshot/...` → `snapshot`
- `wss://business.polygon.io/stocks (T.AAPL)` → `stream T.AAPL`

For each `reason_code`, emit one bullet under the position with the
matching `detail_text`. The skill's CSV-to-text mapping:

| reason_code | example detail_text |
|---|---|
| `stale_mark` | `Last trade 6m 36s stale (vs 14:42 UTC reference time)` |
| `wide_spread` | `Bid × Ask: $24.10 × $24.27 (70bps spread)` |
| `thin_quote` | `Bid or ask missing in snapshot; quote book likely thin` |
| `low_adv` | `Today's volume 240k (well below 500k mid-ADV cutoff)` |
| `fallback_chain_step_3_or_later` | `Mark from minute_close (chain step 3); last_trade was null` |
| `no_ticks_in_window` | `Subscribed for 30s but received 0 ticks; mark backfilled from REST` |
| `stream_downgrade` | `Resubscribed to AM after T returned not_authorized` |
| `prev_day_only` | `Only prev_close available; symbol may be halted or pre-open` |

If multiple flagged positions, separate each block by a blank line.

## Live tape trailer (live mode only, optional)

When `live_tape[]` is non-empty, append after FLAGGED:

```
Live tape (last 5 per ticker that received ticks)
AAPL  14:42:14  $298.74 × 200
AAPL  14:42:13  $298.71 × 100
NVDA  14:42:16  $200.65 × 400
NVDA  14:42:11  $200.60 × 150
SPY   14:42:18  $733.73 × 1,500
```

Format per row:
```
{TICKER:4}  {trade_time_et}  ${trade_price:.2f} × {trade_size:,}
```

Skip if `live_tape[]` is empty (delayed mode, or live mode with no
ticks). Don't emit a "no ticks" line; absence is the signal.

## Worked example: delayed mode, AAPL/NVDA/GME

Given the JSON payload (truncated):

```json
{
  "tier": "B",
  "mode": "delayed",
  "marked_at": "2026-06-23T19:42:18Z",
  "positions": [
    {"ticker": "AAPL", "shares": 100, "mark_price": 298.74, "mark_source": "snapshot.last.price", "confidence": "high", "as_of_et": "14:42:14"},
    {"ticker": "NVDA", "shares": 200, "mark_price": 200.65, "mark_source": "snapshot.last.price", "confidence": "high", "as_of_et": "14:42:16"},
    {"ticker": "GME",  "shares": 50,  "mark_price": 24.18,  "mark_source": "snapshot.min.c",      "confidence": "medium", "as_of_et": "14:36:42"}
  ],
  "flagged": [
    {"ticker": "GME", "mark_price": 24.18, "confidence": "medium",
     "reason_codes": ["stale_mark", "wide_spread"],
     "detail_text": [
       "Last trade 6m 36s stale (vs 14:42 UTC reference time)",
       "Bid × Ask: $24.10 × $24.27 (70bps spread)"
     ],
     "source_endpoint": "snapshot"}
  ],
  "book_value_usd": 80523.50
}
```

Renders as:

```
Book marked: 2026-06-23 19:42:18 UTC · 3 positions · Tier: B (delayed)

Marked
| Ticker | Shares | Mark      | Source     | Confidence | As-of (ET)  |
|--------|--------|-----------|------------|------------|-------------|
| AAPL   |    100 | $298.74   | last_trade | high       | 14:42:14    |
| NVDA   |    200 | $200.65   | last_trade | high       | 14:42:16    |
| GME    |     50 | $24.18    | minute_close | medium   | 14:36:42    |

Book value: $80,523.50 · Last update lag: 6m 36s

FLAGGED (1)
GME · $24.18 · medium confidence
  - Last trade 6m 36s stale (vs 14:42 UTC reference time)
  - Bid × Ask: $24.10 × $24.27 (70bps spread)
  - Source: snapshot · Verified: 14:36:42
```

## What UI devs do instead

A custom UI consumes the JSON payload directly, renders positions as
rows or cards, shows confidence as a colored chip, and surfaces
flagged items in a side panel or modal. The hybrid format is the
Claude Code default; UIs build their own visual layer from the same
JSON.

## Why this format

Pricing systems (Bloomberg PORT, Aladdin, internal NAV runners) all
converge on the same pattern: a table of marks at the top so the
operator can scan the book and tick through, plus an exception
report below for the marks they need to verify before signing the
NAV. Hybrid mode reflects how this work is actually done. Any future
mark / risk / valuation skill should match this format.
