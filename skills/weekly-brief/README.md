# weekly-brief

Sunday-night briefing for the week ahead. Composes market-regime,
sector-rotation-signal, macro-event-calendar (7d), and earnings-
blackout (7d) into a watchlist-focused prep brief.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.weekly_brief import run, render
payload = run(watchlist="NVDA,ALLO,SOFI,HOOD")
print(render(payload))
```

### CLI

```bash
python3 examples/run-weekly-brief.py --watchlist "NVDA,ALLO,SOFI,HOOD" --format render
```

### Claude Code / LLM tool use

Discovered at `skills/weekly-brief/`. In a Claude Code session, say
"run a weekly brief on my watchlist" — Claude threads the tickers
into the composite. Tool-use LLMs consume the `run()` payload
matching [`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

[`SKILL.md`](./SKILL.md).
