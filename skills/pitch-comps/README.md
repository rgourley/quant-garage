# pitch-comps

You're a junior banker or an equity research analyst building a CRM
comp set. You need the software peer group with multiples, growth,
EBITDA margin, plus a regression-adjusted view that controls for the
growth differential. Run it, get a table you can drop straight into
the deck or the model. The one-sentence read at the bottom is the
take the MD or PM wants on page two.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.pitch_comps import run, render
payload = run("CRM")
print(render(payload))
```

### CLI

```bash
python3 examples/run-pitch-comps.py CRM
```

### Claude Code / LLM tool use

Discovered at `skills/pitch-comps/`. In a Claude Code session,
ask "build a CRM comp set" or "how does NVDA screen vs semis on
multiples". Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
CRM: comp set as of 2026-06-23 · 8 peers (curated_override)

Ticker         EV/Sales  EV/EBITDA   P/E  Rev Growth  EBITDA Mgn
CRM (subject)      4.0x      13.7x  19.9x       +11%        29%
ORCL               8.5x      27.6x  36.9x       +15%        31%
ADBE               3.3x       9.1x  11.3x       +14%        36%
INTU               3.8x      13.8x  15.6x       +15%        27%
Median             7.0x      29.4x  35.9x       +15%        13%

Regression-adjusted EV/EBITDA: 4.2x vs subject 13.7x → 227% premium

Read: CRM screens cheap on raw multiples. The biggest gap is on EV/EBITDA (54% discount to peer median).
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`peer-selection.md`](./references/peer-selection.md) — three-layer waterfall, shared with `earnings-drilldown`
- [`multiples-methodology.md`](./references/multiples-methodology.md) — EV/Sales, EV/EBITDA, P/E and Massive-data simplifications
- [`growth-and-profitability.md`](./references/growth-and-profitability.md) — TTM revenue growth and EBITDA margin
- [`cohort-statistics.md`](./references/cohort-statistics.md) — median, mean, 25/75 bands with null handling
- [`regression-adjustment.md`](./references/regression-adjustment.md) — multiple ~ growth + margin, headline read
- [`rendering.md`](./references/rendering.md) — table-mode format inherited from `universe-builder`

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST plus fundamentals. Runs
on free Basic but rate-limited; correlation-peer fallback disabled.
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
