# Rendering: mc-portfolio-simulator

Note-mode. Layout:

1. Header (2 lines)
2. Portfolio composition (per-name weight + annual vol)
3. Cumulative return distribution
4. Path max drawdown distribution
5. Probability grid
6. Take + caveats

## Header

```
MC Portfolio Simulator: 4 names · 60d horizon · 10,000 paths
Lookback 252d (250 obs) · realized vol · normal
```

Line 1: composition summary.
Line 2: fit params + tail model.

## Composition

```
Portfolio composition
  NVDA     weight  25.0%  σ(annual)  35.4%
  ...
```

## Return distribution

```
Cumulative return over 60d
  Mean +5.7% · σ 11.5%
  p5 -12.7%  p10 -9.0%  p25 -2.0%  p50 +5.7%
  p75 +13.3% p90 +20.4% p95 +25.0%
```

Two rows: p5/p10/p25/p50 on top, p75/p90/p95 below. Reader scans
left-to-right for tail severity.

## Path max drawdown

```
Path max drawdown
  Median -8.2% · p25 (typical bad) -12.0% · p10 (bad case) -16.4% · p5 (tail case) -18.7%
```

All numbers negative (drawdowns). Labels bracket the interpretation
for a reader who's not walking through percentile math.

## Probability grid

```
Probability
  Loss > 5%: 24.0%   Loss > 10%:  9.8%   Loss > 20%:  0.7%   Loss > 30%:  0.0%
  Gain > 5%: 55.2%   Gain > 10%: 30.7%   Gain > 20%:  7.8%
```

Left column loss, right column gain. Same thresholds so a reader can
compare "loss > 10%" vs "gain > 10%" directly.

## Take

- Median outcome + p5 tail
- Optional "N% chance of 10%+ drawdown" call-out when P_loss_10 >= 10%
- Worst-case path drawdown (p10)

## Empty case

When history is insufficient, run raises before render fires.
