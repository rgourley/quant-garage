# factor-research

You want to know what factor is working in the current regime. Run
the tool on the S&P 500 over a 5-year window. You get the per-factor
IC at 1M/3M/6M/12M horizons, t-stats, decile long-short spreads, hit
rates, and the cross-factor correlation matrix. Quality at +3.1
t-stat is the only single factor with statistical significance right
now. Low-vol is negative across every horizon (recent regime rewards
risk-taking).

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.factor_research import run, render
payload = run(universe_size=500, years=5)
print(render(payload))
```

### CLI

```bash
python3 examples/run-factor-research.py
```

### Claude Code / LLM tool use

Discovered at `skills/factor-research/`. In a Claude Code session,
ask "what factor is working in this regime" or "run factor
research on the S&P 500 over 5 years". Tool-use LLMs consume the
`run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Factor research: top 500 mcap · 5y window (2021-06 → 2026-06) · 4 factors

| Factor             | 1M IC | 12M IC | t-stat (1M) | 12M decile spread |
|--------------------|------:|-------:|------------:|------------------:|
| Momentum (12-1M)   | +0.03 |  +0.03 |        +1.2 |            -22.5% |
| Low-Vol (1/realiz) | -0.06 |  -0.20 |        -1.6 |            -99.8% |
| Value (1/(P/B))    | +0.02 |  +0.14 |        +0.9 |            +52.5% |
| Quality (ROE)      | +0.05 |  +0.17 |        +3.1 |             -2.9% |

Quality is the only single factor with stat-sig IC (+3.1 t-stat).
Low-vol is negative across every horizon (recent regime rewards risk).
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`factor-definitions.md`](./references/factor-definitions.md) — Momentum (12-1), Low-Vol, Value, Quality formulas
- [`information-coefficient.md`](./references/information-coefficient.md) — rank correlation between factor score and forward return
- [`decile-analysis.md`](./references/decile-analysis.md) — long-short decile spreads as the dollars companion to IC
- [`factor-correlation.md`](./references/factor-correlation.md) — signal correlation, not return correlation
- [`universe-construction.md`](./references/universe-construction.md) — universe choice as research decision, survivorship caveats
- [`rendering.md`](./references/rendering.md) — table-mode format inherited from `universe-builder`

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST plus flat-files entitlement
(bulk daily aggregates). Free Basic runs a degraded single-factor
demo on a small universe. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
