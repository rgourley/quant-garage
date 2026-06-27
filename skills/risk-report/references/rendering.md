# Rendering

The risk-report output mode is `hybrid`: a top-to-bottom PM-style
report. Header, portfolio statistics block, VaR table, max drawdown
one-liner, worst-N stress days with per-name attribution, position
contribution table, concentration line, adaptive Take, caveats.

## Layout

```
Risk Report — NVDA, AMZN, GOOGL, META (gross 100.0%)
Lookback 252d · Benchmark SPY · As of 2026-06-27

Portfolio statistics:
  Annualized vol         21.5%
  Annualized return      +18.0%
  Sharpe (naive)          0.84
  Beta vs SPY             1.32
  Tracking error         12.0%
  R² vs SPY              0.76

Value at Risk (1-day):
                         95%    99%
  Historical            -2.2%  -3.8%
  Parametric            -2.0%  -2.9%
  Expected shortfall    -3.1%  -5.2%   (historical, mean loss beyond VaR)

Max drawdown (252d): -18.0% from 2026-01-15 to 2026-04-08 (60 days, not recovered).

Worst 5 historical days for current book:
  2026-04-04   -5.5%   (SPY -4.1%)   NVDA -2.4pp · META -1.1pp · GOOGL -1.1pp · AMZN -0.9pp
  ...

Position contribution to portfolio variance:
  NVDA      42.3%   (weight 25.0%, vol 48.5%)
  META      21.1%   (weight 25.0%, vol 40.1%)
  GOOGL     19.4%   (weight 25.0%, vol 31.2%)
  AMZN      17.2%   (weight 25.0%, vol 28.7%)

Concentration: top 5 = 100%, Herfindahl 0.25 (effective N = 4.0)

Take: Book runs hot to SPY (beta 1.32). NVDA dominates risk (42% of
variance) despite sitting at 25% weight. Consider whether NVDA's
risk share matches your conviction in NVDA specifically.

Caveats:
- ...
```

## Per-section rules

### Header

Two lines:

- Line 1: `Risk Report — {comma-separated tickers used} (gross X%)`
- Line 2: `Lookback Nd · Benchmark X · As of YYYY-MM-DD`

### Portfolio statistics block

Six rows, label left, value right-aligned to a fixed column. Values:
annualized vol (positive percent), annualized return (signed
percent), naive Sharpe (two decimals), beta vs benchmark (two
decimals), tracking error (positive percent), R² (two decimals).

Sharpe is labeled "naive" because no risk-free rate is subtracted.
Surface honestly.

### VaR table

Three rows, columns are confidence levels. Row labels: `Historical`,
`Parametric`, `Expected shortfall`. Values are signed percents (the
JSON ships positive loss magnitudes; the renderer flips sign so the
PM reads the way they think). The ES row gets a trailing
qualifier: `(historical, mean loss beyond VaR)`.

### Max drawdown

One line. Format:

```
Max drawdown (Nd): -X.X% from YYYY-MM-DD to YYYY-MM-DD (N days, recovered|not recovered).
```

If `recovery_date` is present, the suffix becomes `(N days, recovered YYYY-MM-DD)`.

### Worst-N stress days

One line per scenario. Format:

```
  YYYY-MM-DD   -X.X%   (BENCH -Y.Y%)   T1 -Z.Zpp · T2 -Z.Zpp · T3 -Z.Zpp · T4 -Z.Zpp
```

Up to 4 names per line; if more contributions exist, the renderer
truncates. `pp` (percentage points of the book return) is the unit
for contributions because they add up to the book return.

### Position contribution table

Sorted by variance share descending. One row per position:

```
  TICKER    XX.X%   (weight XX.X%, vol XX.X%)
```

Highlights which names are doing the heavy lifting on the variance
budget. Often surfaces the gap between weight and risk share for the
PM to react to.

### Concentration

One line summarizing top-5 weight, HHI (two decimals), and
effective N (one decimal).

### Take

A paragraph below the concentration line. The narrative is
adaptive:

- High beta (>1.5): leads with the beta clause
- Top-variance-contributor > 40% AND > 5pp above its weight: surfaces
  the name and the gap
- HHI > 0.30: surfaces concentration with effective N
- Drawdown < -20%: surfaces the recent drawdown
- Tracking error < 5%: notes the close benchmark tracking

The renderer picks at most 3 of these clauses to keep the paragraph
readable, then closes with a decision-frame line about whether the
risk share matches conviction.

### Caveats

Bullet list under the Take. Pulled from `tier_caveats`. Always-on:

- "Historical VaR uses N-day window; tail estimates noisy when n=N"
- "Parametric VaR assumes normality; almost always underestimates fat-tailed loss"
- "Beta computed vs BENCH; results differ on a different benchmark"
- "Risk metrics are descriptive of past behavior, not predictions"

Per-run caveats (excluded positions, etc.) come first.

## What the renderer does NOT do

- No color codes / ANSI escapes. Output should look correct in
  plain text, markdown code fences, and a developer terminal.
- No bar charts. Numbers carry the comparison.
- No verdict on whether the book is "good" or "bad." The Take
  surfaces the most striking facts, not a recommendation.

## Output file

The script writes `examples/risk-report-output.md` with a two-layer
structure:

- Layer 1: canonical JSON payload in a fenced code block
- Layer 2: rendered report in a fenced code block

The methodology lives in `references/`; the output file is the run
artifact, not documentation.
