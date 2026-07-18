# Plan Matrix

What your Massive key unlocks, skill by skill.

*Verified 2026-06-23 against [pricing](https://massive.com/pricing),
[rate-limit FAQ](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis),
and the [flat files included blog post](https://massive.com/blog/flat-files).
Re-verify before each release.*

## How to read this

You don't need to memorize Massive's tier ladder. Find the skill you want
to run, check the "min asset access" column, and that's what your key
needs to cover.

A few things to know up front:

- **Asset classes are separate purchases.** A stocks plan doesn't include
  options. Options access is a separate add-on.
- **Benzinga products are separate add-ons.** News, Earnings, Analyst
  Ratings, Bulls/Bears Say, Analyst Insights each cost ~$99/m on top of
  your asset-class plan. Skills marked with **Benzinga Earnings** above
  need that add-on for press release dates and consensus EPS; without
  it, the underlying beat/miss math is wrong.
- **Rate limits aren't the gate on paid plans.** Free Basic is 5
  calls/min. Every paid tier is effectively unlimited. The actual gates
  are real-time data, deep history, options/crypto access, and WebSocket
  streaming.
- **Bulk historical work is cheap, with a caveat.** Massive's blog
  states flat-file S3 access is included in every paid plan. Verified
  2026-06-23: a Stocks Business key returned `403 Forbidden` on every
  flat-files request (list, head, get) despite the same key serving
  REST, options chains, and Benzinga endpoints. The factor-research
  skill demonstrates the fallback: `/v2/aggs/grouped/locale/us/market/stocks/{date}`
  returns ~10,000 US stocks per call, which is equivalent throughput
  to flat-files via REST. If your key is one of the affected accounts,
  the skills marked "Flat files" below auto-fall-back to the grouped
  aggregates endpoint with no functional difference for end-of-day
  workflows.

## The skill matrix

| Skill | Interface | Min asset access | What it adds |
|---|---|---|---|
| `universe-builder` | REST | Stocks Basic | Run on a free key, end-of-day only |
| `corp-actions-reconciler` | REST | Stocks Basic | Runs on free, light API use |
| `news-scanner` | REST | Stocks Basic | News + sentiment included with stocks |
| `t+1-settlement-prep` | REST | Stocks Basic | Logic-heavy, light API |
| `factor-research` (lite) | REST | Stocks Basic | Single factor on free; multi-factor needs paid |
| `factor-research` (full) | Flat files | Stocks Starter | Bulk daily aggregates, no rate-limit pain |
| `pitch-comps` | REST | Stocks Starter | Fundamentals + delayed price |
| `valuation-sanity-check` | REST | Stocks Starter | Current price + financials |
| `position-sizer` | REST | Stocks Starter | Daily aggs per name; runs on free Basic too, slower |
| `technical-briefing` | REST | Stocks Starter | Daily aggs + snapshot for one name; runs on free Basic too, slower |
| `market-regime` | REST | Stocks Starter | Daily aggs for SPY + VIX + 11 sector ETFs; runs on free Basic too, slower |
| `relative-strength` | REST | Stocks Starter | Daily aggs per ticker + benchmark; runs on free Basic too, slower |
| `pairs-scanner` | REST | Stocks Starter | Daily aggs per ticker; every pair tested for Engle-Granger cointegration + OU half-life. Runs on free Basic too, slower |
| `earnings-blackout` | REST | Stocks Basic | Watchlist scanner; Benzinga earnings if entitled, SEC EDGAR fallback otherwise |
| `earnings-drilldown` (lite, Tier B) | REST | Stocks Starter | Works without Benzinga; uses 8-K date as print proxy, reaction-sign bucketing |
| `earnings-drilldown` (lite, Tier A) | REST | Stocks Starter + Benzinga Earnings | Adds consensus EPS, surprise %, beat/miss bucketing |
| `earnings-drilldown` (full, Tier A) | REST | Stocks Starter + Options Developer + Benzinga Earnings | Full fidelity: implied vs realized + beat/miss |
| `crypto-vol-scanner` | REST | Crypto Starter | Crypto Developer for real-time |
| `event-study` (recent) | REST | Stocks Developer | Tick aggregates around recent events |
| `event-study` (historical) | Flat files | Stocks Starter | Bulk pull years of events |
| `backtest-data-prep` | Flat files | Stocks Starter | The primary flat-files workflow |
| `slippage-cost` (historical) | Flat files | Stocks Starter | NBBO from quote files |
| `slippage-cost` (live) | WebSocket | Stocks Advanced | Real-time NBBO stream |
| `options-flow` (scan, delayed) | REST | Stocks Starter + Options Developer | 15-min delayed tape; methodology identical to real-time |
| `options-flow` (scan, real-time) | REST | Stocks Business + Options Business | Sub-second sweep detection on the live tape |
| `options-flow` (live stream) | WebSocket | Options Business | OPRA WebSocket feed |
| `portfolio-mark` (delayed) | REST | Stocks Starter (+ Crypto Starter) | 15-min marks |
| `portfolio-mark` (live) | WebSocket | Stocks Advanced (+ Crypto Developer) | WS stream + fallback chain |
| `risk-report` | REST | Stocks Starter | Daily aggs per name + benchmark; VaR/ES/drawdown/stress |
| `corporate-actions-scanner` | REST | Stocks Basic | 8-K scan + news cross-ref + T+1/T+5 reactions; SEC EDGAR is free |
| `macro-event-calendar` | REST | Stocks Basic | Forward FOMC/CPI/NFP/ISM/GDP/PCE schedule + historical SPY reactions |
| `sector-rotation-signal` | REST | Stocks Starter | Rank-change detection on the 11 SPDR sector ETFs vs SPY |
| `historical-analog-finder` | REST | Stocks Starter | K nearest historical regime analogs + forward SPY return distribution |
| `portfolio-rebalancer` | REST | Stocks Starter | Variance-share-cap solver on top of risk-report; outputs trade tickets |
| `options-structure-analyzer` | REST | Stocks Starter + Options Developer | Rank options structures for direction/vol/hedge views |
| `portfolio-macro-scenario` | REST | Stocks Starter | Per-position OLS on TLT/UUP/USO/GLD; runs on free Basic with `sleep=13` |
| `hedge-suggester` | REST | Stocks Starter + Options Developer | Prices 5 option overlays against a long; needs the chain with greeks + OI |
| `single-name-vs-sector` | REST | Stocks Starter | 3 daily aggs pulls (name + sector ETF + benchmark); runs on Free Basic too |
| `commodity-cycle` | REST | Stocks Starter | 5-6 daily ETF pulls (commodity + UUP/TIP/IEF + GDX/SLV or DBC); runs on Free Basic too |
| `risk-factor-delta` | REST | Stocks Basic | 10-K Item 1A category diff via pre-parsed Massive taxonomy; free tier |
| `filing-sentiment` | REST | Stocks Basic | Loughran-McDonald scoring on 10-K sections; free tier |
| `insider-flow` | REST | Stocks Basic | Form 4 classification + 10b5-1 filter + cluster buy detection; free tier |
| `8-k-scanner` | REST | Stocks Basic | 8-K disclosure taxonomy scan across a ticker or watchlist; free tier |
| `manager-portfolio-diff` | REST | Stocks Basic | 13-F quarterly diff for a fund manager (alias or CIK); free tier |
| `guidance-tracker` | REST | Stocks Basic + Benzinga Corporate Guidance | Guidance raise/cut/reaffirm trajectory; add-on required (approx $99/mo) |
| `analyst-tracker` | REST | Stocks Basic + Benzinga Analyst Ratings | Sell-side rating events + consensus PT; add-on required |
| `hurst-exponent` | REST | Stocks Basic | R/S Hurst estimator on daily returns; single-name persistence classifier; free tier |
| `mc-portfolio-simulator` | REST | Stocks Basic | Monte Carlo forward P&L simulator; free tier |
| `risk-report` (with --mc) | REST | Stocks Basic | Adds path-VaR MC block to existing risk-report; normal or student-t innovations |
| `change-point-detector` | REST | Stocks Basic | Bayesian Online Change-Point Detection (Adams-MacKay 2007) on daily returns; free tier |
| `signal-decay` | REST | Stocks Basic | Rolling IC + exponential decay fit + tearsheet on candidate signals; free tier |
| `rough-vol-forecast` | REST | Stocks Basic | Rough-volatility (Bayer-Friz-Gatheral 2016) multi-horizon vol forecast; free tier |
| `zero-dte-gamma` | REST | Stocks Basic + Options Developer | Dealer gamma exposure aggregation for near-expiry SPY/SPX/QQQ options |
| `filing-triangulation` | Workflow | Stocks Basic | Chains 8-k + risk-factor-delta + filing-sentiment + insider-flow + analyst-tracker |
| `regime-audit` | Workflow | Stocks Basic | change-point-detector + hurst-exponent on SPY + 11 SPDR sector ETFs |
| `vs-benchmark-audit` | Workflow | Stocks Basic | Full tearsheet with DSR correction + rolling IC vs benchmark |
| `smart-money-cluster` | Workflow | Stocks Basic | manager-portfolio-diff across curated cohort of well-known filers |
| `pre-earnings-full-stack` | Workflow | Stocks Basic | earnings-blackout + event-study + guidance + analyst + MC sizing |
| `prediction-market-monitor` | REST | (no Massive key) | Kalshi public API for Fed decisions, CPI, GDP, NFP implied probabilities |

## The workflow composites

Composites chain existing tools. Their tier requirement is the max
tier of any sub-skill invoked; every workflow runs on Stocks Starter.

| Workflow | Chains | Min asset access |
|---|---|---|
| `portfolio-review` | 8 sub-skills (regime, rotation, analog, risk, earnings, macro, corp-actions, rebalancer) | Stocks Starter |
| `weekly-brief` | 4 (regime, rotation, macro-calendar 7d, earnings-blackout 7d) | Stocks Basic |
| `morning-brief` | 3 (regime, macro-calendar 2d, news-scanner last-N) | Stocks Basic |
| `preflight-trade` | 4 (technical, earnings, news, corp-actions) | Stocks Starter |
| `earnings-week-prep` | 3 (earnings-blackout + per-print drilldown + technical) | Stocks Starter |
| `historical-comparison` | 2 (event-study + historical-analog-finder) | Stocks Starter |
| `scan-and-frame` | 3-4 (regime, universe, RS, optional factor) | Stocks Starter |
| `stock-one-pager` | 3 (technical, earnings, regime) | Stocks Starter |

## What you get at each step

**Free Basic key.** Eight skills run end to end:

- `universe-builder`
- `corp-actions-reconciler`
- `news-scanner`
- `t+1-settlement-prep`
- `factor-research` (lite mode)
- `earnings-blackout`
- `corporate-actions-scanner`
- `macro-event-calendar`

The 5/min rate cap will throttle fan-out workflows. Single-name lookups
and small screens are fine. Enough to demo the suite and decide whether
the paid step is worth it.

**Any paid stocks plan.** Adds unlimited REST calls and the entire flat-
files S3 bucket. `backtest-data-prep`, full `factor-research`, historical
`event-study`, and historical `slippage-cost` all become accessible
without touching a higher tier.

**Stocks Developer.** Adds ten years of tick-level trades and quotes via
REST. Useful when you want recent-window detail without setting up the
flat-files batch.

**Options Developer.** Adds the options chain with greeks and trade /
quote tick history. Tape is 15-min delayed on Developer; identical
methodology applies but `options-flow` runs on stale prints. Sufficient
for end-of-day review and methodology validation; insufficient for
intraday actionability.

**Options Business.** Adds real-time options chain, real-time trades
and quotes, and the OPRA WebSocket feed. This is the tier `options-flow`
needs for sub-second sweep detection on the live tape. Also unlocks the
real-time IV side of `earnings-drilldown`.

**Crypto Starter or Developer.** Unlocks `crypto-vol-scanner` and the
crypto leg of `portfolio-mark`.

**Stocks Advanced.** Adds real-time stocks and WebSocket streaming for
live `portfolio-mark` and live `slippage-cost`.

## On Massive's "FMV"

Massive sells a proprietary FMV (Fair Market Value) metric, available
only on the Business plan. The skills in this repo use a generic
snapshot → lastTrade → prevDay fallback chain that works on any paid
plan. They're not the same thing, and these skills won't unlock the
paid FMV metric.

## Verifying

Run `npm run audit:requires` to check every skill's `requires.yml`
against the endpoints it actually calls. Drift fails the build. Pricing
and tier names should be re-verified before each release.
