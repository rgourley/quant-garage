---
name: smart-money-cluster
description: Workflow composite that runs manager-portfolio-diff across a curated cohort of well-known filers (Berkshire, Baupost, Renaissance, Bridgewater, Third Point, Pershing Square, Tiger Global, Scion, Appaloosa) and aggregates the initiations, adds, and exits by issuer. Surfaces cross-fund conviction: names that appeared in >= N funds' new positions this quarter. Requires Stocks Basic. Runs on the free tier.
---

# smart-money-cluster

Runs `manager-portfolio-diff` across a curated cohort of well-known
funds and aggregates the results by issuer. Surfaces names that
appeared in >= N funds' new positions (initiations), adds, or exits
this quarter as a "cross-fund conviction" signal.

Default cohort: Berkshire, Baupost, Renaissance, Bridgewater, Third
Point, Pershing Square, Tiger Global, Scion, Appaloosa. Custom
cohort via `--aliases`.

## When to invoke

- Quarterly 13-F review
- "What did smart money buy this quarter?"
- Screening for names with cross-fund conviction as a starting point
- The user says "smart money", "13-F cluster", "cross-fund"

Not for: real-time (13-F is quarterly and lagged ~45 days). Not for
alpha timing (crowded positions can underperform).

## What you need

- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--aliases` (default: 9-fund cohort)
- `--min-funds` (default 2): minimum fund count to surface a name

## What you get back

**Layer 1: JSON**. Per-fund summary, clustered_initiations,
clustered_adds, clustered_exits, each sorted by fund count then
dollar volume.

**Layer 2: rendered note**. Per-fund one-liners + three cluster
tables + Take.

## Foundations used

- Composes `manager-portfolio-diff` across N filers.

## Doesn't handle (yet)

- **Custom cohort scoring.** Each fund weighted equally. Historical
  accuracy weighting would be a real extension.
- **Price context.** No overlay of cluster picks vs current price /
  RS / vol regime.
- **Time-series cluster tracking.** Only current quarter; no
  quarter-over-quarter momentum.

These are clean composite extensions.
