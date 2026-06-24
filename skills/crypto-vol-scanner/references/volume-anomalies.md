# Volume anomaly detection

The skill flags a volume anomaly when the most-recent 24h USD volume
exceeds 2x the trailing 30-day daily average.

## Definition

```
volume_24h_usd      = prevDay.v * prevDay.vw   (from snapshot)
volume_30d_avg_usd  = mean( daily.v * daily.vw, last 30 trading days )
ratio               = volume_24h_usd / volume_30d_avg_usd
flag                = ratio > 2.0
```

The snapshot's `prevDay` block is the last completed UTC day. The
snapshot's `day` block is the current incomplete UTC day and is NOT
usable for the 24h comparison (it would underweight any name scanned
early in the UTC session). The skill always reads `prevDay`.

USD volume is base-units × VWAP. Massive returns `prevDay.v` in base
currency (e.g. BTC count) and `prevDay.vw` as the day's VWAP in USD.
Multiplying gives a defensible dollar-volume number that doesn't
depend on the spot path during the day.

## Why a ratio, not a z-score, here

Crypto daily volumes are heavily right-skewed: the median day for ETH
is ~$8B, but the top-decile day is $40B and the all-time spike is
$150B+. Taking a z-score against the empirical distribution gives huge
values for any name in a vol regime and hides the fact that 2x normal
is already meaningful.

The skill uses a ratio for the threshold check ("flag if > 2x") and
also computes a log z-score (`ln(ratio) / σ(ln(ratio))_30d`) for the
composite ranking. The log transform tames the skew so the z-score is
comparable across names.

## Seasonality caveat

Crypto volume has a weekly pattern. Sunday is the lowest-volume day of
the week for BTC and ETH (typically 60-70% of the weekly average).
Tuesday and Wednesday are the highest. The 30-day window smooths this
somewhat (it covers ~4 of each weekday), but a Sunday print at 1.4x
the 30d average might really be 2x a "normal Sunday."

The skill doesn't currently do day-of-week conditioning. A v2 candidate
is to use a 30d trailing average of the same weekday only (e.g. Sunday
vs prior 4 Sundays). For v1, the seasonality bias is acknowledged in
the rendered context line when the current weekday is Sunday or
Saturday.

## What 2x means in practice

| Ratio | Interpretation |
|-------|----------------|
| < 0.5 | Dead session. Common around holidays and weekends |
| 0.5-1.5 | Normal range |
| 1.5-2.0 | Elevated, not yet flagged. Often a precursor |
| 2.0-3.0 | Flagged. Real flow change. Could be news-driven or technical breakout |
| 3.0-5.0 | High-impact event. Almost always a clear catalyst |
| > 5.0 | Tail event. Rugpull, listing, regulatory action, or coordinated social campaign |

The skill flags everything above 2.0 and lets the composite ranking
sort the magnitudes. The rendered output preserves the raw ratio so
the trader can scale their response (2.1x is interesting, 4.7x demands
attention).

## Cross-reference

A volume anomaly without a vol spike often means smart-money
accumulation (high volume, contained price range). A volume anomaly
with a vol spike is a directional event. The skill surfaces both and
the composite `signals_fired` array tells the trader which is which.
