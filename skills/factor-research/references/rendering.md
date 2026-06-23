# Rendering: factor-research

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in table mode for a factor study.

Table mode in this repo is the format Bloomberg, FactSet, and quant
blotters use. The canonical table-mode rules live in
[`../../universe-builder/references/rendering.md`](../../universe-builder/references/rendering.md);
this file documents the factor-research-specific overrides: header,
single-factor IC + decay block, decile spread block, correlation
matrix block, current decile membership block, mandatory take.

## Block order

Six blocks, separated by blank lines:

1. Header (one or two lines)
2. Single-factor IC + decay table
3. Long-short decile spread table
4. Factor correlation matrix
5. Current decile membership (top + bottom N per factor)
6. Take (one paragraph)

No prose intros. The reader opens the file expecting a factor research
output; deliver one.

## Header

```
Factor research: S&P 500 universe · 5y window (2021-06 → 2026-06) · 4 factors
```

`Factor research: {universe_definition.label} · {window} · {n_factors}
factors`. The window is rendered as `{Ny window (YYYY-MM → YYYY-MM)}`
where N is the rounded year count.

When `survivorship_mode == "biased"`, a second line:

```
Survivorship: top 500 by CURRENT market cap (forward-looking biased; see SKILL.md)
```

When `tier == "B"`, instead:

```
Tier B run (free Basic, single-factor demo). Multi-factor needs Stocks Starter + flat files.
```

## Single-factor IC + decay table

The headline statistical block. Markdown table or monospaced columns.

```
Single-factor IC + decay
| Factor             | 1M IC | 3M IC | 6M IC | 12M IC | t-stat (1M) | Sample |
|--------------------|-------|-------|-------|--------|-------------|--------|
| Momentum (12-1M)   | +0.08 | +0.12 | +0.10 | +0.04  |  +3.2       | 2,432  |
| Value (1/(P/B))    | +0.04 | +0.06 | +0.07 | +0.09  |  +1.6       | 2,401  |
| Quality (ROE)      | +0.05 | +0.07 | +0.06 | +0.05  |  +2.1       | 2,378  |
| Low-Vol (1/realiz) | -0.02 | -0.04 | -0.06 | -0.05  |  -0.9       | 2,432  |
```

Columns:

- `Factor`: from `factors[].name`. Left-aligned.
- `1M / 3M / 6M / 12M IC`: from `factors[].ic_1m..ic_12m`. Signed,
  two decimals. Right-aligned. Render `n/a` when null.
- `t-stat (1M)`: from `factors[].ic_tstat_1m`. Signed, one decimal.
  Right-aligned. `|t| >= 2.0` is the conventional significance bar; the
  renderer does not bold or highlight (table-mode keeps it spartan),
  but the take at the bottom references this number.
- `Sample`: from `factors[].n_observations`. Comma thousands separator.

Rows are in the order the factors appear in `factors[]` (the skill
emits them in conventional order: Momentum, Value, Quality, Low-Vol).

## Long-short decile spread table

The dollar-magnitude block.

```
Long-short decile spreads (D10 - D1, annualized)
| Factor             | 1M     | 3M     | 12M    | Hit rate (12M) |
|--------------------|--------|--------|--------|----------------|
| Momentum           | +14.2% | +11.8% | +6.4%  | 64%            |
| Value              | +5.1%  | +6.7%  | +9.2%  | 56%            |
| Quality            | +7.8%  | +6.4%  | +4.1%  | 58%            |
| Low-Vol            | -3.2%  | -4.6%  | -5.8%  | 42%            |
```

Columns:

- `Factor`: short name, no parenthetical. (Momentum, not "Momentum
  (12-1M)".)
- `1M / 3M / 12M`: from `factors[].decile_spread_1m / 3m / 12m`. Signed
  percent with one decimal. Annualized.
- `Hit rate (12M)`: from `factors[].hit_rate_12m`. Unsigned integer
  percent (e.g. 64%).

Drop `6M` from this table to keep it scannable; the JSON carries all
four horizons.

## Factor correlation matrix

Lower triangular, monospaced. Symbol-only headers, two-decimal cells.

```
Factor correlation matrix (decile signals)
              Mom   Val   Qual  LowVol
Momentum      1.00 -0.12  0.18  -0.31
Value        -0.12  1.00 -0.04   0.22
Quality       0.18 -0.04  1.00   0.41
Low-Vol      -0.31  0.22  0.41   1.00
```

Header row uses short labels (Mom, Val, Qual, LowVol). Body rows use
long labels (Momentum, Value, Quality, Low-Vol). Each cell is two
decimals with a sign. The diagonal is always 1.00. The matrix is
symmetric so the renderer may show only the lower triangle for
compactness; v1 renders the full square for visual symmetry.

Right-align numeric cells. Use a single space between columns and an
extra space when the next column header is wider than 4 chars
(`LowVol`).

## Current decile membership

Top 5 and bottom 5 names per factor at the end of the window. Compact
three-column layout when 3 factors per row fits the terminal width
(typically 80-100 chars), otherwise stack one factor per block.

Compact (3 factors per row):

```
Current decile membership (top + bottom 5 per factor)
                    MOMENTUM (D10)        VALUE (D10)         QUALITY (D10)
                    NVDA  +178%           CMG    P/B 0.4x      VRTX  ROE 41%
                    AVGO  +118%           ALL    P/B 0.6x      LLY   ROE 38%
                    PLTR   +96%           GM     P/B 0.7x      NVDA  ROE 32%
                    AVGO   +83%           F      P/B 0.8x      AAPL  ROE 30%
                    LLY    +52%           CVS    P/B 0.8x      MSFT  ROE 28%

                    MOMENTUM (D1)         VALUE (D1)          QUALITY (D1)
                    INTC   -34%           AVGO  P/B 12.1x      PLTR  ROE -8%
                    BIDU   -28%           NVDA  P/B 11.4x      RIVN  ROE -22%
                    ...
```

Header row uses uppercase factor name plus `(D10)` or `(D1)`. Each
data row is `TICKER  VALUE_DISPLAY` with the value pre-formatted per
factor convention:

- Momentum: signed percent, no decimal (`+178%`)
- Value: `P/B Nx` with one decimal
- Quality: `ROE NN%` integer
- Low-vol: realized vol displayed as `vol NN%` integer (the
  reciprocal form would confuse readers)

When fewer than 5 names are available in a tail (small universe), pad
with blank rows so the columns line up.

A four-factor matrix on a 100-char terminal needs to wrap. For Low-Vol
(when 4-factor display gets wide), break out as its own two-line block
underneath:

```
                    LOW-VOL (D10)        LOW-VOL (D1)
                    JNJ    vol 14%       RIVN   vol 71%
                    KO     vol 15%       PLTR   vol 64%
                    ...
```

## Take

One paragraph at the bottom. PM-relevant tone, sleeve-construction
implications. Format:

```
Take: Momentum is the strongest single factor in the current regime (IC 0.08 1M,
t-stat 3.2). Low-Vol IC is negative — the recent regime rewards risk-taking, not
defensiveness. Quality and Momentum are positively correlated (0.18), so a combined
sleeve gives less diversification than naive equal weight implies.
```

No em-dashes; this repo convention uses commas, parentheses, periods.
Three sentences typical:

1. Strongest factor by t-stat with the IC and t-stat values
2. Weakest factor (or sign-flipped factor) with the implication
3. Most-correlated pair with the sleeve-construction implication

When no factor has `|t-stat| >= 2`, the first sentence should be
honest: "No factor is statistically significant in the {N}-year
window. The strongest signal is {factor} at t-stat {N.N}; treat as
weak evidence, not actionable."

When the most-correlated pair has `|corr| < 0.3`, omit the third
sentence rather than pad ("Quality and Momentum are uncorrelated at
+0.09" adds no information).

## Sort order

The IC table and decile spread table are rendered in the order the
factors appear in `factors[]`. v1 emits Momentum, Value, Quality,
Low-Vol in that order (the conventional factor zoo presentation).
The schema does not re-sort by IC magnitude or t-stat; that's a UI
extension.

The correlation matrix preserves the same factor order along both
axes.

## What UI devs do instead

A custom UI consumes the JSON and renders a sortable factor matrix
with hover-to-inspect on each cell, a decile-by-decile bar chart per
factor (the schema reserves `decile_returns` for this), the
correlation matrix as a colored heatmap, and the take as a prominent
banner. The rendered format here is the Claude Code default.

## Why this format

FactSet Alpha Testing, Axioma factor research, and Barra's factor
report all converged on the IC table + decile spread table + factor
correlation matrix structure because:

- The IC table is the statistical view (does the factor predict
  forward returns at all)
- The decile spread is the dollar view (how much is the prediction
  worth)
- The correlation matrix is the diversification view (are the factors
  picking different names)
- The current decile membership grounds the abstraction in real names
  the PM recognizes (NVDA in the momentum top decile makes the
  abstraction concrete)
- The take translates the statistics into a sleeve-construction
  recommendation

A table-mode skill that ships another factor (size, accruals, ESG)
swaps rows but keeps the block structure.
