# Rendering: risk-factor-delta

The skill emits canonical JSON matching `output-schema.json`. This
reference describes how that JSON renders in note mode.

## Block order

Up to five blocks, separated by blank lines:

1. Header (two lines)
2. NEW risk categories (only when non-empty)
3. DROPPED risk categories (only when non-empty)
4. MATERIALLY CHANGED text (only when non-empty)
5. Take + caveats

No prose intros. The reader opens the output expecting a diff; deliver
one.

## Header

```
Risk-factor delta: AAPL · 2023-11-03 → 2024-11-01
Categories: prior 20 → current 30 · +10 added, -0 removed, 3 materially changed, 17 retained
```

Line 1: `Risk-factor delta: {ticker} · {prior_date} → {current_date}`.
Line 2: prior/current totals + the four count deltas.

## NEW risk categories

Grouped by `primary_category`, sorted by count descending. Header per
primary category shows the count. Under each, one bullet per (secondary
> tertiary) pair with the supporting-text quote on the next line
(indented, truncated at ~200 chars).

```
NEW risk categories (10)
  financial and market (4):
    · capital structure and performance > dividend policy and capital allocation
      "The Company believes the price of its stock should reflect ..."
    · liquidity and cash management > cash management operations
      "..."
  strategic and competitive (3):
    · innovation and product development > intellectual property protection and infringement
      "..."
```

Category names are humanized (underscore → space) at render time. The
JSON keeps the underscored form for stable machine consumption.

## DROPPED risk categories

Same grouping as NEW, no supporting-text quote (the risk isn't in the
current filing so quoting it would be misleading, and the reader can
diff against the JSON if they want the old text).

```
DROPPED risk categories (2)
  legal and regulatory (1):
    · litigation > shareholder lawsuits
  operational (1):
    · supply chain > single-source suppliers
```

## MATERIALLY CHANGED text

One entry per category where the retained supporting text expanded or
contracted by >= 25% in length. Sorted by `length_delta_pct`
descending. Cap at 10 in the rendered layer; the JSON has the full
list.

```
MATERIALLY CHANGED text (3)
  · financial and market > macroeconomic > interest rate exposure
    text expanded 45% (312 → 452 chars)
    now: "..."
  · legal and regulatory > antitrust > platform market dominance
    text expanded 38% (280 → 386 chars)
    now: "..."
```

The `now:` line quotes the current supporting text, truncated at 200
chars. Prior text is in the JSON at `changes.materially_changed[].prior_supporting_text`.

## Take + caveats

```
Take: 10 new risk categories added YoY (concentrated in financial and market). 3 retained categories rewritten (>= 25% length change).

Caveats:
- Risk-factor categorization is Massive's taxonomy applied per-filing...
```

Take is one line summarizing counts and the concentration of the new
categories. When there's no change, Take reads: "No material changes
YoY: same risk-factor lineup, same language."

## Single-filing fallback

When only one 10-K is on record for the ticker, the renderer skips the
diff blocks entirely and emits a category catalog grouped by primary:

```
Risk-factor catalog: LUNR (2024-04-05): 18 categorized risks (no prior filing on record)

By primary category:
- strategic and competitive              6
- financial and market                   5
- operational                            4
- legal and regulatory                   3
```

## What UI devs do instead

A custom UI consumes the JSON and renders:

- Sankey diagram of primary_category flows: prior → current, with
  band width = category count and colored bands for retained /
  added / removed.
- Word-level diff on the materially-changed supporting text, red for
  removed phrases, green for added phrases.
- Peer-comparison overlay: this diff plus what the peer set added
  the same period, so shared vs idiosyncratic risks separate cleanly.

The rendered note here is the Claude Code default.

## Why this format

A category-level diff on a small number of rows (10-K risk factors
typically 15-40 per filing) reads better as a grouped narrative than
as a table. The three-tier taxonomy already provides the structure;
the render just walks it top-down with the supporting text as
confirmation. A fundamental analyst can copy an added category with
its quote directly into a call note.
