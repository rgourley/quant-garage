---
name: portfolio-review
description: Composite skill that chains market-regime, sector-rotation-signal, risk-report, earnings-blackout, macro-event-calendar, corporate-actions-scanner, and portfolio-rebalancer into one call. Turns the manual 6-command portfolio-review workflow into a single invocation. Emits a headline summary that pulls the most decision-relevant fact from each section plus the full per-section detail below. Use when the operator asks "review my portfolio" or "run the full workflow on my book."
---

# portfolio-review

You hand over positions + book value. The skill runs the full 7-tool
review chain in the right order, threads the shared context (tickers,
weights, lookback windows), skips ETFs on the equities-only tools
(earnings, 8-K), and stitches the outputs into a single briefing.

The headline block is the read-first summary: regime, rotation theme,
next earnings, next macro event, top corporate action, and the
rebalance verdict in six lines. Full per-section detail follows.

## When to invoke

- The operator says "review my portfolio", "run the full workflow",
  "portfolio check", "what should I do with my book"
- Weekly / monthly portfolio hygiene
- After a material market move to sanity-check the book
- Before a large trade to see whether the new sizing survives the
  variance-share cap

## What you need

- `MASSIVE_API_KEY` (Stocks Starter minimum). One skill in the chain
  (corporate-actions-scanner) also hits SEC EDGAR, which is free.
- Positions in the shape `TICKER=WEIGHT,TICKER=WEIGHT,...` where
  weights sum to ~1.0.
- Book value in dollars for the rebalancer's trade-ticket sizing.

## What you get back

**Layer 1 canonical JSON** with a `sections` map keyed by the sub-
skill name, each holding the full sub-payload. The `headline` block
distills one fact from each. `errors` array captures per-section
failures without aborting the run.

**Layer 2 rendered briefing**. Header, headline block (7 lines), then
each section rendered by its own `render()` helper under a titled
divider. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. Parse positions, split into equities vs ETFs (ETFs skipped for
   earnings + 8-K scanners).
2. Run the 7 sub-skills in sequence, sharing a single MassiveClient
   for connection reuse:
   1. `market-regime`
   2. `sector-rotation-signal`
   3. `risk-report`
   4. `earnings-blackout` (equities only)
   5. `macro-event-calendar`
   6. `corporate-actions-scanner` (equities only)
   7. `portfolio-rebalancer` (skippable via `include_rebalance=False`)
3. Build the headline by pulling the top-fact from each sub-payload.
4. Emit the composite payload with per-section detail and errors.

## Foundations used

- All 7 sub-skills. This is a pure composition — no new data pulls
  beyond what the sub-skills already fetch.

## Endpoints used

- Aggregate of every sub-skill's endpoints. Chain sharing a
  MassiveClient means daily-aggs fetches are cached per ticker
  across sub-skills where the cache applies.

## Doesn't handle (yet)

- **Fixed income context.** No rates/credit read yet (waiting on
  fixed-income-context, Part 3 #4).
- **Historical analogs.** Not included in the default chain because
  regime-conditional forecasting is thesis-driven, not portfolio
  review. Callers who want it can invoke historical-analog-finder
  directly.
- **Options context.** Neither options-flow nor options-structure-
  analyzer are part of the default chain; they're view-dependent,
  not review-dependent.
- **No changed-since-last-run diff.** Every review is stateless. A
  future version could take a prior review payload and highlight
  what moved.
