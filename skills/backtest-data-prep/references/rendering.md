# Rendering: backtest-data-prep

Dataset mode is new in this repo. The defining property: the primary
artifact is a machine-readable file on disk (parquet, in this skill's
case), not a rendered string. The rendered string is the operator-
readable companion that describes what was written.

Dataset mode is not table mode. Tables describe what's in the
analysis; datasets describe what's in a file. Don't confuse the two.

## Block order

Eight blocks, separated by blank lines:

1. Header (one line)
2. Files written
3. Universe construction
4. Corporate actions applied
5. Coverage
6. Edge cases
7. Schema (parquet columns)
8. Source endpoints
9. Take (one paragraph)

No prose intros. The reader opens the file expecting a dataset
summary; deliver one.

## Header

```
Backtest dataset: top 100 by mcap · 2022-06-25 → 2026-06-25 · 4y window
```

Format: `Backtest dataset: {universe_definition.label} · {window_start}
→ {window_end} · {Ny window}` where N is the rounded year count.

For sub-year windows: `· 3mo window`, `· 11mo window`. For exactly one
year: `· 1y window`. For non-integer years: round to the nearest;
display as `· 4y window` for 3.7-4.4 years.

## Files written

```
Files written
- backtest-2026-06-25/ohlcv.parquet                  (1,032 trading days × 100 tickers)
- backtest-2026-06-25/manifest.md
- backtest-2026-06-25/edge-cases.log
```

The path is relative to the working directory the skill was run from
(usually a directory containing the output dir). Each file gets one
line. Add the row-shape annotation in parentheses for the parquet:
`(N trading days × M tickers)` where N is `trading_days_in_window`
and M is `universe_definition.size`.

Manifest and edge-cases get no annotation (they're variable-length
text).

## Universe construction

```
Universe construction
- Top-100 by current market cap (forward-looking biased; see survivorship note below)
- Active and delisted both included for survivorship cleanliness
- Excluded: ETFs, ETNs, ADRCs, units, warrants, rights (type filter = CS only)
- Forward-fill rule: none. Missing trading days remain null (not imputed).
```

Bullet list. Four lines, in order:

1. The universe seed verbatim from `universe_definition.label`, with
   the survivorship caveat in parentheses when `survivorship_mode ==
   "biased"`.
2. Active and delisted treatment, from `survivorship_mode`.
3. Type filter, from `universe_definition.type_filter`.
4. Forward-fill / imputation policy. ALWAYS prints this line; the
   policy is "none" in v1 and the line documents it explicitly.

## Corporate actions applied

```
Corporate actions applied
- 14 splits across 8 tickers (AAPL 4:1 2020-08-31, GOOGL 20:1 2022-07-18, NVDA 10:1 2024-06-10, TSLA 3:1 2022-08-25, ...)
- 312 dividends applied (cash, not stock); price adjustment factor included as separate column
- Spinoffs: 2 detected (KD from KMB 2023-12-04, GE Healthcare from GE 2023-01-04); manual override recommended
```

Three lines: splits summary, dividends summary, spinoffs summary.

**Splits line:** count of splits in the window, count of unique
tickers, then up to 4 named examples (ticker, ratio, date), then
`...` if more. The ratio format is `{to}:{from}` for forward splits
(NVDA 10:1) and `1:{N} reverse` for reverse splits.

**Dividends line:** total count across the universe; "cash, not stock"
clarification; reminder that the adjustment factor is in the parquet
schema.

**Spinoffs line:** when zero, print `Spinoffs: none detected.`. When
nonzero, list them with `{spinoff_ticker} from {parent_ticker}
{ex_date}` and append `manual override recommended` (always
recommended in v1 because the basis split isn't auto-applied).

## Coverage

```
Coverage
- 100 tickers requested, 100 retrieved
- 99 with continuous coverage over the window
- 1 partial: ARM (IPO 2023-09-14; 188 missing days at start of window)
- 0 delisted during window (the current top-100 is a clean window for this universe)
```

Four lines:

1. Requested vs retrieved counts from `universe_stats`.
2. Continuous coverage count.
3. Partial coverage list, one ticker per line when there are 1-3;
   summary line `N partial: see edge-cases.log` when there are >3.
4. Delisted-during-window count, with a contextual aside when the
   count is zero (e.g. "the current top-100 is a clean window for
   this universe").

## Edge cases

```
Edge cases
- 12 half-day sessions (early-close holidays): preserved as normal rows with correct volume
- 2 trading halts: AAPL 2023-03-14 14:32 (LULD), GOOGL 2024-02-20 11:18 (news pending). Handled as session-low/high preservation.
- 0 ticker symbol changes within the window
```

This block summarizes by type. Half-day sessions, trading halts,
ticker changes, and other event categories appear as one bullet
each.

- Half-day sessions: count and treatment.
- Trading halts: count and up to 2 named examples; "..." if more.
  Always note treatment ("session-low/high preservation" is the v1
  default).
- Ticker changes: count and named examples.
- Data gaps not otherwise categorized: count and treatment.

When the schema's `edge_cases[]` is empty, this block prints
`Edge cases: none detected.` on one line.

## Schema

```
Schema (parquet columns)
- date, ticker, open, high, low, close, vwap, volume, transactions,
  adj_factor_cumulative, sic_code, sector
```

Single bullet with the columns comma-separated, wrapped to ~80 chars
width with a 2-space continuation indent. The order matches the
parquet's column order.

When the schema is large enough that listing columns is unhelpful,
fall back to:

```
Schema (parquet columns)
- 12 columns; see manifest.md for the full inventory with types
```

## Source endpoints

```
Source endpoints
- /v2/aggs/grouped/locale/us/market/stocks/{date} (REST fallback for flat-files 403)
- /v3/reference/tickers/{ticker} (type + sector enrichment, ~30s per 100 tickers)
- /v3/reference/splits?ticker={ticker} (corp action correctness)
- /v3/reference/dividends?ticker={ticker}
```

One bullet per distinct endpoint. The parenthetical note explains
the role; cost data (timing, calls) is helpful when known. When
the run used flat-files, the first endpoint is the S3 path; when it
used the REST fallback, note that explicitly.

## Take

One paragraph at the bottom. Verdict-style. Format:

```
Take: Dataset is point-in-time clean for OHLCV and corporate actions.
Fundamentals NOT included in this run; use earnings-drilldown or factor-
research helpers if fundamentals are needed. The top-100 universe has
no delistings in the window so the survivorship caveat is academic
here; for backtests pre-2024 use a wider seed (top-500 or full Russell)
and accept the manual delisting curation.
```

Three to five sentences. Structure:

1. The verdict: is the dataset backtest-ready (yes/no/qualified yes).
2. What's NOT in the dataset (fundamentals, intraday, sector-neutral
   panels, etc.) and what skill or workflow handles that gap.
3. The most relevant caveat for the specific universe/window combo.
   For windows post-2024 with a top-N seed, this is usually the
   survivorship-bias-is-academic line. For pre-2024 windows, it's
   the opposite warning.

No em-dashes; this repo convention uses commas, parentheses, periods.

When `corporate_actions_applied.spinoffs[]` is nonempty, the take
adds a sentence: "Manual spinoff review recommended for {N} cases
in window."

When `edge_cases[]` contains any IPO partial coverage, the take adds
a sentence noting which names are partial; the consumer who needs
continuous coverage may drop them.

## What UI devs do instead

A custom UI consumes the JSON and renders:

- The files-written block as a directory tree with clickable links
  to download each file
- The universe block as a stat tile (size, survivorship badge)
- The corporate actions block as a timeline visualization
- The edge cases block as a filterable table
- The schema block as the parquet inspector (column types, null
  rates, sample values)
- The take as a prominent verdict banner

The rendered format here is the Claude Code default.

## Why this format

QuantConnect's data quality report, Bloomberg's BDH audit, and
Refinitiv's pre-flight check all converged on the file-inventory +
universe + corp-actions + coverage + edge-cases structure because:

- The files-written block is the deliverable manifest (what got built)
- The universe block is the methodology (how it got built)
- The corporate actions block is the correctness audit (what was
  reconciled)
- The coverage block is the completeness audit (what's missing)
- The edge cases block is the anomaly audit (what's weird)
- The schema block is the API contract (what the consumer reads)
- The sources block is the citation trail (where it came from)
- The take is the verdict (do I trust this)

A dataset-mode skill that ships another asset class (options,
futures, crypto) swaps the schema and corp-action sections but keeps
the block structure.

## Why dataset mode is new

The first four output modes (note, stream, table, exception-report)
all produce a human-readable artifact as the primary deliverable.
Dataset mode is the first mode where the primary deliverable is
machine-readable and the rendered string is the companion.

The structural difference: dataset mode's rendered output ALWAYS
references the on-disk artifact ("files written: ohlcv.parquet"). A
table-mode skill doesn't say "open the JSON to see the rest"; the
table IS the output. A dataset-mode skill says "the parquet is the
output, this is what's in it."

That distinction lets the consumer pick the right tool:
- Discretionary analyst reading the rendered summary: gets the
  verdict, opens the parquet to chart.
- Quant programmer wiring the dataset into a backtester: reads the
  JSON for the schema columns, loads the parquet, ignores the
  rendered summary entirely.

Both audiences served by one run.
