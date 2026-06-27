# Concentration

The "is this book actually diversified?" question. Vol, beta, and VaR
all describe how the book moves; concentration metrics describe how
the book is constructed. Different question, equally important.

## Why this matters alongside vol

A book with portfolio vol of 21% can be one position (effective N =
1) or thirty positions (effective N = 30). The 21% number says
nothing about which. PMs reading a risk report need to know whether
the volatility is from a wide cohort or from one name carrying the
weight.

Highly-concentrated books have a different failure mode than
diversified ones: idiosyncratic shocks (a CEO departs, a guidance
cut, a fraud disclosure) hit single-name books much harder than
their volatility number implies, because the volatility is computed
on historical price returns that mostly don't include the
discontinuous shock. Concentration is the cheap warning sign that
this risk exists.

## Top-K weights

The simplest concentration metric: what fraction of the book is in
the top 1 / 3 / 5 names by weight?

- `top_1_weight` < 0.15: well-spread book
- `top_1_weight` 0.15-0.30: typical actively-managed
- `top_1_weight` > 0.30: high-conviction or concentrated
- `top_5_weight` > 0.80: most of the risk lives in five names

These are conventions, not rules. A PM running a 25-name book at
4% each looks very different from a PM running 5 names at 20%
each, and both can be "right" for their mandate.

## Herfindahl-Hirschman Index (HHI)

The cleaner single-number summary:

```
HHI = sum_i (w_i ** 2)
```

Properties:

- HHI = 1.0: single position (one weight of 1.0)
- HHI = 1/N: equal-weighted N positions
- 0 < HHI < 1 in general
- Lower = more diversified

For an equal-weighted 4-name book: HHI = 4 × (0.25)² = 0.25. For a
single-name book: HHI = 1. For a 25-name equal-weight: HHI = 0.04.

HHI is the standard concentration metric in antitrust (where it
measures market share concentration), so it has a long pedigree.
For portfolios it's the same math — replace market shares with
position weights.

## Effective N

The interpretable version of HHI:

```
effective_N = 1 / HHI
```

The number of equally-weighted positions that would produce the same
HHI as this book. An effective N of 4 means "this book is as
concentrated as a 4-name equal-weighted portfolio." A book with one
40% position and ten 6% positions has HHI ≈ 0.20, effective N ≈
5.0. The Herfindahl correctly says it's NOT as diversified as the
11-name count suggests.

PMs grok effective N intuitively because it maps to the equal-weight
benchmark they already use.

## What this metric does NOT prove

Concentration is a structure measure, not a risk measure. A
40%-NVDA + 30%-AMZN + 30%-cash book has the same HHI as a 40%-XOM
+ 30%-BAC + 30%-cash book. The first concentrates idiosyncratic
mega-cap-tech exposure, the second concentrates cyclical financials
+ energy — wildly different risk profiles for the same HHI.

The PM should read concentration together with the position variance
contribution decomposition. If one name has 40% weight AND 50%+ of
the variance share, that's the actual concentrated-risk signal. If
the 40%-weight name has only 25% of the variance, the diversification
is working harder than the weight suggests because the name has
relatively low vol vs the cohort.

## Sign and abs

The lib uses `abs(weight)` so long-short books report sensibly. A
+30% long and a -30% short book has top-1 weight of 30% and effective
N of 2 — consistent with how PMs think about it. Pure long-only books
read identically either way.

## Renderer + Take

The render writes a single line:

> Concentration: top 5 = 100%, Herfindahl 0.25 (effective N = 4.0)

The Take surfaces concentration whenever `herfindahl > 0.30`, with
explicit framing of effective N. Combined with the per-position
variance contribution table, the consumer can see both the weight
concentration and the risk concentration at a glance.
