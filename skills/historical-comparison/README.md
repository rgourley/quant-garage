# historical-comparison

Twin decision-support. event-study (event-specific) + historical-
analog-finder (market-wide). Both anchors together instead of one.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.historical_comparison import run, render
payload = run(ticker="NVDA", event_class="earnings", period="most_recent")
print(render(payload))

# Analog-only mode
payload = run(include_event=False)
```

### CLI

```bash
python3 examples/run-historical-comparison.py --ticker NVDA --event-class earnings --period most_recent --format render
python3 examples/run-historical-comparison.py --skip-event --format render
```

### Claude Code / LLM tool use

Discovered at `skills/historical-comparison/`. In a Claude Code
session, ask "what happened around NVDA's last earnings, and what
usually happens in setups like now" — Claude runs both anchors
together. Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Starter. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
