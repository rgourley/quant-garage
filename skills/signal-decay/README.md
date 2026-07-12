# signal-decay

Estimate how quickly a candidate signal loses predictive power. Runs
rolling 63-day IC vs 5-day forward returns, fits an exponential decay,
and reports the half-life in trading days plus a full tearsheet on the
signed-signal PnL.

Motivated by 2024-25 factor decay research (Israel-Moskowitz-Ross,
Falck-Rej-Thesmar 2024, Chen-Zimmermann factor zoo) showing most
published signals have decayed sharply.

## Quick start

```bash
python3 examples/run-signal-decay.py --ticker SPY --signal-kind mean_reversion
```

```python
from quant_garage.skills.signal_decay import run, render
payload = run("SPY", signal_kind="momentum")
print(render(payload))
```

## What you get back

```
Signal decay: SPY · signal=mean_reversion(20d) · forward=5d · IC window=63d · 1198 IC obs
Half-life: n/a · no significant decay

IC statistics
  Mean IC (full window):  +0.1802 (σ=0.2414)
  Mean IC (early quarter): +0.0647
  Mean IC (recent quarter): -0.1643
  Δ recent - early:         -0.2290

Signed-signal PnL tearsheet
  CAGR:              -4.82%
  Sharpe:              -0.64
  Deflated Sharpe p:  0.9242
  Max drawdown:      -35.48%
  ...

Take: Recent IC is meaningfully weaker than early; signal may be regime-broken.
```

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
