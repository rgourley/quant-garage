# single-name-vs-sector rendering (Layer 2)

Output mode: note. Three blocks in order: the RS table, the divergence
block, the take. Caveats footer when present.

No em-dashes. Colons, parentheses, periods.

## Block 1: RS table

Three fixed rows, one per relative-strength leg. Columns: the leg label,
one RS column per window in basis points (signed, `+377` / `-4100`), and
the five-bucket trend label for that leg.

```
SOFI vs XLF (Financials) vs SPY (2026-07-18)

Relative strength (bps)          5d        20d        60d       120d   Trend
------------------------------------------------------------------------------------
SOFI vs XLF                    -180       -820      -2600      -4100   stable_laggard
XLF vs SPY                      +90       +377       +410       +520   stable_leader
SOFI vs SPY                     -90       -443      -2190      -3580   stable_laggard
```

- RS cells are basis points, signed, no decimals. `n/a` when the window
  has insufficient history.
- Row order is fixed: name vs sector, sector vs benchmark, name vs
  benchmark. The first two are the legs the classification reads; the
  third is the plain relative-strength number for cross-reference.

## Block 2: divergence

```
Divergence:
  Score (name vs XLF, avg across windows): -1925 bps
  Composite (avg |name vs XLF|): 1925 bps
  Sector vs SPY (avg): +349 bps
  Classification: diverging
```

- Score is the signed name-vs-sector RS averaged across windows. Composite
  is the mean of the absolute values (magnitude regardless of direction).
- Classification is one of `leading its sector`, `lagging its sector`,
  `diverging`.

## Block 3: take

One sentence. For a diverging name it names the driving window for the
name-vs-sector gap and the window where the sector most leads or lags the
benchmark, in percentage points (bps / 100), and states whether the move
is name-specific:

```
Take: SOFI is diverging: lagging XLF by 41 pts/120d even as XLF leads SPY
by 4 pts/20d. The weakness is name-specific, not sector. Cross-reference
with relative-strength for the watchlist view.
```

For a leading or lagging name the take gives the name-vs-sector gap and
whether the sector agrees (broad-based) or disagrees (name-specific).

## Caveats footer

When `tier_caveats` is non-empty, render:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, always sorts first: a partial pull
can drop the sector or benchmark leg and flip the classification, so the
reader must see it before trusting the take.
