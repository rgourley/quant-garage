# Cohort statistics

How median, mean, and 25 / 75 percentile bands are computed across the
peer set. Short reference; the rules matter mostly because the wrong
handling of nulls and outliers produces a comp table that misrepresents
the cohort.

## What's computed

For each multiple (EV/Sales, EV/EBITDA, P/E) and each metric
(revenue_growth_ttm, ebitda_margin), the skill emits a `summary_block`:

```json
{
  "median": 22.1,
  "mean": 25.6,
  "p25": 19.4,
  "p75": 28.3,
  "n": 7
}
```

`n` is the count of non-null peer values that fed the summary.

## Median is the default; mean is reported but secondary

Use median as the default summary in the rendered table. Mean is
sensitive to outliers: CRWD on +28% revenue growth pulls the mean
growth up by 1-2 percentage points without telling the banker anything
new (everyone already knows CRWD is the fastest grower).

The rendered table shows both:

```
Median                  8.5x      22.1x   36.1x      +13%      35%
Mean                    9.6x      25.6x   45.1x      +16%      35%
```

The banker sees immediately whether the median and mean agree (cohort
is symmetric) or diverge (one or two names skew the cohort). The "Read"
line at the bottom uses median by default; switching to mean for the
headline is a v2 flag when the analyst wants the arithmetic-average
framing.

## 25 / 75 percentile band

The p25-p75 band shows where the middle half of the peer set trades.
Banker convention is to bold the subject when it sits in the band and
italicize when it sits outside (one of the two edits a banker makes to
the comp page before it ships).

The skill emits both values; the renderer doesn't bold/italicize in v1
(Markdown rendering in Claude Code doesn't carry over to the deck),
but a UI on the JSON can.

The format:

```
25/75 %ile         7.4-9.7x   19.4-28.3x   29.5-54.1x  11-17%   28-41%
```

Compact range notation. Same precision as the underlying values.

## Percentile method

Use the linear interpolation method (numpy's default,
`np.percentile(values, 25)`). For small `n`, this avoids the step
discontinuity of the nearest-rank method. With `n = 6`, the 25th
percentile sits between the 2nd-smallest and the 3rd-smallest values,
linearly interpolated.

```python
import numpy as np
p25 = float(np.percentile(values, 25))
p75 = float(np.percentile(values, 75))
```

Don't switch methods between multiples; consistency across the table
matters more than any single multiple's exact percentile choice.

## Handling missing values

Drop nulls. Never impute zero.

A peer with `ev_ebitda = null` (negative EBITDA, or missing financials)
drops out of the EV/EBITDA summary. It still appears in the table with
`n/a` in the EV/EBITDA column. The summary's `n` field reflects how
many peers actually contributed.

Imputing zero would be catastrophic: it would pull the median and mean
down by orders of magnitude. Imputing the sector mean would mask the
fact that the peer is missing the data. Banker convention is to drop
and report `n`; the skill follows.

## Subject excluded from peer summary

The summary stats are computed over peers only, not peers + subject.
The whole point of the comp table is to compare the subject against
the peer cohort; including the subject in the cohort blurs the
comparison.

When the subject's multiple is the same as the median, that's signal
(the cohort fairly prices the subject). When the subject differs, the
diff tells the banker how to position the deal.

## Rendered format

The summary block appears after the peer rows, separated by a blank
line for spacing:

```
ORCL   Oracle             6.8x      13.1x   24.4x       +9%        47%
... (peers) ...
CRWD   CrowdStrike       14.2x      45.8x   78.4x      +28%        24%

Median                    8.5x      22.1x   36.1x      +13%        35%
Mean                      9.6x      25.6x   45.1x      +16%        35%
25/75 %ile           7.4-9.7x   19.4-28.3x  29.5-54.1x  11-17%   28-41%
```

Right-align numeric columns; left-align labels. The three summary rows
sit one above the other. When `n < 3` for a given multiple, the summary
cell shows `n/a` (the percentiles aren't meaningful with 1-2 values).

## Why not mean by default

Banker convention favors median for the headline. CapIQ defaults to
median in its comp page; Bloomberg's RV screen shows both with median
as the highlighted row. The skill follows.

A v2 CLI flag could let the analyst pick `--summary-stat mean|median`
for the regression's `actual_vs_implied` framing, but the v1 default
is median for the table header and median for the "Read" generator.

## Computing on small cohorts

The default override map produces 8 peers. After drops (foreign issuer
empty financials, negative EBITDA), the effective `n` for some multiples
is 5-7. The percentile band is still meaningful at n=5 (with 4
quartiles, p25 sits at position 1.25 and p75 at position 3.75 with
linear interpolation). Below n=4, percentiles get noisy; the renderer
shows them but the analyst should know.

The schema's `n` per multiple lets the consumer suppress percentiles
below their preferred threshold without re-computing from raw peer
values.
