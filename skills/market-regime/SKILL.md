---
name: market-regime
description: Daily macro context. Pulls SPY trend (5 buckets via 20/50/200-day SMA stack), VIX state with percentile rank vs the trailing year, breadth proxy from 11 sector ETFs above their own 50-day / 200-day SMAs, and 20-day relative-strength sector leadership. Combines the four blocks into a single composite regime label (risk_on, risk_off, mixed_risk_on, mixed_risk_off, neutral) with explicit reasons[] so the operator sees the evidence, not just the label. Anchor every research session with this; closest competitor (Jow Dones) leads with the same idea. Use when the day's question is "what's the tape doing right now" or "is this still a risk-on regime."
---

# market-regime

You hand over nothing — the skill defaults to SPY + the 11 GICS sector
SPDRs + VIX. It returns a single regime label backed by four
independently-computed evidence blocks, with no opinionated tone and
no editorializing.

This is the morning-briefing tool. Run it once before opening any
single-name research; the regime label decides whether you're looking
for breakouts (risk_on) or pullback entries (mixed) or capital
preservation (risk_off).

## When to invoke

- The operator opens the day and asks "what's the regime" or "what's
  the market doing right now"
- A research session starts and you want the macro frame before
  drilling into a name (so a value pitch in a stressed-VIX risk_off
  tape gets weighed differently than the same pitch in risk_on)
- The user says "morning brief", "market check", "is this still
  risk-on", "what sectors are leading"
- A different skill (factor-research, event-study, options-flow) wants
  to qualify its read with the current regime, e.g. "momentum IC is
  positive in a confirmed risk-on regime; here's the regime block"

## What you need

- `MASSIVE_API_KEY` exported. Stocks Starter or higher is sufficient
  (the skill is one daily-aggs call per of 13 tickers — SPY, VIX, 11
  sector ETFs — well under the Starter rate limit).

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Four blocks (`spy_trend`, `vix_state`, `breadth`, `sector_leadership`)
plus the composite (`composite_regime` with `label` and `reasons[]`)
and the per-source provenance. UIs, downstream agents, and other
skills that need a regime tag consume this directly.

**Layer 2: rendered briefing**. PM-facing morning briefing format. See
[`references/rendering.md`](./references/rendering.md). The header is
the label in ALL CAPS, each block renders one stanza, and a one-line
adaptive "Take" closes the report — adaptive in the sense that the
take is keyed off the actual readings (which pillars confirm, which
gap, what to watch for a regime change), not hardcoded per label.

## How it works

1. **Pull daily aggregates** for SPY, VIX, and the 11 sector ETFs
   (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC). Window
   is `lookback_days * 1.6` calendar days back (covers weekends +
   holidays). One REST call per ticker via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`. Cached
   per-ticker module-level so re-using the same ticker (SPY as both
   trend subject and RS denominator) is one network call, not two.

2. **VIX fallback.** Try `VIX` first. If the response has no rows,
   retry with `I:VIX` (Massive's indices namespace). If both fail,
   surface a `tier_caveat`: "VIX data unavailable; regime read
   computed without volatility component" and proceed without the VIX
   block. The composite regime still resolves on the remaining three.

3. **Compute SPY trend.** Latest price, SMA(20), SMA(50), SMA(200).
   Trend bucket via the SMA stack ordering per
   [`references/regime-taxonomy.md`](./references/regime-taxonomy.md):
   - `uptrend_strong` — price > 20 > 50 > 200
   - `uptrend_weak`   — price above 50 and 200 but not stacked
   - `range`          — mixed (price above some, below others)
   - `downtrend_weak` — price below 50 and 200 but not stacked
   - `downtrend_strong` — price < 20 < 50 < 200

   Plus 1-day, 5-day, 20-day price change.

4. **Compute VIX state.** Current level, percentile rank vs the
   trailing `lookback_days` (via `lib.quant_garage.percentile_rank`),
   the `rank_label`, the absolute-level state bucket:
   - `quiet` < 15
   - `normal` 15 to 22
   - `elevated` 22 to 30
   - `stressed` >= 30

   Plus the 20-day average for context.

5. **Compute breadth.** Sector-ETF proxy: count of sector ETFs whose
   latest close is above their own 50-day SMA, and above their own
   200-day SMA. Reported as percentages. Surfaced as a caveat that
   this is **not** the full advance/decline line; it's a 13-ticker
   proxy that captures the same risk-on / risk-off story but not
   fine-grain breadth divergences. See
   [`references/breadth-methodology.md`](./references/breadth-methodology.md)
   for why this proxy is good enough for a regime read.

6. **Compute sector leadership.** For each of the 11 sector ETFs:
   1-day, 5-day, 20-day return. Relative strength vs SPY: 20-day RS
   delta in basis points (sector_20d - spy_20d). Sorted by 20-day RS;
   the top 3 are leaders, bottom 3 are laggards.

7. **Compute the composite regime.** Combine the four blocks per
   [`references/regime-taxonomy.md`](./references/regime-taxonomy.md):
   - `risk_on` — SPY uptrend + VIX quiet/normal + breadth > 50% above
     50-day + growth sector leadership (>= 2 of XLK/XLY/XLC in top 3)
   - `risk_off` — SPY downtrend + VIX elevated/stressed + breadth < 50%
     + defensive sector leadership (>= 2 of XLP/XLU/XLV in top 3)
   - `mixed_risk_on` — SPY uptrend but at least one negative offset
     (narrow breadth, rising VIX, defensive leadership)
   - `mixed_risk_off` — SPY downtrend with at least one positive offset
     (recovering breadth, VIX retreating, growth returning)
   - `neutral` — SPY in `range` (no clear directional read)

   Each label ships with explicit `reasons[]` so the operator can see
   which pillars supported the call.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth and
  the daily-aggs endpoint

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`:
  one call per ticker (SPY, VIX, XLK, XLF, XLE, XLV, XLY, XLP, XLI,
  XLB, XLU, XLRE, XLC). 13 calls per run, cached per ticker.

## Doesn't handle (yet)

- **Full advance/decline breadth.** Breadth is a sector-ETF proxy.
  Real A/D from a snapshotted US equity universe would catch breadth
  divergences (e.g. S&P up but most names below their 50-day) that the
  11-ticker proxy can miss. Clean PR extension: swap the sector loop
  for a universe scan from `universe-builder`. Documented as a tier
  caveat in every run.

- **Macro overlay.** No interest-rate (US10Y), credit (HYG/LQD spread),
  dollar (DXY), or commodity (GLD/USO) inputs. The regime is equity-
  only for v1. A real PM macro frame folds in at least 10Y and DXY;
  that's a clean follow-on skill (or an extension here) once the equity
  regime is stable.

- **Intraday / weekly regimes.** Daily close only. An "intraday regime"
  (SPY 5-min trend + VIX intraday + sector RS on the day) is a
  different cadence and a different skill.

- **Regime change-point detection.** The skill returns today's label
  but doesn't tell you the last time the regime flipped or how long
  this regime has been live. A regime-history time series would be a
  clean Layer-2 addition; the output schema reserves space for it.

- **Custom universes.** The 11 GICS sector SPDRs are hardcoded
  (canonical for US equity regime work). A version parameterized on a
  different universe (e.g. global sector ETFs, factor ETFs) is a clean
  PR extension.

These are clean PR extensions. The output schema reserves space for
each so adding them later doesn't break consumers.
