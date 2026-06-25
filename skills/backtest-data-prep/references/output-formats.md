# Output formats

Why parquet, what's in the schema, how the consumer reads it.

## Parquet vs CSV

For any dataset over 100k rows, use parquet. The tradeoffs:

|                | CSV       | Parquet (Snappy) |
|----------------|-----------|-------------------|
| Bytes on disk  | ~10x      | 1x (baseline)     |
| Read speed     | ~10x slow | 1x (baseline)     |
| Column-pull    | full scan | column-pruned     |
| Schema record  | absent    | embedded          |
| Types          | strings   | native            |
| Diff-friendly  | yes       | no                |

For a top-100 x 4-year dataset (~100k rows x 12 columns), parquet
lands around 6-12 MB; CSV would be 60-120 MB. The read time gap is
larger than the disk gap because parquet skips parsing.

For a top-500 x 5-year dataset (~625k rows), the gap is decisive:
parquet ~40 MB and 2-second read, CSV ~400 MB and 30-second read.

The downstream backtester (pandas, polars, dask, R's arrow, Julia's
Parquet.jl) reads parquet natively in every modern language. The
language-agnostic argument is the dispositive one.

For datasets under 10k rows (a small custom-ticker watchlist over a
quarter), CSV is fine. The skill always emits parquet for
consistency; the consumer who needs CSV converts.

## Compression

Snappy is the default. It's the right tradeoff for backtest IO:

- LZ4: faster decompression, smaller compression ratio (~10-15%
  larger files). Good for streaming pipelines.
- Snappy: balanced (slightly slower than LZ4, smaller files). The
  pandas+pyarrow default.
- Gzip: smaller files (~20% smaller than Snappy), much slower
  decompression. Good for archival.
- Zstd: best ratio, modern, requires recent pyarrow. Slightly
  experimental in some downstream tools.

The skill uses Snappy because pyarrow ships it by default and every
mainstream parquet reader supports it. Reading 6 MB Snappy is
indistinguishable from reading 5 MB Zstd; pick the boring choice.

## Schema design

The parquet schema is denormalized: one row per (ticker, trading
day). The columns:

```
date                       date32        # trading day, US Eastern boundary
ticker                     string        # uppercase, no exchange prefix
open                       float64       # split-adjusted open
high                       float64       # split-adjusted high
low                        float64       # split-adjusted low
close                      float64       # split-adjusted close
vwap                       float64       # volume-weighted average (raw prints)
volume                     int64         # raw share volume
transactions               int64         # raw count of unique trades
adj_factor_cumulative      float64       # multiply close by this for raw close
sic_code                   string        # SEC industry code (4 digits)
sector                     string        # human-readable, from sic_description
```

Naming convention: lowercase, underscore-separated, no abbreviations.
This matches pandas convention and reads cleanly in every language.

**Why one row per (ticker, day) instead of pivot?** Long-format is
the only sane choice when the universe has missing days. A pivot
(date as index, ticker as columns) requires forward-fill or
densification rules baked into the file format; long-format pushes
those decisions to the consumer. Every modern backtester operates on
long-format DataFrames natively (pandas' `.groupby('ticker')`,
polars' `.partition_by('ticker')`, vectorbt's wide-format converter).

**Why include `sic_code` and `sector` per row?** Denormalization
costs ~5% disk space (most rows have the same value across the
ticker's history) and saves a join on every read. The consumer who
wants to filter "tech sector only" runs `df[df.sector == 'Technology']`
without loading a separate metadata file.

**Why no `name` (company name) column?** The name changes over time
(FB → META, Square → Block) and isn't a backtest signal. The consumer
who needs names joins against the manifest or against
`/v3/reference/tickers` at read time.

## Reading the parquet

The canonical read in pandas:

```python
import pandas as pd
df = pd.read_parquet("backtest-2026-06-25/ohlcv.parquet")
df["date"] = pd.to_datetime(df["date"])
# Long-format: rows = (date, ticker) pairs
print(df.head())
```

To pivot to a wide-format close panel (date x ticker), one line:

```python
panel = df.pivot(index="date", columns="ticker", values="close")
```

The pivot drops rows where any ticker has NaN; for a survivorship-
clean dataset that's the wrong default. Use:

```python
panel = df.pivot(index="date", columns="ticker", values="close")
# panel now has NaN where a ticker didn't trade that day; backtest decides
```

To un-adjust a single ticker's close to raw prints:

```python
nvda = df[df.ticker == "NVDA"].copy()
nvda["raw_close"] = nvda["close"] * nvda["adj_factor_cumulative"]
```

## Schema versioning

The parquet schema is recorded in the manifest as `schema_columns`. A
v2 schema change (adding `dividend_amount`, `fundamentals_path`,
etc.) bumps `schema_version` in the manifest; existing consumers
break gracefully because the new columns are additive.

The skill does NOT version-stamp the parquet itself (no embedded
"backtest-data-prep v1.0" metadata). The manifest is the version
record. If you need to read a parquet without its manifest, the
column inventory is self-describing.

## Manifest format

The manifest is markdown, not JSON. Three reasons:

1. **Diffability.** A git-versioned dataset directory shows
   meaningful diffs across runs (the universe grew by 2 names, the
   window extended a quarter, a new spinoff was detected).
2. **Read-without-tools.** A quant pulling the parquet onto a remote
   server can `cat manifest.md` to see what they're loading without
   running pandas.
3. **Re-rendering.** The schema's `take`, `universe_definition`, and
   `corporate_actions_applied` are also in the JSON; the manifest is
   the rendered companion, same as Layer 2 for note-mode skills.

The manifest sections, in order:

1. Header (one line: dataset name, window, universe, run timestamp)
2. Universe construction (label, seed, type filter, survivorship,
   note)
3. Files written (relative paths, row/column counts, sizes)
4. Window (start, end, trading days)
5. Corporate actions applied (splits, dividends count, spinoffs)
6. Coverage (continuous, partial, delisted counts)
7. Edge cases (one line per case with type, ticker, detail)
8. Schema (parquet columns with one-line descriptions)
9. Sources (endpoints called)
10. Take (the operator-readable verdict)

The renderer in the implementation file produces this; see
`references/rendering.md` for the full format spec.

## Edge cases log

`edge-cases.log` is line-delimited JSON (jsonl). One edge_case per
line. The format matches the schema entries verbatim:

```
{"type":"ipo_partial_coverage","ticker":"ARM","date":"2023-09-14","detail":"IPO during window; 313 trading days missing at start","missing_days":313}
{"type":"trading_halt","ticker":"AAPL","date":"2023-03-14","detail":"LULD halt 14:32 ET; session bar preserved"}
```

JSONL is the right shape for a log: append-friendly, line-oriented,
parseable by every language without a JSON-array bracket dance. A
consumer who wants Python dicts:

```python
import json
edge_cases = [json.loads(line) for line in open("edge-cases.log")]
```

A consumer who wants pandas:

```python
import pandas as pd
edge = pd.read_json("edge-cases.log", lines=True)
```
