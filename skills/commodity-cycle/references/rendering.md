# commodity-cycle rendering (Layer 2)

Output mode: note. Three blocks in order: the setup headline, the drivers
block, the take. Caveats footer when present.

No em-dashes. Colons, parentheses, periods.

## Block 1: setup headline

The commodity name and ticker, the as-of date, and the one-word setup
label (constructive / neutral / headwind).

```
Commodity Cycle: Gold (GLD) as of 2026-07-18

Macro setup: headwind
```

## Block 2: drivers

One line per driver, labelled with its desk term, the value, and the
parenthetical read. Include the window in the header. Correlations are
signed with two decimals; returns as signed percentages.

```
Drivers (60d):
  Dollar (UUP) corr:     -0.71  (inverse to the dollar)
  Real-yield corr:       +0.44  (real-yield sensitive, real yields rising)
  Miner divergence:      -3.20%  (miners lagging (warning))
  Silver co-movement:    +0.68  (broad metals move (high co-movement))
  Momentum quintile:     Q1  (bottom quintile, return -6.10%)
```

- The miner-divergence and silver-co-movement lines appear only for GLD.
  For a non-gold commodity, render the broad-commodity line instead:

```
  Broad-commodity corr:  +0.55  (moves with broad commodities)
```

- Any value that is null renders as `n/a` with its label read.

## Block 3: take

One or two sentences. Names the dominant macro driver (larger absolute
effect first), then the miner confirmation clause (gold only), then the
momentum quintile:

```
Take: Gold: headwind. The dominant driver is a strong dollar (60d corr
-0.71) plus rising real yields; miners are lagging, confirming weakness.
Momentum in the bottom quintile. Cross-reference with macro-basket for the
full cross-asset picture.
```

## Caveats footer

When `tier_caveats` is non-empty, render:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, always sorts first: a dropped context
ETF can flip the setup, so the reader must see it before trusting the take.
