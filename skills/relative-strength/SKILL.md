---
name: relative-strength
description: Rank a watchlist of tickers by relative strength versus a benchmark (default SPY) across multiple lookback windows (default 5/20/60/120 trading days). Emits per-window RS in basis points, total return, a within-watchlist composite percentile rank, and a trend label per name (stable_leader, improving, deteriorating, stable_laggard, mixed). Use when a PM or trader has a watchlist and asks "rank these by RS vs SPY across week/month/quarter/half." Lightweight watchlist ranker, not a universe-wide factor study. Requires Stocks Starter.
---

# relative-strength

You hand over a watchlist and a benchmark. The skill returns each name's
return and relative strength versus the benchmark over several lookback
windows, ranks the watchlist by a consistency-weighted composite, and
labels the trend per name.

This is not alpha. It does descriptive math on the names you already
chose. It tells you which ones have been leading, which have been
lagging, which are accelerating, and which are rolling over. The PM
decides what to do with that.

## When to invoke

- A PM with a 5-30 name watchlist asks "rank these by RS vs SPY across
  several windows"
- A trader scanning a sector basket wants to know which names are
  acting strongest right now and which are deteriorating
- A researcher pairing this with `market-regime` to decide whether
  leadership is broad or narrow
- The user says "rank these by relative strength", "which names are
  leading", "show me RS vs SPY", "is this name still leading the group"

For universe-wide momentum work (top 500, IC + decile spreads, t-stats),
use [`factor-research`](../factor-research). This skill is the
lightweight watchlist ranker, not the heavyweight factor study.

## What you need

- A watchlist of tickers (`--watchlist`, required, comma-separated)
- A benchmark ticker (`--benchmark`, default `SPY`)
- Lookback windows in trading days (`--windows`, default `5,20,60,120`)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter plan minimum (unlimited REST for the daily aggs pull)

Optional:

- `--include-sectors` adds the 11 SPDR sector ETFs (XLK, XLF, XLE,
  XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) to the ranking. Useful for
  asking "is NVDA's leadership the name or just XLK leadership?"

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-ticker `rs_by_window` (basis points), `return_by_window` (decimal),
`composite_rs_percentile` (0-100, within the ranked universe),
`trend_label`, and `n_obs_per_window`. A top-level `ranking` block
exposes `leaders_top_3` and `laggards_bottom_3`. UIs, downstream
agents, and scripts consume this.

**Layer 2: rendered table** sorted by composite RS percentile,
descending. One row per ticker, one column per window, plus a trend
column and a composite percentile column. The footer carries the
leader / laggard summary. See
[`references/rendering.md`](./references/rendering.md).

UI devs build their own dashboards from the JSON. Claude Code users
read the rendered table.

## How it works

1. **Pull daily aggregates** for each ticker (watchlist + benchmark +
   optional sector ETFs) over `max(windows) * 1.6` calendar days, via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`. The
   1.6x overshoot covers weekends and holidays. Cached per-process.
2. **Compute per-window return** for each ticker. Total return =
   `(close_today / close_window_days_ago) - 1`. The benchmark goes
   through the same calculation on the same dates.
3. **Compute RS in basis points** per ticker per window:
   `RS_bps = (ticker_return - benchmark_return) * 10_000`.
   Basis points keep magnitudes comparable across windows; a 100bp
   1-day move and a 100bp 1-year move read the same way.
4. **Compute composite percentile rank** per ticker. For each window,
   percentile-rank the ticker's RS within the watchlist. The composite
   is the mean of those window-level percentiles. This rewards
   consistency: a name in the top quartile of every window scores
   higher than a name that's #1 in one window and last in three others.
   See [`references/methodology.md`](./references/methodology.md).
5. **Label the trend** per ticker:
   - `improving`: short-window RS strictly greater than long-window RS
     (5d > 20d > 60d), recent acceleration
   - `deteriorating`: short-window RS strictly less than long-window RS
     (5d < 20d < 60d), recent decay
   - `stable_leader`: every window RS > 0, no clear acceleration
   - `stable_laggard`: every window RS < 0
   - `mixed`: anything else (no clear pattern)
6. **Sort and label** the results table by composite percentile,
   descending. Pick the top 3 as `leaders_top_3` and the bottom 3 as
   `laggards_bottom_3`.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, the `/v2/aggs` daily endpoint conventions.

## Output mode: table

A wide, sortable table is the right canvas for "rank these names." Each
row is a ticker, each column is a window's RS, plus a trend column and
a composite percentile. Leader / laggard summary in the footer. The
canonical table-mode rules live in
[`../universe-builder/references/rendering.md`](../universe-builder/references/rendering.md);
the relative-strength overrides live in
[`references/rendering.md`](./references/rendering.md).

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily aggregates per ticker. One call per ticker per run
  (watchlist + benchmark + optionally 11 sector ETFs).

## Doesn't handle (yet)

- **Volume confirmation.** RS captures price relative to benchmark,
  not whether the leadership comes with above-average volume. A clean
  PR extension would multiply by a volume-Z factor; queued.

- **Risk-adjusted RS.** Pure RS doesn't penalize high-vol names. A
  Sharpe-like adjustment (`RS / realized_vol`) would tilt the ranking
  toward names that lead with less noise. Queued.

- **Industry/peer benchmark.** v1 uses a single benchmark for every
  name. Ranking a semis basket vs SPY mixes the cap-weighted market
  signal with the sector signal. `--include-sectors` partially
  addresses this by surfacing the sector context alongside; per-name
  custom benchmarks (e.g., NVDA vs XLK) are queued.

- **Multi-window weighting.** The composite is an equal-weighted mean
  of per-window percentiles. A weighted version (heavier on the
  longer windows for trend, heavier on the shorter for momentum)
  would let the caller bias the rank to a horizon. Queued.

- **History gating.** Tickers with fewer than `window` bars for a
  given window get a null RS for that window and a tier_caveats note.
  The composite is computed from whatever windows produced a non-null
  RS; new listings can still be ranked, with a smaller sample.

These are clean PR extensions. The output schema is forward-compatible
so adding them later doesn't break consumers.
