---
name: universe-builder
description: Build a filtered, ranked equity universe from a candidate pool and emit a Bloomberg EQS / FactSet screener-style table. Chain composable predicates (market cap, momentum, valuation, options activity), rank survivors by composite z-score, flag sector concentration, and document survivorship handling. Use when the user wants a screen, a watchlist, or a defensible starting universe for backtests or factor research. Runs on a free Massive Basic key (with rate-limit caveats) and the first table-mode skill in the suite.
---

# universe-builder

You hand over a filter chain. The skill walks a candidate universe of US
stocks, applies each filter in order, ranks the survivors by a composite
z-score, flags sector concentration in the top decile, and emits two
output layers from one analysis.

This is the lowest-barrier skill in the suite. Free Basic keys can run
it end to end (with throttling) on a small candidate set. Paid keys can
run it across thousands of names without rate-limit pain. The output is
the starting point for everything else: factor-research backtests,
pitch-comps, options-flow watchlists.

Unlike a generic screener, universe-builder:

- Records every step of the filter chain so the survivor count is
  auditable, not "trust the dashboard"
- Computes a composite z-score across surviving factors, not just
  single-factor sorts
- Flags sector concentration in the top decile (the screen result you
  get often has more sector skew than you expected)
- Documents survivorship handling explicitly (delisted names retained
  for any historical lookback)

## When to invoke

- A PM says "give me US large-caps with strong momentum and decent
  cash yield"
- A quant says "starting universe for a 3M momentum backtest, no
  micro-caps, no illiquid names"
- A discretionary analyst says "what are the top 20 names by 3M
  momentum that also have positive operating cash flow"
- A retail user says "screen US stocks above $10B, filter by mom and
  valuation"

## What you need

- A filter chain (CLI flags or a JSON config)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Basic plan minimum (free works end to end at small candidate
  sizes; paid removes the 5/min rate cap)

### Canonical CLI flags

The screener-style flags all use the `--<bound>-<factor>` shape so a
filter chain reads top to bottom like a SQL `WHERE`:

```
--min-price 20             # last close >= $20.00
--min-adv 400000           # 20d avg daily volume >= 400,000 shares
--min-mom-3m 0.10          # 3M momentum >= +10% (canonical)
--max-week-return 0.0      # 5d return below threshold (see semantic below)
--min-mcap 10e9            # market cap >= $10B
--max-mcap 100e9           # market cap <= $100B
--ocf-yield-min 0.03       # operating CF yield >= 3%
--include-sectors X,Y      # categorical sector include
--exclude-sectors X,Y      # categorical sector exclude
--include-types CS         # security type whitelist (default 'CS', see below)
```

`--mom-3m-min` is a deprecated alias for `--min-mom-3m`. It still works
but prints a warning. Use the canonical `--min-mom-3m` so the flag style
matches `--min-price` and `--min-adv`.

### `--max-week-return` semantic

The threshold is signed and the comparison operator changes at zero so
the natural-language reading matches the math:

- `--max-week-return 0.0` keeps names where week return is **strictly
  less than 0** (excludes flat names — we want pullbacks, not stasis)
- `--max-week-return -0.05` keeps names where week return is **at or
  below -5%** (the user-intuitive reading of "down 5% or more"
  includes the exact -5.0% case)

In other words: zero is strict `<`, negative thresholds are inclusive
`<=`.

### `--include-types` (default `CS`)

The default keeps **common stock only** so a "stock screen" doesn't
silently include ETFs, leveraged products, foreign ADRs, or warrants.
The enrichment pass (see below) tags every survivor with its Massive
type before this filter runs.

Override examples:

- `--include-types CS,ADRC` — also include foreign ADRs (LEGN, etc.)
- `--include-types CS,ETF,ETN` — include ETFs and exchange-traded
  notes
- `--include-types '*'` — disable the type filter entirely

Massive returns these types in practice: `CS` (common stock), `ETF`,
`ETN`, `ETV` (ETF / ETN / ETV variants), `ADRC` (foreign ADR), `PFD`
(preferred), `WARRANT`, `RIGHT`, `UNIT`, `FUND`. The list endpoint
exposes the filter, but the `type` field is only populated on
per-ticker details, which is why the skill defers the type filter to
the enrichment pass.

### Enrichment pass

After the cheap price / volume / momentum filters reduce the working
set from ~12,000 names to 300-2,000, the skill makes a parallel fan-out
of per-ticker `/v3/reference/tickers/{T}` calls (16 workers) to pull
`type`, `sic_code`, `sic_description`, `market_cap`, and the human
name. Without this pass the grouped-aggs path has no security-type
data, so leveraged ETFs and 2x products leak through and the
concentration check shows everything as "Unknown".

Cost on Business tier is under 30 seconds for a 345-name cohort.
Massive's list endpoint silently ignores `ticker.any_of=...` for batch
lookup (probed 2026-06-24), so per-ticker fetches in parallel are the
right shape.

The skill runs at two fidelity tiers, flagged in the output JSON as
`tier`.

- **Tier A (paid Stocks Starter or above):** Unlimited REST. The
  candidate pool can be a few thousand names; the full filter chain
  runs in under two minutes.
- **Tier B (free Basic):** 5 calls/min. The candidate pool defaults to
  100 names (top US mcap by curated seed). Documented as the on-ramp,
  not the production configuration.

## What you get back

The skill ships two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
The filter chain steps with per-step survivor counts; the surviving
rows with their per-factor z-scores and composite rank; the sector
concentration analysis with std-devs overweight per dimension; the
source endpoints with fetched-at timestamps. UIs, downstream agents,
and Python scripts consume this.

**Layer 2: rendered table** in Bloomberg EQS / FactSet screener style.
See [`references/rendering.md`](./references/rendering.md) for the
canonical format rules. Monospaced columns, signed percentages, a
filter survival funnel as a separate small table, a concentration
callout when the top decile is sector-skewed, and a survivorship line
at the bottom.

UI devs build their own table from the JSON. Claude Code users see the
rendered form.

## How it works

1. Build the candidate pool. On Tier A, paginate
   `/v3/reference/tickers?market=stocks&active=true` until the cap is
   hit. On Tier B, use a curated seed list of large-cap tickers to stay
   under the rate limit.
2. Apply filters in cost order per
   [`references/filtering-methodology.md`](./references/filtering-methodology.md).
   Cheap predicates (active flag, exchange, type) come first; expensive
   ones (multi-day momentum, financials) come last. Each step records
   its survivor count so the funnel is auditable.
3. For each surviving name, fetch market cap and sector from the
   ticker details endpoint, momentum from a daily aggregate window,
   operating cash flow yield from the quarterly financials endpoint
   (FCF requires CapEx which Massive doesn't expose separately, see
   [`references/filtering-methodology.md`](./references/filtering-methodology.md)),
   and options ADV from the contracts list if Options Developer is
   available.
4. Compute per-factor z-scores within the surviving universe per
   [`references/composite-zscore.md`](./references/composite-zscore.md).
   Each factor is signed per direction (higher mcap = better, higher
   FCF yield = better, lower P/E = better), then equal-weighted into
   a composite z-score that drives the final rank.
5. Detect sector concentration in the top 20 names per
   [`references/concentration-analysis.md`](./references/concentration-analysis.md).
   If a sector is >2σ overweight vs its share of the starting universe,
   flag it.
6. Document survivorship per
   [`references/survivorship-handling.md`](./references/survivorship-handling.md).
   The `active` flag from Massive's reference endpoint indicates whether
   a ticker is currently trading; backtests should set `active=false`
   in the lookback window to avoid the standard bias.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, rate
  limiting, and the best-price fallback chain

## Output mode: table

Table mode is the format Bloomberg EQS, FactSet screeners, and any
quant blotter use to compare a ranked set of names on a fixed set of
columns. Monospaced or markdown-table layout so values line up; the
filter survival funnel renders as a second small table; the
concentration callout renders as bullet lines.

[`references/rendering.md`](./references/rendering.md) is the canonical
format reference for any future table-mode skill (factor-research,
pitch-comps). Match this format.

## Endpoints used

- `GET /v3/reference/tickers?market=stocks&active=true`: paginated
  candidate pool. Cheap; one-call per page.
- `GET /v3/reference/tickers/{ticker}`: per-name market cap, sector
  (SIC code), industry, shares outstanding. One call per surviving
  name.
- `GET /v2/aggs/grouped/locale/us/market/stocks/{date}`: one call per
  date returns OHLCV for all ~12,000 active US stocks. Used to compute
  momentum efficiently (two calls: today and N days ago).
- `GET /vX/reference/financials?ticker={ticker}&timeframe=quarterly`:
  per-name TTM operating cash flow for the cash-flow yield factor.
  Free Basic includes this endpoint.
- `GET /v3/reference/options/contracts?underlying_ticker={ticker}`:
  per-name options ADV, when Options Developer is available. Optional
  factor; skipped on Stocks-only plans.

## Doesn't handle (yet)

- True free cash flow yield. Massive's financials endpoint does not
  expose CapEx as a separate line, only `net_cash_flow_from_investing`
  which lumps CapEx with securities purchases and acquisitions. The
  skill uses **operating cash flow yield** as the most-defensible
  substitute and labels it as such. Add a separate CapEx parser keyed
  off `source_filing_file_url` (the raw XBRL) in a future PR.
- Forex / non-USD market caps. The skill restricts to US-listed
  common stock (`type=CS`, `market=stocks`).
- Custom factor weights. v1 uses equal-weighted composite z-score.
  Per-factor weights are wired into the schema (`composite_weights`)
  but not exposed as CLI flags yet.
- Intraday filters. Momentum windows are in trading-day units; intra-
  session screens belong to a separate live-screener skill.
- Cross-sectional industry adjustment. The concentration check is
  per-sector, not industry-adjusted within sector. A semi-heavy screen
  flags as semi-heavy, which is the right call most of the time.

These are clean PR extensions. The filter chain is composable by
design.
