## Adverse selection

Measure price movement in the 30 seconds AFTER each fill. If the
price moved against the trader (down for a BUY, up for a SELL), the
fill experienced adverse selection: the counterparty likely had
information the trader didn't, or the order leaked, or the venue's
liquidity was toxic.

The skill flags individual fills with `adverse_selection_bps > 5`.
The signal is probabilistic, so single instances are noise. The value
of the flag is in clusters: many adverse fills from one venue, one
counterparty, or one time of day points at a structural problem
worth fixing.

## Measurement window

Thirty seconds is the conventional buy-side window. Long enough that
the price has time to drift past microstructure noise; short enough
that it's still the same liquidity event the trader participated in.
Longer windows (60s, 5min) capture more drift but also more
unrelated news.

For each fill at timestamp T:

1. Pull `/v2/aggs/ticker/{ticker}/range/1/second/{T}/{T+30s}` to get
   the per-second bars for 30 seconds after the fill.
2. Take the last bar's close as `final_price`. If no bars are
   returned (illiquid name, no trades in 30s), fall back to the
   1-minute aggregate bar covering [T, T+60s]. If still empty,
   `adverse_selection_bps = null` and the flag isn't applied.
3. Compute the signed adverse drift (see below).

The skill pulls these aggregates for every fill, so the API budget
scales linearly with the fill count. For 50 fills, that's 50 extra
calls. On a Stocks Starter rate limit (5/min), the run is throttled;
on Developer (unlimited per-second) it's instant.

## Sign convention

Positive adverse_selection_bps = price moved against the trader.

- BUY: trader paid for the asset. Adverse = price fell after the
  fill. `adverse_bps = (fill_price - final_price) / fill_price * 10000`.
- SELL: trader received cash for the asset. Adverse = price rose
  after the fill (could have sold higher). `adverse_bps = (final_price
  - fill_price) / fill_price * 10000`.

Negative adverse_selection_bps means the trader was on the right side
of the next 30 seconds (price moved in their favor). Not a violation;
in fact a positive outcome, but the skill doesn't celebrate it. The
flag triggers only on positive values above the 5bp threshold.

## The signature pattern

Single fills with mild adverse drift (5-15bps in 30s) are common and
not actionable. The pattern that matters is clustering:

- **Venue clustering.** Every adverse fill came from venue X. Suggests
  toxic liquidity at that venue; review the smart-order-router
  config.
- **Counterparty clustering.** Every adverse fill traded against the
  same MPID (when available). Suggests an informed counterparty;
  add to the "avoid trading against" list.
- **Time-of-day clustering.** Every adverse fill happened in the
  first 15 minutes or last 15 minutes. Suggests the algo's
  aggressiveness profile is off at open or close.

The skill doesn't compute clusters; it emits per-fill bps so the
operator's downstream analysis (or a v2 skill) can group and detect.

## Thresholds

- `> 5 bps` (the flag threshold): noteworthy, worth tracking
- `> 15 bps`: meaningful; investigate the venue/counterparty
- `> 50 bps`: pathological; likely an informed flow or a news event;
  pull the news scanner output for the same window

The skill flags everything above 5bps. The suggested-next-action
prose escalates by magnitude.

## Tier interaction

Adverse selection uses post-fill aggregates, not pre-fill quotes.
Both Tier A and Tier B produce the same measurement; there is no
quote-data dependency.

The only failure mode is illiquid names with no prints in the 30
seconds after the fill. The skill emits `adverse_selection_bps: null`
in that case and doesn't apply the flag. The operator sees the null
and knows the measurement couldn't be made (rather than seeing a
false negative).

## Why measure after, not before

Some TCA frameworks compare the fill price to the price 30s BEFORE
the fill (looking for "price ran away from the trader"). The skill
measures after because:

1. Pre-fill drift is more commonly a signal of legitimate trading
   (the trader saw the move and reacted). Post-fill drift is more
   commonly a signal of informed counterparty or leakage.
2. Pre-fill drift is captured by VWAP slippage (a trader who entered
   well above VWAP after the price already ran is already flagged).
3. The 30s-after measurement is the canonical metric in the
   buy-side literature (see Almgren, Edhec quant guides). Match the
   convention so the output reads as expected.
