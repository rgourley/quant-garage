# risk-factor-delta

You want to know what management added to Item 1A this year. This skill
diffs two 10-K risk-factor filings using Massive's pre-parsed and
taxonomy-classified endpoint. No NLP on our side, no EDGAR scraping.
Categories added, categories dropped, categories rewritten. Grouped by
primary risk axis so the shape of the change is legible.

## Quick start

### Python library

```python
from quant_garage.skills.risk_factor_delta import run, render
payload = run("AAPL")
print(render(payload))
```

### CLI

```bash
python3 examples/run-risk-factor-delta.py --ticker AAPL
```

### Claude Code / LLM tool use

Discovered at `skills/risk-factor-delta/`. In a Claude Code session,
ask "what did AAPL add to its 10-K risk factors this year?" Tool-use
LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
Risk-factor delta: AAPL · 2023-11-03 → 2024-11-01
Categories: prior 20 → current 30 · +10 added, -0 removed,
3 materially changed, 17 retained

NEW risk categories (10)
  financial and market (4):
    · capital structure and performance > dividend policy and capital allocation
      "The Company believes the price of its stock should reflect ..."
    · liquidity and cash management > cash management operations
      "..."
  strategic and competitive (3):
    ...

Take: 10 new risk categories added YoY (concentrated in financial
and market). 3 retained categories rewritten (>= 25% length change).
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the note in
Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`methodology.md`](./references/methodology.md): why the taxonomy
  is trustworthy, how the material-change threshold is chosen, what
  the endpoint does and doesn't cover
- [`rendering.md`](./references/rendering.md): section order,
  grouping rules, quote length, Take construction

## Plan requirement

Runs on Stocks Basic (free tier). The
`/stocks/filings/vX/risk-factors` endpoint is included on every Stocks
plan. One paginated call per ticker per run; typical volumes are
small (15-40 rows per filing). See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
