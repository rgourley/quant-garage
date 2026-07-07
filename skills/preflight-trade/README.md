# preflight-trade

Before-you-execute check on a single ticker + intended action.
Composes technical-briefing + earnings-blackout (14d) + news-scanner
(last N) + corporate-actions-scanner (90d) into a verdict (go, wait,
review) plus red/green flag lists.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.preflight_trade import run, render
payload = run(ticker="ALLO", action="reduce")
print(render(payload))
```

### CLI

```bash
python3 examples/run-preflight-trade.py --ticker ALLO --action reduce --format render
```

### Claude Code / LLM tool use

Discovered at `skills/preflight-trade/`. In a Claude Code session,
ask "should I reduce ALLO now" or "preflight NVDA buy" — Claude
runs the composite and surfaces the verdict + flags. Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Starter. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
