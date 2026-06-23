# Rendering: pitch-comps

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in table mode for a comp set.

Table mode in this repo is the format Bloomberg RV / CapIQ comp pages
use. The canonical table-mode rules live in
[`../../universe-builder/references/rendering.md`](../../universe-builder/references/rendering.md);
this file documents the comp-set-specific overrides: subject row at top,
divider, peer rows, divider, cohort statistics (median / mean / 25-75),
optional regression-adjusted block, mandatory one-sentence read at the
bottom.

## Block order

Five blocks, separated by blank lines:

1. Header (one or two lines)
2. Subject line (one line, anchored)
3. Comp table (subject row, divider, peer rows, blank, cohort statistics)
4. Regression-adjusted block (optional)
5. Read (one sentence)

No prose intros. The reader opens the file expecting a comp page;
deliver one.

## Header

```
CRM: comp set as of 2026-06-23 · 8 peers selected via curated_override
```

`{subject_ticker}: comp set as of {date} · {n_peers} peers selected via
{peer_selection.method}`

When `tier == "B"`, a second line:

```
Tier B run (free Basic, peer fanout rate-limited). Re-run on Stocks Starter for full fanout.
```

When `peer_selection.method == "correlation"`, the header note adds:

```
SHOP: comp set as of 2026-06-23 · 6 peers selected via correlation (top 6 by 1Y daily ρ)
```

When `peer_selection.method == "sic_fallback"`, similarly:

```
SMCI: comp set as of 2026-06-23 · 8 peers selected via sic_fallback (SIC 3674)
```

The renderer reads the JSON's `peer_selection.method` and emits the
appropriate marker without the user needing to know the methodology.

## Subject line

One line after the header, anchored as the subject of the analysis:

```
Subject: CRM (Salesforce, Inc.)  MCap $315B  EV $290B
```

Format: `Subject: {ticker} ({name})  MCap ${mcap_b}B  EV ${ev_b}B`.

Market cap and EV in $B with no decimal when above $100B, one decimal
when below $100B (`Subject: WDAY (Workday)  MCap $28.9B  EV $31.9B`).

## Comp table

Default columns, in order:

| Column        | Source field                       | Format                                       |
|---------------|------------------------------------|----------------------------------------------|
| `Ticker`      | `ticker`                           | Uppercase, left-aligned                      |
| `Name`        | first word of `name`               | Short form (Oracle, not Oracle Corp); left   |
| `EV/Sales`    | `multiples.ev_sales`               | `Nx` with one decimal, right; `n/a` if null  |
| `EV/EBITDA`   | `multiples.ev_ebitda`              | `Nx` with one decimal, right; `n/a` if null  |
| `P/E`         | `multiples.p_e`                    | `Nx` with one decimal, right; `n/a` if null  |
| `Rev Growth`  | `metrics.revenue_growth_ttm`       | Signed % with one decimal, right; `n/a`      |
| `EBITDA Mgn`  | `metrics.ebitda_margin`            | Unsigned % with no decimal, right; `n/a`     |

Subject row at the top, labeled `(subject)`:

```
CRM (subject)                7.2x      18.4x   28.1x      +11%        38%
```

A divider line of dashes separates the subject from the peer block; a
second divider separates the peer block from the cohort statistics.

```
ORCL  Oracle                 6.8x      13.1x   24.4x       +9%        47%
SAP   SAP SE                  n/a       n/a    n/a        n/a       n/a
NOW   ServiceNow             12.3x      34.6x   65.2x      +22%        29%
WDAY  Workday                 7.4x      22.1x   42.0x      +16%        27%
ADBE  Adobe                   9.6x      19.4x   29.5x      +11%        45%
INTU  Intuit                  8.5x      21.7x   36.1x      +13%        41%
PANW  Palo Alto               9.7x      28.3x   54.1x      +17%        28%
CRWD  CrowdStrike            14.2x      45.8x   78.4x      +28%        24%
```

Peer order is the order from `peers[]` in the JSON, which is the order
the curated map / correlation rank returns them. The schema does not
re-sort by any column; that's a UI extension. (A sorted view on EV/Sales
would re-rank ORCL to the top; for a banker pitching CRM-vs-the-cohort,
the map-order is the more honest framing.)

Right-align all numeric columns. Cap each column's header width to the
widest data row so values line up under the header.

When a peer has `data_status: "empty"` (foreign issuer with no Massive
financials), render `n/a` in every multiple cell and every metric cell
but keep the row. The peer-set membership is itself information.

## Cohort statistics block

Three rows immediately after the peer block, separated by a blank line:

```
Median                       8.5x      22.1x   36.1x      +13%        35%
Mean                         9.6x      25.6x   45.1x      +16%        35%
25/75 %ile              7.4-9.7x   19.4-28.3x  29.5-54.1x  11-17%   28-41%
```

The label column is left-aligned and indented to match the
ticker+name columns of the peer block. Numeric columns right-aligned.
Range notation for percentiles: `{low}-{high}` with no spaces around
the hyphen, same precision as the underlying value.

When `n < 3` for any multiple, the cell renders `n/a` for all three
rows on that column.

## Regression-adjusted block

Optional. Render only when at least one `regression_adjusted.results[*]`
entry has non-null `implied`.

```
Regression-adjusted (controls for growth + EBITDA margin)
- Implied EV/Sales:    8.4x  vs subject 7.2x  → subject trades at 14% discount
- Implied EV/EBITDA:  23.1x  vs subject 18.4x → subject trades at 20% discount
- Implied P/E:        45.6x  vs subject 28.1x → subject trades at 38% discount
```

One bullet per multiple where the regression produced a result. Format:

```
- Implied {Multiple}: {implied}x  vs subject {actual}x  → subject trades at {abs(disc_pct)}% {discount|premium}
```

When `discount_or_premium > 0`, say "premium"; when negative, say
"discount." Magnitude as integer percent.

When `regression_adjusted.low_n_warning` is true, a one-line caveat
follows the bullets:

```
Regression note: n=6 peers, DoF tight; coefficients indicative.
```

## Read

One sentence at the bottom. Anchored, banker-tone, action-relevant.
No hedge words ("potentially," "arguably"). No banned phrases
("mispriced upside/downside," "implied fair value").

Format: lead with where the subject sits vs peers, then identify the
multiple driving the divergence. Two-clause structure works well:

```
Read: CRM screens cheap on growth-adjusted multiples. The discount is
concentrated on EV/EBITDA (margin compression vs growthier peers), not
on revenue multiples.
```

The "Read" is generated from the regression-adjusted block:

- Headline: median across `discount_or_premium` for the three
  multiples; "cheap" when median <= -0.10, "fair" when -0.10 <
  median < +0.10, "rich" when median >= +0.10.
- Driver: the multiple with the largest |discount_or_premium| is the
  driver; mention it with a short explanation tied to the metric
  that explains it (lower margin → higher EV/EBITDA discount;
  lower growth → higher P/E discount).

When the regression didn't produce results (n_peers_used < 4 across
all multiples), fall back to the median-vs-actual framing:

```
Read: CRM trades at 7.2x EV/Sales vs the peer median 8.5x; the discount
sits at -15% before adjusting for growth and margin.
```

The renderer reads the structured fields in the JSON and emits the
right framing.

## Sort order

The peer block is rendered in the order from `peers[]` in the JSON,
which is the curated-map order or correlation-rank order. A
banker-facing UI typically lets the user re-sort by any column (the
JSON carries everything needed). The default Claude Code rendering
does not re-sort because the map order communicates intent: "these are
the peers you compare to, in the order the playbook lists them."

## What UI devs do instead

A custom UI consumes the JSON payload directly. A comp page in a
pitch tool typically shows the table as a sortable, filterable grid,
the cohort statistics as a stickied footer, the regression block as a
collapsible side panel with a scatter plot of growth-vs-multiple
showing where the subject sits relative to peers, and the read as a
prominent banner. The rendered format here is the Claude Code default;
UIs build their own visual layer from the same JSON.

## Why this format

Bloomberg RV, CapIQ comp pages, FactSet RA all converged on the
subject-divider-peers-divider-statistics structure because:

- The subject row at the top anchors the reader on what's being valued.
- The cohort block at the bottom makes the comparison explicit without
  the reader needing to mentally aggregate the peer rows.
- The regression-adjusted block is the analytical value-add; it's what
  separates a sell-side comp page from a screenshot of a CapIQ export.
- The one-sentence read is the headline the MD asks for in the meeting.

This format is the floor for comp-set output across the suite. A
future skill that ships sector-specific multiples (P/B for banks,
EV/EBITDAX for energy) should swap the column set but keep the block
structure.
