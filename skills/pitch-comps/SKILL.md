---
name: pitch-comps
description: Build a Bloomberg / CapIQ-style comparable companies set for a subject ticker. Identifies peers via a curated override map (with correlation and SIC fallbacks), pulls current multiples (EV/Sales, EV/EBITDA, P/E) for the subject and peers, renders the comp table with median / mean / 25-75 percentile summary stats, runs a regression-adjusted multiples view that controls for growth and profitability, and surfaces a one-line banker read at the bottom. Use when an analyst or banker is preparing pitch materials, a fairness opinion, or a valuation memo. Requires Stocks Starter for financials.
---

# pitch-comps

You hand over a subject ticker. The skill identifies peers using the same
three-layer methodology as `earnings-drilldown`'s peer-reaction (curated
override → correlation → SIC fallback), pulls TTM revenue, operating
income, EPS, and balance-sheet items for the subject and the peers,
computes current multiples (EV/Sales, EV/EBITDA, P/E) plus growth and
margin metrics, summarizes the peer set with median / mean / 25-75
percentile bands, runs a regression of each multiple on growth and
margin to compute a peer-implied multiple for the subject, and emits a
one-sentence banker read.

The output drops into a pitch deck or fairness-opinion appendix
unchanged. The structure matches what bankers already read in
Bloomberg's RV (relative value) screen and CapIQ's comp set page.

## When to invoke

- A banker is building a comp page for an MD's pitch deck
- An analyst is writing a coverage initiation and needs a valuation
  table for the subject vs. its peer set
- A buy-side PM is sizing a position and wants to know "what would the
  subject's multiple look like if peers traded it"
- The user says "build comps for $TICKER", "where does $TICKER trade vs.
  peers", "is $TICKER cheap or rich on multiples"

## What you need

- A subject ticker (CRM, NVDA, etc.)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter plan minimum. The full peer fetch fans out 9+
  ticker-details and 9+ financials calls; on free Basic (5/min)
  this will run but will take ~5 minutes. Starter (unlimited) finishes
  in under 30 seconds.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Subject metadata, peer list with each peer's multiples and metrics,
summary statistics (median / mean / p25 / p75 per multiple), the
regression-adjusted block (per-multiple implied vs. actual and
discount / premium), the one-sentence read, and the source endpoints
with fetched-at timestamps. UIs, downstream agents, and Python scripts
consume this.

**Layer 2: rendered comp table** in Bloomberg RV / CapIQ comp page
style. See [`references/rendering.md`](./references/rendering.md). Subject
highlighted at the top, divider, peers, divider, summary stats,
optional regression-adjusted section, mandatory one-sentence read at
the bottom.

## How it works

1. **Peer selection** per [`references/peer-selection.md`](./references/peer-selection.md).
   Three-layer waterfall: curated override map first (covers the top
   ~30 US large-caps where SIC misclassifies), correlation-based for
   uncurated names, SIC fallback last. Records the selection method
   in the JSON so the consumer knows the peer-set quality.
2. **Pull current price + market cap** for the subject and each peer.
   One snapshot call and one ticker-details call per name. Compute
   enterprise value per [`references/multiples-methodology.md`](./references/multiples-methodology.md)
   as `market_cap + total_debt - cash + operating_leases +
   minority_interest`. `total_debt` and `cash` are required; if either
   is missing for a name, EV-based multiples are emitted as `null` and
   the missing field is recorded on the per-name `ev_components` audit
   trail.
3. **Pull TTM financials** for the subject and each peer: revenue,
   operating income, depreciation and amortization (often null on
   software comps, see methodology), net income, diluted EPS. Compute
   revenue growth TTM vs prior TTM and EBITDA margin per
   [`references/growth-and-profitability.md`](./references/growth-and-profitability.md).
4. **Compute multiples**: EV/Sales, EV/EBITDA, P/E. Per peer.
   `null` where the inputs aren't available (e.g. SAP's ADR financials
   gap, or a peer with negative EBITDA where the multiple is
   meaningless).
5. **Cohort statistics** per [`references/cohort-statistics.md`](./references/cohort-statistics.md):
   median, mean, 25th and 75th percentiles, computed over peers
   only (subject excluded so the comparison is honest). Drop nulls from
   the summary; never impute zero.
6. **Regression-adjusted multiples** per [`references/regression-adjustment.md`](./references/regression-adjustment.md).
   Fit `multiple ~ growth + ebitda_margin` across peers with `numpy`
   least-squares. Predict the subject's multiple given its own growth
   and margin. Compare to actual; surface as a discount or premium.
   Flag when `n_peers < 8` because the regression's degrees of freedom
   get tight.
7. **Generate the read.** One sentence keyed off the regression output:
   where the subject sits vs. peers on growth-adjusted multiples and
   which multiple drives the divergence. Banker-tone, no hedge words,
   no "mispriced upside."

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, rate
  limiting, and the 4-step `lastTrade.p → min.c → day.c → prevDay.c`
  snapshot fallback chain. FMV is not a field on the v2 snapshot
  response and is not in this waterfall.

## Output mode: table

Same table mode as `universe-builder`. The format follows the canonical
table rules in `universe-builder/references/rendering.md` with the
comp-set-specific overrides in `references/rendering.md`: subject row at
top, divider, peer rows, divider, summary stats rows, optional
regression-adjusted block, mandatory one-sentence read.

A custom UI consumes the JSON and renders a sortable grid with hover-to-
inspect on each cell's source endpoint. Claude Code users read the
rendered table.

## Endpoints used

- `GET /v3/reference/tickers/{ticker}`: subject and per-peer ticker
  details. Market cap, sector, shares outstanding, name. One call per
  name.
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: current
  price for each name. Cheap; falls back through the 4-step
  `lastTrade.p → min.c → day.c → prevDay.c` waterfall per the API
  patterns foundation.
- `GET /vX/reference/financials?ticker={ticker}&timeframe=quarterly&limit=8&order=desc`:
  eight quarters of financials per name. TTM revenue = sum of the most
  recent 4 with revenue data, prior TTM = sum of quarters 5-8 for the
  growth calculation. Operating income, D&A, net income, diluted EPS.
  Balance sheet for long-term debt.
- Optional: `GET /v2/aggs/ticker/{ticker}/range/...` when correlation-
  based peer selection is needed (uncurated subject tickers).

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- **D&A often missing for software comps.** EBITDA in v1 is computed as
  `operating_income + d_a_or_zero` and labeled as such in the schema.
  For software peers where D&A isn't broken out, the "EV/EBITDA"
  multiple is closer to EV/EBIT. Documented in `multiples-methodology.md`.
- **Preferred stock** is not added to EV. The balance sheet endpoint
  doesn't expose it as a separate field; the simplification is
  documented and is a clean PR extension. Minority interest IS pulled
  via `_first_non_null` against `noncontrolling_interest` /
  `redeemable_noncontrolling_interest`.
- **P/B and P/FCF** are out of scope for v1. P/B requires book value
  per share (derivable from `equity / shares`); P/FCF requires true
  FCF which is the same blocker as the operating-cash-flow-yield work
  in `universe-builder`.
- **Forward multiples** (NTM EV/Sales, NTM P/E) require consensus
  estimates. Benzinga has analyst ratings but not consensus estimates
  in the bundle currently subscribed; this is a Tier A extension when
  consensus is available.
- **Foreign issuer ADRs** like SAP, ASML, NVO often have empty
  financials in Massive's endpoint. The skill keeps these in the peer
  set with `null` multiples (they still inform the "Salesforce trades
  vs which peers" framing) but they don't contribute to summary stats
  or the regression.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
