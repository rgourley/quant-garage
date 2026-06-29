# Rendering: relative-strength

The skill emits canonical JSON matching `output-schema.json`. This
reference describes how that JSON renders in table mode.

Table-mode conventions in this repo are inherited from
[`../../universe-builder/references/rendering.md`](../../universe-builder/references/rendering.md);
this file documents the relative-strength-specific layout: header,
ranked rows, leader / laggard footer.

## Block order

Four blocks, separated by blank lines:

1. Header (two lines)
2. Ranked table (one row per ticker)
3. Leader / laggard summary
4. Caveats

No prose intros. The reader opens the file expecting a ranking; deliver
one.

## Header

```
Relative Strength vs SPY — 2026-06-29
Watchlist: 10 tickers · Windows: 5/20/60/120 days
```

Line 1: `Relative Strength vs {benchmark} — {as_of}`.
Line 2: `Watchlist: {n_tickers} tickers · Windows: {a/b/c/...} days`.

When `include_sectors == true`, line 2 reads:

```
Watchlist: 21 tickers (incl. SPDR sectors) · Windows: 5/20/60/120 days
```

## Ranked table

Sorted by `composite_rs_percentile` descending. Null composites sort
last (they sit at the bottom of the table). Monospaced columns. One
RS column per window, plus trend and composite percentile.

```
Ticker     5d RS    20d RS    60d RS   120d RS   Trend              Comp %ile
─────────────────────────────────────────────────────────────────────────────
NVDA      +142bp    +380bp    +720bp   +1850bp   stable_leader            92
AVGO       +95bp    +210bp    +480bp   +1020bp   stable_leader            85
AMD        +60bp    +180bp    +320bp    +680bp   improving                71
MU         +25bp    +145bp    +210bp    +420bp   stable_leader            58
KLAC       -10bp     +85bp    +120bp    +250bp   deteriorating            42
LRCX       -45bp     +20bp     +90bp    +180bp   deteriorating            33
AMAT       -30bp     -50bp     -10bp     +50bp   mixed                    28
TXN        -55bp    -120bp    -180bp    -250bp   stable_laggard           22
QCOM       -70bp    -190bp    -310bp    -480bp   stable_laggard           15
INTC       -85bp    -290bp    -540bp   -1100bp   stable_laggard            8
```

Columns:

- `Ticker`: from `results[].ticker`. Left-aligned, width 8.
- `{N}d RS`: from `results[].rs_by_window["{N}d_bps"]`. Signed,
  integer, suffix `bp`. Right-aligned. Render `n/a` when null.
- `Trend`: from `results[].trend_label`. Left-aligned. The five
  values fit in 18 chars (`stable_laggard` is 14).
- `Comp %ile`: from `results[].composite_rs_percentile`. Integer
  (rounded), right-aligned. Render `n/a` when null.

## Leader / laggard summary

```
Leaders:  NVDA, AVGO, AMD
Laggards: INTC, QCOM, TXN
```

From `ranking.leaders_top_3` and `ranking.laggards_bottom_3`. Two
lines, comma-separated. Leaders first (descending order from the top
of the table). Laggards from the bottom up, so the weakest name
appears first.

When the watchlist has fewer than 3 ranked names, the lists shrink
naturally (e.g., a 2-name watchlist surfaces 2 leaders and 2
laggards, which will overlap).

## Caveats

```
Caveats:
- RS is past-return relative to benchmark; not predictive on its own. Pair with regime read.
- AMAT: only 95 bars for 120d window; RS over that window reported as null.
```

From `tier_caveats`. One bullet per item. The "not predictive" line
is always present.

## Sort order

Always by `composite_rs_percentile` descending, nulls last. The order
of `results[]` in the JSON already reflects this sort, so the renderer
walks the array in order.

For sub-rankings (e.g., "show me improving names only"), a UI dev
filters on `trend_label` against the JSON and re-renders. The default
rendered table doesn't split by trend; that's a UI extension.

## What UI devs do instead

A custom UI consumes the JSON and renders a sortable table with
clickable column headers (sort by 5d RS, then 20d, then composite),
a trend filter, and a per-ticker mini sparkline of the close vs
benchmark close. The rendered table here is the Claude Code default.

## Why this format

The single-table layout matches what Stockcharts, Finviz, and the
Bloomberg WATC function present for watchlist relative strength: one
row per name, several columns of period returns or RS, sorted by
the most recent or composite measure. A trader scanning a watchlist
wants to see the whole list ranked, not a per-name detail page.

The composite percentile column is the headline; the per-window RS
columns are the supporting evidence. The trend label is the "which
way is it moving" annotation. The leader / laggard footer is the
"if you only read one thing" callout.
