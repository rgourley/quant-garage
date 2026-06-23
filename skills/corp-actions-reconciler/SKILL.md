---
name: corp-actions-reconciler
description: Reconcile a position file against splits, dividends, and spinoffs to catch breaks before they hit P&L or T+1 settlement. Use when an operator hands over a CSV of positions and asks "are these right after the recent corporate actions." Runs on a free Massive Basic key.
---

# corp-actions-reconciler

You hand over a CSV of positions. The skill walks every line against the
splits and dividends history, flags anything where the recorded share
count or cost basis is off, and emits a clean reconciliation report with
citations.

This is the highest-grounding-value workflow in the suite. LLMs
hallucinate split factors constantly (was it a 3-for-1 or 7-for-1?
forward or reverse?). Massive's splits endpoint has the truth, so the
skill never guesses.

## When to invoke

- Operator says "reconcile my positions" or "check this CSV against
  corporate actions"
- A monthly close turned up a position-file break and you want to find
  which corporate action caused it
- Before a T+1 settlement window when a stale position file would break
  settlement instructions

## What you need

- A position file in CSV format with columns: `ticker`, `shares`,
  `cost_basis`, `as_of_date`
- A Massive API key (free Basic tier works)
- `MASSIVE_API_KEY` exported in the environment

## What you get back

The skill ships two output layers:

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
This is what UI dashboards, downstream agents, and Python scripts consume.
Every field is typed, every break carries its source endpoint and a
fetched-at timestamp for audit trail.

**Layer 2: rendered exception report** for Claude Code users. See
[`references/rendering.md`](./references/rendering.md) for the full
rendering rules. A short example:

```
2 BREAKS found across 47 positions checked.

BREAK 1: AAPL
  Recorded:    100 shares as of 2024-08-01
  Action:      2-for-1 split, ex-date 2026-03-10
  Expected:    200 shares
  Delta:       +100 (under-allocated)
  Source:      api.massive.com/v3/reference/splits?ticker=AAPL
  Verified:    2026-06-23T14:32:08Z
```

UI devs work from the JSON. Claude Code users see the rendered form.
Same compute, two consumption surfaces.

## How it works

1. For each row in the input CSV, call the splits and dividends
   endpoints with `ticker` and `execution_date > as_of_date`
   (splits) and `ex_dividend_date > as_of_date` (dividends).
2. Apply each split's `split_to / split_from` ratio to the recorded
   share count, in chronological order. Cost basis moves inversely.
3. Walk dividend records: cash dividends are informational by default;
   special-cash, return-of-capital, and stock dividends adjust basis or
   share count per the [dividends methodology](./references/dividends-methodology.md).
4. Read spinoff entries from a `spinoffs.json` overrides file (no
   dedicated Massive spinoffs endpoint as of June 2026); adjust the
   parent position's basis and create a new position for the
   subsidiary at the issuer's allocation. See [spinoffs methodology](./references/spinoffs-methodology.md).
5. Compare projected share count and cost basis to the recorded values
   in the input file.
6. Emit any mismatch as a BREAK with citation.

The four methodology references are the IP. Read them in this order if
you're extending the skill:

1. [`splits-methodology.md`](./references/splits-methodology.md): forward
   and reverse splits, compounding, fractional results.
2. [`dividends-methodology.md`](./references/dividends-methodology.md):
   cash, special, RoC, stock dividends, timing rule.
3. [`spinoffs-methodology.md`](./references/spinoffs-methodology.md):
   parent basis allocation, new-position creation, Massive endpoint
   gaps and override file format.
4. [`edge-cases.md`](./references/edge-cases.md): CIL, ADR/ordinary
   mapping, reverse-split delisting watch, symbol changes,
   same-day actions, FX.

## Endpoints used

- `GET /v3/reference/splits` (paginated; one call per ticker)
- `GET /v3/reference/dividends` (paginated; one call per ticker)
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{spin_date}/{spin_date}`
  (only when computing first-session cost-basis allocation for a spinoff)

The splits and dividends endpoints are included in the free Basic
tier. Rate-limited to 5 calls/min on free, so a CSV with more than ~50
rows will take a few minutes on a free key. Any paid tier eliminates
the wait. The aggs call for spinoff basis allocation also runs on
Basic.

## Example

```bash
# Quick test against a small position file
echo "ticker,shares,cost_basis,as_of_date
AAPL,100,150.00,2024-08-01
GOOGL,50,2800.00,2022-06-01" > positions.csv

# Invoke from Claude Code
# > /corp-actions-reconciler positions.csv
```

The skill streams findings as it processes each row, so you see breaks
the moment they're found instead of waiting for the full report.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for auth and rate
  limiting

## Doesn't handle (yet)

- Tender offers and exchange offers
- Mergers (cash, stock, and mixed consideration)
- Rights offerings (subscription rights)
- Currency-redenominated cost basis (FX conversion of basis is the
  operator's settlement-system problem; see edge-cases.md)
- ADR ratio changes that the splits endpoint misses (workaround:
  manual entry in `spinoffs.json`)

Add these in a PR if you need them. The patterns are straightforward
extensions of the existing splits/dividends/spinoffs logic.
