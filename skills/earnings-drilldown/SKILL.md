---
name: earnings-drilldown
description: Produce a sell-side-grade earnings preview for a single ticker: implied vs realized move, beat/miss history, post-earnings drift, peer reaction, and a one-line take. Use when an analyst, PM, or trader is preparing for a specific company's earnings print. Lite mode runs on Stocks Starter; full mode adds IV crush analysis and needs Options Developer.
---

# earnings-drilldown

You hand over a ticker and the upcoming print date. The skill returns a
sell-side-quality preview: a bold take, implied vs realized move
comparison, eight-quarter beat/miss history, post-earnings drift
pattern, peer reaction analysis, and the catalysts to watch in the
print.

This is the "what does a senior analyst already know walking into the
print" workflow. The take at the top is the trade: is the straddle
mispriced, is consensus too low, is the drift pattern favorable to
holders through T+5.

## When to invoke

- An analyst is writing a morning note for tomorrow's print
- A PM is sizing a position into an earnings catalyst
- A trader is deciding whether to fade the straddle
- The user says "preview $TICKER earnings", "what's priced in for X",
  or "should I hold X through earnings"

## What you need

- A ticker symbol
- Optional: the print date (the skill looks it up if omitted)
- `MASSIVE_API_KEY` exported in the environment

The skill runs at three fidelity tiers depending on what your key
unlocks. See [`references/data-source-tiers.md`](./references/data-source-tiers.md)
for the full matrix. Short version:

- **Tier A (full fidelity):** Stocks Starter + Benzinga Earnings.
  True press release dates, consensus EPS, surprise %, classical
  beat/miss bucketing. ~$130/m combined.
- **Tier B (degraded but usable):** Stocks Starter only. 8-K acceptance
  date as print date proxy (24-48hr lag from press release). No
  consensus, so beat/miss replaced by reaction-sign bucketing. Implied
  vs realized still fully works. $29/m.
- **Tier C (free basic):** 5 calls/min throttle makes a single run
  take ~10 minutes. Documented but not actively supported. The skill
  warns and runs at Tier B.

The skill detects available data and picks the highest tier it can
serve, then flags the choice in the output JSON as `tier` with any
caveats.

## What you get back

The skill ships two output layers.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Every analysis ships its underlying datapoints, sample sizes, statistical
tests, and the source endpoint for each Massive call. UIs, dashboards,
and downstream agents consume this.

**Layer 2: rendered note** in sell-side morning-note format. See
[`references/rendering.md`](./references/rendering.md) for full rules.
Claude Code users read this.

## How it works

The skill runs three analyses by default plus an optional fourth, each
documented in its own reference:

1. **Implied vs realized move** ([`references/implied-vs-realized.md`](./references/implied-vs-realized.md)):
   front-week straddle pricing vs realized 8-print average, with IV30
   percentile vs trailing year and the closest historical analog.
2. **Print history** ([`references/print-history.md`](./references/print-history.md)):
   beat rate, average surprise on EPS and revenue, best and worst
   reactions over the last 8 quarters.
3. **Post-earnings drift** ([`references/post-earnings-drift.md`](./references/post-earnings-drift.md)):
   abnormal returns T+1 to T+5 conditional on beat vs miss, with t-stats
   so the user knows whether the pattern is significant or noise.
4. **Peer reaction** ([`references/peer-reaction.md`](./references/peer-reaction.md)):
   how sector peers traded same-day on this name's past prints, with
   per-peer betas to the print-day return. Tier A `run-aapl.py` emits
   `peer_reaction: null` with a `peer_reaction_note` explaining the
   skip; SIC-based selection misclassifies mega-cap tech and a curated
   override list lands separately. Tier B `run-aapl-tier-b.py` runs the
   analysis against an explicit `PEER_OVERRIDES` map.

The take at the top of the rendered output is generated from whichever
analyses produced data: it surfaces the most actionable insight,
usually the implied-vs-realized mispricing or a sharp PEAD pattern.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST patterns,
  the best-price fallback chain, and rate limit handling
- Optional: [`massive-flat-files`](../massive-flat-files) if a user
  wants historical IV across more than ~2 years of prints (the
  options aggregates endpoint covers recent prints; deeper history
  needs flat files)

## Mode: lite vs full

Mode is independent of tier. Mode controls whether the implied-vs-realized
analysis runs. Tier controls how the print history and PEAD analyses
are bucketed (see data-source-tiers.md).

- **Lite mode** (no options data): skips implied vs realized. Other
  three analyses still run, at whichever tier the key supports.
- **Full mode** (Options Developer or higher): runs all four analyses.

The skill detects available data and adjusts. Lite mode + Tier B is
the cheapest workable combination ($29/m Stocks Starter alone): three
analyses, all reaction-based bucketing, no implied move. Lite mode +
Tier A adds beat/miss bucketing. Full mode + Tier A is the maximum
fidelity output.

## Endpoints used

Tier A (with Benzinga):
- `GET /benzinga/v1/earnings?ticker={ticker}&limit=20&order=desc&sort=date`:
  press release date + time + consensus + actuals + surprise % in one
  call. Canonical when available.

Tier B (Stocks-only fallback):
- `GET /v1/reference/sec/filings?ticker={ticker}&form_type=8-K&limit=20`:
  8-K filings (filter client-side for items containing "2.02" to isolate
  earnings filings). Acceptance date used as print date proxy.
- `GET /vX/reference/financials?ticker={ticker}&limit=8`: EPS and revenue
  actuals only (no consensus). Do NOT use the `filing_date` from this
  endpoint as the print date: it's the 10-Q filing date, weeks after
  the press release.

Both tiers:
- `GET /v2/aggs/ticker/{ticker}/range/...`: historical price aggregates
- `GET /v3/snapshot/options/{ticker}`: current options snapshot (full mode)
- `GET /v3/reference/tickers/{ticker}`: sector classification
- `GET /v2/aggs/ticker/SPY/range/...`: SPY closes for PEAD beta-adjustment
- Optional: `GET /v2/aggs/ticker/O:{occ_ticker}/range/...` for historical
  IV (full mode only)

Verify endpoint paths against current docs at massive.com/docs before
shipping; field names and versions shift.

## Doesn't handle (yet)

- Whisper numbers (no analyst whisper data via Massive)
- Pre-announce or guidance revisions (would need a news endpoint
  integration; queued for `news-scanner` interop)
- Multi-ticker comparison (one ticker per call; for cross-sectional
  reaction across many names, use `event-study` instead)
- Index-level previews (skill is single-name)

These are clean PR extensions and welcome contributions.
