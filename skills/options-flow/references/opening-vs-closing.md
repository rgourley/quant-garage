# Opening vs closing

Whether today's volume on a contract is opening new interest or closing
existing positions changes the read. New interest is the story; closing
volume is housekeeping.

## The metric

Compare today's volume to open interest (OI) at the start of the
session. OI is the count of contracts outstanding overnight; today's
volume is whatever has traded today.

```
ratio = today_volume / oi_at_session_start
```

OI updates once per day, typically published by OCC the morning after.
The `open_interest` field on Massive's `/v3/snapshot/options/{ticker}`
response is the most recent OI value, which means it's typically OI as
of the previous session's close (i.e., the start of today's session).

## Thresholds

| ratio | Signal | Read |
|---|---|---|
| > 10.0 | clearly opening | Almost all volume is new interest |
| > 1.0 | opening | Volume exceeds existing OI, mostly new positions |
| 0.5 to 1.0 | mixed | Some new, some closing, can't separate |
| < 0.5 | closing | Likely unwinding existing positions |

A ratio above 1.0 is the cleanest opening signal: by definition, the
volume could not have happened entirely against existing OI (you can't
close more than exists). Some of those trades opened new interest.

A ratio above 10.0 is rare and informative: it suggests a coordinated
push into a strike that wasn't being held by many traders. The clearest
"someone knows something" tell.

## Why opening matters more than closing

A closing trade is housekeeping. The trader took a position previously,
the original reasoning may have decayed, they're flat. The exit doesn't
tell you what they think now.

An opening trade is a fresh thesis. The trader is committing capital
based on a view that's actionable right now. That view, whether
correct or not, is informative.

For flow scanning, default to surfacing opening trades and downweight
closing trades. The skill's score function weights `vol/OI` at 0.30 of
the total ranking, which means a closing-bias contract scores lower
than an opening-bias contract at the same volume / 30-day avg ratio.

## The "near OI but multiple trades" case

A contract with 1,000 OI that does 1,200 volume today is technically
"opening" by the threshold (ratio = 1.2), but the picture is muddier
than a 5,000 volume day on 200 OI (ratio = 25). The first case might
be 600 closes + 600 opens (no net change in OI tomorrow). The second
case has to be mostly opens (insufficient OI to be closes).

The skill emits the raw ratio so the operator can read the texture
themselves. The `oi_position_signal` is the headline summary; the JSON
carries the raw `volume_to_oi_ratio` for finer interpretation.

## Estimating end-of-day OI

The skill emits `oi_pre_trade` (session-start OI) and an optional
`oi_post_trade_estimate`. The estimate is a coarse proxy:

```python
def estimate_post_oi(volume, oi_pre, vol_to_oi_ratio):
    if vol_to_oi_ratio < 0.5:
        # mostly closing
        return max(0, oi_pre - int(volume * 0.7))
    if vol_to_oi_ratio > 5.0:
        # mostly opening
        return oi_pre + int(volume * 0.7)
    # mixed: ~50/50 splits, no net change estimate
    return oi_pre
```

This is intentionally rough. True post-OI is published by OCC the next
morning. The estimate is to give the operator a directional read on
whether the position should be larger or smaller tomorrow, not a
precise number.

## What the timeframe affects

The methodology is the same intraday and EOD. Tier A (real-time) gets
the OI signal sooner; Tier B (15-min delayed) sees the same OI value
but the volume number is whatever has accumulated as of the 15-min-old
snapshot. The opening/closing read doesn't degrade with delay; only the
actionability does.

## Sample edge cases

- **New strike just listed.** OI starts at 0; any volume is opening by
  definition. The skill surfaces these with `oi_position_signal:
  "opening"` and a `related_prints` note flagging the new listing.
- **Day of expiry.** OI dissolves as positions exercise or expire. The
  ratio loses meaning in the last hour because the denominator is
  shrinking faster than the standard intraday rate. The skill downweights
  expiry-day prints in the score (multiplier of 0.5 if expiry < 1 day
  out and time < 1 hour to close).
- **Earnings-week strikes.** OI builds up in the 2-3 sessions before
  earnings as traders position. A 5x vol/OI on the morning of earnings
  is opening fresh exposure for the print itself. This is one of the
  most actionable flow signals; the skill doesn't downweight it.
