# Stress scenarios — worst-N historical days

The empirical answer to "what does it look like when this book has
a bad day?" Pick the N most-negative days in the portfolio return
series, attribute each day's loss to the individual names that
caused it, and report the benchmark's return on the same day.

## Why historical instead of Monte Carlo

Monte Carlo stress (sampling from a parametric distribution, or
bootstrapping with replacement) has appeal: you can generate
arbitrarily many scenarios, you can stress correlations to extremes,
you can model regime shifts. It also requires you to commit to a
distributional assumption that the real returns don't satisfy.

Historical worst-N is the conservative choice: it's exactly what
already happened to a book like this one. No model risk. The
trade-off is sample size — at 252 days you get 252 candidate days
for "the worst." If the lookback didn't include 2020 or 2008, the
worst days in the sample may not look that bad.

The skill emits worst-N alongside VaR and ES, which TOGETHER give
the consumer a triangulated view: VaR/ES quantify the distribution
shape; worst-N shows the specific days that caused the tail.

## The algorithm

```
1. Compute daily portfolio returns r_p[t] over the aligned window.
2. Sort by return value ascending. Ties broken by chronological
   index (earlier date wins) for stable output.
3. Take the top N (most negative).
4. For each picked day, attribute the loss to individual names:
   contribution_i = w_i * r_i_on_that_day
   where w_i is the position weight and r_i is name i's log return.
5. Sort contributions ascending (worst first) for the render.
6. Annotate each day with the benchmark return on that same date.
```

## What the per-name attribution actually says

The contributions are additive: `sum_i (w_i * r_i_on_day) =
r_p_on_day`. So a portfolio return of -5.5% might decompose as:
NVDA -2.4pp, META -1.1pp, GOOGL -1.1pp, AMZN -0.9pp. The `pp` unit
(percentage points of the BOOK return) is the right one because it
adds up to the total. The render uses `pp`; the JSON emits decimal
fractions of the book.

This attribution is descriptive of THAT specific day. It is NOT a
forecast of which name will lead the next bad day. The names that
hurt most on past stress days tend to be the high-vol, high-weight,
high-correlation-to-the-stressor names — which usually overlap with
the position_variance_contribution decomposition. When they
disagree, it's because the historical stress day had a name-specific
shock that doesn't show up in unconditional variance.

## Why we annotate with the benchmark return

A book down -5.5% on a day SPY was down -4.1% behaved roughly in
line with its beta (1.32 implies the book moves 1.32x SPY, so 1.32
× -4.1% = -5.4%). A book down -5.5% on a day SPY was -0.4% is
exhibiting idiosyncratic stress — the book did something the index
didn't, and the PM should care more about understanding why.

The benchmark column is the dividing line between "market risk
showed up" and "book-specific risk showed up." It's the cheapest
way to flag the latter.

## Edge cases the math handles

- **Tied return values on the worst day.** The lib uses a stable
  sort on `(value, original_index)`. Ties resolve to the
  chronologically earlier day. PMs reading the output get a
  deterministic ordering rather than a non-reproducible one.
- **Fewer than N days in the sample.** The lib clamps N to the
  available sample. If you ask for stress-N=20 on a 60-day book the
  output has 60 scenarios. The render still works; the PM sees the
  full distribution rather than getting an error.
- **Per-name returns array shorter than the portfolio return array.**
  Shouldn't happen — the caller aligns everything — but the lib
  defensively skips per-name attribution for any out-of-range index
  rather than raising mid-render.
- **Weight-zero names in the per_name_returns dict.** Skipped
  silently. Common when the caller passes the full panel for a book
  where some weights are 0; the JSON output then omits zero-weight
  contributions, which is what the PM wants to see.

## The N parameter

Default 5 because that's enough to see the shape of the tail
without overwhelming the report. Risk officers often want more (10,
20); the `--stress-n` flag honors any value. Above ~15 the rendered
report gets long; the JSON consumer can still use everything.

## What this metric does NOT prove

Worst-N is in-sample. Picking the worst 5 days in a 252-day window
does not tell you whether worse days are possible — they are, in
both directions. The methodology is exposed in `tier_caveats` so the
consumer doesn't read worst-N as a worst-case forecast.

For a forward-looking stress (which the script does NOT do), the
pattern is to perturb covariance and re-evaluate parametric VaR
under the stressed cov. Queued for a v2; non-trivial because every
stress scenario needs an assumption about correlation behavior.
