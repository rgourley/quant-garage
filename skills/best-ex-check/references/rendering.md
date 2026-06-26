# Rendering: best-ex-check

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for human consumption in
exception-report mode.

## Mode: exception-report

Show only flagged items. Suppress fills that hit zero flag categories
unless the user explicitly asks for the full view.

## Header

Always lead with a one-line summary plus the run metadata:

```
TCA: {fills_checked} fills checked · {flagged_count} BREAKs flagged · run {as_of_utc} UTC
```

If `flagged_count === 0`:

```
TCA: {fills_checked} fills checked · No breaks flagged · run {as_of_utc} UTC
```

Then a two-line universe block:

```
Universe: {fills_checked} fills from {scan_params.file_in} {scan_params.date_range.from} to {scan_params.date_range.to}
Reference quote source: {quote_source_label}
```

Where `quote_source_label` is:

- Tier A: `v3 NBBO ticks (microsecond precision)`
- Tier B: `1-second aggs (NBBO proxy)`

If Tier B, append a one-line entitlement note so the operator sees
the downgrade:

```
Note: /v3/quotes returned 403 on this key; falling back to 1-second
aggregate band as NBBO proxy. Off-NBBO calls are a lower bound on this
tier.
```

Then a blank line and the `FLAGGED FILLS ({n})` section header.

## Per-break block

One block per item in `flagged[]`, separated by a blank line. The
core block:

```
BREAK {index}: {ticker} {side} {qty:,} @ ${price:.2f} · {timestamp_et} ET
  Slippage:    {sign}{abs_slippage_bps:.1f} bps vs reference {reference_label} ${reference_price:.2f} at {reference_timestamp_et}
  Spread:      ${reference_bid:.2f} × ${reference_ask:.2f} ({spread_bps_at_fill:.0f} bps inside, {spread_label})
  VWAP slip:   {sign}{abs_vwap_bps:.1f} bps vs session VWAP ${session_vwap:.2f}
  Reasons:     {reasons_csv}
  Adverse:     {sign}{abs_adverse_bps:.1f} bps within 30s of fill ({adverse_label})
  Suggest:     {suggested_next_action}
```

Where:

- `sign` is `+` (always shown for positive bps; positive = bad)
- `reference_label` is `ask` on BUY, `bid` on SELL
- `spread_label` is one of: `normal` (<10bps), `medium` (10-50bps),
  `wide` (>50bps)
- `reasons_csv` is the `reasons` array joined with `, ` (no quotes)
- `adverse_label` is one of: `no clear adverse selection`, `mild adverse`
  (5-15bps), `meaningful adverse` (15-50bps), `severe adverse` (>50bps),
  `no post-fill prints` (when null)

Skip lines whose underlying value is null:

- If `reference_bid` and `reference_ask` are null, drop the `Spread:`
  line entirely
- If `session_vwap` is null, drop the `VWAP slip:` line
- If `adverse_selection_bps` is null, drop the `Adverse:` line
- `Slippage:`, `Reasons:`, and `Suggest:` lines are required on every
  block; if `slippage_bps` is null, render `Slippage: not computable
  (no reference quote)`

When a fill has `wide_spread_at_fill` as the only reason, append
`(in {spread_bps:.0f}bps spread window)` after the timestamp on the
BREAK header line so the operator sees the context immediately:

```
BREAK 3: NVDA BUY 200 @ $201.18 · 2026-06-23 14:08:42 ET (in 80bps spread window)
  Spread:      $201.10 × $201.26 (80 bps inside, wide)
  Reasons:     wide_spread_at_fill
  Suggest:     No clear best-ex violation; trader took available liquidity in thin tape
```

## Summary block

After all BREAK blocks, render the summary:

```
Summary
- {flagged_count} flagged of {fills_checked} ({break_rate_pct:.0f}% break rate)
- Crossed-spread: {by_reason.crossed_spread} fills · avg cost +{avg_crossed_spread_bps:.0f}bps
- VWAP slippage: {by_reason.high_vwap_slippage} fills
- Off-NBBO: {by_reason.off_nbbo_buy + by_reason.off_nbbo_sell} fills
- Adverse selection: {by_reason.adverse_selection} fills
- Wide-spread context: {by_reason.wide_spread_at_fill} fills (genuinely thin moments, not violations)
- Estimated implementation shortfall: ${total_implementation_shortfall_usd:,.0f} across all flagged fills
```

Skip any line whose count is zero. The "Wide-spread context" line is
phrased as context, not violation, because that reason alone is
informational.

## Footer (Tier B only)

On Tier B, append a one-line methodology footer so the analyst knows
where the precision floor is:

```
Methodology note: Tier B uses 1-second aggregate bars as the NBBO proxy.
Off-NBBO counts are a lower bound. For a Reg NMS compliance review,
upgrade to Stocks Developer (entitles /v3/quotes) or use flat-files
quotes_v1 for the day.
```

## Take line

The top-level `take` field in the JSON is a one-line desk-ready
summary. Always render it as the first line above the header when
present:

```
{take}

TCA: 18 fills checked · 6 BREAKs flagged · run 2026-06-25 15:32 UTC
...
```

Example takes:

- `6 of 18 fills flagged · $1,247 implementation shortfall · crossed-spread is the dominant cost driver`
- `No breaks across 18 fills · clean session`
- `2 off-NBBO prints in 18 fills · verify timestamps and check for block carveouts`

## Full example

Given a JSON payload with two flagged fills (one crossed-spread, one
wide-spread-only):

```
6 of 18 fills flagged · $1,247 implementation shortfall · crossed-spread is the dominant cost driver

TCA: 18 fills checked · 6 BREAKs flagged · run 2026-06-25 15:32 UTC

Universe: 18 fills from examples/sample-fills.csv 2026-06-23 13:30:00 to 2026-06-23 20:00:00
Reference quote source: v3 NBBO ticks (microsecond precision)

FLAGGED FILLS (6)

BREAK 1: AAPL BUY 1,000 @ $300.85 · 2026-06-23 10:14:18 ET
  Slippage:    +18.0 bps vs reference ask $300.31 at 10:14:18
  Spread:      $300.25 × $300.31 (2 bps inside, normal)
  VWAP slip:   +18.4 bps vs session VWAP $296.88
  Reasons:     crossed_spread, high_vwap_slippage
  Adverse:     +4.2 bps within 30s of fill (no clear adverse selection)
  Suggest:     Investigate venue routing; price improvement opportunity missed

BREAK 2: NVDA BUY 200 @ $201.18 · 2026-06-23 14:08:42 ET (in 80bps spread window)
  Spread:      $201.10 × $201.26 (80 bps inside, wide)
  Reasons:     wide_spread_at_fill
  Suggest:     No clear best-ex violation; trader took available liquidity in thin tape

Summary
- 6 flagged of 18 (33% break rate)
- Crossed-spread: 4 fills · avg cost +21bps
- VWAP slippage: 3 fills
- Off-NBBO: 1 fill
- Wide-spread context: 1 fills (genuinely thin moments, not violations)
- Estimated implementation shortfall: $1,247 across all flagged fills
```

## What UI devs do instead

A custom UI consumes the JSON payload directly, ignores this rendering
guide, and builds whatever interface fits. A TCA dashboard might show
flagged fills as cards with reason-code badges, sort by
implementation shortfall, and let the analyst click through to the
reference-quote source endpoint. The skill provides the data; the UI
provides the visual layer. Same compute, two surfaces.
