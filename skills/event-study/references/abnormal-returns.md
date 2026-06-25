# Abnormal returns: AR and CAR

The skill measures event reactions in abnormal-return space, not raw
return space, so that broad market moves on the event day don't
contaminate the signal. A name printing earnings on a day SPY is +2%
is not the same setup as a name printing on a day SPY is flat, even
if the raw next-day return is identical.

## Definitions

For each ticker and each horizon (T+1, T+3, T+5):

```
raw_return(T+k) = (close(T+k) / close(T0)) - 1
spy_return(T+k) = (spy_close(T+k) / spy_close(T0)) - 1
ar(T+k)         = raw_return(T+k) - spy_return(T+k)
```

`car(T+5)` is the cumulative abnormal return through T+5. For the
SPY-naive model with daily close-to-close measurement at each horizon,
`car(T+5) == ar(T+5)` because we measure from T0 close to T+5 close in
one step. The field is preserved as `car_t5_pct` so that a v2 daily-
compounded model or a multi-day window event class doesn't break
consumers.

## Anchor: what counts as T0

T0 is the trading day whose close is the last close that did NOT
reflect the event. The classifier:

- **AMC event** (press release after 16:00 ET): T0 = the trading day of
  the press release. The reaction starts at next session's open and is
  measured at next session's close = T+1.
- **BMO event** (press release before 09:30 ET): T0 = the trading day
  BEFORE the press release. The reaction starts at the open of the
  press-release day and is measured at that day's close = T+1.
- **Intraday event** (between 09:30 and 16:00): T0 = the trading day
  before. The reaction includes the intraday move on the release day.
  This is noisier but the daily-close framework can't do better
  without an intraday endpoint.

This matches the rule used in `earnings-drilldown` Tier B. The session
classifier lives in
[`event-class-definitions.md`](./event-class-definitions.md).

## SPY as the benchmark (v1)

v1 uses SPY as the market proxy with no beta adjustment. The choice is
deliberate:

- Computationally trivial.
- Mega-cap betas are close to 1 over short windows; the residual is
  small.
- Beta estimation on a 60-day pre-event window introduces its own
  noise; the simple version is more robust for the skill's primary
  use case.

The output schema reserves `model: "spy" | "capm"` so the CAPM upgrade
in v2 (per-name beta estimated on the 60d pre-event window) doesn't
break consumers. A row in the JSON's `model` field always names the
benchmark used.

## Missing-data handling

If SPY is missing on any horizon date (holiday data drop, very rare),
the AR for that horizon is `null` in the JSON and the rendered output
prints `n/a`. Cross-sectional aggregation skips null entries and notes
the reduced n in the summary block.

If the ticker's close is missing (suspension, halt), the entire
subject is flagged as `event_window_returns: incomplete` and excluded
from the cross-section average. This is rare in mega-cap names but
common for thinly-traded biotechs in the dividend_changes class.

## Edge case: event date is not a trading day

Resolvers (per `event-class-definitions.md`) snap event_date to the
nearest trading day BEFORE the event for AMC/intraday and to the
trading day OF the event for BMO. Earnings prints occasionally land
on a weekend (SEC EDGAR shows acceptance on Saturday for an after-
hours Friday release); the snap maps these to Friday's close as T0.

## Why not a CAPM model in v1

Two reasons:

1. **Beta drift.** A 60-day pre-event beta on NVDA from 2024 is a
   different number than the realized 2026 beta. The CAPM-AR series
   ends up dominated by stale beta when momentum stocks re-rate.
2. **Reader cognition.** "Stock moved +7%, SPY moved +0.3%, abnormal
   +6.7%" is a number anyone on the desk can sanity-check. "Stock
   moved +7%, model expected +0.5% given alpha 0.0001 and beta 1.3,
   abnormal +6.5%" requires trusting the model. For v1 the simpler
   number wins; v2 will offer both.
