# historical-comparison rendering

Hybrid mode. Header + headline + 2 titled sections.

## Header

```
Historical Comparison — {TICKER} · {AS_OF}
```

Ticker prefix skipped in analog-only mode.

## Headline

```
HEADLINE
──────────
Event ({CLASS}, {DATE}): T+5 CAR {SIGNED_PCT} (prior mean {SIGNED_PCT}, {N}th %ile)
Analog {H}d SPY: median {SIGNED_PCT}, {HIT_PCT} > 0 across {N} analogs · IQR [{SIGNED_PCT}, {SIGNED_PCT}]
```

Skip lines when the corresponding side didn't produce a summary.

## Sections

1. `EVENT STUDY` — event_study.render() (skipped in analog-only)
2. `HISTORICAL ANALOGS` — historical_analog_finder.render()

## Errors

Standard block.
