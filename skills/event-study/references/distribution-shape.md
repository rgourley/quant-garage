# Distribution shape (KDE)

The mean and t-stat summarize the sample's location and dispersion but
hide its shape. Two very different CAR distributions can share the same
mean:

- Symmetric unimodal: 24 prints clustered around +0.5% with normal tails.
  The mean is the right take.
- Bimodal: 12 prints ripped +3%, 12 dropped -2%. Mean of +0.5% is
  arithmetic; there is no "typical" reaction. You need to classify each
  observation before quoting the average.
- Fat right tail: 20 muted reactions near +0.3%, 4 huge beats +5%. The
  mean is inflated by the tail. The median is closer to what happens
  most of the time.

`event-study` runs a Gaussian kernel density estimate on the T+5 CAR
sample, finds prominent modes, and reports skew and excess kurtosis.
The result lives at `payload.summary.distribution_shape`. Populated
only when `n_subjects >= 10`; below that the shape read is unreliable.

## Method

1. **KDE.** Gaussian kernel over the sample:
   $$ \hat{f}(x) = \frac{1}{n h \sqrt{2\pi}} \sum_{i=1}^{n} \exp\left(-\frac{(x - x_i)^2}{2h^2}\right) $$

   Bandwidth via Silverman's rule: `h = 1.06 · σ · n^(-1/5)`.
   Silverman only mildly over-smooths bimodal samples with clear
   separation, so it's the right default for a screen. Callers who
   need more resolution can pass an explicit `bandwidth` to
   `quant_garage.stats.gaussian_kde`.

2. **Peak finding.** Local maxima on the density grid, filtered by
   prominence: a peak must exceed the deeper of its two adjacent
   valleys by at least 10% of the max density. This kills spurious
   wobbles from Silverman over-smoothing.

3. **Modality label.**
   - `unimodal`: 0 or 1 peak.
   - `bimodal`: exactly 2 peaks.
   - `multimodal`: 3+ peaks. Rare on real event samples; when it
     fires, the sample is likely a mix of event subclasses.

4. **Skew and excess kurtosis.** Standard sample moments on
   `z = (x - mean) / std`. Excess kurtosis of ≥ +1.5 flags a fat
   tail; ≤ -1.5 flags a thin tail; between is `normal`.

5. **`warn_mean_misleading`.** True when any of:
   - `modality_label` is `bimodal` or `multimodal`
   - `tail_label` is `fat`
   - `|skew| >= 1.0`

## Rendered output

Cross-section mode surfaces:

```
- Shape: bimodal (2 modes at -2.05%, +2.79%), tail thin
- Density: ▆▇█████▇▆▅▅▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▅▅▆▆▇█████▇▆
- Mean is misleading (bimodal, fat-tailed, or skewed); read the shape.
```

Aggregate mode adds skew and excess kurtosis to the shape line since
that block already has more real estate. The sparkline uses eight
UTF-8 block characters over 40 columns.

## When the shape matters

- **Soft-signal 8-K events** (item 7.01 Reg FD, item 8.01 Other).
  These are the ones with bimodal reactions: half the time the
  filing is bullish news, half the time it's bearish, and averaging
  produces a t-stat near zero. The shape line converts "no signal"
  into "two signals; classify the filing to pick the trade."
- **Guidance-driven prints.** When guidance dominates (like the
  mega-cap tech example in `event-study`'s README), the CAR
  distribution can look normal on the surface but have a fat left
  tail where guidance disappoints. Excess kurtosis catches this.
- **Dividend-change reactions across policy regimes.** A raise from
  a serial raiser (routine) vs a raise from an unexpected payer
  (rerating). Same event class, different reaction distribution.

## What this does NOT do

- **Cluster the observations.** The peak locations tell you the
  distribution has two centers; they don't tell you which prints
  belong to which cluster. That's a downstream job for the analyst
  or for a future `cluster-by-shape` extension.
- **Test bimodality significance.** Hartigan's dip test would give
  a p-value on unimodality vs multimodality; not implemented. The
  prominence filter handles the common cases and the honest read is
  "the KDE has two peaks; treat as a hypothesis, not a proof."
- **Fit a mixture model.** No two-Gaussian EM. If callers want
  cluster means and variances they need a full mixture; this is a
  screen, not a mixture fit.
