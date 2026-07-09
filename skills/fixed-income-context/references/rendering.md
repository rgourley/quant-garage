# fixed-income-context rendering

Hybrid mode. Header + regime line + proxy table + spread block +
correlation line + caveats.

## Header

```
Fixed-Income Context — {AS_OF}
ETF proxies · benchmark {BENCH} · correlation window {N}d
```

## Regime

```
Regime: {LABEL_UPPERCASE}
  {SHORT_ENGLISH_READ}
```

Labels: RISK_OFF, CREDIT_STRESS, GOLDILOCKS, REFLATION, RATE_PRESSURE,
NEUTRAL.

## Proxy table

```
Ticker Segment                       Price     1d     5d    20d    60d   120d  %ile252
--------------------------------------------------------------------------------
{T:<6} {NAME:<30} {PRICE:>9.2f} {SIGNED_PCT:>7} {..} ... {PERCENTILE:>7}
```

Sort order matches FI_PROXIES: short duration first, then IEF, TLT,
TIP, LQD, HYG, AGG.

## Spread block

```
Spread deltas (HY-IG credit; long-short duration):
  HYG-LQD 5d     {SIGNED_PCT}
  HYG-LQD 20d    {SIGNED_PCT}
  HYG-LQD 60d    {SIGNED_PCT}
  TLT-IEF 20d    {SIGNED_PCT}
```

Negative HYG-LQD = credit widening (HYG lagging LQD).

## Correlation

```
HYG-{BENCH} rolling 60d correlation: {SIGNED}
  {ONE-LINE READ: high/moderate/low correlation}
```

## Caveats

Standard block.
