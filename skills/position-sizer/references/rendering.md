# Rendering

The position-sizer output mode is `table`: a wide side-by-side
comparison where each column is one sizing method and each row is
one ticker. Three footer rows (Σ|w|, port vol, binding constraint)
summarize the book-level properties. A "Take" paragraph below the
table reads the actual numbers and explains each method's tilt.

## Layout

```
Position sizes — AMZN, GOOGL, META, NVDA
Target vol 12% · Lookback 252d · Max weight 30% · Leverage cap 1.0x

Ticker     σ(annual)    Vol-Target   Kelly(0.25)   Risk-Parity      Equal-Wt
----------------------------------------------------------------------------
AMZN           27.9%         20.7%         30.0%         30.0%         25.0%
GOOGL          30.1%         19.4%         23.2%         27.9%         25.0%
META           40.0%         14.6%         22.3%         21.7%         25.0%
NVDA           41.0%         14.3%         24.5%         20.3%         25.0%
----------------------------------------------------------------------------
Σ |w|                        69.0%        100.0%        100.0%        100.0%
Port vol                     12.0%         17.8%         17.4%         18.2%
Binding                 target_vol    max_weight    max_weight          none

Take: Vol-target tilts away from NVDA (σ 41%) and toward AMZN ...
```

## Per-section rules

### Header

Two lines:

- Line 1: `Position sizes — {comma-separated tickers used}`
- Line 2: Settings line with target vol, lookback days, max-weight
  cap (or "Max weight none"), leverage cap.

The header should fit on a 100-char terminal width.

### Table body

One row per used ticker, sorted alphabetically (matches
`tickers_used` in the JSON). Columns:

- `Ticker` (left-aligned, width 8)
- `σ(annual)` (right-aligned, width 10, percent with 1 decimal)
- One column per emitted method (right-aligned, width 12, percent
  with 1 decimal)

Method column headers come from a fixed label map:

- `vol_target` → `Vol-Target`
- `kelly_quarter` → `Kelly({scale})` where {scale} is the actual
  fractional Kelly scale (e.g., `Kelly(0.25)`)
- `risk_parity` → `Risk-Parity`
- `equal_weight` → `Equal-Wt`

Methods that weren't emitted (e.g., Kelly when no edges supplied)
don't get a column. The footer row counts adjust accordingly.

### Footer rows

Three rows separated from the body by a horizontal rule:

- `Σ |w|`: gross exposure per method, as percent (1 decimal).
- `Port vol`: portfolio annualized vol per method, as percent
  (1 decimal).
- `Binding`: binding-constraint label per method. `"none"` when no
  cap bound.

### Take

One paragraph below the table. The narrative reads the actual
numbers:

- Vol-target sentence: identifies the highest-vol and lowest-vol
  name in the basket and explains the tilt away from the former.
- Kelly sentence: identifies the name with the highest
  edge-per-variance (μ/σ²) and explains the tilt toward it.
- Risk-parity sentence: identifies the largest-weight name and
  explains it as the smallest-share-at-equal-weight name.
- Equal-weight sentence: one-line acknowledgment that this column
  ignores risk.
- Closing line: "Pick the method that matches your conviction model."

The Take is adaptive — it reads the actual basket and edges, not a
hardcoded NVDA/AMZN story. If only some methods emitted (Kelly
skipped), only those sentences appear.

### Caveats

Bullet list under the Take. Pulled from the JSON's `tier_caveats`
array. Always-on caveats are appended last:

- "Vol estimates use N-day realized; future vol may differ."
- "Correlation matrix shrunk X% toward identity for numerical safety."
- "Kelly assumes user-supplied edges are correct..." (only when
  Kelly emitted)

Per-run caveats (excluded tickers, missing edges, convergence
fallback) come first.

## What the renderer does NOT do

- No color codes / ANSI escapes. The output should look correct in
  plain text, in markdown code fences, and in a developer's terminal.
- No bar charts in the body. Numbers carry the comparison; bars
  would add visual noise for a four-column table.
- No verdict on which method is "best." Picking the method is the
  PM's decision; the take explains the worldview behind each column
  but doesn't recommend one.

## Output file

The script writes `examples/position-sizer-output.md` with a two-layer
structure inherited from the rest of the repo:

- Layer 1: the canonical JSON payload in a fenced code block.
- Layer 2: the rendered table in a fenced code block.

Both layers are gitignored (the test artifact, not the methodology).
The methodology lives in `references/`.
