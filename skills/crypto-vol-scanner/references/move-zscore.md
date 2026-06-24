# 24h move z-score

The skill computes the trailing 24h return and normalizes it against
the trailing 30-day daily-return distribution. The result is a
z-score that says "this move is N standard deviations from the
recent baseline."

## Definition

```
ret_24h    = (spot / prevDay.c) - 1
log_ret_24h = ln(spot / prevDay.c)

daily_log_returns_30d = [ ln(c_t / c_{t-1}) for t in last 30 days ]
σ_30d                  = stdev(daily_log_returns_30d)

zscore = log_ret_24h / σ_30d
```

Sign is preserved: a -5% move with 2% trailing std is z = -2.5.

## Why log returns

Log returns are additive and symmetric around zero. A +10% move
followed by -10% on simple returns leaves you at -1%, but log returns
sum to zero. Symmetric handling matters for the z-score: a +5% z = 2.0
and a -5% z = -2.0 should be the same magnitude.

For small returns (under ~5%) log and simple returns are within 0.1
percentage points. The skill emits `move_24h_pct` as the simple
return because that's what traders read on a quote, but uses log
returns internally for the z-score calc.

## Thresholds

| |z| | Interpretation |
|-----|----------------|
| < 1.0 | Within normal daily range. Not flagged. |
| 1.0-2.0 | Above average. Worth noting if combined with other signals. |
| 2.0-3.0 | Notable. Flagged as `tail_move`. About 2-3 such moves per 30d for any given name. |
| 3.0-4.0 | Tail event. Real news or technical break. Maybe 1 per 30d. |
| > 4.0 | Extreme. Sub-1% probability under normal regime. Almost always a catalyst. |

The skill flags at `|z| >= 2.0` by default.

## Sample-size sanity

30 daily returns is enough for a stable σ estimate but the tail of
the distribution is poorly characterized. The actual frequency of
|z| >= 3 events in crypto is higher than a Gaussian would predict
(fatter tails); reading "z=3.5" as "0.05% probability" is wrong.

Treat the z-score as a ranking tool, not a probability statement.
The narrative is "this is in the top 5% of moves we've seen in the
last 30 days for this name," not "this is statistically improbable
under a normal model."

## Why not vs trailing year

30 days catches the current regime. A trailing-year σ would mix bull
and bear, vol and quiet, which inflates σ and depresses every recent
z-score. The crypto desk needs to know "is this big relative to the
regime I'm trading in right now," and 30 days is the right window for
that.

For a regime classifier (separate skill), a trailing 90 or 180 day
window is appropriate. Not what this skill is doing.

## Cross-reference

A high |z| on the 24h move plus a high realized vol percentile is a
"trending hard, vol regime change" tape. The skill's composite signal
type `combined` catches this case explicitly.

A high |z| on the move with normal realized vol is a "single-shock"
event: a big move concentrated in one hour, then quiet. Usually a
catalyst like a Fed remark, a listing announcement, or an ETF flow
print.
