# Growth and margin sanity

The analyst hands over `assumed_growth` and `assumed_margin`. The skill
compares each to the peer 25-75 percentile band on the same metric.

This is the simplest of the three sanity checks and the one that
usually surfaces the most obvious model drift: it's common for the
analyst to be 5-15pp above or below the cohort on growth or margin
without realizing how far the assumption sits from peer reality.

## Inputs

- `assumed_growth` (decimal): the analyst's projected revenue CAGR
  over the horizon. The skill compares this against the peer **TTM**
  revenue growth distribution (see "TTM as proxy" below for the
  trade-off).
- `assumed_margin` (decimal): the analyst's projected steady-state
  EBITDA margin. Compared against peer **TTM** EBITDA margin.

## Peer distributions

For each peer, pull `revenue_growth_ttm` and `ebitda_margin` from the
per-peer metrics computed in `pitch-comps`. Use the same percentile
methodology as `pitch-comps/references/cohort-statistics.md`:

- Drop nulls. Never impute zero.
- Compute p25, p50, p75 across the non-null peers.
- Record `n_peers_in_distribution` per metric so the consumer knows
  the sample size that fed the comparison.

## Status

| `assumed`        | Peer band     | Status     |
|------------------|---------------|------------|
| inside [p25, p75]| any           | `in_line`  |
| > p75            | any           | `above`    |
| < p25            | any           | `below`    |
| null             | any           | `n_a`      |

## delta_pp

The schema records `delta_pp = (assumed - peer_p50) × 100`. This is the
percentage points of difference from peer median, which is the number
the take generator quotes. A reader sees "growth assumption is +8pp
ahead of the cohort" and knows the magnitude of the gap immediately,
without having to do arithmetic between "28%" and "20%".

Sign convention: positive `delta_pp` means assumed > peer median.
Negative means assumed < peer median.

## TTM as proxy

The peer-side metric is **trailing twelve months**: `revenue_growth_ttm`
and `ebitda_margin` (TTM). The analyst-side `assumed_growth` is a
**multi-year CAGR over the horizon**.

This is a deliberate trade-off:

- **The correct comparison** for `assumed_growth` would be the peer
  **forward 5-year CAGR**, requiring either consensus estimates
  (Benzinga doesn't currently expose them in the subscribed bundle) or
  20 quarters of trailing financials per peer to fit a CAGR.
- **The shipped v1 comparison** uses TTM growth as the proxy. This
  understates the gap for cohorts whose growth is decelerating (e.g.
  hardware semis post-cycle) and overstates it for cohorts that just
  came off a low-growth year. For the mega-cap tech cohort, TTM and
  5y CAGR are typically within a few pp.

This is documented in the rendered note as a single line:
"Peer growth distribution uses TTM as 5y CAGR proxy." A clean v2 PR
pulls 20q per peer and computes the true 5y CAGR.

For `assumed_margin`, the comparison is more defensible: EBITDA margins
are slower-moving than revenue growth, and the peer TTM margin is a
reasonable comparator for the analyst's steady-state margin
assumption.

## What's recorded in the JSON

```json
"growth_sanity": {
  "assumed": 0.28,
  "peer_p25": 0.11,
  "peer_p50": 0.18,
  "peer_p75": 0.22,
  "delta_pp": 10.0,
  "status": "above",
  "n_peers_in_distribution": 7
}
```

`margin_sanity` has the identical shape with the `assumed_margin` and
peer EBITDA margin distribution.

## Edge cases

- **Missing peer metric data.** A peer with negative TTM revenue
  (rare, but possible on a divestiture) gets `revenue_growth_ttm` null
  and drops out of the distribution. The skill never imputes a value;
  the `n_peers_in_distribution` field communicates the drop.
- **Small peer set on a check (n < 4).** The percentile estimates are
  noisy. The renderer flags this in the section header
  ("Growth sanity (n=3, small sample)").
- **Analyst assumption is null** (the user passed `--assumed-growth`
  but not `--assumed-margin`, etc.). Status flips to `n_a` and the
  rendered section either omits the row or shows "(not provided)".
