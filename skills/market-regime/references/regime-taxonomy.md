# Regime taxonomy

The composite regime is one of five labels. Each label has explicit
evidence rules that combine readings from the four blocks (SPY trend,
VIX state, breadth, sector leadership). The reasons[] array in the
output is the audit trail: which pillars supported the call, which
offset it.

## The five labels

### `risk_on`

All four blocks confirm:

- SPY trend is `uptrend_strong` or `uptrend_weak`
- VIX state is `quiet` (<15) or `normal` (15-22)
- Breadth > 50% of sector ETFs above their 50-day SMA
- Growth leadership: at least 2 of {XLK, XLY, XLC} in the top-3 by
  20-day RS

This is the "everything is working" tape. Breakouts work. Pullbacks
get bought. Momentum strategies outperform value.

### `risk_off`

All four blocks confirm to the downside:

- SPY trend is `downtrend_strong` or `downtrend_weak`
- VIX state is `elevated` (22-30) or `stressed` (>=30)
- Breadth < 50% of sector ETFs above their 50-day SMA
- Defensive leadership: at least 2 of {XLP, XLU, XLV} in the top-3
  by 20-day RS

This is the capital-preservation tape. Stops widen, position sizes
shrink, gross exposure comes down. Value and quality outperform
momentum.

### `mixed_risk_on`

SPY trend is up, but at least one block offsets:

- Breadth has thinned below 50%, OR
- VIX has lifted to `elevated`/`stressed`, OR
- Defensive sectors are leading (>= 2 in top-3), OR
- VIX data unavailable (incomplete picture)

Treat as constructive but discount confidence. The trend is your
friend, but the internals are no longer confirming. Common at index
tops: the cap-weighted benchmark holds up on a narrow set of leaders
while the median name has already broken.

### `mixed_risk_off`

SPY trend is down, but at least one block offsets:

- Breadth has recovered above 50%, OR
- VIX has retreated to `quiet`/`normal`, OR
- Growth sectors have returned to the top of the RS table

This is the early-recovery tape. The benchmark is still in a downtrend,
but the internals are turning. Common at bottoms before the index
follow-through confirms.

### `neutral`

SPY trend is `range` (price above some MAs, below others — no clean
stack ordering). No directional read. The other blocks are reported
for context but the composite is neutral until SPY commits.

## Why these rules

The rules deliberately require confirmation across multiple blocks
before stamping a directional label. A single block flipping doesn't
flip the regime; the goal is to avoid relabeling on noise (VIX up a
point, one sector pair rotation) while still catching real transitions
(sustained drop in breadth + VIX confirmation + leadership rotation).

The thresholds are set so the system biases toward `mixed_*` rather
than aggressively flipping between `risk_on` and `risk_off`. In
practice this matches how PMs talk: "still risk-on but watching the
breadth" is a mixed regime, not a flip.

## When to suspect the label

The label is built from four independent readings, but it inherits
the limitations of each:

- **Breadth is a sector-ETF proxy**, not the full A/D line. A `risk_on`
  label can be issued while the median single name is in a downtrend
  (see breadth-methodology.md). For a tighter check, swap in a real
  A/D from `universe-builder`.
- **The VIX block can be missing** if neither `VIX` nor `I:VIX`
  resolves on the key. The composite still computes but a one-pillar
  gap is real.
- **No macro overlay.** A rising 10Y or widening credit spread would
  meaningfully shade a risk-on read, but the v1 skill is equity-only.
- **Daily close only.** Intraday regime shifts (a Powell speech that
  re-rates the tape in 90 minutes) won't show until tomorrow's run.

## The reasons[] array

Every regime label ships with reasons[]. Read them. The label is the
summary; the reasons are the evidence. If a label feels wrong, the
reasons usually explain why (e.g. `mixed_risk_on` "but defensive
leadership" reveals that growth has stopped working even while SPY
held up).
