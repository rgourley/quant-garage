# Methodology: relative-strength

The skill answers one question: across several lookback windows, which
names in this watchlist have been leading the benchmark, which have
been lagging, and where is the leadership changing?

## Why basis points

Per-window RS is reported in basis points:

```
RS_bps = (ticker_return - benchmark_return) * 10_000
```

A 1.42% outperformance over 5 days reads as `+142bp`. A 18.5%
outperformance over 120 days reads as `+1850bp`. The unit is the
same. The reader can scan across columns without translating between
percent and decimal mentally.

Percentage points would work for short windows but become unwieldy at
the 6-month and 12-month horizon, where 1500bp is a typical leader
gap. Decimals would be invisible at the daily level. Basis points are
the standard in fixed income and increasingly in equity sleeves; this
skill follows the convention.

## Why composite percentile rank

A naive single-window rank tells you who's leading right now but
penalizes consistency. A name that's #1 in the 5d column but bottom
quartile in the 60d and 120d columns isn't a leader, it's a one-week
mean-reversion candidate. A name that's in the top quartile of every
window is the actual leader.

The composite is built like this:

1. For each window `w`, take the ticker's RS in that window.
2. Compute its percentile rank within the watchlist (using
   `lib.quant_garage.percentile_rank`, which uses the standard "mean"
   rule: strictly-less + half-equal).
3. Average the per-window percentiles. That mean is the composite.

The result is a 0-100 score. A name with `composite_rs_percentile = 92`
is in the top decile of the watchlist averaged across windows. The
metric naturally degrades gracefully when a window's RS is null
(insufficient history): that window simply doesn't contribute to the
mean.

### Why mean of percentiles instead of mean of bps

Raw bps means weight the longest window most. A 1850bp 6-month gap
swamps a 100bp 1-week gap if you average them directly. Percentile
ranks normalize each window onto the same 0-100 scale before
averaging, so a name that's consistently strong across all four
windows beats a name that's huge in the 120d column and meh
everywhere else.

### Within-watchlist, not absolute

The percentile is computed within the ranked universe (watchlist plus
sectors if `--include-sectors`). This is the right default for "rank
my watchlist": every name's score is contextualized against the other
names the caller chose. An absolute percentile against a top-500
universe would be a different question and is the job of
`factor-research`, not this skill.

## Trend label heuristic

Five buckets. The label is assigned by looking at the RS series
ordered shortest -> longest window (e.g., 5d, 20d, 60d, 120d).

- **improving**: the short-window RS is strictly greater than the
  next-longer-window RS, repeated across the head of the series
  (5d > 20d > 60d). Read this as "recent acceleration"; the name
  has been beating the benchmark more sharply in the last week than
  it was over the last quarter. Doesn't require all windows positive;
  a name climbing out of laggard status still earns this label.
- **deteriorating**: short < long across the head (5d < 20d < 60d).
  Read this as "recent decay"; the name's edge over the benchmark is
  fading or rolling into underperformance. The mirror image of
  improving.
- **stable_leader**: every window's RS is positive but the series
  isn't strictly improving. The name has been steadily ahead, no
  sharp acceleration.
- **stable_laggard**: every window's RS is negative. Steadily behind.
- **mixed**: anything else. Includes cases where RS flips sign mid-
  series, or where the series is non-monotonic without being all
  positive or all negative.

### Why head-of-series only

The "strictly improving" / "deteriorating" tests look at the first
three windows (or fewer, if the caller passed fewer). The 6-month and
12-month tail can lock the label into "mixed" forever even when the
recent trend is clear; the operator cares more about the near-term
turn than the back-window noise.

### Why no `accelerating_leader` / `accelerating_laggard` combo

Tested it in development. The 5-bucket scheme already covers it:
a name with positive RS across every window AND strictly improving
gets `improving` (the more actionable label). Adding a sixth bucket
forced the caller to read more without distinguishing useful
behavior. The composite percentile carries the "how strong overall"
signal; the label carries the "which way is it moving" signal. Keep
them orthogonal.

## Data quality

- Daily aggregates are pulled with `adjusted=true` so splits and
  dividends don't contaminate the return. SPY's distribution-adjusted
  history is the right baseline against which to measure RS.
- The lookback pull is `max(window) * 1.6 + 14` calendar days, which
  comfortably covers weekends, holidays, and gives a small buffer
  for half-days.
- A ticker with fewer than `window + 1` bars for a given window
  reports `null` for that window's RS and shows up in
  `tier_caveats`. The composite is still computed from whatever
  windows did produce a non-null RS.

## Why this isn't predictive

RS is a past-return statistic. The academic momentum factor (12-1M
return) has a small positive IC in most regimes (mean ~0.03-0.05),
but raw RS over a watchlist isn't an alpha signal on its own. This
skill exists to surface the leadership structure of the watchlist,
not to recommend trades. Pair the output with a regime read
(`market-regime`) and a name-level briefing (`technical-briefing`)
before sizing.
