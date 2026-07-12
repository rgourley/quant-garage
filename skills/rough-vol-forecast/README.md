# rough-vol-forecast

Rough-volatility-scaled vol forecast (Bayer-Friz-Gatheral 2016) across
multiple horizons. Under rough vol, realized vol scales as h^H with H
around 0.14 — much slower than sqrt(t). This dampens long-horizon
extrapolation.

## Quick start

```bash
python3 examples/run-rough-vol-forecast.py --ticker SPY
```

```python
from quant_garage.skills.rough_vol_forecast import run, render
payload = run("SPY", horizons_days=[1, 5, 20, 60, 120])
print(render(payload))
```

## What you get back

```
Rough volatility forecast: SPY · 504d lookback · 504 returns · H = 0.14
Realized ann vol: 16.89% · EWMA (λ=0.94) ann vol: 13.63%

Horizon      Traditional    EWMA    Rough (H=0.14)    Ratio
--------------------------------------------------------------
1               1.06%       0.86%        1.06%        1.00x
5               2.38%       1.92%        1.33%        0.56x
20              4.76%       3.84%        1.62%        0.34x
60              8.24%       6.65%        1.89%        0.23x
120            11.66%       9.41%        2.08%        0.18x

Take: Rough vol scaling damps the 120-day vol forecast to 0.18x traditional.
```

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
