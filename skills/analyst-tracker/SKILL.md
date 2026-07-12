---
name: analyst-tracker
description: Track sell-side analyst positioning on a name via Benzinga Analyst Ratings. Pulls every rating event over the lookback window, classifies each as upgrade / downgrade / initiation / reiteration / drop-coverage / PT-change, aggregates the latest rating and price target per firm, and reports the current consensus (median PT across firms plus buy/hold/sell distribution). Uses Massive's built-in Benzinga integration. Requires Stocks Basic + Benzinga Analyst Ratings entitlement.
---

# analyst-tracker

You hand over a ticker. The skill pulls every analyst rating event
Benzinga has on record over the lookback window, buckets each firm's
raw rating string to a standard scale (buy / hold / sell), classifies
the event (upgrade / downgrade / initiation / reiteration / PT-change /
drop-coverage), aggregates the latest per firm, and reports the
consensus median price target plus buy/hold/sell distribution.

This is the sell-side lens the repo was missing. Pairs cleanly with
`insider-flow` (internal vs external sentiment on the same name)
and `guidance-tracker` (analyst reaction vs management's own
projections).

## When to invoke

- A PM asks "how is sell-side positioning shifting on NVDA?"
- Fundamental analyst wants to see who's changed their view and by
  how much
- Screening for consensus PT vs current price gap as a mean-reversion
  cue
- The user says "analyst ratings", "sell-side", "upgrades",
  "downgrades", "price target", "consensus"

Not for: analyst quality or track record (Benzinga returns the ratings,
not the historical accuracy). Not for real-time (Benzinga updates
within minutes but this is not a tick feed).

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported
- Stocks Basic plan + **Benzinga Analyst Ratings** entitlement.
  The endpoint returns NOT_AUTHORIZED without it; skill emits a
  clean tier caveat rather than a raw error.

Optional:

- `--lookback-days` (default 180): calendar-day window back from today.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-event: `date`, `firm`, `analyst`, `rating_raw`, `rating_bucket`
(buy / hold / sell / other / unknown), `previous_rating_raw`,
`event_label` (upgrade / downgrade / initiation / reiteration /
price_target_change_only / drop_coverage / other), `price_target`,
`previous_price_target`, `pt_direction`, `pt_delta_pct`. Top-level
`summary` with event counts, per-firm latest rating distribution,
and consensus median / low / high price target.

**Layer 2: rendered note**. Header + summary line + rating
distribution + consensus PT, timeline of the last 25 events, one-line
Take. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull ratings** via
   `GET /benzinga/v1/ratings?ticker={T}&date.gte={D}&limit=1000&sort=date.desc`.
2. **Bucket rating strings** using standard synonym sets. Buy set:
   buy, strong buy, outperform, overweight, positive, add,
   accumulate, top pick. Hold set: hold, neutral, market perform,
   equal-weight, in-line. Sell set: sell, underperform, underweight,
   reduce, negative. Anything else lands in "other".
3. **Classify each event.** Rating actions like initiates_coverage_on,
   upgrade, downgrade, maintains, reiterates map directly. When
   Benzinga omits the action but the bucket changed, infer
   upgrade/downgrade from the bucket transition.
4. **Detect price-target-only changes.** Reiterated rating + changed
   PT gets labeled `price_target_change_only`.
5. **Aggregate latest per firm.** Because ratings come newest-first,
   the first row per firm gives the latest state. Median across
   firms is the consensus PT; buy/hold/sell counts describe the
   distribution.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and pagination.

## Output mode: note

Narrative note with a timeline. A single-name analyst history is
typically 20-100 events over 6 months; note format reads better than
a wide table.

## Endpoints used

- `GET /benzinga/v1/ratings?ticker={T}&date.gte={D}` — every rating
  event for the ticker in the window.

## Doesn't handle (yet)

- **Firm track record.** No historical accuracy metric per firm or
  analyst.
- **Consensus rating vs consensus PT reconciliation.** Firms often
  raise PT while keeping a Hold rating (or vice versa); these are
  reported separately, not reconciled.
- **Guidance overlay.** Pair with `guidance-tracker` for a workflow
  composite: "did the analyst move after management guided?"
- **Bulls Bears Say text.** The endpoint returns rating actions and
  PTs; the narrative from Bulls Bears Say is a separate endpoint.
- **Peer comparison.** No "is NVDA's PT delta above peer set median?"
  Queued as a composite.

These are clean PR extensions. Output schema is forward-compatible.
