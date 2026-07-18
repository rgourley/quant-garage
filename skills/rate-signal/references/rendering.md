# rate-signal rendering (Layer 2)

Output mode: note. Four blocks in order: the instruments return table, the
signals block, the confidence lines, the take. Caveats footer when present.

No em-dashes. Colons, parentheses, periods.

## Block 1: instruments table

One row per Treasury ETF, in curve order (SHY, IEF, TLT, TIP). Columns:
instrument (ticker + curve-position label), the window return as a signed
percent, and the observation count.

```
Rate Signal (Treasury curve) - 2026-07-18

Instrument                            Return   Obs
--------------------------------------------------
SHY 1-3 Year Treasuries (short end)   +0.42%   251
IEF 7-10 Year Treasuries (belly)      -1.10%   251
TLT 20+ Year Treasuries (long end)    -3.80%   251
TIP TIPS (inflation-protected)        -0.60%   251
```

- Return is `return_pct` as a signed percent, two decimals. `n/a` when the
  window has insufficient history.

## Block 2: signals

Label each line with the desk term. Include the window in the header. The
momentum line notes the short sub-window it uses.

```
Signals (60d window):
  Curve:      bear flattening
  Real yield: rising
  Breakevens: inflation expectations rising
  Momentum:   aligned (TLT vs IEF, 15d)
```

## Block 3: confidence

The level, then the agreements (`+`) and conflicts (`-`) that produced it.

```
Confidence: high
  + real yields rising consistent with bear regime
  + TLT and IEF momentum aligned
```

When there are conflicts they render with a `-` prefix:

```
Confidence: low
  - real yields falling conflict with bear regime
  - TLT and IEF momentum divergent
```

## Block 4: take

One sentence, the four-way curve label with the confidence read and the
component phrases:

```
Take: Bear flattening (high confidence): long rates rising, curve
flattening, real yields rising, TLT and IEF aligned. See macro-basket for
the broad cross-asset read.
```

## Caveats footer

When `tier_caveats` is non-empty, render:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, always sorts first: a dropped series
can flip the curve label or the confidence, so the reader must see it
before trusting the take.
