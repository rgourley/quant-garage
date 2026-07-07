# earnings-week-prep

Sunday-night prep for the week's earnings prints. Runs earnings-
blackout across the watchlist, then earnings-drilldown + technical-
briefing per imminent print (top-N by proximity).

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.earnings_week_prep import run, render
payload = run(watchlist="NVDA,ALLO,SOFI,QCOM", window_days=7)
print(render(payload))
```

### CLI

```bash
python3 examples/run-earnings-week-prep.py --watchlist "NVDA,ALLO,SOFI,QCOM" --format render
```

### Claude Code / LLM tool use

Discovered at `skills/earnings-week-prep/`. In a Claude Code
session, say "prep for this week's earnings" plus your watchlist
— Claude finds who prints and runs the per-name drilldown.
Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Starter (earnings-drilldown needs the financials endpoint).
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
