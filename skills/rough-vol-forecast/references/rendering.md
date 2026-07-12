# Rendering: rough-vol-forecast

Note-mode. Layout:

1. Header (2 lines: identity + realized/EWMA vol)
2. Per-horizon table
3. Take + caveats

## Header

```
Rough volatility forecast: SPY · 504d lookback · 504 returns · H = 0.14
Realized ann vol: 16.89% · EWMA (λ=0.94) ann vol: 13.63%
```

Line 1: identity + inputs + H used.
Line 2: baseline realized + EWMA annual vols for reference.

## Per-horizon table

Fixed 5-column layout:

```
Horizon    Traditional   EWMA    Rough (H=0.14)   Ratio
-----------------------------------------------------
1               1.06%    0.86%          1.06%     1.00x
5               2.38%    1.92%          1.33%     0.56x
...
```

- **Traditional**: sigma_daily × sqrt(h). The old-school scaling.
- **EWMA**: same sqrt-time but on decay-weighted vol.
- **Rough**: sigma_daily × h^H. The rough vol answer.
- **Ratio**: rough / traditional. < 1 means rough damps traditional.

## Take

- Ratio at longest horizon < 0.7: "Rough vol scaling damps the {h}-day
  vol forecast to {ratio}x traditional sqrt-time."
- Ratio > 1.3: "Rough vol scaling lifts the {h}-day vol forecast to
  {ratio}x traditional (rare — check H)."
- Otherwise: baseline comparison note.
