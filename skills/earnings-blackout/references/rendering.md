# Rendering: earnings-blackout

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for Claude Code users.

The output mode is `exception_report`: group by status, lead with the
buckets that need action, summarize the rest. A reader should scan it
in 5 seconds and identify which positions need a pre-print decision
before the open.

## Header

Always lead with the scan date and the window configuration:

```
Earnings Blackout Scan — {as_of}
Watchlist: {n_tickers} tickers · Forward window {window_days_forward} days · Past window {window_days_past} days
```

## Status buckets — order matters

Render in this fixed order. Imminent comes first because that's where
the trader has to make a decision before the next open.

1. **BLACKOUT IMMINENT (0-3 days forward)** — always show, even if empty
2. **BLACKOUT SOON (4-7 days forward)** — always show, even if empty
3. **BLACKOUT EXTENDED (8+ days forward)** — only render when
   `window_days_forward > 7` (otherwise the bucket is logically empty)
4. **JUST PRINTED (within past 3 days)** — always show, even if empty
5. **RECENT PRINT (4-7 days past)** — only render when
   `window_days_past > 3`
6. **CLEAR (no earnings in window)** — collapsed to a single line of
   comma-separated tickers; the user doesn't need per-line detail
7. **UNRESOLVED** — only render when at least one ticker is unresolved

Empty buckets render as `(none)` on the buckets that matter (the first
five). The `clear` bucket collapses to one line. `unresolved` is
omitted entirely when empty (most of the time it is).

## Per-row format

For detailed buckets (imminent, soon, extended, just_printed,
recent_print):

```
  {ticker:6}  {wd} {date} {session?}  ({days_until} day{s})  {consensus?}
```

- `wd` is the three-letter weekday from the earnings date
- `session` is BMO/AMC/DMH; omit when `unknown` or null
- `days_until` is the absolute integer. Past dates render as `(N days
  ago)`. Singular `day` vs plural `days` matters.
- `consensus` is `consensus EPS $X.XX, rev $Y.YB` when available
  (Tier A). Falls back to `signal_strength: strong` or
  `signal_strength: soft` for EDGAR-only rows.

Sort each bucket by `abs(days_until)` ascending so the most urgent
print in each bucket appears first.

## CLEAR bucket — collapsed

```
CLEAR (no earnings in window):
  AMZN, GOOGL, META, MSFT
```

Sort tickers alphabetically. The user only needs to confirm
membership, not see per-ticker detail.

## UNRESOLVED bucket — explicit

```
UNRESOLVED:
  AAPL   resolver returned no events (source attempted: edgar_8k)
```

Always include the `source attempted:` parenthetical so the user can
diagnose: Tier B (`edgar_8k`) means no 8-K with items 2.02/7.01/8.01
in the window, or the CIK lookup failed. Tier A (`benzinga`) means
Benzinga ran clean but returned nothing — typical for very thinly-
covered tickers.

Never silently drop an unresolved ticker. Surfacing them is the entire
point of having the `unresolved` status — `clear` and `unresolved`
look identical in summary numbers but mean very different things for
risk management.

## Tier caveats

If `tier_caveats[]` is non-empty, append a footer:

```
Tier {tier} caveats
- {caveat 1}
- {caveat 2}
```

This always appears under any mixed-source run (some tickers on
Benzinga, others on EDGAR) and under any pure Tier B run. On a pure
Tier A run the caveats array is empty and the footer is omitted.
