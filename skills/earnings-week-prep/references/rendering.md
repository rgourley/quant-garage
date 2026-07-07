# earnings-week-prep rendering

Hybrid mode. Header + headline listing prints + earnings-blackout
section + one block per imminent print.

## Header

```
Earnings Week Prep — {AS_OF}
Watchlist: {N} tickers · Forward window: {WINDOW_DAYS}d · Drilldown top {TOP_N}
```

## Headline

```
HEADLINE
──────────
{N} prints this window:
  {TICKER:<8} {DATE} {SESSION} ({DAYS_OUT}d)
  ...
```

Or `No watchlist earnings in the next {N}d.`.

## Sections

1. `EARNINGS BLACKOUT (watchlist)` — earnings_blackout.render()
2. One `{TICKER} · {DATE} {SESSION} ({DAYS}d out)` block per print:
   - earnings_drilldown.render()
   - `--- Technical read for {TICKER} ---`
   - technical_briefing.render()

## Errors

Standard block.
