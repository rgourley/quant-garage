# Rendering: insider-flow

The skill emits canonical JSON matching `output-schema.json`. This
reference describes how that JSON renders in note mode.

## Block order

Up to six blocks, separated by blank lines:

1. Header (two lines: identity + sentiment)
2. Transaction flow (fixed table of counts and dollars per category)
3. Cluster buys (only when detected)
4. Notable open-market buys (only when non-empty)
5. Notable discretionary sales (only when non-empty)
6. By role (only when non-empty)
7. Take + caveats

No prose intros. The reader opens the note expecting a signal read;
deliver one.

## Header

```
Insider flow: NVDA · 180-day lookback · 128 Form 4 rows
Sentiment: Bearish (net conviction -$4.20M)
```

Line 1: identity + row count.
Line 2: sentiment label + net conviction dollars in parentheses.

Sentiment display map:
- `strong_bullish` → `STRONG BULLISH` (uppercase for emphasis)
- `bullish` → `Bullish`
- `neutral` → `Neutral`
- `bearish` → `Bearish`
- `strong_bearish` → `STRONG BEARISH`

## Transaction flow

Fixed four-line block, always in this order:

```
Transaction flow
  Conviction buys (P):        2 txns    $85.0k
  Discretionary sales (S):    6 txns    $4.29M
  Scheduled sales (10b5-1):  35 txns   $92.10M  (filtered out)
  Routine comp (A/M/F):      45 txns  (grants + exercises)
```

The parenthetical after "Scheduled sales" is essential: it tells the
reader those dollars are excluded from sentiment. Routine comp
doesn't show dollars because they aren't informative.

## Cluster buys

```
CLUSTER BUYS (2)
  · 2026-04-08 → 2026-04-19: 3 insiders, 5 txns, $850.0k
    KRESS COLETTE, HUANG JEN-HSUN, DALLY WILLIAM
  · 2026-01-15 → 2026-01-22: 2 insiders, 2 txns, $120.0k
    SHOQUIST DEBORA, ROBERTSON DAWN
```

At most 3 clusters render. `insider_names` truncates at 4 with a
`+N` more indicator. The full list lives in the JSON.

## Notable open-market buys / discretionary sales

Top 5 by dollar value, descending. One line per transaction:

```
Notable open-market buys
  · 2026-05-14 · KRESS COLETTE (Officer (EVP, CFO)) · 500 sh @ $85.00 = $42.5k
```

Format:
- `transaction_date`
- `owner_name` + `role` in parens
- Shares (integer with commas)
- Price per share
- Dollar value (compact formatting)

## By role

```
By role
  · Director                                 buys      $0  sales   $250.0k  net  -$250.0k
  · Officer (EVP, CFO)                       buys    $42.5k  sales   $2.10M  net  -$2.06M
  · Officer (CEO)                            buys      $0  sales   $1.94M  net  -$1.94M
```

Sorted alphabetically by role. Dollars formatted with the same
compact scheme as elsewhere.

## Take + caveats

```
Take: Insider read is Bearish: discretionary sales of $4.29M vs $85.0k in open-market buys.

Caveats:
- Form 4 is filed within 2 business days of the transaction; insider decisions are days-to-weeks fresh, not real-time.
- Discretionary sales (non-10b5-1) are noisier than open-market buys: insiders sell for diversification and taxes, not always because they see downside. Buys are the cleaner signal.
```

Take is one line. When there's a cluster buy, it gets appended to the
take even when the top-line sentiment is bearish (e.g., "Insider read
is Bearish ... Cluster buy caught: 3 insiders buying $850k").

Caveats are always present. Third caveat about 10b5-1 fires only when
scheduled sales are non-zero.

## Empty case

When `n_rows == 0`:

```
Insider flow: NVDA · 180-day lookback · 0 Form 4 rows

- No Form 4 filings returned for NVDA in the last 180 days.
```

## What UI devs do instead

A custom UI consumes the JSON and renders:

- A timeline chart with green dots for buys, red dots for
  discretionary sales, orange dots for scheduled sales, dot size
  proportional to dollar value.
- A per-insider table with position size, cumulative net flow, and
  a link to their SEC EDGAR filer profile.
- A price overlay on the transaction dates so buys near lows and
  sales near highs are visually obvious.

The rendered note here is the Claude Code default.
