# Monte Carlo fair-value distribution

## Why this exists

The single-point `fair_value_at_peer_median` number multiplies three
peer medians (growth, margin, exit multiple), discounts at a hardcoded
WACC, and emits one share price. It reads like a forecast even though
it is three medians stacked. PMs and bankers anchor on the number;
researchers correctly mistrust it. MC mode replaces the point with a
distribution sampled across the joint peer driver pool, so the headline
becomes "current price sits at the 19th percentile of plausible
outcomes" instead of "$826 fair value."

## What the drivers are

Three peer-derived inputs are sampled per draw:

- `growth` — peer revenue growth TTM. Same pool the growth-sanity
  section consumes.
- `margin` — peer EBITDA margin TTM. Same pool the margin-sanity
  section consumes. Peers without reported D&A are already excluded
  upstream (C7 fix); MC inherits the cleaned pool.
- `exit_multiple` — peer EV/EBITDA. Same pool the reverse-DCF
  consumes when picking the peer-median exit multiple.

The peer set itself is the same waterfall the rest of the script uses
(curated override → SIC fallback). If the peer cohort is bad, MC mode
will sweep around bad inputs and produce a smooth, confident,
worthless fan.

## Independence assumption

Drivers are sampled INDEPENDENTLY. In reality peer growth and peer
margin show rho ~ 0.3-0.5 historically (high-growth software names
also tend to run at higher margins), so the joint distribution has
fatter tails than independent sampling produces. The bias is one-
directional: independent sampling understates p5 and p95, both pulled
toward the body. The point estimate at p50 is approximately
unbiased; the IQR (p25-p75) is approximately right; only the tails
suffer.

The mitigation is a future Cholesky-decomposed correlated sampling
path. For v1 the independence caveat is surfaced in `tier_caveats`
so consumers can discount the tails accordingly.

## Why Spearman for sensitivity

Spearman rank correlation is used instead of Pearson because:

1. Fair-value distributions are heavy-tailed (long right tail when
   growth and multiple both draw high), and Pearson is sensitive to
   tail outliers in a way Spearman is not.
2. The relationship between margin and fair value is monotone but
   nonlinear (compound growth amplifies margin sensitivity at the
   horizon), and Spearman correctly captures monotone nonlinear
   coupling where Pearson would underweight it.
3. Spearman makes no distributional assumption about the input.

The output ranks drivers by `abs(rho)` so PMs can read which lever
moves fair value most in the current cohort.

## Why 10000 samples

10000 keeps p5 and p95 standard errors below 1% of the mean for
reasonable peer cohorts, runs in under 100ms end-to-end after the API
fanout, and produces stable percentile bands across re-runs with
different seeds. 1000 samples is a noisy floor; 100000 is overkill
for a sensitivity sweep and the marginal precision gain is invisible
in the rendered output. The CLI clamps `--mc-samples` to `[1000,
100000]` and errors on out-of-range values.

## How to read the percentile output

- `p10` to `p90` is the headline plausible range. Anchor pitches and
  PM conversations here.
- `p5` and `p95` are tail context. Useful for "what does the bear
  case need to be" framing; biased toward the body by the independence
  assumption, so read them as conservative tails not real ones.
- `p25` to `p75` is the IQR body, the place to debate cohort
  assumptions.
- The `current_price_percentile` and `target_price_percentile` fields
  are the one-line PM takeaways: "current sits at the 19th, target at
  the 73rd."

## Caveats

- **WACC held constant.** The cost-of-capital input is a hardcoded
  9% in both the point estimate and MC paths. A future flag will let
  WACC vary across draws; for now run sensitivity at +/- 100bps
  separately.
- **Drivers assumed independent.** See independence section above.
  Tail percentiles understate slightly.
- **Peer set determines the universe of outcomes.** MC samples from
  the peer pool. If the peer cohort is wrong (wrong sub-industry,
  wrong scale, wrong growth stage), the entire distribution is wrong
  too. Bad peers in, bad MC out. Inspect the `drivers_used` block in
  the JSON to confirm the peer median sits where it should before
  trusting the percentile output.
- **Insufficient peers triggers a skip.** Fewer than 5 valid values
  on any driver returns a `reason: insufficient_peers` block and
  skips the MC render. The point-estimate path still emits.

## What MC mode does NOT do

MC mode is not a forecast. It is not Monte Carlo simulation of the
business. It does not model competitive dynamics, capital structure
changes, macro shocks, or product cycles. It is a sensitivity sweep
over the same three peer-derived inputs the point estimate
multiplies, producing the distribution of fair-value outcomes
consistent with the peer cohort's empirical driver pool. The right
framing is "if drivers come from this peer set, here is the fair-
value fan," not "here is what the stock will be worth in five years."
