---
name: valuation-sanity-check
description: Sanity-check an internal analyst valuation thesis against the live peer set. Input the target price, assumed revenue growth, assumed EBITDA margin, and horizon; the skill pulls the current name, builds the peer cohort, computes target-implied multiples vs the peer 25-75 band, compares the growth and margin assumptions to the peer distribution, runs a simplified reverse-DCF, and emits either a single-point fair-value estimate or a full fair-value distribution (--mc flag) as a one-page sell-side flash note answering "is this target defensible or has the model drifted from reality." Use when a banker, PM, or analyst is stress-testing a price target or pitch-deck valuation. Requires Stocks Starter.
---

# valuation-sanity-check

You hand over a subject ticker and the thesis: `target_price`,
`assumed_growth`, `assumed_margin`, `horizon_years`. The skill pulls
the current price, market cap, balance sheet, and TTM financials,
builds the peer set using the same waterfall as `pitch-comps`
(curated override → correlation → SIC fallback), pulls peer multiples
and growth/margin metrics, and emits a one-page flash note covering
four sanity checks.

This is the "is the model defensible or has it drifted" workflow. The
take at the top says whether the target survives the peer-distribution
sanity check; the four supporting sections show where the air is.

## When to invoke

- A banker is stress-testing an MD's pitch-deck target price
- A PM is reading a sell-side note that says "$250 target" and wants
  to know what's already baked into the current price
- A junior analyst handed off a model and you need to figure out
  whether the assumptions are defensible vs the peer set
- The user says "sanity-check $TICKER target $X", "is $X realistic
  for $TICKER", "what growth does the current price assume"

## What you need

- A subject ticker (NVDA, CRM, etc.)
- The analyst's thesis: `target_price` (USD/share), `assumed_growth`
  (decimal, e.g. 0.28 for 28%), `assumed_margin` (decimal, e.g. 0.60
  for 60%), `horizon_years` (integer, default 5)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter plan minimum. The full peer fanout is the same
  ~9 ticker-details + ~9 financials calls as `pitch-comps`; under 30
  seconds on Starter, ~5 min on free Basic.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Subject metadata, analyst inputs, three sanity-check blocks
(`multiple_sanity[]`, `growth_sanity`, `margin_sanity`), the
`reverse_dcf` block, peer list with each peer's contributing data,
the bold take, the closing read, and per-call source endpoints with
fetched-at timestamps.

**Layer 2: rendered note** in sell-side flash-note style, modeled on
`earnings-drilldown` note mode. See [`references/rendering.md`](./references/rendering.md).
Bold take at the top, three sanity sections, reverse-DCF block,
closing read.

## How it works

1. **Pull the subject's live state.** Snapshot for current price (via
   the standard `lastTrade → day.c → prevDay.c → fmv` waterfall), ticker
   details for shares outstanding and market cap, financials for TTM
   revenue, operating income, D&A, balance sheet (long-term debt).
   Same data layer as `pitch-comps`.
2. **Compute target-implied financials.** `target_mcap = target_price ×
   shares_outstanding`. `target_EV = target_mcap + long_term_debt`
   (cash not subtracted; documented in [`references/multiple-sanity.md`](./references/multiple-sanity.md)).
   `target_revenue_horizon = subject_revenue_ttm × (1 + assumed_growth)^horizon`.
   `target_ebitda_horizon = target_revenue_horizon × assumed_margin`.
   `target_eps_horizon` derived from the operating-margin-implied net income.
3. **Build the peer set** per [`references/peer-selection.md`](./references/peer-selection.md).
   Reuses the pitch-comps three-layer waterfall and the shared override
   map.
4. **Pull peer multiples and metrics.** Same per-peer fanout as
   `pitch-comps`: current price, market cap, TTM financials. Compute
   each peer's EV/Sales, EV/EBITDA, P/E, revenue growth TTM, EBITDA
   margin.
5. **Multiple sanity** per [`references/multiple-sanity.md`](./references/multiple-sanity.md).
   For each multiple (EV/Sales, EV/EBITDA, P/E), compute the
   target-implied value at the horizon and compare to the peer
   25-75 percentile band. Status: `in_line` (inside band), `above`
   (above p75), `below` (below p25).
6. **Growth and margin sanity** per [`references/growth-margin-sanity.md`](./references/growth-margin-sanity.md).
   Compare the analyst's `assumed_growth` and `assumed_margin` to the
   peer 25-75 bands on revenue growth and EBITDA margin. Same status
   labels. Records the `delta_pp` (assumed minus peer median, in
   percentage points) so the reader can quote the gap.
7. **Reverse-DCF** per [`references/reverse-dcf.md`](./references/reverse-dcf.md).
   At the current stock price, given the assumed margin and the
   peer-median EV/EBITDA exit multiple, what 5-year revenue CAGR is
   implied? Compare to peer-median 5y CAGR (proxied from TTM growth
   when 5y history is missing). Surfaces "air in the current price":
   the gap between the implied CAGR and the peer-median CAGR.
8. **Generate the take and the read** per [`references/take-generator.md`](./references/take-generator.md).
   Bold take at the top in one paragraph: the CAGR/margin the target
   requires and how far it sits from peer median. Closing read at the
   bottom: if you trim assumptions to peer median, where does the
   target land. Banker-tone, no hedge words.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, rate
  limiting, the snapshot fallback chain, and the financials-endpoint
  null-handling.

## Output mode: note

Same note mode as `earnings-drilldown`. Bold take at top, grouped
supporting sections, closing read, one-page max. The format follows
the sell-side flash-note convention. See [`references/rendering.md`](./references/rendering.md)
for the per-section rules.

A custom UI consumes the JSON and renders the three sanity sections
as side-by-side comparison bars (assumption vs peer band) with a
scatter inset for the reverse-DCF view. Claude Code users read the
rendered note.

## MC mode

Pass `--mc` when the single-point fair value reads as implausibly
precise; emits a sampled distribution + sensitivity ranking instead.
Drivers (growth, margin, exit multiple) come from the same peer set
the point-estimate path uses. See [`references/monte-carlo.md`](./references/monte-carlo.md)
for the methodology, sampling defaults, and what MC mode does NOT do
(it is not a forecast; it is a sensitivity sweep around peer-derived
inputs).

Flags:

- `--mc` — enable Monte Carlo fair-value distribution (default off).
- `--mc-samples N` — sample count, clamped to `[1000, 100000]`,
  default `10000`.
- `--mc-distribution {peer,normal}` — `peer` resamples from the peer
  empirical distribution (default); `normal` fits N(mu, sigma) to the
  peer set, useful for small cohorts where the empirical histogram
  is chunky.
- `--mc-seed N` — seed for reproducible runs.

When `--mc` is on the JSON gains a `monte_carlo` block with the p5..p95
fair-value distribution, the percentile of current and target price
within that distribution, per-driver Spearman sensitivity, and the
underlying driver pools. The rendered note appends a distribution
table, an adaptive "Translation:" line keyed to where the current
price sits inside the IQR, and a sensitivity bar chart.

Drivers are sampled INDEPENDENTLY. True peer growth and margin
correlate (rho ~ 0.3-0.5 historically), so MC tail percentiles
understate slightly. This caveat is surfaced in `tier_caveats`.

When `--mc` is off the script's behavior, JSON keys, and rendered
output are byte-identical to the pre-MC release.

## Endpoints used

- `GET /v3/reference/tickers/{ticker}`: subject and per-peer ticker
  details. Market cap, sector, **shares outstanding (load-bearing for
  target_mcap)**, name. One call per name.
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: current
  price for the subject and each peer. Cheap; falls back through the
  `lastTrade → day.c → prevDay.c → fmv` waterfall per the API patterns
  foundation.
- `GET /vX/reference/financials?ticker={ticker}&timeframe=quarterly&limit=8&order=desc`:
  eight quarters of financials per name. Subject reuses these for TTM
  revenue, operating income, EBITDA margin baseline, balance-sheet
  long-term debt. Peers feed the cohort distributions.
- Optional: `GET /v2/aggs/ticker/{ticker}/range/...` for the peer-set
  correlation fallback (uncurated subjects).

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- **Cash not subtracted from EV.** Same simplification as `pitch-comps`.
  Massive's financials endpoint doesn't expose `cash_and_equivalents`
  as a named field. EV is `market_cap + long_term_debt`. The
  target-implied EV uses the subject's current LTD (the analyst's
  thesis usually doesn't change capital structure). Documented in
  `multiple-sanity.md`.
- **Cost of capital hardcoded at 9%** in the reverse-DCF. A proper
  bottom-up WACC requires Beta, tax rate, marginal cost of debt, and
  the equity risk premium, none of which are exposed cleanly by the
  current API set. 9% is the rough cross-cap-structure midpoint for
  large-cap US equities at current rates and is consistent enough
  across the peer cohort that the relative comparison holds.
  Documented in `reverse-dcf.md` with the explicit caveat.
- **Single-stage terminal model.** The reverse-DCF discounts a single
  terminal EBITDA at the horizon × peer-median EV/EBITDA exit
  multiple. A multi-stage DCF would let growth decay toward a steady
  state. The simplification produces a slightly lower implied CAGR
  than a full DCF for high-growth names (because all the growth has
  to fit in the explicit horizon); documented and acknowledged.
- **5-year peer CAGR is TTM-based proxy.** Pulling a true 5-year
  revenue CAGR per peer requires 20 quarters of financials per name.
  Massive's endpoint supports `limit=20`, but for the v1 release the
  skill uses TTM revenue growth as the peer-cohort proxy. Documented;
  a clean v2 PR adds true 5y CAGR per peer.
- **Forward consensus not used.** Benzinga has analyst ratings but
  not consensus estimates in the bundle currently subscribed. When
  consensus is available, the "assumed growth vs peer band" check
  could include a "vs consensus growth" comparison; queued for v2.
- **Negative-EBITDA peers** drop out of the EV/EBITDA distribution
  (multiple is meaningless). The peer count in the schema records
  `n_peers_used` per check so the reader knows the sample size.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
