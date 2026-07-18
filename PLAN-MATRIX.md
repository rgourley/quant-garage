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
| `market-regime` | REST | Stocks Starter | Daily aggs for SPY + VIX + 11 sector ETFs; runs on free Basic too, slower. VIX is an index (`I:VIX`), not an equity; Massive may not expose it, in which case the VIX pillar is permanently absent and the regime is computed on trend + breadth + leadership with a caveat (not a bug, not an add-on you can buy). On Free Basic, use `--sleep 13` so the 13-series batch stays under the 5-calls/min cap. |
| `relative-strength` | REST | Stocks Starter | Daily aggs per ticker + benchmark; runs on free Basic too, slower |
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

## What you get at each step

**Free Basic key.** Six skills run end to end:

- `universe-builder`
- `corp-actions-reconciler`
- `news-scanner`
- `t+1-settlement-prep`
- `factor-research` (lite mode)
- `earnings-blackout`

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
