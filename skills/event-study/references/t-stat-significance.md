# T-stat and significance: thresholds, formulas, sample size

The skill emits t-stats in two places: per-subject (this event's CAR
vs the name's own history) and cross-sectional (the average CAR
across events vs zero). Both follow the same arithmetic; the
distinction is what's in the denominator.

## Formulas

**Per-subject (single event vs prior distribution).** The skill
computes a z-score because the test is "is this observation extreme
given the prior distribution," not "is the mean different from zero."

```
z = (this_event_t5_car - prior_mean_t5_car) / prior_std_t5_car
percentile = rank(this_event in prior_distribution) / (prior_n + 1)
```

`prior_n` is the number of prior events for the same name in the same
class. For NVDA earnings, that's the count of NVDA earnings in the
trailing window (typically 8-12 over 2-3 years).

**Cross-section (mean CAR vs zero).** Standard one-sample t-test:

```
t = mean_car / (std_car / sqrt(n))
```

Two-tailed significance at p < 0.05 corresponds roughly to |t| > 2.0
for the sample sizes the skill operates on (n=5 to n=100).

## Sample size threshold: n=8

Both per-subject and cross-section computations require n ≥ 8 prior
events before the rendered output calls the result "significant" or
acts on the t-stat in the take.

Rationale:

- Below n=8, the t-distribution has fat enough tails that a
  |t| > 2.0 result happens by chance more often than the headline
  p-value suggests. The conservative cutoff for "you can act on
  this" is closer to n=12, but n=8 is the lower bound where the
  estimate is at least informative.
- For earnings specifically, 8 prior events is two full years of
  quarterly prints. This is enough to capture one full earnings
  cycle in different market regimes.
- For dividend changes, n=8 is rare. Most mature dividend payers
  have changed their dividend 4-6 times in the last 8 years. The
  rendered output emits the number but flags `underpowered: true`.

When `prior_n < 8`, the JSON still emits `z_score` and `percentile`
(so a UI can render the raw comparison), but adds
`underpowered: true`. The rendered output prints the number with the
explicit warning rather than hiding it. Hiding data leads operators
to compute the same number badly in a spreadsheet; showing it with a
clear flag is the more honest path.

## Why z-score for per-subject, t-stat for cross-section

A subtle but important distinction.

**Per-subject:** the question is "is this observation extreme in the
distribution of prior reactions for the same name." That's a z-score
of one observation against a known distribution. T-tests apply when
you're estimating a mean from a sample; here, the single event IS the
sample, and the prior distribution is the reference.

**Cross-section:** the question is "is the average reaction across N
events reliably non-zero." That's a one-sample t-test of the mean
against zero, with the standard error of the mean in the denominator.

Mixing these up is the most common LLM-generated finance error in
event studies. The skill keeps the two computations explicit and
labels the output field accordingly (`z_score` per-subject,
`t_stat_avg_vs_zero` for cross-section).

## Direction concurrence (earnings class only, Tier A)

When the event class carries a direction signal (surprise %), the
per-subject output adds a `direction_concurrence` count: of the
prior_n events, how many had abnormal returns whose sign matched the
surprise sign. The format is `X/N` (e.g. `5/8`).

This is a separate, weaker check: it tells the reader whether the
market historically agrees with the surprise direction at all. A name
where 8/8 prior surprises produced same-sign reactions is a different
setup than a name where 4/8 did, even if the magnitudes look similar.

Direction concurrence is null for dividend_changes and
large_volume_spike (no signed direction signal).

## What "significant" means in the rendered output

The rendered output uses one of these phrases based on the result:

| Condition | Phrase |
|---|---|
| n >= 8 AND |t| > 2.0 | `significant` |
| n >= 8 AND |t| <= 2.0 | `not significant` |
| n < 8 | `underpowered (n={n})` |
| n < 4 | section omitted entirely |

The take at the top of the rendered output cites the t-stat only when
`significant` per the above. An "underpowered" result is shown in the
detail block but doesn't drive the take.
