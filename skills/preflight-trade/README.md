# preflight-trade

Before-you-execute check on a single ticker + intended action.
Composes technical-briefing + earnings-blackout (14d) + news-scanner
(last N) + corporate-actions-scanner (90d) into a verdict (go, wait,
review) plus red/green flag lists.

## Quick start

```bash
python3 examples/run-preflight-trade.py --ticker ALLO --action reduce --format render
```

## Plan requirement

Stocks Starter. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
