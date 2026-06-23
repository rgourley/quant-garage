# Implied vs realized move

## What we measure

The difference between what options pricing says the print will move
the stock and what the stock has actually moved on past prints. A
straddle priced for ±4% when the stock has averaged ±6% on the last
eight prints suggests the straddle is cheap, all else equal.

## The numbers

**Implied move**: the front-week at-the-money straddle, multiplied by
0.85, expressed as percentage of spot. From the options chain.

```
straddle_pct = (call_mid + put_mid) / spot
implied_move_pct = straddle_pct * 0.85
```

The 0.85 multiplier is industry standard (used by CBOE, ORATS, Optionslam,
SpotGamma). A raw ATM straddle overstates the true expected move because
of convexity: the straddle pays max(|move|, 0), not |move|, and the
distribution has fat tails. The 0.85 factor adjusts for this empirically.
If a user explicitly asks for the raw straddle, surface `straddle_pct`
alongside `implied_move_pct` in the JSON.

Use the front-week expiry that captures the print (typically the
weekly that expires the Friday after AMC prints, or Friday of the
same week for BMO prints). If the expiry is more than 5 trading days
out, the straddle prices more than just earnings; flag this in the
output and use the closest available expiry.

**Realized move**: the average absolute close-to-close return from
the print day to the next trading day, over the last N prints.

```
for each historical print at date d:
    p_print = close(d) for AMC prints, close(d-1) for BMO prints
    p_next  = close(d+1) for AMC prints, close(d) for BMO prints
    realized_pct[d] = abs((p_next - p_print) / p_print)

realized_avg_pct  = mean(realized_pct)
realized_median_pct = median(realized_pct)
```

Use N=8 quarters by default. If fewer prints exist (recent IPO),
report what's available and downweight the take.

**Regime-shift detection (optional 16q baseline).** For names whose
options vol is pricing in a return to a prior regime, the 8q realized
can mislead. NVDA is the canonical case: the 8q window covers calm
2025-2026 prints (±3.7% avg), but options were pricing ±10% into the
Aug 2026 print because traders remembered the Blackwell-era ±15-20%
moves from late 2024 (16q lookback would catch those).

When the 16q realized differs materially from the 8q realized
(|16q - 8q| > 1.0pp on the average absolute move), emit both in the
JSON and add a `regime_shift_flag: true`. The rendered note adds a
single line under "Implied vs realized":

```
- Regime check: 16q avg ±7.4% vs 8q ±3.7%. Implied may be pricing
  return to the wider regime, not the recent calm.
```

This is the difference between "straddle is rich" and "straddle is
priced for the regime you remember, not the regime that's been
happening." Important read for any name where the vol surface and
recent realized are out of step.

**IV30**: the 30-day implied volatility from Massive's options
snapshot. Used for percentile context.

**IV30 percentile (TTM)**: where the current IV30 sits in the
distribution of daily IV30 values over the trailing twelve months.

```
iv30_percentile_ttm = percentile_rank(current_iv30, iv30_history_252d)
```

## Mispricing

```
mispricing_pct = (implied_move_pct - realized_avg_pct) / realized_avg_pct
mispricing_pp  = implied_move_pct - realized_avg_pct  # absolute pp gap
```

Negative `mispricing_pct` means implied is below realized (straddle
looks cheap). Positive means implied above realized (straddle looks
rich).

Always emit BOTH `mispricing_pct` and `mispricing_pp` in the JSON.
When realized is small (AAPL's recent ±1.6%), even a modest absolute
implied premium produces extreme `mispricing_pct` numbers (4.2pp over
1.6% = 263% rich). The pp number stays interpretable when the ratio
explodes.

The rendering layer uses `mispricing_pct` only when `|mispricing_pct| <= 1.0`
(i.e. within ±100%). Beyond that, it switches to the pp form so the
take doesn't look like the script is broken. See
`references/rendering.md`.

This is a simple comparison, not a true edge. The realized average
assumes the next print resembles the historical distribution, which
isn't guaranteed (regime changes, M&A, secular shifts). The skill
surfaces the comparison; the user decides whether to trade it.

## Closest historical analog

Find the prior print where IV30 pre-print was closest to current
IV30. Report the realized move at that analog. This anchors the take
in a specific historical case rather than just the average.

```
analogs = [(period, iv30_at_print, realized_pct) for each past print]
closest = min(analogs, key=lambda a: abs(a.iv30 - current_iv30))
```

If no analog within 20% of current IV30 exists, omit this field
(forcing an analog from a different vol regime is misleading).

## Edge cases

- **N < 4 prints**: not enough sample. Report what we have but mark
  the take as "low confidence."
- **Front straddle > 5 days out**: pricing more than the print. Use
  the closest available expiry, flag the gap in days.
- **Recent corporate action**: a split or spinoff in the window
  contaminates the realized series. Apply the corp-action adjustment
  to historical closes before computing realized moves.
- **Pre-print gap up/down**: if the stock moved >2% the prior session
  on no specific news, the implied is reacting to that drift. Worth
  noting in the take, not in the schema.

## Endpoints used

- `GET /v3/snapshot/options/{ticker}`: current chain for the front
  straddle and current IV30
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`: historical
  daily closes for realized move calculation
- `GET /vX/reference/financials`: historical earnings dates
- Full mode only: `GET /v2/aggs/ticker/O:{occ}/range/1/day/...` for
  per-print IV at past prints (used for the analog calculation)

## What goes in the JSON

```json
{
  "implied_vs_realized": {
    "straddle_pct": 0.069,
    "implied_move_pct": 0.058,
    "realized_avg_pct": 0.016,
    "realized_median_pct": 0.013,
    "n_quarters": 8,
    "iv30": 26.0,
    "iv30_percentile_ttm": 0.61,
    "mispricing_pct": 2.625,
    "mispricing_pp": 0.042,
    "closest_analog": {
      "period": "Q3 2024",
      "iv30_at_analog": 25.8,
      "realized_at_analog_pct": 0.014
    }
  }
}
```

Numbers above mirror what a real AAPL run produces today (June 2026
window). When realized has compressed as much as AAPL has, the pp form
is what the take should use.
