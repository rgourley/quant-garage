# hurst-exponent

Single-name persistence classifier. Runs R/S analysis on 2 years of
daily log returns and reports the Hurst exponent with a bootstrap
confidence band. Classifies the name as mean-reverting (H < 0.45),
random walk (H in [0.45, 0.55]), or trending (H > 0.55).

Companion to `pairs-scanner`: pairs handles two-name cointegration,
hurst handles single-name persistence.

## Quick start

```bash
python3 examples/run-hurst-exponent.py --ticker AAPL --lookback-days 504
```

```python
from quant_garage.skills.hurst_exponent import run, render
payload = run("AAPL", lookback_days=504)
print(render(payload))
```

## What you get back

```
Hurst exponent: AAPL · 504d lookback · 502 log returns
H = 0.516 → random walk

Bootstrap band (n=100): p5 0.472 · p50 0.512 · p95 0.549

R/S per block size
  n=  10   R/S = 2.94
  n=  13   R/S = 3.28
  n=  17   R/S = 3.72
  ...
  n= 125   R/S = 10.83

Take: No persistence detected. Neither trend nor mean-reversion
strategies have a structural edge over the sample. Trade the
fundamentals, not the tape.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
