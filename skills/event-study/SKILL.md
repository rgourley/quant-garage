---
name: event-study
description: Measure abnormal returns around a corporate event for one or many tickers. Three input modes pick the output shape automatically: single ticker + single event renders a sell-side note (with t-stat vs that name's reaction distribution); many tickers + one event class renders a cross-section table; many events + many tickers renders aggregate statistics. Supports earnings (Benzinga or SEC EDGAR fallback), dividend changes, and computed volume spikes out of the box. Generalizes earnings-drilldown's PEAD work to any event class.
---

# event-study

You hand over an event (a date + a class) and either one ticker or a
basket. The skill measures abnormal returns over the event window,
compares each reaction to the name's own history, and aggregates across
the cross-section when the input is wider than one event.

This is the workflow a quant or event-driven PM runs when asking
"did the market actually react to this," "is the cross-section
consistent," or "is this kind of event a tradeable signal." The output
matches the format an analyst already reads: morning-note style for a
single event, screener table for a cross-section, summary stats for an
aggregate.

## When to invoke

- A PM is sizing into a name post-print and wants to know "what's the
  T+5 base rate after a beat like this"
- A quant is testing whether dividend hikes (or cuts) lead to
  measurable abnormal returns across a sector
- A trader saw an unusual volume day on a peer and wants to know
  whether the event class historically resolves
- The user says "event study on X", "what's the average abnormal
  return after Y", "did the market price in Z", or "cross-section
  reaction across mega-cap tech earnings"

## Three modes (determined by input shape)

The same code path runs all three; the shape of `--tickers` and
`--event-date` (vs `--window`) picks the output mode.

### Mode 1: single (single ticker, single event)

Input: `--ticker NVDA --event-date 2026-05-20 --event-class earnings`

Output: a sell-side note with the event window returns, t-stat of this
event's abnormal return vs the name's prior reaction distribution, and
a one-line take. Matches the layout of
[`../earnings-drilldown`](../earnings-drilldown) but generalized to any
event class. See [`references/rendering.md`](./references/rendering.md).

### Mode 2: cross-section (many tickers, one event period)

Input: `--tickers AAPL,NVDA,MSFT,GOOGL,META --event-class earnings --period 2026Q2`

Output: a comparison table (one row per ticker), plus a "Cross-section"
footer with the average T+5 CAR, the median, the t-stat of the average
against zero, and the correlation between surprise magnitude and
reaction.

### Mode 3: aggregate (many tickers, many events)

Input: `--tickers AAPL,NVDA,MSFT,GOOGL,META --event-class earnings --window 2025-06-01..2026-06-24`

Output: only the aggregate statistics. Average CAR by horizon (T+1,
T+3, T+5), t-stat against zero, percentile distribution, n. No
per-event detail in the rendered output (it's in the JSON for UIs).
Used for "is this event class a tradeable signal at all" questions.

## Event classes supported

| Class | Source | Trigger definition |
|---|---|---|
| `earnings` | Benzinga (Tier A) or SEC EDGAR 8-K item 2.02 (Tier B) | Press release date + time |
| `dividend_changes` | `/v3/reference/dividends` | First dividend whose amount differs from the prior payment by ≥1% |
| `large_volume_spike` | computed from `/v2/aggs/ticker/{T}/range/1/day/...` | Days where volume > 3σ of the trailing 30d mean, with a 5-day cooldown |

Each class has its own resolution helper documented in
[`references/event-class-definitions.md`](./references/event-class-definitions.md).
Adding a new event class is a clean PR: implement the resolver, add
a row to the table above, and the skill picks it up.

Out of scope for v1: analyst upgrades/downgrades (the Benzinga
analyst-ratings endpoint wasn't reliably reachable in prior sessions;
queued for v2), index inclusions/exclusions, M&A announcements.

## What you need

- A list of tickers (one or many)
- Either a specific event date or a window
- `MASSIVE_API_KEY` exported

Tiers:

- **Tier A** (full fidelity for earnings): Stocks Starter + Benzinga
  Earnings. True press release dates, consensus, surprise %, allows
  the "surprise vs reaction" correlation column in cross-section.
- **Tier B** (degraded earnings): Stocks Starter only. 8-K item 2.02
  acceptance date as print date; no surprise %, so the cross-section
  drops the surprise-vs-reaction correlation and falls back to
  reaction-sign bucketing. `dividend_changes` and
  `large_volume_spike` run identically on either tier.

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching
[`output-schema.json`](./output-schema.json). Discriminated by
`output_mode`: `single`, `cross_section`, or `aggregate`. Each mode
exposes the per-subject `event_window_returns`, `abnormal_returns`,
and `t_stat_vs_history`. Cross-section and aggregate add the
cross-sectional `summary` block, which includes `distribution_shape`
(KDE-derived n_modes, modality label, tail label, skew, excess
kurtosis, sparkline) when n_subjects >= 10 so bimodal or fat-tailed
reactions surface instead of hiding behind a benign mean. UIs and
downstream agents consume this.

**Layer 2: rendered output** in hybrid mode:
- `single` → sell-side note
- `cross_section` → comparison table + cross-section footer
- `aggregate` → summary stats block

See [`references/rendering.md`](./references/rendering.md) for the
full rules.

## How it works

The pipeline is the same regardless of input shape; what changes is
how the rendering layer collapses the result.

1. **Resolve events.** Per
   [`references/event-class-definitions.md`](./references/event-class-definitions.md),
   convert the input (ticker + class + date-or-window) into a list of
   concrete `(ticker, event_date, event_metadata)` tuples.
2. **Pull daily aggregates** for each ticker and SPY across the
   union of event windows plus a 30-day buffer on either side.
3. **Compute abnormal returns** per
   [`references/abnormal-returns.md`](./references/abnormal-returns.md).
   AR = raw_return − SPY_return at each horizon (T0, T+1, T+3, T+5).
   CAR = sum of ARs from T+1 through the horizon.
4. **Compute t-stats** per
   [`references/t-stat-significance.md`](./references/t-stat-significance.md).
   For a single event, compare this event's T+5 CAR to the name's
   prior reaction distribution. For a cross-section, t-stat is the
   mean CAR across events vs zero. Both require n≥8 to be reported
   as significant; below that, the rendered output prints the t-stat
   but marks it "underpowered."
5. **Aggregate cross-sectionally** per
   [`references/cross-section-methodology.md`](./references/cross-section-methodology.md)
   when `n_subjects > 1`. Average CAR, median, t-stat vs zero, and
   the Pearson correlation between event magnitude and reaction.
6. **Detect regime stability** per
   [`references/regime-stability.md`](./references/regime-stability.md):
   for any aggregate-mode result, compare the most-recent 4 events to
   the full window mean and flag when the gap is >1σ. Recent regime
   often differs from headline number.
7. **Generate the take** off the strongest signal: significant
   t-stat, regime shift, or surprise-vs-reaction correlation in
   cross-section.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  pagination, and the fallback chain.

## Endpoints used

Earnings event class:
- `GET /benzinga/v1/earnings?ticker={T}&limit=20&order=desc&sort=date`
  (Tier A): press release date, time, surprise %, fiscal period.
- `GET https://data.sec.gov/submissions/CIK{cik}.json` (Tier B
  fallback): SEC EDGAR 8-K filings filtered to item 2.02. Free,
  public, no API key required. Same date-resolution logic as
  `earnings-drilldown` Tier B.

Dividend change event class:
- `GET /v3/reference/dividends?ticker={T}&limit=20&order=desc&sort=ex_dividend_date`:
  cash dividend history; the resolver picks the first ex-date where
  the cash amount differs from the prior payment by ≥1%.

Volume spike event class:
- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}`: same daily
  aggregates used for the return computation; the resolver computes
  volume z-score in-memory.

All classes:
- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}`: daily closes
  for the ticker. One call per ticker.
- `GET /v2/aggs/ticker/SPY/range/1/day/{from}/{to}`: daily closes
  for SPY (the benchmark). One call total.

## Doesn't handle (yet)

- **CAPM-style abnormal returns.** The skill uses a simple SPY-naive
  benchmark (AR = raw − SPY). A v2 would estimate per-name beta on
  the 60-day pre-event window and compute AR = raw − (alpha + beta *
  SPY). For mega-caps the difference is small (beta is close to 1);
  for higher-beta names it matters. Schema reserves `model: "spy" |
  "capm"` so the upgrade doesn't break consumers.
- **Multi-day pre-event run-up.** Some event types (M&A leaks,
  guidance pre-announces) show abnormal returns before the official
  event date. The skill measures from T0 forward only.
- **Sample-aware significance.** Below n=8, t-stats are reported but
  marked "underpowered" rather than computing a small-sample
  correction. Bootstrap CIs would be cleaner; queued.
- **Event clustering.** When multiple events fire in the same window
  (e.g. earnings + dividend hike same week), the skill attributes
  the full return to whichever event the user asked about. A cleaner
  treatment would attribute by cross-section dummy; queued.
- **Intraday windows.** Event windows are daily closes only. No
  pre-market or 30-minute reaction measurement.

These are clean PR extensions and welcome contributions.
