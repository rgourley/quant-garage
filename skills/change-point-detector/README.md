# change-point-detector

Bayesian Online Change-Point Detection on a ticker's daily log returns.
Reports specific dates where the return distribution appears to have
shifted, the posterior confidence at each boundary, and the annualized
return and vol per detected segment.

Uses Adams and MacKay (2007) BOCPD with a Normal-Gamma prior and
Student-t predictive so hyperparameters update in closed form.

## Quick start

```bash
python3 examples/run-change-point-detector.py --ticker SPY --lookback-days 504
```

```python
from quant_garage.skills.change_point_detector import run, render
payload = run("SPY", lookback_days=504)
print(render(payload))
```

## What you get back

```
Change-point detector: SPY · 504d lookback · 503 log returns · 2 change point(s)
Prior mean run length: 250 obs · P(r=0) threshold: 0.5

Detected change points
  · 2025-08-01 · confidence 0.912 (index 154)
  · 2026-04-04 · confidence 0.834 (index 322)

Segments (return regime per interval)
  #1: n=154 obs · ann-return +18.2% · ann-vol +11.4%
  #2: n=168 obs · ann-return -5.6% · ann-vol +17.8%
  #3: n=181 obs · ann-return +23.4% · ann-vol +12.1% (current)

Take: 2 regime shift(s) detected; the most recent was around 2026-04-04 (confidence 0.834).
Current vs prior regime: return +29.0%, vol -5.7%.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
