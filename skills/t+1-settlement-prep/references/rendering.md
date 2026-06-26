# Rendering: t+1-settlement-prep

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for human consumption in
exception-report mode.

## Mode: exception-report

Show only flagged trades. Suppress every trade that walks the
calendar cleanly with no SHORT, no ex-dividend in the window, no
split overlap, no half-day settlement.

## Header

Always lead with the take line (if present), then a one-line summary
plus run metadata:

```
{take}

T+1 settlement prep: {trades_checked} trades checked · {flagged_count} BREAKs flagged · run {as_of_utc} UTC
```

If `flagged_count === 0`:

```
T+1 settlement prep: {trades_checked} trades checked · No breaks flagged · run {as_of_utc} UTC
```

Then a two-line universe block:

```
Universe: {trades_checked} trades from {scan_params.file_in} {min_trade_date} → {max_settlement_date}
Settlement cycle: T+1 (US equities, post-May 2024)
```

Then a blank line and the `FLAGGED TRADES ({n})` section header.

## Per-break block

One block per item in `flagged[]`, separated by a blank line. The
core block:

```
BREAK {index}: {ticker} {side} {qty:,} · trade {trade_date} · settlement {computed_settlement_date}
  Reason:        {reason_human}
  Impact:        {impact_text}
  Suggest:       {suggested_next_action}
```

When the computed settlement date differs from the naive T+1, append
the original date in the settlement field so the operator sees the
push:

```
BREAK 1: AAPL BUY 1,000 · trade 2026-07-02 · settlement 2026-07-03 → 2026-07-06
```

The arrow form `{naive} → {computed}` only when they differ.

### Half-day settlement special form

When `half_day_settlement` is the (or a) reason, append the parenthetical
to the settlement field:

```
BREAK 4: COST BUY 100 · trade 2026-11-25 · settlement 2026-11-27 (half-day, 13:00 ET close)
  Reason:        Settlement date is a half-day session (Day after Thanksgiving)
  Impact:        DTCC cutoff is 12:30 ET instead of 15:00 ET
  Suggest:       Confirm trade is in DTCC queue before noon ET; tighter cash-management window
```

### Ex-dividend special form

When `ex_dividend_in_window` is the (or a) reason, append the ex-date
to the BREAK header and show the dollar impact in the Impact line:

```
BREAK 3: JPM BUY 500 · trade 2026-07-02 · settlement 2026-07-06 · ex-div 2026-07-06
  Reason:        Buyer entitled to dividend (purchased before ex-date)
  Impact:        $1.50/share dividend (~$750) allocated to buyer
  Suggest:       Verify dividend entitlement flag on trade ticket
```

### Corp-action special form

When `corp_action_overlap` is the (or a) reason, append the ex-date to
the BREAK header and the ratio in the impact line:

```
BREAK 5: NVDL BUY 300 · trade 2026-06-25 · settlement 2026-06-26 · split ex-date 2026-06-26
  Reason:        3-for-1 forward split with ex-date in settlement window
  Impact:        DTCC delivers 900 shares post-adjustment instead of 300
  Suggest:       Confirm position-reconciliation system reflects the split
```

## Reason mapping (reason_codes → reason_human)

| Reason code | Rendered reason text |
|---|---|
| `weekend_crossing` | Settlement falls on weekend; pushed to next business day |
| `holiday_adjacency` | Settlement crosses {holiday_name}; pushed to next business day |
| `short_sale_locate` | Short sale; locate confirmation required |
| `ex_dividend_in_window` | Buyer entitled to dividend (purchased before ex-date) |
| `corp_action_overlap` | {ratio} {forward/reverse} split with ex-date in settlement window |
| `half_day_settlement` | Settlement date is a half-day session ({reason}) |
| `symbol_change` | Ticker changed symbol between trade and settlement |

When a trade hits multiple reasons, the rendered reason text is a
semicolon-joined list in the same order as `reason_codes`. The
impact and suggest lines combine likewise.

## Summary block

After all BREAK blocks, render the summary:

```
Summary
- {flagged_count} flagged of {trades_checked} ({break_rate_pct:.0f}% break rate)
- Holiday adjacency: {by_reason.holiday_adjacency} trade(s)
- Weekend crossing: {by_reason.weekend_crossing} trade(s)
- Short sale locate: {by_reason.short_sale_locate} trade(s)
- Ex-dividend timing: {by_reason.ex_dividend_in_window} trade(s)
- Half-day session: {by_reason.half_day_settlement} trade(s)
- Corporate-action overlap: {by_reason.corp_action_overlap} trade(s)
- Settlement window: {settlement_window.from} through {settlement_window.to}
```

Skip any line whose count is zero. If `corp_action_overlap` count is
zero, render `- 0 corporate-action overlap detected` to reassure ops
that the skill checked.

## Take line

The top-level `take` field in the JSON is a one-line ops-ready
summary. Render it as the first line above the header.

Example takes:

- `4 of 12 trades flagged · 1 short-locate prompt · 1 ex-div allocation question · clean July 4 weekend visibility`
- `No breaks across 12 trades · all settlements fall on full business days`
- `12 of 14 trades flagged · long July 4 weekend pushed every Thursday-Friday settle into the next week`

## Full example

Given a JSON payload with four flagged trades:

```
4 of 12 trades flagged · 1 short-locate prompt · 1 ex-div allocation question · clean July 4 weekend visibility

T+1 settlement prep: 12 trades checked · 4 BREAKs flagged · run 2026-06-25 16:42 UTC

Universe: 12 trades from examples/sample-trades.csv 2026-06-23 → 2026-07-06
Settlement cycle: T+1 (US equities, post-May 2024)

FLAGGED TRADES (4)

BREAK 1: AAPL BUY 1,000 · trade 2026-07-02 · settlement 2026-07-03 → 2026-07-06
  Reason:        Settlement crosses Independence Day; pushed to next business day
  Impact:        Cash needed Monday 2026-07-06, not Friday 2026-07-03
  Suggest:       Update cash forecast; notify treasury for Monday funding

BREAK 2: NVDA SHORT 200 · trade 2026-06-25 · settlement 2026-06-26
  Reason:        Short sale; locate confirmation required
  Impact:        Trade may fail without locate on file before T+1 cutoff
  Suggest:       Confirm locate ticket with prime broker before EOD

BREAK 3: JPM BUY 500 · trade 2026-07-02 · settlement 2026-07-06 · ex-div 2026-07-06
  Reason:        Buyer entitled to dividend (purchased before ex-date)
  Impact:        $1.50/share dividend (~$750) allocated to buyer
  Suggest:       Verify dividend entitlement flag on trade ticket

BREAK 4: NVDL BUY 300 · trade 2026-06-25 · settlement 2026-06-26 · split ex-date 2026-06-26
  Reason:        3-for-1 forward split with ex-date in settlement window
  Impact:        DTCC delivers 900 shares post-adjustment instead of 300
  Suggest:       Confirm position-reconciliation system reflects the split

Summary
- 4 flagged of 12 (33% break rate)
- Holiday adjacency: 1 trade
- Short sale locate: 1 trade
- Ex-dividend timing: 1 trade
- Corporate-action overlap: 1 trade
- 0 weekend-only crossings
- 0 half-day session settlements
- Settlement window: 2026-06-24 through 2026-07-06
```

## What UI devs do instead

A custom UI consumes the JSON payload directly, ignores this
rendering guide, and builds whatever interface fits. A settlement-prep
dashboard might show flagged trades as cards grouped by reason code,
color-coded by suggested action priority (locate confirm > cash
forecast update > info), with click-through to the source endpoint.
The skill provides the data; the UI provides the visual layer. Same
compute, two surfaces.
