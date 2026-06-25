---
name: backtest-data-prep
description: Build a clean, point-in-time, ready-to-backtest OHLCV dataset for a US equity universe across an arbitrary date window. Emits parquet plus a manifest plus an edge-case log, with corporate actions reconciled, survivorship treatment documented, holidays and half-days preserved correctly, and any IPO partial coverage or trading halts flagged. The dataset is the artifact a downstream Python/R/Julia backtester consumes; the rendered summary is the operator-readable companion. First skill in the suite that ships dataset output mode.
---

# backtest-data-prep

You hand over a universe definition, a date window, and an output directory.
The skill emits a clean OHLCV+volume parquet plus a manifest plus an
edge-case log. The downstream consumer is a backtester (your Python loop,
QuantConnect, vectorbt, zipline-reloaded, etc.), not a human reader.

The skill's value is correctness. Quants spend 80% of their time on data
prep, and the most common backtest bugs come from five sources:

1. **Survivorship bias.** Backtesting against today's top-500 silently
   excludes 2022-2024 failures (FRC, SVB, BBBY, SI). The remaining
   cohort outperforms the true historical population because the
   losers are gone.
2. **Look-ahead bias on fundamentals.** Using the latest revised
   consensus instead of point-in-time. The signal contains
   forward-looking information that wasn't available at the rebalance.
3. **Corporate action mis-adjustment.** Pre-split prices not adjusted,
   or adjusted using the wrong factor.
4. **Holiday and half-day handling.** Missing days treated as errors or
   filled wrong.
5. **Inconsistent calendar alignment.** Calendar dates vs trading
   dates, US vs global market hours.

The skill addresses all five and documents the treatment in the manifest.

## When to invoke

- A quant says "I need clean OHLCV for the top 500 from 2022 to today
  for my mean-reversion backtest"
- A researcher needs a survivorship-clean dataset for a paper
- A factor-research extension wants a longer window than the in-memory
  panel can carry
- The user says "prep a backtest dataset", "build me a clean OHLCV
  parquet", "I need point-in-time prices for a universe"

## What you need

- A universe (CLI flag: `top100`, `top500`, `top1000`, `sp500`,
  `custom:path/to/tickers.csv`)
- A window (`--window YYYY-MM-DD..YYYY-MM-DD`)
- An output directory (`--out path/to/dataset/`)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter plan minimum (flat files included with any paid plan;
  flat-files entitlement probed and REST fallback used when not granted)

## Expected runtime

A 4-year top-100 run:
- Flat-files cold: ~5-10 minutes
- REST grouped cold: ~10-15 minutes
- Warm (parquet day-cache hit): under 60 seconds

A 5-year top-500 run is roughly 5x the REST time and 2-3x the
flat-files time (the bottleneck shifts from day-bucket fetch to
per-ticker corp action calls).

## What you get back

Three files in the output directory, plus a rendered summary printed
to stdout.

**Files:**
- `ohlcv.parquet`: one row per ticker x trading day. Columns documented
  in [`references/output-formats.md`](./references/output-formats.md).
- `manifest.md`: human-readable run record. Universe definition, window,
  survivorship treatment, corp action methodology, source endpoints,
  every parameter that affects reproducibility, run timestamp.
- `edge-cases.log`: line-delimited JSON. One entry per edge case
  detected: trading halt, IPO partial coverage, delisting during
  window, ticker change, data gap.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Emitted to stdout alongside the rendered summary so downstream tools
that wrap the CLI can parse the run metadata without re-reading the
manifest.

**Layer 2: rendered dataset summary** in the style of a clean-data
report (think QuantConnect's data quality report or Bloomberg's BDH
audit). See [`references/rendering.md`](./references/rendering.md).
Files written, universe construction, corporate actions applied,
coverage, edge cases, schema, sources, take. The take answers the
question a quant asks at the top of every backtest: "is this dataset
actually clean enough to trust?"

## How it works

1. **Construct the universe** per [`references/survivorship-handling.md`](./references/survivorship-handling.md).
   For the default top-N seeds, pull `/v3/reference/tickers` (active
   and delisted both), enrich with market cap from
   `/v3/reference/tickers/{ticker}`, rank, and keep the top N. The
   universe label records whether the seed is forward-looking
   biased (it usually is for current top-N).
2. **Filter to common stock.** Reference-endpoint `type` field; drop
   ETFs, ETNs, ADRCs, units, warrants, rights. Documented in the
   manifest because the consumer may want to override.
3. **Pull daily aggregates** for the window. Flat-files preferred
   (`s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz`,
   parallelized 16 workers). REST fallback when entitlement is missing:
   `GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`,
   one call per trading day. Pattern matches `factor-research`. See
   [`../massive-flat-files/SKILL.md`](../massive-flat-files/SKILL.md)
   entitlement section.
4. **Pull splits and dividends** per
   [`references/corporate-action-adjustment.md`](./references/corporate-action-adjustment.md).
   `GET /v3/reference/splits?ticker={T}` and
   `GET /v3/reference/dividends?ticker={T}`. Massive's grouped aggs
   are split-adjusted by default (`adjusted=true`), so the skill does
   NOT re-apply the split ratio to OHLC; it does emit a cumulative
   adjustment factor as a separate column so the consumer can
   un-adjust if their backtester wants raw prints.
5. **Pull ticker reference** for sector enrichment. One call per
   ticker, parallelized. Adds `sic_code` and `sector` columns to the
   parquet.
6. **Detect edge cases.** IPO partial coverage (window starts before
   `list_date`), delisting during window, ticker changes (the
   reference endpoint doesn't link old to new, so flag is best-effort),
   and trading halts (LULD pauses surface as session OHLC with
   continuous-trade range; not always detectable from daily aggs).
   Holidays and half-days are NOT edge cases; they're handled by the
   calendar (see [`references/calendar-alignment.md`](./references/calendar-alignment.md)).
7. **Emit parquet, manifest, edge-cases log.** Parquet uses Snappy
   compression by default (the right tradeoff for backtest IO; see
   [`references/output-formats.md`](./references/output-formats.md)).
   Manifest is markdown so it diffs cleanly in git.

## Foundations used

- [`massive-flat-files`](../massive-flat-files) for the bulk daily
  aggregates pull (S3 auth, path layout, parallelism).
- [`massive-api-patterns`](../massive-api-patterns) for REST auth, the
  grouped-daily fallback, and the throttle pattern when pulling
  per-ticker splits/dividends.

## Output mode: dataset

Dataset mode is new in this repo (this is the fifth output mode,
joining note, stream, table, exception-report). The defining property:
the primary artifact is a machine-readable file on disk, not a
rendered string. The rendered string is the operator-readable
companion that describes what was written.

The format follows the convention QuantConnect and Bloomberg BDH have
converged on for data quality reports: file inventory, universe
construction, corporate actions, coverage, edge cases, schema,
sources, take. See [`references/rendering.md`](./references/rendering.md)
for the canonical format rules.

## Endpoints used

- `s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz`:
  one file per trading day; ~250 files per year. Parallelize 16
  workers. Flat-files first.
- `GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`:
  REST fallback when flat-files returns 403. Same one-call-per-day
  pattern.
- `GET /v3/reference/tickers?market=stocks&active=true`: paginated
  candidate pool for top-N seeds.
- `GET /v3/reference/tickers?market=stocks&active=false`: delisted
  pool for survivorship-clean universes.
- `GET /v3/reference/tickers/{ticker}`: market_cap (for ranking),
  type, sic_code, sic_description, list_date, delisted_utc.
- `GET /v3/reference/splits?ticker={T}`: split history for the corp
  action adjustment factor.
- `GET /v3/reference/dividends?ticker={T}`: dividend history for the
  price-only adjustment factor (when `--adjustment total-return`).

## Doesn't handle (yet)

- **Fundamentals join.** This skill emits OHLCV only. For fundamentals
  joined point-in-time, use `earnings-drilldown` (which carries the
  8-K acceptance methodology) or chain factor-research downstream. The
  schema reserves `fundamentals_path` for a future fundamentals file
  in the same output directory.
- **Total-return adjustment.** Default adjustment is price-only
  (splits applied by Massive, cumulative factor for un-adjustment).
  Total-return adjustment (treating dividends as reinvested) is
  documented in
  [`references/corporate-action-adjustment.md`](./references/corporate-action-adjustment.md)
  but the v1 implementation uses price-only. The `adjustment` field in
  the manifest records the choice so the consumer knows.
- **Intraday data.** Daily aggregates only. Minute and tick aggregates
  are queued as a separate skill (the file sizes change the problem).
- **Spinoff cost-basis allocation.** Detected and flagged
  (`spinoffs[]` in the schema), but the basis split is not applied;
  the manifest recommends manual override for spinoff-heavy windows.
- **Non-US universes.** US common stock only in v1. Massive's flat
  files cover other locales but the survivorship machinery here is
  US-specific (CIK linkage, exchange filter, holiday calendar).
- **Point-in-time universe reconstruction.** Same caveat as
  `factor-research`. The default `top100/top500/top1000` seeds use
  current market cap; for a true point-in-time universe, reconstruct
  per period. Queued as a clean PR extension.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
