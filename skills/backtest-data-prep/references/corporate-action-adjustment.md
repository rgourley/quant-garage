# Corporate action adjustment

How splits and dividends are reconciled in the OHLCV parquet. Two
methods are documented; v1 implements price-only.

## The two methods

**Price-only adjustment.** Multiply pre-action OHLC by the split
ratio so the time series is continuous across the action date. The
recorded price on, say, 2022-08-25 (TSLA 3-for-1) is the post-split
$\$$300 close, and every TSLA close before that date is divided by 3 so
the chart is smooth. Dividends are NOT subtracted; the ex-date drop is
preserved as a discontinuity.

**Total-return adjustment.** Same as price-only, plus on each ex-date
the dividend amount is subtracted from the prior close before the
adjustment factor is computed. The chart shows the dividend
"reinvested," so total return matches a buy-and-hold tracker's NAV.
This is the right convention for performance attribution; it's the
wrong convention for charting daily returns to the eye (the ex-date
discontinuity is informative).

The skill defaults to **price-only** because:

1. The downstream backtester is the place to decide how to handle
   dividends (reinvest into cash, into a position, ignore). The
   dataset should not pre-commit.
2. The cumulative adjustment factor is emitted as a separate column
   (`adj_factor_cumulative`), so the consumer who wants total-return
   has the building blocks.
3. Total-return adjustment requires the dividend amounts as point-in-
   time (the announced amount, not subsequently revised). Massive's
   dividend feed exposes the announced amount, but verifying point-in-
   time for special dividends is a separate audit.

## What Massive does by default

`/v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true` returns
**split-adjusted** prices. NVDA's close on 2024-06-09 (the day before
the 10:1 split) was reported as $\$$120.88 in the live market; the grouped
endpoint with `adjusted=true` returns it as $\$$120.88. NVDA's close on
2024-06-10 was reported as $\$$1,208.88 (because the new shares started
trading at 1/10 the price); the grouped endpoint returns it as $\$$120.88.
The time series is continuous.

The flat-files day-aggregate schema is the same: split-adjusted.

This means the skill does NOT re-apply the split ratio to the OHLC
columns. Re-applying would double-adjust and produce nonsense. The
skill emits the cumulative adjustment factor as a separate column so
the consumer who wants the raw (un-adjusted) print can recover it by
multiplying: `raw_close = close * adj_factor_cumulative`.

For NVDA on 2022-06-25, `adj_factor_cumulative = 10` (one 10-for-1
split between then and the run date) and `close = $16.50` (the
post-2024-split-adjusted price). The raw close on the day was $\$$165.
The consumer who wants raw prints multiplies; the consumer who wants
adjusted reads directly.

**Verify before doing your own adjustment.** Spot-check NVDA's close on
2024-06-10 in your output. If it's near $\$$120 and the prior day is
also near $\$$120, the source is split-adjusted (correct). If 2024-06-09
is near $\$$1,200 and 2024-06-10 is near $\$$120, you got raw prints and
need to apply the factor yourself. The flat-files schema and the REST
`adjusted=true` flag should both produce adjusted output; deviation is
a bug, not a feature.

## Computing the cumulative factor

For each ticker, walk the split feed (`/v3/reference/splits?ticker=T`)
in chronological order. For each split with `execution_date` AFTER the
window end (so it doesn't affect rows inside the window), accumulate:

```
factor *= split_to / split_from
```

Then walk backwards through the window; for each split with
`execution_date` inside the window, accumulate the factor as the
split's date is crossed (going backward in time). Bars before the
split date carry the running factor; bars on or after carry the
remaining factor.

Worked example for NVDA on a 2022-06-25 → 2026-06-25 window:

- NVDA had a 10:1 split on 2024-06-10 (inside the window).
- Bars BEFORE 2024-06-10: `adj_factor_cumulative = 10.0` (raw_close =
  adjusted_close * 10).
- Bars ON OR AFTER 2024-06-10: `adj_factor_cumulative = 1.0` (raw =
  adjusted).

If there were another split after the window (none for NVDA), it
would multiply the factor on every bar in the window.

For a name with multiple splits inside the window (rare for the
top-100), the factor steps down at each split date.

## Reverse splits

A 1-for-4 reverse split is `split_from=4, split_to=1`. The
`adj_factor_cumulative` for bars before the reverse split is 0.25
(raw_close = adjusted_close * 0.25, i.e. raw prices were 4x lower).

The `ratio_display` field in the schema renders these as
`"1:4 reverse"` so the manifest reader doesn't have to do the math.

## Dividends and the price-only choice

Cash dividends are emitted via `/v3/reference/dividends?ticker=T` with
`cash_amount`, `ex_dividend_date`, `pay_date`, and `dividend_type`
(`CD` for cash, `SC` for special cash, `LT` for long-term capital
gains, `SO` for stock dividend / spinoff).

In price-only mode, the dividend has no effect on the OHLCV columns
beyond the natural ex-date drop (which is preserved as a real
discontinuity). The `corporate_actions_applied.dividends_count` field
records the count for transparency; individual amounts are NOT
emitted as columns in v1 because the dataset is OHLCV-only.

A future PR can add a `dividend_amount` column (zero for non-ex-dates,
the amount on ex-dates) so the consumer can implement total-return
themselves. v1 leaves this as an explicit gap.

## Spinoffs

Massive's dividend feed records spinoffs as `dividend_type = "SO"`.
The cash_amount is null and a separate field documents the new
ticker, when known. The skill flags these in
`corporate_actions_applied.spinoffs[]` with
`manual_override_recommended = true` because:

1. The basis split (what fraction of the parent's cost basis goes to
   the spinoff) is announced by the company, not the data feed.
2. The post-spinoff price drop on the parent is real; without the
   basis adjustment, a momentum signal will trigger sell on the
   parent on the ex-date for the wrong reason.
3. The spinoff ticker may or may not be in the universe; if it is,
   the new entity's price history starts on the ex-date with no
   priors.

Worked example for GE Healthcare (GEHC) from GE on 2023-01-04:

- GE close on 2023-01-03 (last cum-dividend): $84.04
- GE close on 2023-01-04 (ex-spinoff): $74.59 (~11% drop, the basis
  going to GEHC)
- GEHC opened on 2023-01-04 at ~$56 with no prior history

The skill flags `spinoffs[{parent_ticker: "GE", spinoff_ticker:
"GEHC", ex_date: "2023-01-04"}]` and the manifest take recommends
manual review. The OHLCV parquet shows GE's ex-date drop as a real
bar; a backtest that doesn't know about the spinoff will read it as
a normal price drop.

## What the consumer should do

Read the manifest. Look at `corporate_actions_applied.spinoffs`. For
each, decide whether the strategy needs a basis adjustment or whether
treating the ex-date as a normal price drop is acceptable. Most
mean-reversion and trend strategies are insensitive to spinoffs;
factor strategies that compute book value or returns over the
ex-date care a lot.

The skill's job is to surface the events, not to make the call.

## What does NOT get adjusted

- Volume: raw volume on the un-adjusted share basis. A 10-for-1 split
  multiplies volume by 10 on the split date going forward; the
  consumer who wants split-adjusted volume divides by
  `adj_factor_cumulative`.
- Transactions count: raw count of unique trades, not affected by
  splits.
- VWAP: computed from raw prints, so it carries the same adjustment
  status as close (split-adjusted by source).
