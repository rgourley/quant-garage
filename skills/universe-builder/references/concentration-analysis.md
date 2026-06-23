# Concentration analysis

How to detect sector or industry concentration in a screen's top-N
results, and when to surface it as a callout. Read this before changing
the flag threshold or adding a new dimension.

## The problem

A user runs "top 20 names by 3M momentum, market cap > $10B." They
expect a diversified list. What they get is 12 semiconductor stocks,
2 software, 1 healthcare, and 5 other. The screen is technically
correct; it's just that semis ripped over the lookback window and
dominated the top of the rank.

The user usually doesn't notice until they regress on this set, see
crazy sector beta, and burn an afternoon figuring out where the
loading came from. The concentration check surfaces it up front.

## The method

Compare the observed sector distribution in the top-N against the
expected distribution under the starting universe's sector weights.

```
expected_count(sector) = N * (starting_universe_count(sector) / starting_universe_total)
observed_count(sector) = top_n_count(sector)
```

The expected count is what you'd see if the screen's top-N picked
sectors uniformly relative to the starting pool. The observed count is
what actually surfaced.

The flag metric is **standard deviations overweight**:

```
expected_p = starting_universe_count(sector) / starting_universe_total
expected_count = N * expected_p
expected_stdev = sqrt(N * expected_p * (1 - expected_p))    # binomial
std_devs_overweight = (observed_count - expected_count) / expected_stdev
```

This treats top-N membership as a Bernoulli trial against the
starting weight. The binomial standard deviation tightens when the
sector is small in the starting pool and loosens when it's already
large; both effects are correct (a heavy single-stock IT services
sector at 1% of the pool getting 5 in the top 20 is a much bigger
surprise than the same count from Industrials at 12%).

Flag when `|std_devs_overweight| >= 2.0`. The schema's
`concentration[]` array records every group that crossed the
threshold, sorted by absolute value descending.

## Zero-variance guards

When `expected_p == 0` (a sector that has zero members in the starting
universe but somehow shows up in the top N), set `std_devs_overweight =
+inf` rendered as `>5σ`. This shouldn't happen with the standard
candidate sources but does occasionally with manually-supplied
candidate pools.

When `expected_p == 1` (every name in the starting universe belongs to
the same sector, e.g. a Banking-only screen), skip the concentration
check entirely. There's nothing to flag.

When `N < 10`, the binomial approximation gets noisy. The reference
implementation skips the concentration check when `N < 10` and emits
an empty `concentration[]` array.

## Dimensions

By default, run the check on `sector` (SIC-based bucket: Semiconductors,
Software, Pharmaceuticals, Banking, etc.). The implementation can also
run on `industry` (a finer SIC description) but industry buckets are
often too sparse to flag reliably; v1 reports sector only.

The schema accepts both via the `dimension` field. Future versions
might add `country` or `market_cap_bucket` (mega vs large vs mid) as
additional dimensions; the methodology is the same.

## When NOT to flag

The check exists to warn the user about an unintended concentration.
Skip the callout when:

1. The screen explicitly filtered to one sector (`--include-sectors
   Semiconductors`). The user asked for sector concentration; flagging
   it is noise.
2. The candidate pool is already heavily one-sided (a sector ETF's
   constituents, a single-industry index). The starting weight makes
   the concentration unsurprising.
3. The top-N is smaller than 10. Statistics break down.

The reference implementation tracks (1) and (3) automatically. (2) is
the user's call; the schema records the finding regardless and the
rendering layer can suppress it based on `scan_params`.

## Output format

Per concentration entry:

```json
{
  "dimension": "sector",
  "value": "Semiconductors",
  "count_in_topn": 6,
  "expected_count": 1.8,
  "std_devs_overweight": 3.21,
  "top_n": 20
}
```

The rendered table surfaces only the most-overweight entry in the
first bullet of the concentration block, then summarizes the rest of
the top 20 in plain English ("3 software, 2 healthcare, 9 other") so
the reader sees both the flag and the context.

## A worked example

Starting universe: 1,243 US large-caps (mcap > $10B). Sector weights:

- Semiconductors: 47 names (3.8%)
- Software: 158 names (12.7%)
- Healthcare: 215 names (17.3%)
- Industrials: 187 names (15.0%)
- Other: 636 names (51.2%)

Top 20 by composite z-score (mom + ocf yield):

- Semiconductors: 6 (NVDA, AVGO, AMD, MU, LRCX, KLAC)
- Software: 3
- Healthcare: 2
- Industrials: 1
- Other: 8

For semis:

```
expected_p = 47 / 1243 = 0.0378
expected_count = 20 * 0.0378 = 0.756
expected_stdev = sqrt(20 * 0.0378 * 0.9622) = 0.853
std_devs_overweight = (6 - 0.756) / 0.853 = 6.15
```

That's a >6σ overweight, well past the 2σ threshold, and the callout
flags it. The user sees:

```
Concentration check
- Top 20 by Z-score: 6 semis (+6.2σ vs sector weight in starting universe)
- Top 20 by Z-score: 3 software, 2 healthcare, 1 industrials, 8 other
- Worth knowing before regressing on this set
```

## Why binomial, not chi-squared

A chi-squared test against the full sector distribution is the more
formal approach. It catches multi-sector concentration that any single
sector might not trigger.

The skill uses the per-sector binomial because:

1. The user wants to know **which** sector dominated, not just that
   the distribution differs.
2. The output renders one bullet per flagged sector. Chi-squared
   produces a global p-value that's hard to act on at the row level.
3. The 2σ threshold is interpretable: "more than 95% chance this
   sector is overweighted by chance alone." A chi-squared p-value
   says nothing about magnitude.

Both checks have a place. The binomial fits the rendering format and
the user's question.
