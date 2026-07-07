---
name: historical-comparison
description: Twin decision-support. Chains event-study (what happened around a specific event) with historical-analog-finder (what usually happens in setups like now). Useful before making a call where both name-specific event evidence and market-wide regime analog matter. Also runs analog-only mode when no ticker is supplied.
---

# historical-comparison

Twin comparison: event-study on the ticker + historical-analog-finder
on the market. The idea: don't rely on one anchor when you can use
both.

## When to invoke

- Analyst wants both "here's what happened around this specific event"
  and "here's what usually happens in setups like this"
- Before a directional call where both name-specific and market
  context matter
- Analog-only mode (no ticker): just the market regime forward
  distribution

## Modes

- **Event mode**: pass ticker + event_class + optional event_date /
  period. Both event and analog run.
- **Analog-only**: pass `include_event=False`. Only the market
  analog runs.

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Headline distills the T+5 CAR + prior percentile from event-study
plus the median/IQR/hit-rate at 90d from the analog.

**Layer 2 rendered brief**. See
[`references/rendering.md`](./references/rendering.md).
