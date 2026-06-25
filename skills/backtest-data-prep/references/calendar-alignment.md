# Calendar alignment

Trading days, calendar days, holidays, half-days. Why missing rows
aren't errors. Resampling rules.

## The core distinction

A backtest uses **trading days**, not calendar days. The US equity
market trades roughly 252 days per year. The other 113 (weekends,
holidays, early-close days when the market is "open" for a partial
session) are not zero-volume; they're not in the dataset at all.

A row in the parquet exists if and only if the market traded that
day. A missing date for a ticker means one of two things:

1. The market was closed (Saturday, Sunday, holiday). All tickers
   are missing.
2. The ticker had no trading activity (very rare for top-N names,
   common for low-liquidity small caps). The market was open, but
   this name didn't print.

The skill emits the dataset with `(date, ticker)` rows only on
trading days. Resampling to a calendar grid is the consumer's choice;
the dataset doesn't make it.

## US equity market calendar

The NYSE / NASDAQ trading calendar has nine full holidays per year
when the market is closed:

- New Year's Day
- MLK Day (third Monday of January)
- Presidents' Day (third Monday of February)
- Good Friday (the only floating Christian holiday observed)
- Memorial Day (last Monday of May)
- Juneteenth (June 19; added 2022)
- Independence Day
- Labor Day (first Monday of September)
- Thanksgiving (fourth Thursday of November)
- Christmas

Plus the half-day sessions, which trade until 1pm ET instead of 4pm
ET:

- Day after Thanksgiving (Black Friday)
- Christmas Eve (when it falls on a weekday)
- Independence Day Eve (when it falls on a weekday)

Half-day sessions ARE trading days. The bar exists. Volume is roughly
30-40% of a full day. The skill does NOT flag half-days as edge cases;
they're normal sessions with normal bars. The consumer who wants to
filter them out can join against an external calendar.

The skill's window-to-trading-days conversion uses a naive
"weekday and not in a holiday list" check. For the 2022-06-25 → 2026-06-25
window, the trading day count is ~1,007 (252 per year over 4 years,
minus a few). Missing some holidays in the lookup just produces empty
day-bucket fetches that get dropped; it's robustness-of-the-empty-set,
not a correctness bug.

## Why missing rows aren't errors

A naive data-loader treats `df.isna().any()` as a quality problem. For
backtest OHLCV, a missing row is information:

- ARM IPO'd 2023-09-14. Rows for ARM before that date should NOT
  exist. A loader that fills NaN with zero, or with the next
  available price, is wrong; the position-sizing logic will think
  ARM was tradeable when it wasn't.
- SVB delisted 2023-03-17. Rows for SVB after that date should NOT
  exist. A loader that forward-fills the last close is wrong; the
  backtest will hold SVB through a phantom recovery.
- A trading halt mid-day still produces a session bar (the
  pre-halt and post-halt activity sum into open/high/low/close). A
  halt that spans an entire day is rare but produces zero volume.
  Zero volume is meaningful; don't fill it.

The skill emits NaN for any (date, ticker) cell where the underlying
session had no bar. The manifest documents this:

```
Forward-fill rule: none. Missing trading days remain null (not imputed).
```

The consumer's backtester decides whether to fill, forward-fill,
skip, or treat missing as untradeable. The dataset doesn't pre-commit.

## IPOs and the partial-coverage flag

When a name's `list_date` is INSIDE the window, the parquet has fewer
rows for that ticker than for the universe baseline. The skill flags
this in `edge_cases[]` as `ipo_partial_coverage` with the count of
missing days at the start of the window.

ARM example (window 2022-06-25 → 2026-06-25, ARM list_date 2023-09-14):

```
edge_cases: [
  {
    "type": "ipo_partial_coverage",
    "ticker": "ARM",
    "date": "2023-09-14",
    "detail": "IPO during window; 313 trading days missing at start",
    "missing_days": 313
  }
]
```

The consumer who needs continuous coverage drops ARM from the
universe; the consumer running a "trade only after IPO+90 days"
strategy keeps it.

## Delistings and the trimmed-coverage flag

When a name's `delisted_utc` is INSIDE the window, the parquet has
fewer rows for that ticker than for the universe baseline. The skill
flags this in `edge_cases[]` as `delisting_during_window` with the
delisting date.

SVB example (window 2023-01-01 → 2023-12-31, SVB delisted 2023-03-17):

```
edge_cases: [
  {
    "type": "delisting_during_window",
    "ticker": "SIVB",
    "date": "2023-03-17",
    "detail": "Delisted during window; last bar 2023-03-10 (halted 2023-03-10, formally delisted 2023-03-17)",
    "missing_days": 198
  }
]
```

The backtest sees SIVB's price collapse and trades around it; the
delisting itself is not a price event but a "no more rows" event.

## Resampling

Backtesters that work in calendar time (weekly, monthly) resample
from the trading-day parquet. The canonical resample:

```python
weekly = df.set_index("date").groupby("ticker").resample("W-FRI").last()
monthly = df.set_index("date").groupby("ticker").resample("ME").last()
```

`W-FRI` aligns weeks to Friday (the standard finance week boundary).
`ME` aligns months to the last calendar day; for trading days, use
`BME` (business month end) to align to the last trading day of the
month.

The skill does NOT resample. The parquet is daily; resampling is the
backtester's job because the right resample depends on the strategy
(some momentum strategies use month-end-aligned signals; some
mean-reversion strategies want straight calendar weeks; option
strategies care about expiration cycles).

## Time zones

All dates in the parquet are NAIVE dates (no time component, no
timezone). The implicit time zone is US Eastern, the trading day
boundary. A "2026-06-23" row represents the trading session that ran
09:30 to 16:00 ET on that calendar date.

The `run_at` timestamp in the manifest is UTC.

## Window-end semantics

`--window 2022-06-25..2026-06-25` is inclusive on both ends. If
2026-06-25 is a trading day, it has a row. If it's a weekend or
holiday, the last row is the most recent prior trading day.

The schema records `window_end` as the requested calendar date, NOT
the last trading day actually pulled. The trading day count
`trading_days_in_window` is the authoritative count of session-rows.

This is by design: re-running with the same window flag produces the
same dataset, regardless of whether the boundary day was a session.
A pipeline that depends on the boundary being a session needs to
pass a known-session date.
