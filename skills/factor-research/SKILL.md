---
name: factor-research
description: Run a quant-style multi-factor backtest on a defined US equity universe. For momentum, value, quality, and low-vol factors, compute decile spreads, information coefficients with t-stats, IC decay curves at 1M/3M/6M/12M forward horizons, single-name attribution at the long and short tails, and the factor correlation matrix. Emits FactSet/Axioma factor research-style table output a buy-side quant would hand to a PM. First skill to exercise the flat-files foundation: a 5-year x top-500 daily aggregates pull is ~80,000 ticker-days, done via a few day-bucket downloads instead of 80,000 REST calls.
---

# factor-research

You hand over a universe definition and a window. The skill runs a
multi-factor IC + decile analysis and emits two output layers from one
analysis.

The output drops into a quant strategy meeting unchanged. The structure
matches what FactSet's Alpha Testing, Axioma's factor research module,
and any internal quant team's signal review document already use.

## When to invoke

- A PM asks "what's working in the factor zoo right now"
- A quant analyst is sizing a multi-factor sleeve and needs IC and
  correlation evidence
- A researcher is testing whether momentum's IC has decayed in the
  current regime
- The user says "run a factor study on the S&P 500", "is value working",
  "show me the decile spread for momentum"

## What you need

- A universe (defaults to top 500 by current market cap)
- A window (defaults to 2021-06-01 to today)
- `MASSIVE_API_KEY` exported in the environment (used as both S3 access
  key and S3 secret key per the flat-files convention)
- Stocks Starter plan minimum (flat files included with any paid plan)

## Expected runtime

This skill is the first one in the suite where a real run takes meaningful
wall-clock time. A default 5-year, top-500 run:

- Cold: ~10-20 minutes (downloads ~1,260 daily aggregate files plus
  ~500 financials calls plus ~500 ticker-details calls)
- Warm (with on-disk cache of the daily files): under 2 minutes

That's the legitimate cost of universe-wide work. If a quant
proposition is wrong, finding out in 20 minutes beats finding out in a
six-week production cycle. The same workflow over REST would be
80,000+ calls and require an unlimited paid tier just to complete.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
The universe definition (with the survivorship caveat made explicit),
the window, per-factor IC at four forward horizons with t-stats and
sample sizes, decile spread returns (D10 - D1) annualized, hit rates,
factor correlation matrix on signal ranks, the current top-5 and
bottom-5 deciles per factor, and the take. UIs, downstream agents, and
Python scripts consume this.

**Layer 2: rendered table** in FactSet Alpha Testing / Axioma factor
research style. See [`references/rendering.md`](./references/rendering.md).
Four blocks: single-factor IC + decay table, long-short decile spread
table, factor correlation matrix, current decile membership block, and a
mandatory one-paragraph take at the bottom.

UI devs build their own dashboards from the JSON. Claude Code users
read the rendered tables.

## How it works

1. **Build the universe** per [`references/universe-construction.md`](./references/universe-construction.md).
   Default: top 500 by current market cap, filtered to names with
   continuous daily price history across the window. The JSON labels
   this `current_top500_survivorship_biased` so consumers know that
   for a true point-in-time backtest you reconstruct the top-500 per
   month.

2. **Pull the daily aggregates via flat files.** One S3 day-bucket per
   trading day across the window. Files at
   `s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz`.
   Schema is lowercase columns (`ticker`, `volume`, `open`, `close`,
   `high`, `low`, `window_start`, `transactions`); see
   [`../massive-flat-files/SKILL.md`](../massive-flat-files/SKILL.md).
   Parallelized 16 workers; rate-limit-free.

3. **Pull TTM fundamentals via REST** for the value and quality factors.
   One call per name to `/vX/reference/financials?ticker={T}&timeframe=annual&limit=2`.
   Returns shareholders' equity (for book value), net income (for ROE),
   and gross profit / revenue / total assets (for gross profitability
   and leverage).

4. **Compute factor scores** per [`references/factor-definitions.md`](./references/factor-definitions.md).
   Momentum is 12M-1M return (skip the most recent month, the academic
   standard, to avoid mean-reversion contamination). Value is `1 / (P/B)`
   (price-to-book inverse so higher is cheaper). Quality is ROE. Low-vol
   is `1 / realized_vol_252d`. Cross-sectional rank within the universe
   each month. Winsorize raw values at the 1st and 99th percentile
   before ranking.

5. **Compute information coefficients** per [`references/information-coefficient.md`](./references/information-coefficient.md).
   Per month, take the Spearman rank correlation between factor score
   and forward return. Compute for 1M, 3M, 6M, 12M forward horizons.
   Report mean IC, IC standard error, and the t-stat
   (mean_IC / IC_se * sqrt(n_months)). IC decay is the table across the
   four horizons; a healthy alpha factor has positive IC at all
   horizons but decays gradually.

6. **Compute decile spread returns** per [`references/decile-analysis.md`](./references/decile-analysis.md).
   Sort the universe into 10 deciles by factor score per month.
   Equal-weight names within each decile. Compute the long-short
   spread `D10 - D1` per forward horizon. Annualize. The hit rate is
   the percentage of months where D10 beats D1 over the 12M horizon.

7. **Compute factor correlation** per [`references/factor-correlation.md`](./references/factor-correlation.md).
   The correlation matrix is built on factor SIGNALS (rank scores),
   not factor RETURNS. Two factors with 0.7+ signal correlation are
   capturing the same thing; a sleeve weighted equal across them
   gives less diversification than naive equal weight implies.

8. **Generate the take.** One paragraph keyed off the strongest factor
   by t-stat, the weakest, and the most-correlated pair. PM-relevant
   tone: which factor is working in the current regime, which isn't,
   and the implication for sleeve construction.

## Foundations used

- [`massive-flat-files`](../massive-flat-files) for the bulk daily
  aggregates pull (S3 auth, path layout, parallelism patterns).
- [`massive-api-patterns`](../massive-api-patterns) for REST auth and
  the financials endpoint used to compute value and quality.

## Output mode: table

Same table mode as `universe-builder` and `pitch-comps`. The canonical
table rules live in
[`../universe-builder/references/rendering.md`](../universe-builder/references/rendering.md);
this skill's overrides live in [`references/rendering.md`](./references/rendering.md):
single-factor IC + decay block, decile spread block, correlation matrix
block, current decile membership block, take.

A custom UI consumes the JSON and renders a sortable, hover-to-inspect
factor matrix with click-through to a name's per-factor history. The
rendered format here is the Claude Code default.

## Endpoints used

- `s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz`:
  one file per trading day; ~1,260 files for a 5-year window. Each file
  has ~10,000 rows (all US-listed stocks for that day). Parallelize 16
  workers.
- `GET /v3/reference/tickers/{ticker}`: per-name market cap (for
  universe construction), name, sector. One call per name.
- `GET /vX/reference/financials?ticker={T}&timeframe=annual&limit=2`:
  per-name shareholders' equity, net income, gross profit, revenue,
  total assets. Two annuals so book value uses the latest filed
  fiscal year. One call per name.
- `GET /v3/snapshot/locale/us/markets/stocks/tickers`: optional, used
  to enrich the current decile membership block with company names if
  not already in the ticker details cache.

## Doesn't handle (yet)

- **Point-in-time universe construction.** The default universe is
  "current top 500 by market cap," which is forward-looking biased
  for a backtest (NVDA wasn't a top-500 name in 2021). For a true
  point-in-time backtest you reconstruct the top-500 each month from
  `/v3/reference/tickers` with `date=` parameter; this is a clean PR
  extension and is queued. The JSON labels the bias explicitly.

- **Transaction costs.** Decile spreads are gross of trading costs.
  Real long-short implementation of a monthly-rebalanced factor sleeve
  costs ~25-50bps annualized in spread and impact for a $1B AUM
  vehicle. The skill emits gross spreads; the consumer subtracts
  their cost model.

- **Sector-neutral factor returns.** Factors here are run on the raw
  universe. A real quant sleeve neutralizes sector exposure before
  ranking (so the value tilt isn't just an energy-and-banks tilt).
  The sector-neutral version is a clean PR extension; the schema
  reserves space for it (`factor_returns_sector_neutral`).

- **Fundamental-data lag.** The financials endpoint returns the most
  recent annual filing. For a true point-in-time, the factor on
  2021-12-15 should use the 10-K filed by 2021-12-15, not the one
  filed later. The skill currently uses "most recent annual" for the
  full window; this overstates value and quality ICs because the
  signal contains forward-looking information. Documented as a
  caveat. PR queued.

- **Macro/style regime overlay.** The take identifies which factor is
  working in the current regime but doesn't run a formal regime
  classifier (growth vs value regime, risk-on vs risk-off). A regime
  overlay is a follow-on skill, not part of v1.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
