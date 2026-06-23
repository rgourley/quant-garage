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

## Kind-specific formatting

Different `kind` values render the `Action:` line differently:

| Kind | Action line format |
|---|---|
| `split` | `{ratio} split, ex-date {ex_date}` |
| `reverse_split` | `{ratio} reverse split, ex-date {ex_date}` |
| `stock_dividend` | `{amount}% stock dividend, ex-date {ex_date}` |
| `spinoff` | `Spinoff to {spinoff_ticker}, ex-date {ex_date}` |
| `cash_dividend_basis` | `Cash dividend ${amount}/share, ex-date {ex_date}` |

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
