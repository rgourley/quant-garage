# Rendering: analyst-tracker

Note-mode skill.

## Layout

1. Header (identity + counts)
2. By-action line + latest rating distribution + consensus PT
3. Timeline (last 25 events)
4. Take + caveats

## Header

```
Analyst tracker: NVDA · 180d lookback · 47 rating event(s)
By action: upgrade: 6 · downgrade: 2 · reiteration: 30 · PT: 8 · initiation: 1
Latest per firm (32): Buy 28 (88%) · Hold 3 (9%) · Sell 1 (3%)
Consensus PT (median of latest per firm): $195.00 (low $120.00, high $425.00)
```

The action row walks a fixed priority list so a scanning reader sees
directional changes first (upgrade / downgrade before reiteration /
PT / drop / other).

## NOT_AUTHORIZED case

```
Analyst tracker: NVDA — ENTITLEMENT REQUIRED

- This key is NOT entitled to the Benzinga Analyst Ratings product...
```

## Timeline

Each event is 1-2 lines:

```
  2026-06-05 · China Renaissance (Jack Zhou) · initiated · buy
    PT $319.00
  2026-06-02 · Needham (N. Quinn Bolton) · reiterated · buy
    PT $270.00 → $270.00 (+0.0%)
```

Line 1: `{date} · {firm} ({analyst}) · {label} · {rating_transition}`.
When rating changed, shows `{prior} → {current}`. Otherwise just the
current rating.

Line 2 (only when PT present): `PT $prior → $current (delta%)`.
New coverage or no prior PT gets just `PT $current`.

Cap at 25. Overflow: `... and N more`.

## Take

- Net-bullish: `Sell-side net-bullish over the window: N upgrades vs M downgrades.`
- Net-bearish: `Sell-side net-bearish: N downgrades vs M upgrades.`
- No rating changes but PTs move: `No rating changes but price targets are rising: ...`
- Neither: `Sell-side positioning stable; no meaningful direction.`

Followed by `Consensus PT $X across N firms.` when the consensus is
defined.

## What UI devs do instead

- Timeline chart with markers colored by event label.
- Per-firm rating history strip.
- Consensus PT vs current price gap over time as a mean-reversion cue.
- Cross-name heatmap: watchlist × event label × frequency.
