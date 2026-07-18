# macro-basket rendering (Layer 2)

Output mode: hybrid. Three blocks in order: the RS table, the
derived-signals block, the take. Caveats footer when present.

No em-dashes. Colons, parentheses, periods.

## Block 1: RS table

One row per basket instrument, sorted by longest-window RS descending
(leaders on top). Columns: instrument (ticker + asset-class label), one
RS column per window in basis points (signed, `+250` / `-888`), the trend
label, and the range-position percentile.

```
Macro Basket vs SPY — 2026-07-18

Instrument                    5d       20d       60d      120d   Trend           Range%
---------------------------------------------------------------------------------------
SLV Silver                   +20       +79      +250      +523   stable_leader       92
...
TLT 20+ Year Treasuries      -76      -292      -888     -1753   stable_laggard       7
```

- RS cells are basis points, signed, no decimals. `n/a` when the window
  has insufficient history.
- Range% is `curve_position_pct` as an integer (percentile of the latest
  close in its trailing max-window range).

## Block 2: derived signals

Label each line with the desk term. Include the signal window in the
header.

```
Derived signals (60d):
  Rates:      tightening (long rates rising)
  Curve:      bear steepening
  Real yield: falling (or breakevens widening)
  Credit:     widening (stress)
  Dollar:     strong
  Commodity:  carry on (rising)
  Gold/Silver ratio: 18.4 (defensive (gold leading))
  Gold vs dollar beta (60d): -0.62 (dollar-sensitive)
```

Omit the gold/silver and gold-beta lines when their value is null
(insufficient history).

## Block 3: take

One sentence, composed from the rate, curve, dollar, credit, and
commodity labels:

```
Take: Rates tightening (bear steepening), strong dollar, widening credit,
commodity carry off. Cross-reference with market-regime for the equity
side.
```

## Caveats footer

When `tier_caveats` is non-empty, render:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, always sorts first: a partial pull
can flip a derived signal, so the reader must see it before trusting the
take.
