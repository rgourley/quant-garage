# morning-brief

Daily open briefing. market-regime + macro-event-calendar (2d) +
news-scanner (last N per watchlist ticker).

## Quick start

Three ways to invoke. Watchlist is optional in every mode; without
it, morning-brief runs macro-only.

### Python library

```python
from quant_garage.skills.morning_brief import run, render
payload = run(watchlist="NVDA,ALLO,SOFI")
print(render(payload))
```

### CLI

```bash
python3 examples/run-morning-brief.py --watchlist "NVDA,ALLO,SOFI" --format render
```

### Claude Code / LLM tool use

Discovered at `skills/morning-brief/`. In a Claude Code session,
say "morning brief" — Claude runs macro-only or you can add
"on my watchlist NVDA,ALLO,SOFI" to include news. Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
