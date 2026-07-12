---
name: change-point-detector
description: Bayesian Online Change-Point Detection (BOCPD) on a ticker's daily log returns. Detects points in time where the return-generating distribution changed (regime shift in mean, vol, or both), reports the confidence at each detected boundary, and emits per-segment statistics (annualized return, annualized vol) so the reader can see what changed. Uses Adams and MacKay (2007) BOCPD with a Normal-Gamma prior on (mu, tau) and a Student-t predictive so hyperparameters update in closed form. Requires Stocks Basic. Runs on the free tier.
---

# change-point-detector

You hand over a ticker. The skill pulls 2 years of daily closes,
computes log returns, and runs Bayesian Online Change-Point Detection.
Reports the specific dates where the return distribution appears to
have shifted, the confidence at each boundary, and the annualized
return + vol per segment so you can see what changed.

## When to invoke

- "When did SPY's regime shift this cycle?"
- Sharpening `market-regime` when the rule buckets miss the edge
- Auditing a pairs-scanner result: "did this pair's cointegration
  break, and if so when?"
- Post-hoc labeling on a name that behaved differently pre- and
  post-a specific event

Not for: real-time entries. BOCPD lags real change points by 5-20
observations; the algorithm needs enough post-shift data to update
the posterior.

## What you need

- A ticker (`--ticker`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--lookback-days` (default 504, ~2 years). Minimum 100.
- `--lambda-run` (default 250): prior mean run length between change
  points in observations. 250 = "expect roughly one change per
  year." Raise to 500 to suppress smaller regime edges; lower to
  100 to be more sensitive to short-lived regimes.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON**.
`change_points` with per-detection date, index, and posterior
confidence. `segments` with per-segment n_obs, mean/std daily return,
and annualized return + vol. `current_run_length_obs` for how many
observations since the last detected boundary. Full setup echoed
(lambda_run_prior, threshold).

**Layer 2: rendered note**. Header + summary of counts, detected
change point list, segment stats table, one-line Take comparing
current vs prior regime.

## How it works

Adams and MacKay (2007) BOCPD:

1. **Model.** Assume returns are drawn from a Normal, with unknown
   mean mu and precision tau. Put a Normal-Gamma prior on (mu, tau)
   with hyperparameters (mu0=0, kappa0=1, alpha0=0.1, beta0=0.01).
   This gives a Student-t predictive with closed-form updates when
   a new observation arrives.
2. **Run length posterior.** Maintain P(r_t = r | x_{1:t}), the
   posterior over "run length since last change point." At each t:
   - Growth: with prob 1 - hazard, r_t = r_{t-1} + 1. Weight by the
     Student-t predictive under the sufficient stats accumulated for
     that run.
   - Change: with prob hazard, r_t = 0. Weight by the marginal
     predictive summed over all previous run lengths.
   - Normalize.
3. **Hazard.** Geometric with rate 1/lambda_run. lambda_run is the
   prior mean run length between change points.
4. **Detection.** A time t is flagged as a change point when
   P(r_t = 0 | x_{1:t}) exceeds the threshold (0.5 by default).
   Consecutive detections within 20 observations are merged.
5. **Segments.** The boundaries partition the return series into
   segments; per-segment stats let a reader see the shift.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and daily aggs.

## Output mode: note

Narrative note with a per-segment stats block. A single-name change
point analysis is typically 0-5 segments; note format reads better
than a table.

## Endpoints used

- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`
  One call per run.

## Doesn't handle (yet)

- **Multivariate.** Single-ticker only. A cross-name change-point
  detector on a portfolio's daily P&L would extend cleanly by
  swapping the univariate predictive for a multivariate one.
- **PELT.** Adams-MacKay BOCPD is Bayesian. PELT (Killick, Fearnhead,
  Eckley 2012) is a frequentist alternative that scales O(N) and
  gives L2-optimal segmentation. Queued as `pelt-segmentation`.
- **Real-time flag.** No streaming mode. Adding one would just wrap
  the same update inside a loop.
- **Hyperparameter tuning.** The prior on (mu, tau) is fixed and mild.
  A caller who cares about specific regime types (vol regime vs mean
  regime) could tune this.

These are clean PR extensions.
