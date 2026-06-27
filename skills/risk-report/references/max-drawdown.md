# Max drawdown

The peak-to-trough decline in cumulative portfolio NAV. The number
PMs read first because it answers "what was the worst stretch?" in
one statistic.

## The math

Reconstruct portfolio NAV from cumulative log returns:

```
NAV_t = exp(sum_{i<=t} r_i)
```

Index `i=0` is initialized to 1.0; subsequent points accumulate the
daily portfolio return. The running peak at each point is the maximum
NAV seen so far:

```
peak_t = max(NAV_0, NAV_1, ..., NAV_t)
```

The drawdown at each point is the decline from running peak:

```
dd_t = (NAV_t - peak_t) / peak_t        # always <= 0
```

Max drawdown is the most negative `dd_t`:

```
max_dd = min(dd_t)
```

The trough is the index where this minimum occurs. The peak is the
index where the running peak that produced this trough was last
updated.

## Duration and recovery

**Duration** = `trough_index - peak_index`. In trading days when
the underlying series is daily NAV. Most PMs care about this
because it scales the psychological weight of the drawdown — a 15%
loss over 6 months feels different from a 15% loss in 3 weeks.

**Recovery** is the first index after the trough where NAV reaches
or exceeds the peak value. If the series never recovers, the
recovery index is `null` and `recovered` is `false`. The rendered
output says "not recovered" in that case.

A book stuck in a drawdown that hasn't recovered is a meaningfully
different state from one that drew down and bounced back. The PM
reading the report needs to know which they're in.

## Why log returns, not arithmetic

The lib uses log returns end-to-end (consistent with
`position-sizer`). Cumulating them produces the geometric NAV
trajectory, which is what an actual book experiences. Arithmetic
mean returns overstate compound growth and understate compound
drawdowns; log returns are correct.

The trade-off: a log return of -0.20 corresponds to an arithmetic
loss of -18.1%. The drawdown_pct emitted is the geometric loss, so
PMs reading "-18%" can take it at face value.

## Edge cases the math handles

- **Monotone-up series.** No decline ever occurred. `drawdown_pct =
  0`, peak and trough both at index 0, `duration = 0`,
  `recovered = false` (there's nothing to recover from). The
  rendered output reads "Max drawdown: 0% over [first date] to [first
  date] (0 days, not recovered)." Unusual; surfaces when someone
  runs the report on a freshly-uptrending book.
- **Drawdown still ongoing.** Trough at the last observation,
  `recovered = false`, `recovery_date = null`. The Take should flag
  this; the skill does.
- **Multiple equal-magnitude drawdowns.** `np.argmin` returns the
  first occurrence. So the earliest-occurring worst drawdown wins.
  This is the standard convention; PMs care about the deepest
  unique trough, and "deepest" is well-defined.
- **Non-finite NAV.** The lib raises rather than silently producing
  garbage. Indicates a bad return series upstream; surfaces the bug
  loudly.

## Why peak-to-trough and not rolling

There are other drawdown metrics: time-under-water, average
drawdown, Calmar ratio (annualized return / max drawdown). The skill
emits just the max because it's the single most-asked-about number
on a risk report. The others are reasonable v2 additions; the
output schema doesn't preclude them.

## The duration_days vs duration_periods naming

Internal lib name is `duration_periods` (generic). The renderer maps
to `duration_days` in the canonical JSON because the script always
feeds it daily aggs. If someone wires the lib helper into a weekly
or monthly cadence later, that mapping changes; the lib stays
generic.

## What this metric does NOT prove

Max drawdown is in-sample. It tells you the worst stretch the book
ALREADY went through over the lookback window. A book that has
never drawn down more than 5% in its history can still draw down
30% next month if conditions change. Don't treat max_dd as a worst-
case forecast. The tier_caveats note this; the take is careful not
to extrapolate.

Pair max_dd with VaR + ES for a more complete picture: max_dd tells
you the realized worst stretch; VaR + ES tell you the day-to-day
tail behavior. They speak to different horizons.
