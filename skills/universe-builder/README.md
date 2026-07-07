# universe-builder

You want every US common stock above $20 with quarterly +10% momentum
that pulled back this week. Run the screen. The tool walks the full
12,000-name universe, applies your filter chain, ranks the survivors
by composite z-score, and flags sector concentration. Last week's
run surfaced a trucking cluster (ARCB, RXO, SNDR, TFII, WERN, SAIA)
all off the same macro freight pullback. Real mean-reversion
candidates.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.universe_builder import run, render
payload = run(candidate_source="curated", min_mcap=10e9)
print(render(payload))
```

### CLI

```bash
python3 examples/run-universe-builder.py \
  --min-price 20 --min-adv 400000 --min-mom-3m 0.10 --max-week-return 0.0
```

### Claude Code / LLM tool use

Discovered at `skills/universe-builder/`. In a Claude Code session,
ask "find me every US name above $20 with quarterly momentum that
pulled back this week". Tool-use LLMs consume the `run()` payload
matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Filter chain -> 279 names from 12,245

Rank  Ticker     Price  MCap($B)  3M Mom  Wk Ret  Z-score
1       SPCX  $154.60   2,056.6  +607.7%   -3.9%    +5.68
2       BAND   $50.82       1.7  +207.4%  -22.2%    +2.93
3       ARCB  $145.60       3.2   +58.2%  -15.9%    +1.25
4       STLD  $250.98      35.1   +50.6%  -11.2%    +1.24
5      GOOGL  $349.68   4,223.7   +15.8%   -2.8%    +1.16

Survival: 12,245 → 7,549 (price) → 2,803 (mom) → 1,041 (ADV) → 345 (week) → 279 (CS)
Concentration: 7 Software in top 20 (+4.2σ vs starting universe)
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`filtering-methodology.md`](./references/filtering-methodology.md) — ordered filter chains and predicate composition
- [`composite-zscore.md`](./references/composite-zscore.md) — combining factors into a single ranking metric
- [`concentration-analysis.md`](./references/concentration-analysis.md) — sector and industry concentration detection in the top-N
- [`survivorship-handling.md`](./references/survivorship-handling.md) — keeping delisted names in the lookback
- [`rendering.md`](./references/rendering.md) — canonical table-mode format (template for `factor-research`, `pitch-comps`)

## Plan requirement

Runs on free Stocks Basic with a curated seed pool. Stocks Starter
($29/mo) unlocks the full 12k-name candidate pool via the bulk
grouped-aggregates endpoint and sub-2-minute runtime. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
