# Corporate-action overlap

## What this flag means

A stock split or reverse split has an `execution_date` (ex-date) that
falls inside the settlement window
`[trade_date, computed_settlement_date]`. Operations needs visibility
because the delivered share count differs from the recorded trade
quantity.

DTCC handles the math automatically. The trader doesn't need to do
anything. But settlement and position-reporting teams need to know so
the position-keeping system isn't surprised.

This is informational, not a hard break.

## The mechanics

Example: trader buys 100 shares of XYZ on Wednesday. XYZ has a 4-for-1
split with ex-date Friday. Settlement is Thursday (T+1).

- The trade settles Thursday for 100 shares at the pre-split price.
- Friday morning, DTCC adjusts the holding to 400 shares at the
  post-split price.
- The cash settlement (Thursday) is at the pre-split price; the share
  count delivered (Thursday) is the pre-split 100; the position the
  trader sees Friday morning is the post-split 400.

If the position-keeping system reads the trade ticket literally
(100 shares delivered) and the DTCC feed (400 shares held), the
mismatch is the split, not a break. The skill flags this so the ops
team confirms the position reconciliation is split-aware.

## Reverse splits

Same mechanics, opposite direction. A 1-for-8 reverse split with
ex-date inside the window means the 100-share trade delivers 12
shares post-adjustment, with the broker paying cash-in-lieu on the
0.5 fractional share at the pre-split price.

For settlement prep the operator needs to:

1. Confirm the position system handles the share-count adjustment.
2. Watch for the CIL credit on the fractional residual.

## Spinoffs

The Massive splits endpoint does not return spinoffs. There is no
dedicated `/v3/reference/spinoffs` endpoint as of June 2026 (see
corp-actions-reconciler's spinoffs-methodology.md). For
settlement-prep purposes a spinoff in the window would mean the
buyer of the parent receives both the parent shares and the
subsidiary shares at the ex-date.

v1 of this skill does not flag spinoffs. An operator who knows a
spinoff is upcoming can either:

- Load a `spinoffs.json` override file (same format as
  corp-actions-reconciler uses)
- Add the spinoff event into the input CSV as a comment line

v2 would integrate the override file properly.

## Data source

`/v3/reference/splits?ticker={t}&execution_date.gte={trade_date}` returns
splits with execution_date on or after the trade date. The skill
filters in-Python to keep only those with execution_date in
`[trade_date, computed_settlement_date]`.

Response shape:

```json
{
  "results": [
    {
      "ticker": "NVDL",
      "execution_date": "2026-06-26",
      "split_from": 1,
      "split_to": 3,
      "id": "..."
    }
  ],
  "status": "OK"
}
```

`split_to / split_from > 1` is a forward split; `split_to / split_from
< 1` is a reverse split. The skill encodes the ratio as a string:

- Forward: `f"{int(split_to)}-for-{int(split_from)}"` (e.g. `3-for-1`)
- Reverse: `f"{int(split_from)}-for-{int(split_to)}"` (e.g. `8-for-1`,
  but rendered as `1-for-8` in the kind field per convention)

## Post-action quantity

The `corp_action.post_action_qty` field is `qty * (split_to / split_from)`,
floored to whole shares for forward splits. The skill does NOT floor
to whole shares for the trade settlement itself; DTCC delivers
fractional shares for splits with non-round ratios (e.g. 21-for-20
stock dividend). The post_action_qty is informational.

## Why this isn't a hard break

The trade settles. The cash moves. The share count delivered is
correct under DTCC's adjustment math. There's nothing the trader
needs to fix. The flag exists so the ops team:

- Doesn't get a phone call from a portfolio manager who sees an
  unexpected share count
- Confirms the position-reconciliation system handled the
  adjustment correctly
- Notes the event on the trade ticket for the audit trail

## Edge case: same-day trade and ex-date

If the trade date IS the ex-date (e.g. trader buys post-split shares
on the ex-date), the split has already happened. The Massive
splits endpoint returns the split with `execution_date` equal to
trade_date; the skill includes it in the window check. The
post_action_qty equals the recorded qty (already post-split).

This is technically a no-op flag, but surfaces it for completeness.
An operator can filter at the next layer if it's noise.
