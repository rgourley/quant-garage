# Rendering: 8-k-scanner

The skill emits canonical JSON matching `output-schema.json`. This
reference describes how that JSON renders in note mode.

## Block order

1. Header (identity + counts + optional category filter)
2. Signal-bucket summary (one line)
3. Filings, grouped by signal bucket in priority order
4. Take + caveats

## Header

```
8-K scan: NVDA,RKLB,AAPL,MSFT · 30d lookback · 4 filings (12 disclosure rows)
By signal: M&A / Strategic: 1 · Leadership change: 1 · Earnings / Guidance: 2
```

Line 1: comma-separated tickers (truncated to 8 with `...+N` when the
list is longer), lookback window, filing count, raw disclosure-row
count.
Line 2: bucket counts in priority order (only buckets with at least
one filing appear).

When the caller passed `--categories`, a filter line appears between
1 and 2:

```
Filter: primary_category in [strategic_transactions, leadership_and_governance]
```

## Filings

Filings render grouped by `headline_bucket`, one bucket header per
transition, all filings within a bucket sorted by `filing_date`
descending. Each filing block:

```
[M&A / Strategic]
  2026-06-29 · RKLB · accession 0001753926-26-001085
    · strategic transactions > deal agreements > merger agreement
      "On June 28, 2026, Rocket Lab Corporation ... entered into an
       Agreement and Plan of Merger with Iridium Communications Inc..."
    · strategic transactions > deal agreements > acquisition agreement
      "..."
      link: https://www.sec.gov/Archives/edgar/data/1819994/...
```

Structure:
- Bucket header in `[brackets]` on its own line.
- Filing line: `{filing_date} · {tickers} · accession {accession_number}`.
  Tickers truncated to first 3 (rare edge case: cross-listed
  securities). CIK fallback when no tickers are mapped.
- One or more Item lines: `· {primary} > {secondary} > {tertiary}`
  humanized (underscores replaced with spaces).
- Supporting text quoted on the next line, indented, truncated to
  ~220 chars.
- Filing URL on the last line (indented).

## Take + caveats

```
Take: 1 strategic-transaction filing in the window (top of the report).
1 leadership-change filing.

Caveats:
- 8-K disclosures come from Massive's pre-parsed taxonomy...
```

Take pieces together whichever buckets fired, in priority order.
When no high-signal buckets fired, Take reads: "No M&A, restatement,
or leadership signal in the window. The scan surfaced routine 8-K
items only."

## Empty case

When `n_filings == 0`:

```
8-K scan: NVDA · 30d lookback · 0 filings (0 disclosure rows)

- No 8-K disclosures returned for NVDA in the last 30 days.
```

## What UI devs do instead

A custom UI consumes the JSON and renders:

- A calendar heatmap of filings across the watchlist keyed on
  signal bucket color.
- A per-ticker card carousel with the top-3 signal-bucket filings
  and a "see all" drawer.
- Direct integration with `event-study` to overlay the price
  reaction on each filing date.
- A quick-filter by primary_category chip row above the results.

The rendered note here is the Claude Code default.
