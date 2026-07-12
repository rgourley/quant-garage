# regime-audit

Workflow composite: change-point-detector + hurst-exponent on SPY +
11 SPDR sector ETFs. Per-name regime map with recent-shift dates,
Hurst classification, and current segment stats.

## Quick start

```bash
python3 examples/run-regime-audit.py
```

Custom basket:

```bash
python3 examples/run-regime-audit.py --tickers NVDA,AMD,AVGO,MU
```

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
