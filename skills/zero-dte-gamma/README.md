# zero-dte-gamma

Estimate net dealer gamma exposure and identify gamma pins for
same-day-expiry (or nearest-expiry) SPY / SPX / QQQ / IWM options.

Motivated by 2024-25 research on how 0DTE options now drive
systematic intraday moves through dealer hedging.

## Quick start

```bash
python3 examples/run-zero-dte-gamma.py --underlying SPY
```

```python
from quant_garage.skills.zero_dte_gamma import run, render
payload = run("SPY")
print(render(payload))
```

## What you get back

```
0DTE gamma flow: SPY · exp 2026-07-13 (0DTE) · spot $754.95 · 148 strikes
Net dealer gamma: -$1.25B → SHORT GAMMA (destabilizing)
Gamma flip strike: $500.00

Top gamma pins (strikes with largest total notional gamma)
  Strike   Distance   Call γ ($)   Put γ ($)   Call OI    Put OI
   759.00    +0.54%      $326.2M       $1.2M     9,412        34
   758.00    +0.40%      $291.2M      $18.5M     6,590       404
   750.00    -0.66%      $117.0M     $177.0M     4,271     6,420
   ...

Take: Dealers are net-short gamma at these strikes; expect intraday moves to accelerate.
```

## Plan requirement

Stocks Basic + Options Developer entitlement.

## Skill spec

[`SKILL.md`](./SKILL.md).
