# Cross-section methodology

When the input shape is "many tickers, one event period" or "many
tickers, many events," the skill aggregates the per-subject CARs into
a cross-sectional summary. The math is straightforward; the gotchas
are in what to include and what to drop.

## Aggregation

Across N subjects (each a `(ticker, event)` pair with a computed
`car_t5_pct`):

```
mean   = sum(car_t5_pct) / N
median = middle value of sorted car_t5_pct
std    = sqrt(sum((x - mean)^2) / (N-1))   # sample std
t_stat = mean / (std / sqrt(N))
```

Also compute the same triple (mean, median, std, t_stat) at T+1 and
T+3 for the `horizon_breakdown` block. This lets the reader see
whether the effect concentrates in the first day or builds across
the week.

Percentiles emitted for the T+5 distribution: p10, p25, p50, p75, p90.

## What gets dropped

A subject is excluded from the aggregate when:

- `car_t5_pct` is null (missing close data, ticker halted)
- The event date is outside the requested window
- The event is from a class that doesn't match the query class (the
  resolvers shouldn't return wrong-class events, but the aggregator
  double-checks)

The summary always reports the surviving `n_subjects` separately from
the input ticker count (`n_tickers`); these can differ in aggregate
mode where each ticker contributes K events.

## Surprise-vs-reaction correlation (earnings, Tier A only)

For the `earnings` class on Tier A, the cross-section block adds a
`surprise_reaction_correlation` measurement: the Pearson correlation
between the per-event `surprise_eps_pct` and `ar_t5_pct`.

```
rho = cov(surprise, ar_t5) / (std(surprise) * std(ar_t5))
r_squared = rho ** 2
```

Interpretation:

- |rho| > 0.7: the market is pricing the surprise efficiently; high-
  surprise names reacted more.
- 0.3 to 0.7: weak signal; surprise explains some but not most of
  the cross-sectional variation.
- < 0.3: surprises did not predict reactions; either the market
  already discounted the surprise or other factors (guidance,
  commentary, sector flow) dominated.

The rendered take cites the correlation only when |rho| > 0.5 AND
n >= 5; below that the relationship is too noisy to act on.

This block is null for non-earnings classes (no signed magnitude) and
for Tier B (no surprise data).

## Regime check (aggregate mode only)

In aggregate mode, when `n_subjects >= 8`, the summary adds a
`regime_check` block. The logic:

1. Sort all subjects by event_date.
2. Compute `recent_mean = mean(t5_car of last 4 events)`.
3. Compute `full_mean = mean(t5_car of all events)`.
4. Compute `se_full = std / sqrt(n)`.
5. Flag `regime_shift: true` when
   `abs(recent_mean - full_mean) > se_full`.

The flag fires when the most recent quarter of data has materially
shifted from the full-window average. It's the difference between
"PEAD-after-beats has averaged +1% over 5 years" and "PEAD-after-
beats was +1% historically but is -0.3% in the last 4 events." The
take should cite the recent figure when the regime has shifted.

When `n_subjects < 8`, the regime_check is null. The full sample is
too small to slice into "recent" and "full."

## Why Pearson, not Spearman, for surprise correlation

Quant convention is Spearman rank correlation for cross-sectional
factor analysis (see `factor-research` for the rank-IC approach).
For event studies the magnitude of surprise matters: a +12% surprise
is not the same setup as a +1% surprise, and the t-stat of the
reaction depends on the spread of surprise sizes. Pearson preserves
that magnitude information.

Spearman would still work and gives a cleaner number when outliers
dominate (e.g. GOOGL's +92% surprise in Q1 FY2026 distorts a Pearson
on 5 events). The skill emits Pearson by default; consumers wanting
Spearman can compute it from the per-subject JSON.

## Handling missing surprise data in cross-section

When one or two subjects in the cross-section lack surprise data
(Tier B fallback fired for those specific names), the
surprise_reaction_correlation block is null IF more than 25% of the
subjects are missing surprise. Below that threshold, the missing
subjects are dropped from the correlation computation only, and the
`n` field in the surprise correlation block reflects the reduced
sample.
