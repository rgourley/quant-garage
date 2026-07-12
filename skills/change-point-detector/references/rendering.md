# Rendering: change-point-detector

Note-mode. Layout:

1. Header (2 lines: identity + prior params)
2. Detected change points list
3. Segment stats block
4. Take + caveats

## Header

```
Change-point detector: SPY · 504d lookback · 503 log returns · 2 change point(s)
Prior mean run length: 250 obs · P(r=0) threshold: 0.5
```

Line 1: identity + counts.
Line 2: prior params for reproducibility.

## Detected change points

```
Detected change points
  · 2025-08-01 · confidence 0.912 (index 154)
  · 2026-04-04 · confidence 0.834 (index 322)
```

One line per detection, sorted chronologically. Confidence is the
posterior P(r=0) at the boundary (the probability the model assigned
to "a change point just occurred here"). Index is the position in
the log-return series for downstream callers.

## Segments

```
Segments (return regime per interval)
  #1: n=154 obs · ann-return +18.2% · ann-vol +11.4%
  #2: n=168 obs · ann-return -5.6% · ann-vol +17.8%
  #3: n=181 obs · ann-return +23.4% · ann-vol +12.1% (current)
```

One line per segment. `(current)` tag on the last segment. The reader
sees at a glance whether the shift was primarily in mean (return) or
variance (vol).

## Take

- No change points detected:
  "No regime shifts detected. The return distribution has been
  stable across the window."
- Change points detected:
  "N regime shift(s) detected; the most recent was around {date}
  (confidence {c})."
  Followed by:
  "Current vs prior regime: return {delta_ret}, vol {delta_vol}."

## What UI devs do instead

- Time-series line chart with vertical bars at each detected change
  point.
- Run-length posterior heatmap over time.
- Cross-name change point calendar for a watchlist.
