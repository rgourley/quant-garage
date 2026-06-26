# earnings-drilldown

You're long NVDA into Thursday's print. Trim, hold, or fade the
straddle? Run the tool. You get the implied move vs the 8-quarter
realized average, the post-earnings drift t-stat conditional on the
reaction direction, and which semis trade with NVDA on print days.
Output reads like a sell-side morning note: bold take at the top,
supporting numbers below.

## Quick start

```bash
python3 examples/run-tier-b.py NVDA
```

## What you get back

```
AAPL: Q3 2026 Preview
Print: Thu 2026-07-30 AMC · Consensus: $1.89 EPS, $109.0B rev

**Take:** Straddle prices 4.2pp above 8q realized (implied ±5.8%, realized ±1.6%). Premium sellers have a setup; long holders can fade IV crush.

Implied vs realized: implied ±5.8% · realized 8q avg ±1.6% · IV30 26.0 (61st %ile)
Print history: 8/8 beats, avg surprise +4.7% EPS · best +3.2%, worst −3.7%
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`data-source-tiers.md`](./references/data-source-tiers.md) — Benzinga, SEC EDGAR, stocks-only tiers
- [`implied-vs-realized.md`](./references/implied-vs-realized.md) — straddle vs 8q realized, IV30, closest analog
- [`peer-reaction.md`](./references/peer-reaction.md) — three-layer peer waterfall and basket reaction
- [`post-earnings-drift.md`](./references/post-earnings-drift.md) — T+1 to T+5 drift with t-stat and significance
- [`print-history.md`](./references/print-history.md) — surprise, beat rate, GAAP vs adjusted method mix
- [`rendering.md`](./references/rendering.md) — sell-side morning-note format

## Plan requirement

Stocks Starter ($29/mo) runs Tier B (reaction-sign bucketing, no
consensus EPS). Add Benzinga Earnings (~$99/mo) for Tier A
beat/miss bucketing. Full mode adds Options Developer ($79/mo) for
straddle pricing. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
