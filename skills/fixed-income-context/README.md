# fixed-income-context

Rates and credit view via ETF proxies. Fills the equity-only gap in
the rest of the toolkit without needing a FRED integration.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.fixed_income_context import run, render
payload = run()
print(render(payload))
```

### CLI

```bash
python3 examples/run-fixed-income-context.py --format render
```

### Claude Code / LLM tool use

Discovered at `skills/fixed-income-context/`. In a Claude Code
session, ask "what are rates and credit doing" or "is there credit
stress right now" — Claude runs the skill. Tool-use LLMs consume
the `run()` payload matching [`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
