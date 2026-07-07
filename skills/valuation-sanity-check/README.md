# valuation-sanity-check

The analyst on your team has a $250 NVDA target with assumed 28%
growth and 60% margin. Does it survive a peer-distribution sanity
check? The tool runs the reverse-DCF at the current price and tells
you what CAGR is actually priced in. When I ran it, the $250 target
came back understated against the semi peer set, which was the
opposite of what I expected.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.valuation_sanity_check import run, render
payload = run("NVDA", target_price=250,
               assumed_growth=0.28, assumed_margin=0.60, horizon=5)
print(render(payload))
```

### CLI

```bash
python3 examples/run-valuation-sanity-check.py NVDA \
  --target 250 --growth 0.28 --margin 0.60 --horizon 5
```

### Claude Code / LLM tool use

Discovered at `skills/valuation-sanity-check/`. In a Claude Code
session, ask "sanity-check the $250 NVDA target with 28% growth,
60% margin, 5-year horizon" and Claude runs the reverse-DCF plus
peer distribution. Tool-use LLMs consume the `run()` payload
matching [`output-schema.json`](./output-schema.json).

## What you get back

```
NVDA · Valuation sanity check as of 2026-06-25
Target: $250.0 · Current: $195.9 · Implied upside +27.6%

Take: Target requires 28% CAGR x 5y at 60% EBITDA margin. Peer median is 32% / 26%. Target's implied multiples sit BELOW the peer band; gap means thesis is undemanding.

Multiple sanity: EV/Sales 7.0x, EV/EBITDA 11.6x, P/E 14.7x — all BELOW peer 25-75 band
Reverse-DCF at $195.9: implied 5yr CAGR 0% vs peer median 32% (-32.7pp air)
Fair value at peer-median growth: $826.2
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`peer-selection.md`](./references/peer-selection.md) — three-layer waterfall, cross-links to `pitch-comps`
- [`multiple-sanity.md`](./references/multiple-sanity.md) — target-implied multiples vs peer 25-75 band
- [`growth-margin-sanity.md`](./references/growth-margin-sanity.md) — assumed growth and margin vs cohort distribution
- [`reverse-dcf.md`](./references/reverse-dcf.md) — solving for the revenue CAGR the market is pricing in
- [`take-generator.md`](./references/take-generator.md) — banker-tone bold-take and closing-read
- [`rendering.md`](./references/rendering.md) — sell-side flash-note format inherited from `earnings-drilldown`

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST plus fundamentals. Free
Basic runs slowly with curated peer overrides only. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
