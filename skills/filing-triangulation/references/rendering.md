# Rendering: filing-triangulation

Composite note. Layout:

1. Header (2 lines)
2. Bullish signals list
3. Concerns list
4. Other signals list
5. Per-sub-skill summary blocks (8-K, risk-factor-delta, filing-sentiment, insider-flow, analyst-tracker)
6. Sub-skill errors (when any)
7. Caveats

## Header

```
Filing triangulation: AAPL
Verdict: PREDOMINANTLY CONCERNING
```

Verdict tags:
- `PREDOMINANTLY CONSTRUCTIVE`
- `PREDOMINANTLY CONCERNING`
- `MIXED SIGNALS`
- `no clear signal`

## Signal blocks

Simple bulleted lists under `Bullish signals`, `Concerns`, `Other`.
Each line is a compact human-readable signal string from the
triangulation logic.

## Per-sub-skill summaries

Fixed-order compressed summary per sub-skill so a reader can see the
component reads at a glance without opening the JSON.

## What UI devs do instead

- Dashboard with per-source badges (green/yellow/red) and drill-down
  panels.
- Timeline of insider trades, analyst events, filings, and 8-Ks
  overlaid on the price chart.
- Cohort comparison: this ticker vs sector median on each sub-skill
  score.
