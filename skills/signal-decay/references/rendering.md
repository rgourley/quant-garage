# Rendering: signal-decay

Note-mode. Layout:

1. Header (2 lines: identity + half-life + classification)
2. IC statistics block (4 lines)
3. Tearsheet block (per-signal PnL)
4. Take + caveats

## Header

```
Signal decay: SPY · signal=momentum(20d) · forward=5d · IC window=63d · 1198 IC obs
Half-life: 145.3 trading days · MODERATE DECAY
```

Line 1: identity + inputs + observation count.
Line 2: half-life + classification tag.

Classification tags:
- `FAST DECAY` (uppercase)
- `MODERATE DECAY` (uppercase)
- `slow decay`
- `STABLE` (uppercase)
- `no significant decay`

## IC statistics

```
IC statistics
  Mean IC (full window):   +0.0721 (σ=0.1830)
  Mean IC (early quarter): +0.1204
  Mean IC (recent quarter): +0.0303
  Δ recent - early:         -0.0901
```

Full-window IC, early quarter, recent quarter, delta. If delta < -0.02
the Take fires an extra "regime-broken" flag.

## Tearsheet

Full performance summary on the signed-signal PnL. Fixed column-aligned
table format so a reader can scan Sharpe/DSR/DD/Calmar side by side.

## Take

- fast_decay: "Fast decay: {signal} on {ticker} loses half its
  predictive power every {N} trading days. Refit signals monthly or
  drop them."
- moderate_decay: "Moderate decay: {signal} half-life {N} days.
  Signal is still useful but retune quarterly."
- slow_decay: "Slow decay: {signal} half-life {N} days. Structural
  signal, but monitor quarterly."
- essentially_stable: "{signal} on {ticker} appears essentially
  stable over the window (half-life > 1000 days)."
- not_significantly_decaying: "No significant decay detected in the
  IC series."

Followed by an extra "Recent IC weaker than early" line when
delta < -0.02.

## What UI devs do instead

- IC time-series chart with the fitted exponential decay overlay.
- Signed-signal equity curve.
- Cross-signal comparison heatmap (momentum vs mean_reversion vs
  vol_expansion vs trend_break) across a watchlist.
