# Filtering methodology

How to compose a filter chain that produces a defensible starting
universe. Read this before adding a new predicate or changing the
order of operations.

## The chain is ordered

Filters run in sequence. Each step takes the survivors of the prior
step and applies its predicate. The order matters because cheap
predicates (no API call) should come before expensive ones (one API
call per surviving name). On a free Basic key with a 5/min cap, getting
the order wrong turns a one-minute screen into a half-hour wait.

**Cost tiers, lowest to highest:**

1. **Free.** Predicates that operate on data already in the candidate
   list: `active=true`, `market=stocks`, `type=CS`, `primary_exchange
   in {XNAS, XNYS, ARCX}`. Massive's `/v3/reference/tickers` returns
   these fields on the list endpoint; no extra call per name.
2. **One bulk call.** Predicates that need today's prices or a window
   of prices but only one API call total: market cap (technically
   needs ticker details per name, but `share_class_shares_outstanding *
   close` from grouped aggs is a defensible substitute on free), 3M
   momentum (two `/v2/aggs/grouped/...` calls cover all 12,000 stocks).
3. **One call per survivor.** Predicates that genuinely need a per-name
   API call: ticker details (sector, exact market cap, listing date),
   financials (cash flow, earnings), options ADV (contracts list).

Always order the chain so that one-call-per-survivor predicates run
last. On 1,243 starting names with two cheap filters reducing to ~300,
fanning out per-name calls on 300 takes 60s on paid and 60 minutes on
free. The chain order is the difference between "fast demo" and "user
gives up."

## Canonical filter types

The skill ships these out of the box. Each has a known direction
(higher is better, lower is better, or band), used by the composite
z-score sign correction.

### Security type (`include_types`, default `CS`)

- Field: `type` on `/v3/reference/tickers/{ticker}` (one call per
  survivor). The list endpoint exposes `?type=CS` for filtered list
  pulls, but does NOT populate the `type` field in the response rows,
  and the `ticker.any_of=...` batch syntax is silently ignored as of
  2026-06-24. The only reliable way to read `type` for a known cohort
  is per-ticker fetches.
- Direction: categorical whitelist
- Defaults to `{"CS"}` so a "stock screen" excludes ETFs, leveraged
  products, foreign ADRs, warrants, and units by default. Override
  with `--include-types CS,ETF` etc.
- Massive types observed in production: `CS` (common stock), `ETF`
  (exchange-traded fund), `ETN` (exchange-traded note), `ETV` (ETV
  variant), `ADRC` (foreign ADR), `PFD` (preferred), `WARRANT`,
  `RIGHT`, `UNIT`, `FUND`.
- Names that fail to enrich (no detail row) are dropped under a
  CS-only filter, since we can't verify they're common stock.

### Enrichment pass: order-of-operations rule

The type, sector, and market_cap filters depend on per-ticker detail
calls. Run those AFTER the cheap price / volume / momentum filters have
narrowed the working set from ~12,000 names to 300-2,000. A 16-worker
ThreadPoolExecutor handles a 345-name enrichment in <30s on Business
tier. Doing it before momentum trims would burn ~12,000 calls on names
you're about to drop anyway.

### Market cap (`min_mcap`, `max_mcap`)

- Field: `market_cap` on `/v3/reference/tickers/{ticker}` (one call
  per name) or `weighted_shares_outstanding * close_price` from the
  daily aggregate (one bulk call for the whole market)
- Direction: higher = better (large-cap quality bias) or band (for
  small-cap or mid-cap screens)
- Threshold examples: $10B = large-cap, $2B = mid-cap, $300M = small-cap
- Gotcha: Massive's `market_cap` field on ticker details is updated
  daily but lags the previous close. Don't expect intraday accuracy.

### Sector / industry (`include_sectors`, `exclude_sectors`)

- Field: `sic_description` on `/v3/reference/tickers/{ticker}`. SIC
  codes group into sector buckets via a mapping table maintained in
  `examples/run-universe-builder.py` (see `SIC_TO_SECTOR`).
- Direction: categorical
- Common buckets: Semiconductors (357x), Software (737x), Banking
  (602x, 603x), Pharmaceuticals (283x), Energy (291x, 131x),
  Healthcare (806x), Industrials (357x, 371x).

### Week return (`max_week_return`)

- Field: derived from daily close-to-close, 5 trading days back.
- Direction: lower = better for mean-reversion / pullback screens.
- Operator: zero threshold uses strict `<` (exclude flat names — a
  pullback screen wants names that moved down, not names that didn't
  move); any negative threshold uses inclusive `<=` so the
  user-intuitive reading of `--max-week-return -0.05` ("down 5% or
  more") includes the exact -5.0% case.
- Threshold examples: `0.0` = any pullback; `-0.05` = down 5%+;
  `-0.10` = down 10%+ (deep dip).

### Momentum (`min_mom_3m`, `mom_6m`, `mom_12m`)

- Field: derived from daily close-to-close. 63 trading days back =
  3M, 126 = 6M, 252 = 12M.
- Direction: higher = better for trend-following; lower = better for
  mean-reversion (specify `--mom-3m-bottom-quartile` instead)
- Implementation: pull `/v2/aggs/grouped/locale/us/market/stocks/{date}`
  for today and `{date - 63 sessions}`. One call per date, returns
  ~12,000 rows of OHLCV each. Momentum = `(close_today / close_then) -
  1`.
- Gotcha: grouped aggs return 0 results on market holidays and
  weekends. Walk backward until a non-empty response. The example
  implementation hardcodes this.

### Operating cash flow yield (`ocf_yield_min`)

- Field: `financials.cash_flow_statement.net_cash_flow_from_operating_activities`
  on `/vX/reference/financials?ticker={ticker}&timeframe=quarterly&limit=4`,
  summed TTM, divided by market cap.
- Direction: higher = better
- Threshold examples: 3% = decent yield; 5% = strong; 8% = exceptional
- **Why operating CF, not FCF.** True FCF = Operating CF - CapEx.
  Massive's financials endpoint exposes
  `net_cash_flow_from_investing_activities` which lumps CapEx with
  securities purchases, acquisitions, and divestitures. Subtracting it
  from operating CF produces noise, not signal. The skill emits
  operating CF yield and labels it `ocf_yield` (not `fcf_yield`) so
  the user knows. To get true FCF, parse the raw XBRL filing
  referenced in `source_filing_file_url`; left as a v2 extension.

### Options activity (`opt_adv_min`)

- Field: derived from per-contract aggregate volume on
  `/v3/reference/options/contracts?underlying_ticker={ticker}`, summed
  across all active contracts.
- Direction: higher = better (liquidity)
- Requires Options Developer plan or higher. Skipped silently on
  Stocks-only plans; the schema records the absence so downstream
  consumers know.
- Threshold examples: 50,000 contracts ADV = solid options liquidity
  for screens that need to express a view with options.

## Order-of-operations defaults

The reference implementation runs this order, top-down:

1. `active=true` (free, on the list endpoint)
2. `market=stocks` (free)
3. `primary_exchange in {XNAS, XNYS, ARCX}` (free, drops OTC and
   foreign listings)
4. `min_price`, `min_adv`, `mom_3m`, `max_week_return` (one
   grouped-aggs call per anchor date, computed in-memory)
5. **Enrichment pass**: parallel per-ticker `/v3/reference/tickers/{T}`
   for every survivor. Populates `type`, `sic_code`, `market_cap`,
   `name`.
6. `include_types` (default `CS`, applied to enriched survivors)
7. `min_mcap` (applied to enriched survivors; on grouped path this
   filter only runs after the enrichment pass)
8. `ocf_yield_min` (one financials call per surviving name)
9. `opt_adv_min` (one contracts-list call per surviving name; skipped
   without Options Developer)

Steps 5 and 8 are the expensive ones. Putting price / volume / momentum
before them shrinks the working set 10x typically (12k -> ~300-1k).
The free-tier on-ramp uses a 100-name curated seed to skip the
expensive market-cap fan-out entirely.

## Adding a new filter

Add to `examples/run-universe-builder.py` in `apply_filter_chain()`.
Each filter is a function that takes a list of name dicts and returns
the filtered list, plus appends a step entry to `filter_chain[]`. The
function should also call `record_factor(name, factor_name, value)` so
the composite z-score has the values to z-score later.

If your new filter needs an API call per name, document the call cost
in this file and place it in the cost-ordered list. Don't drop in a
per-name call before the cheap filters; the audit script doesn't catch
this but the user's clock will.

## Documented filter examples

The reference CLI accepts these flags:

```
--min-mcap 10e9              # large-cap floor
--max-mcap 100e9             # mid-cap ceiling
--min-price 20               # last close >= $20
--min-adv 400000             # 20d avg daily volume >= 400k shares
--include-sectors Semiconductors,Software
--exclude-sectors Banking
--include-types CS           # default 'CS'; pass CS,ETF to keep ETFs
--mom-3m-top-quartile        # top 25% by 3M momentum
--min-mom-3m 0.10            # >=10% 3M momentum (canonical)
--max-week-return 0.0        # 5d return < 0 (--max-week-return -0.05 uses <=)
--ocf-yield-min 0.03         # >3% operating CF yield
--opt-adv-min 50000          # 50k+ contracts ADV (requires options)
--candidate-source curated   # 'curated' / 'reference' / 'grouped'
--candidate-cap 100          # cap on candidate pool size (curated/reference only)
```

Default chain when no flags are passed:

```
--candidate-source curated --candidate-cap 100 --min-mcap 10e9 --mom-3m-top-quartile
```

This produces a defensible large-cap momentum screen on free Basic in
under two minutes.
