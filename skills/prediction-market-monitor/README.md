# prediction-market-monitor

Kalshi prediction market monitor for Fed decisions, CPI, GDP, NFP,
and other macro / market events. Reports implied probability per
outcome, cross-strike probability distribution, expected value, and
modal outcome.

## Quick start

```bash
# Fed funds rate distribution across upcoming meetings
python3 examples/run-prediction-market-monitor.py --series fed

# CPI monthly
python3 examples/run-prediction-market-monitor.py --series cpi

# Pin a specific event
python3 examples/run-prediction-market-monitor.py --event-ticker KXFED-27APR
```

## Plan requirement

No Massive key required. Uses Kalshi's public read-only API.

## Skill spec

[`SKILL.md`](./SKILL.md).
