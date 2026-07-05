# historical-analog-finder rendering

Note mode. Header block, current-regime snapshot, forward-distribution
table, top-analog list, effective-sample size note, caveats.

## Header

```
Historical Analog Finder — {AS_OF}
K={K} nearest analogs over {HISTORY_YEARS}y history · Benchmark {BENCHMARK} · Feature set: {N_FEATURES} regime features
```

## Current regime snapshot

For every feature in `params.feature_names`, one row with raw value +
z-score. Format the raw value per feature type:
- returns / drawdown: signed percentage (`+13.0%`, `-1.9%`)
- realized_vol: unsigned percentage (`18%`)
- above_sma_* (binary): `yes` if raw > 0.5 else `no`
- rsi: 1-decimal number (`54.6`)
- everything else: 3-decimal number

```
Current regime snapshot (raw · z-score):
  {FEATURE_NAME:<32} {RAW:>10}  ·  z {Z:+.2f}
```

## Forward distribution table

```
Forward SPY return distribution across {N_ANALOGS} analogs:

 Horizon     n       p10       p25    median       p75       p90      mean     >0
--------------------------------------------------------------------------
    {H}d    {N}   {PCT}   {PCT}   {PCT}   {PCT}   {PCT}   {PCT}   {HIT_PCT}
```

Percentages are signed, 1-decimal (`+3.8%`). Hit rate as unsigned
integer percent (`75%`).

## Top analog dates

```
Top analog dates (nearest first):
  {DATE}  (z-dist {DIST})  ->  {HORIZON} {PCT} · {HORIZON} {PCT} · ...
```

Show first 8; append `... {N_REMAINING} more analogs` when trimmed.

## Effective sample size note

```
K={K} requested, {N_ACCEPTED} accepted after dedupe. Deduplication requires min {GAP}d gap between analogs to prevent one historical window from dominating.
```

## Caveats footer

Standard block: structural regime change caveat, SPY-only feature
set, IQR-not-mean-is-the-honest-read, effective-sample warning when
K is thin.
