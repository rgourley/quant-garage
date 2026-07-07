# morning-brief rendering

Hybrid mode. Header + headline block + 3 titled sections.

## Header

```
Morning Brief — {AS_OF}
Watchlist: {N} tickers   (skip if no watchlist)
```

## Headline

```
HEADLINE
──────────
Regime:     {LABEL_UPPERCASE}
Today:      {EVENT_NAME} ({TIME} ET, {IMPACT}), ...
  {TICKER}: {HEADLINE_100_CHARS} ({SENTIMENT})
  ...  (up to 5 news items)
```

If `today_events` is empty, print `Today: no scheduled macro releases`.

## Sections

1. `MACRO REGIME`
2. `MACRO CALENDAR (2d)`
3. `WATCHLIST NEWS`

Skip `WATCHLIST NEWS` when no watchlist passed.

## Errors

Standard block.
