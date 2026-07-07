---
name: scan-and-frame
description: Research-tier idea generation with regime framing. Chains market-regime (context) + universe-builder (candidates) + relative-strength (rank candidates vs SPY). Optionally adds factor-research for broader factor context (heavy, off by default). Different from portfolio-review (positions) and weekly-brief (macro-only) — this is discovery mode.
---

# scan-and-frame

Discovery-mode composite. Runs universe-builder with the operator's
filters, ranks the top N by relative strength vs SPY, and frames the
whole thing with the current market regime. Optional heavy factor-
research pass for factor context.

## When to invoke

- Analyst says "what should I look at right now", "find me candidates
  in X sector", "screen for momentum names"
- Weekly / periodic universe scan with regime context
- Different from portfolio-review (positions) and stock-one-pager
  (retail single-name)

## Modes

- **Fast** (default): market-regime + universe-builder + relative-
  strength. Runs on Stocks Starter, ~10-30s depending on universe size.
- **Full** (`include_factor_research=True`): also runs factor-research.
  Heavy — 3-year factor panel over 200-name universe. Use for weekly
  cadence, not per-run.

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Headline: regime, universe count, top-5 RS-ranked candidates, top
factor (if factor-research ran).

**Layer 2 rendered brief**. See
[`references/rendering.md`](./references/rendering.md).
