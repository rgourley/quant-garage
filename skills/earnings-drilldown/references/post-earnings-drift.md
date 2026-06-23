# Post-earnings drift (PEAD)

## What we measure

Whether the stock continues moving in the direction of the surprise
over the trading week after the print. Classic PEAD: stocks that beat
keep drifting up T+1 to T+5, stocks that miss keep drifting down.
Useful for sizing whether to hold through the print and the week after.

The pattern is real but heterogeneous: it's stronger for small-cap
and analyst-light names, weaker for mega-caps with saturated coverage.
The skill reports the per-name pattern, not the universe-wide one.

## The numbers

**Per-print drift**: cumulative return from close of the print day
(or open of T+1 for BMO prints) through close of T+5, minus the
contemporaneous SPY return to control for market beta of 1.

```
for each historical print at date d, with sign(surprise):
    if AMC: r_t1_t5 = (close(d+5) - close(d)) / close(d)
    if BMO: r_t1_t5 = (close(d+5) - open(d)) / open(d)
    
    spy_t1_t5 = same window on SPY
    abnormal = r_t1_t5 - spy_t1_t5
    
    bucket = "beat" if surprise > 0 else "miss"
    drifts[bucket].append(abnormal)
```

**Conditional averages and t-stat**

```
for each bucket:
    n = len(drifts[bucket])
    mean = mean(drifts[bucket])
    stderr = std(drifts[bucket]) / sqrt(n)
    t_stat = mean / stderr
    significant = abs(t_stat) > 2.0
```

t_stat threshold of 2.0 is a rough 5% significance test. Small samples
(n < 8) inflate t-stats; cap interpretation at "directional" not
"significant" when n is small.

## Sample size rules

- **n < 4 in a bucket**: don't report a number, mark "sample too small"
- **4 ≤ n < 8**: report mean and direction, omit t-stat
- **n ≥ 8**: report mean and t-stat, mark significant if |t_stat| > 2.0

These are conservative. PEAD literature often uses much larger samples
(thousands of prints across thousands of names). Single-name PEAD is
noisier; treat the t-stat as a sanity check, not a tradable signal on
its own.

## Why beta-adjust

A name that beat in a strong-market quarter would show drift even if
the print didn't matter. Subtracting SPY return removes the market
component. For names with high market beta (high-beta tech, financials),
this matters more.

For names that don't trade with SPY (rate-sensitives, niche sectors),
consider sector beta instead. For v1 of this skill, SPY is the default;
sector beta is queued as a `references/peer-reaction.md` extension.

## Edge cases

- **Earnings date overlaps with FOMC or other macro event**: the T+1
  to T+5 window contaminated. Flag the print in the source list with
  a `notes` field, don't drop it (sample is already small).
- **Halt or trading suspension during the window**: pricing gap is not
  a return. Skip the print.
- **Corporate action during the window**: adjust closes for the
  split/spinoff/etc before computing returns.
- **Multiple prints close together (acquisition-related)**: when a
  company prints quarterly results adjacent to a special filing, the
  T+5 window may include the second event's reaction. Document but
  include.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`: for both
  the name and SPY
- `GET /vX/reference/financials`: for surprise sign per historical
  print

## What goes in the JSON

```json
{
  "post_earnings_drift": {
    "on_beats": {
      "n": 7,
      "avg_t5_return_pct": 0.013,
      "t_stat": 2.14,
      "significant": true
    },
    "on_misses": {
      "n": 1,
      "avg_t5_return_pct": -0.004,
      "t_stat": null,
      "significant": false
    }
  }
}
```

When `n < 4`, t_stat is null and significant is false. The rendering
layer reads this and writes "sample too small" instead of the number.

## Take generation

The take generator should fire on PEAD only if:
- A bucket has n ≥ 8 AND |t_stat| > 2.5 (high-significance)
- AND |avg_t5_return_pct| > 0.01 (meaningful magnitude)

Otherwise PEAD goes in the body of the note, not the headline.
