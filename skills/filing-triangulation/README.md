# filing-triangulation

Workflow composite: runs 8-k-scanner + risk-factor-delta +
filing-sentiment + insider-flow + analyst-tracker on a single ticker
and returns a unified fundamental report with a cross-source verdict.

## Quick start

```bash
python3 examples/run-filing-triangulation.py --ticker AAPL
```

```python
from quant_garage.skills.filing_triangulation import run, render
payload = run("AAPL")
print(render(payload))
```

## Plan requirement

Stocks Basic (free tier). analyst-tracker section requires Benzinga
Analyst Ratings add-on; skipped gracefully without it.

## Skill spec

[`SKILL.md`](./SKILL.md).
