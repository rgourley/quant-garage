# Rendering: corp-actions-reconciler

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders for human consumption in
exception-report mode.

## Mode: exception-report

Show only flagged items. Suppress everything that reconciled cleanly,
unless the user explicitly asks for the full view.

## Header

Always lead with a summary line:

```
{breaks_found} BREAKS found across {positions_checked} positions checked.
```

If `breaks_found === 0`:

```
{positions_checked} positions checked. No breaks found.
```

## Per-break block

One block per item in `breaks[]`, separated by a blank line:

```
BREAK {index}: {ticker}
  Recorded:    {current_shares} shares as of {recorded_as_of}
  Action:      {action.ratio} {kind}, ex-date {action.ex_date}
  Expected:    {expected_shares} shares
  Delta:       {delta_shares} ({direction})
  Source:      {source.endpoint}
  Verified:    {source.fetched_at}
```

Where `direction` is:
- `"under-allocated"` if `delta_shares > 0`
- `"over-allocated"` if `delta_shares < 0`

When `expected_cost_basis` is non-null and differs from
`current_cost_basis`, append a cost-basis line under Expected:

```
  Basis:       ${expected_cost_basis}/sh (was ${current_cost_basis}/sh)
```

## Kind-specific formatting

Different `kind` values render the `Action:` line differently:

| Kind | Action line format |
|---|---|
| `split` | `{ratio} split, ex-date {ex_date}` |
| `reverse_split` | `{ratio} reverse split, ex-date {ex_date}` |
| `stock_dividend` | `{amount}% stock dividend, ex-date {ex_date}` |
| `spinoff` | `Spinoff of {spinoff_ticker} at {spin_ratio_y_per_x} per share, ex-date {ex_date}` |
| `spinoff_new_position` | `New position from {parent_ticker} spinoff, ex-date {ex_date}` |
| `cash_dividend_basis` | `Cash dividend ${amount}/share, ex-date {ex_date}` |
| `cash_in_lieu_expected` | `Fractional from {ratio} split, ex-date {ex_date}: broker CIL expected` |

For `spinoff` kind, render the parent-position basis adjustment with an
extra line so the operator sees the cost-basis change isn't a math
error:

```
BREAK {index}: {ticker} (parent)
  Recorded:    {current_shares} shares as of {recorded_as_of}
  Action:      Spinoff of {spinoff_ticker} at {spin_ratio_y_per_x} per share, ex-date {ex_date}
  Expected:    {expected_shares} shares (no change), basis ${expected_cost_basis} (was ${current_cost_basis})
  Source:      {source.endpoint}
  Verified:    {source.fetched_at}
```

For `spinoff_new_position` kind, render as a missing position rather
than a share-count delta:

```
BREAK {index}: {ticker} (new position from {parent_ticker} spin)
  Recorded:    not in input file
  Action:      Spinoff distribution, ex-date {ex_date}
  Expected:    {expected_shares} shares, basis ${expected_cost_basis}/share
  Source:      {source.endpoint}
  Verified:    {source.fetched_at}
```

## Footer

If the run completed without errors, no footer. If the rate limit
forced partial processing, append:

```
Note: {processed} of {total} positions processed before rate limit.
Re-run with paid tier for full reconciliation.
```

## Full example

Given this JSON payload:

```json
{
  "summary": {
    "positions_checked": 47,
    "breaks_found": 2,
    "passes_count": 45,
    "as_of": "2026-06-23T14:32:11Z"
  },
  "breaks": [
    {
      "ticker": "AAPL",
      "kind": "split",
      "recorded_as_of": "2024-08-01",
      "expected_shares": 200,
      "current_shares": 100,
      "delta_shares": 100,
      "action": {
        "ex_date": "2026-03-10",
        "ratio": "2-for-1",
        "split_to": 2,
        "split_from": 1
      },
      "source": {
        "endpoint": "https://api.massive.com/v3/reference/splits?ticker=AAPL",
        "fetched_at": "2026-06-23T14:32:08Z"
      }
    },
    {
      "ticker": "GOOGL",
      "kind": "split",
      "recorded_as_of": "2022-06-01",
      "expected_shares": 1000,
      "current_shares": 50,
      "delta_shares": 950,
      "action": {
        "ex_date": "2022-07-18",
        "ratio": "20-for-1",
        "split_to": 20,
        "split_from": 1
      },
      "source": {
        "endpoint": "https://api.massive.com/v3/reference/splits?ticker=GOOGL",
        "fetched_at": "2026-06-23T14:32:10Z"
      }
    }
  ],
  "passes": [],
  "sources": [...]
}
```

Renders as:

```
2 BREAKS found across 47 positions checked.

BREAK 1: AAPL
  Recorded:    100 shares as of 2024-08-01
  Action:      2-for-1 split, ex-date 2026-03-10
  Expected:    200 shares
  Delta:       +100 (under-allocated)
  Source:      api.massive.com/v3/reference/splits?ticker=AAPL
  Verified:    2026-06-23T14:32:08Z

BREAK 2: GOOGL
  Recorded:    50 shares as of 2022-06-01
  Action:      20-for-1 split, ex-date 2022-07-18
  Expected:    1000 shares
  Delta:       +950 (under-allocated)
  Source:      api.massive.com/v3/reference/splits?ticker=GOOGL
  Verified:    2026-06-23T14:32:10Z
```

## What UI devs do instead

A custom UI consumes the JSON payload directly, ignores this rendering
guide, and builds whatever interface fits. A reconciliation dashboard
might show breaks as cards with severity colors, sortable by delta
size, with click-through to the source endpoint. The skill provides
the data; the UI provides the visual layer.
