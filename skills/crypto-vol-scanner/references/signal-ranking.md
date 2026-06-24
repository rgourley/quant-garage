# Composite signal ranking

The skill emits the top N most-notable events from the universe. With
four signal types and up to 10 names, the ranking has to be defensible.

## The composite score

For each ticker, compute four normalized scores:

```
vol_score    = max(0, vol_percentile_30d - 0.5) * 2
                 (0 at median, 1.0 at 100th percentile)

volume_score = max(0, ln(volume_vs_avg_ratio) / ln(5))
                 (0 at ratio=1, 1.0 at ratio=5)

move_score   = min(1.0, abs(move_zscore) / 4.0)
                 (0 at zero move, 1.0 at ±4σ)

basis_score  = min(1.0, basis_bps / 50.0) if basis_bps else 0
                 (0 at no basis, 1.0 at 50bps)
```

Composite is the **max** of these (not the sum):

```
composite_score = max(vol_score, volume_score, move_score, basis_score)
```

Max not sum because we don't want a slightly-elevated reading on all
four to outrank a single dominant signal. A 95th-percentile vol spike
with no other anomalies should sort above a name with 60th-percentile
vol, 1.5x volume, and 1.5σ move all firing.

## Signal type assignment

The dominant signal determines the tag:

```
signals_fired = [
  "vol_spike"      if vol_percentile_30d > 0.90,
  "volume_anomaly" if volume_vs_avg_ratio > 2.0,
  "tail_move"      if abs(move_zscore) > 2.0,
  "cross_exchange" if basis_bps > 20,
  "quiet"          if vol_percentile_30d < 0.25 and volume_vs_avg_ratio < 0.7
]
```

The `signal_type` field on the event is:
- The single tag from `signals_fired` if exactly one fired
- `combined` if 2+ fired
- The single dominant tag (vol_spike > volume_anomaly > tail_move >
  cross_exchange) if 1 fired but the composite is below the noise
  floor
- `quiet` if the quiet rule fired

## Top-N selection

The skill ranks ALL universe members by composite_score and emits the
top N (default 15). Names that didn't fire any threshold still appear
in the rank list with their context, but only the top N are emitted to
the stream.

A universe of 10 names with default N=15 will emit all 10. That's
intentional: in a quiet regime, the "quiet" events ARE the signal
("BTC and ETH both 22nd percentile; calm-before-storm watch"). The
trader skim-reads the stream top to bottom; quiet names sort to the
bottom naturally.

For a 30-name universe with default N=15, the bottom 15 (quiet, no
basis, no move) are clipped. The summary block reports the universe
size separately.

## Quiet as a signal

A trader who runs this scan daily will read low-impact days as
information: "the universe is in a quiet regime, expect a vol
expansion." Surfacing the quietest names alongside the loudest names
preserves that context.

## Why not a weighted sum

Tried and dropped. A weighted sum (e.g.
`0.3*vol + 0.3*volume + 0.3*move + 0.1*basis`) has two problems:

1. It outranks single-signal extremes (a 99th-pct vol spike) with
   multi-signal mediocrity (60th-pct vol + 1.5x volume + 1.5σ move).
   The single-signal extreme is more actionable.

2. The weights are arbitrary. With 4 signals and no labeled training
   data ("which events actually preceded profitable trades"), there's
   no principled way to set them. Max-of-normalized avoids the weight
   debate entirely.

If a future version of the skill collects outcome data (did the
flagged event precede a > 2% follow-through over the next 12 hours),
a logistic regression on the four signals would beat max-of-normalized.
That's a v3 candidate, not v1.

## Edge cases

- **Two signals tied within 5%:** the tie-breaker is the order
  `vol_spike > volume_anomaly > tail_move > cross_exchange`. Vol
  spikes have more analytical depth and are more replicable than the
  others.
- **All four below threshold:** the event is tagged `quiet` if it
  meets the quiet rule, otherwise the dominant single-signal tag is
  used but `composite_score < 0.3` and the rendered context line is
  empty.
- **basis_bps is null (single exchange):** the basis score is 0,
  doesn't disqualify the event from ranking, and is omitted from the
  rendered context line.
