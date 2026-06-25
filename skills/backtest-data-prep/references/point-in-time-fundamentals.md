# Point-in-time fundamentals

The look-ahead bias trap. When fundamentals (revenue, EPS, book value,
operating cash flow) are joined to a price-based backtest, the join
date matters more than the values.

## The bug

A naive join uses the `report_period` (the quarter or fiscal year the
financials cover) as the join date. This is wrong. The financials for
Q4 2022 (ending 2022-12-31) were not available on 2022-12-31. They
were filed somewhere between 2023-01-26 (Apple's typical 8-K) and
2023-05-15 (the 10-Q filing deadline). A backtest that joins on
report_period is using information that didn't exist yet; the signal
contains forward-looking knowledge.

The correct join uses the **date the financials became publicly
available**: the 8-K acceptance time on SEC EDGAR, plus a 1-day
settlement to be conservative.

## How wrong does this get?

For a value factor (P/B, low P/E) the look-ahead bias is small but
non-zero: 100-200bps annualized in academic studies. The big-cap
universe's book values are revised slowly and the market mostly
prices them correctly.

For a quality factor (ROE, ROIC) the bias is bigger: 200-400bps
annualized, because earnings surprises drive a meaningful chunk of
short-term ROE variability and the look-ahead captures the surprise.

For an earnings-momentum factor (post-earnings drift, analyst
revisions) the bias is enormous: the signal is partially the surprise
itself, so using the post-revision number triples the IC.

## The available-date rule

For US-listed common stock, the canonical methodology:

1. The 8-K filing acceptance time on SEC EDGAR is the "earliest
   possible" disclosure time. This is when the press release became
   public.
2. Add 1 trading day. So if the 8-K was accepted at 16:30 ET on
   2023-01-26, the fundamentals are "available" to a backtest starting
   2023-01-27 (or later, if you want to model time for the market to
   digest the print).
3. NEVER use the `filing_date` from Massive's `/vX/reference/financials`
   endpoint as the join date. That's the 10-Q date, which is typically
   1-2 days AFTER the 8-K, sometimes a week. Using filing_date is
   conservative (you under-use information) but it's not the
   methodologically correct date; the 8-K is.

## How earnings-drilldown does it

Cross-link: [`../../earnings-drilldown/references/data-source-tiers.md`](../../earnings-drilldown/references/data-source-tiers.md)
documents the SEC EDGAR 8-K acceptance methodology in detail. Quote
from there:

> **`https://data.sec.gov/submissions/CIK{cik}.json`** for 8-K filing
> acceptance times. Free, public, no auth (just send a `User-Agent`
> header with your email per SEC fair-use policy). Filter for 8-K
> filings where the `items` field contains `"2.02"` (Results of
> Operations and Financial Condition); these are the earnings releases.

The earnings-drilldown skill verifies the methodology against AAPL's
last 8 prints: SEC 8-K acceptance times match the Benzinga press
release times to the day in every case. Acceptance is ~20:30 UTC
(16:30 ET) in EDT months, ~21:30 UTC in EST months.

## What this skill does (v1)

**Nothing.** The v1 backtest-data-prep skill emits OHLCV only. Joining
fundamentals point-in-time is a follow-on for v2, queued as a clean PR
extension. The output-schema.json reserves `fundamentals_path` for a
future fundamentals file in the same output directory.

The reason for the deliberate gap: getting fundamentals right requires
the 8-K acceptance methodology from earnings-drilldown, the CIK
mapping from `/v3/reference/tickers/{T}`, the SEC EDGAR `User-Agent`
discipline, and a clear opinion on what financial fields belong in a
"backtest-ready" fundamentals file. Bundling all of that into v1 hides
the OHLCV correctness work behind a bigger surface.

## What the consumer should do (v1)

Three options, in increasing rigor:

1. **Ignore fundamentals.** Most price-only strategies (mean
   reversion, momentum, trend) don't need them. Run the backtest
   without fundamentals; the dataset is complete for those strategies.

2. **Join with a lag.** If the backtest needs fundamentals, join
   Massive's `/vX/reference/financials` with `filing_date + 5 trading
   days` as the join date. The 5-day lag is conservative; it under-
   uses information but never look-ahead. Adequate for academic-style
   factor work where the bias direction matters more than the
   magnitude.

3. **Build the 8-K acceptance file separately.** Use the
   earnings-drilldown skill or its references to fetch SEC EDGAR
   submissions for each ticker, pull the 8-K acceptance timestamps,
   merge against Massive's financials by `filing_date - 3 days
   window`, and use `acceptance_date + 1 trading day` as the join.
   This is the right answer; it's the v2 of this skill.

The manifest take should reference this gap explicitly so the
consumer doesn't paper over it.

## Special case: restated fundamentals

Even with the 8-K acceptance date, fundamentals get restated.
SOX-era 8-K/A amendments push corrected numbers months or quarters
later. Massive's financials endpoint returns the most-recent revision,
not the originally-filed value. A truly point-in-time fundamentals
feed reconstructs the originally-filed value as of each rebalance
date.

This is hard. Even institutional fundamentals vendors (FactSet,
Compustat) charge a premium for "point-in-time" feeds because of
this. For most equity-factor work, the restatement bias is
second-order; the look-ahead from using report_period is first-order.
Fix the first-order bug, document the second-order one, move on.

## What the manifest says

In v1, the manifest line is:

```
Fundamentals: NOT INCLUDED. This dataset is OHLCV-only. For point-in-time
fundamentals, use earnings-drilldown (8-K acceptance methodology) or join
/vX/reference/financials with a 5-trading-day lag on filing_date as a
conservative substitute.
```

When v2 ships fundamentals, the line changes to document the join
method actually used (8-K + 1 day, or filing_date + 5 days, or the
consumer's choice).
