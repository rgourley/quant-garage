# Edge cases

The default split/dividend/spinoff math covers ~95% of US-equity
corporate actions. The remaining 5% is where reconcilers actually earn
their keep: every one of the cases below has caused a real ops break
at a real shop in the last five years.

## Fractional shares from splits

When `current_shares * (split_to / split_from)` doesn't divide evenly,
the result is fractional. Three things can happen:

1. **Broker pays cash-in-lieu (most common, all US retail brokers,
   most prime brokers).** Operator gets whole shares plus a cash
   payment. The reconciler floors `expected_shares` and flags
   `cash_in_lieu_expected = true`.
2. **Broker rounds to whole shares with no cash adjustment (rare,
   some international brokers).** Operator gets whole shares only.
   The reconciler still flags `cash_in_lieu_expected = true`; the
   operator suppresses the flag at run time if they know their broker
   doesn't pay CIL.
3. **Broker holds the fractional as a fractional position (Robinhood,
   Fidelity for select securities).** Operator keeps the fractional.
   The reconciler should NOT floor; it should keep the fractional. Set
   `allow_fractionals = true` in run config to disable the floor.

Cash-in-lieu valuation is broker-specific and uses the ex-date close
or VWAP. The reconciler does NOT attempt to compute the CIL dollar
amount: that's a separate reconciliation against the broker's confirm.

## Special distributions misclassified

Massive's `dividend_type` codes don't always match what the company
called the distribution. Examples:

- **Spinoff distributions** sometimes carry `dividend_type: "CD"` (regular
  cash) with `cash_amount: 0` and a separate share distribution.
  Inspect the record for any non-cash fields before treating it as a
  zero-dollar cash dividend.
- **Stock dividends** sometimes carry `dividend_type: "SD"` and
  sometimes appear as a split with a tiny ratio (e.g., `split_to: 105,
  split_from: 100`). Prefer the splits record when both endpoints have
  the same ex-date.
- **Return of capital** sometimes carries `dividend_type: "CD"` for
  tax-deferred accounts and the issuer only flags the 1099-DIV treatment
  at year-end. The reconciler can't catch this case without the operator
  supplying the company's 1099-DIV ahead of time.

When unsure, surface it as a soft break (`kind: cash_dividend_basis`)
with the raw record and let the operator make the call.

## Foreign-domiciled tickers and ADRs

A US-listed ADR represents a fixed number of underlying ordinary shares
(the ADR ratio). Splits on the ordinary share don't necessarily
translate 1-to-1 to the ADR. Two cases:

- **Ordinary splits while ADR ratio stays constant.** ADR holder's
  position changes 1-to-1 with the split ratio. Massive usually
  reports this on the ADR ticker. No extra logic needed.
- **ADR ratio change without an ordinary split.** ADR holder's share
  count changes but the underlying ordinary doesn't. Massive
  inconsistently reports these: sometimes as a split, sometimes as a
  ratio-change announcement. The reconciler should fall back to the
  depositary bank's notice when ratio changes are suspected (no API
  access, manual override required).

The `spinoffs.json` override file is the safety valve: include
`adr_ratio_change` entries with explicit `split_to` and `split_from`
when the API misses them.

## Same-day splits and dividends

When a ticker has both a split and a dividend on the same ex-date,
apply the split first, then the dividend. The dividend amount in the
record is per the post-split share count, so applying them in the
wrong order under-allocates the dividend cash by the split ratio.

The reconciler always sorts events for a single ticker by:

1. `execution_date` / `ex_dividend_date` ascending
2. Within the same date: splits before dividends

## Reverse-split delisting watch

A reverse split is often a delisting precursor. When the reconciler
detects a reverse split with `split_from >= 5` (1-for-5 or steeper),
it appends a soft warning to the break:

```
Note: 1-for-{n} reverse split is a common delisting precursor;
verify ticker is still trading.
```

The reconciler does NOT check for delisting itself (that needs a
status check against the tickers endpoint). The warning is to prompt
the operator to confirm.

## Spinoffs with no first-day price

If the reconciler can't get a first-session close for the subsidiary
(common for OTC-listed spinoffs in the first week), it falls back to:

1. The day-2 close (if available).
2. The IPO opening price (if the subsidiary is an IPO-style listing).
3. Skip the cost-basis allocation, surface a soft break:
   `kind: spinoff, basis_allocation_unavailable: true`.

In case 3, the operator must allocate basis manually from the
company's Form 8937 filing. The reconciler still creates the new
position with the correct share count.

## Tickers that change symbols mid-window

If a position has `as_of_date = 2022-01-01` for ticker `FB` and the
current symbol is `META`, the reconciler needs to know about the
symbol change to find the right corporate actions. Massive's tickers
endpoint records the change in the `name_change` field, but the
splits and dividends endpoints don't auto-redirect. The reconciler's
default approach: query the input ticker, then if the ticker metadata
endpoint shows a recent symbol change, also query the new symbol and
merge results.

For the demo data set in `examples/sample-positions.csv`, no symbol
changes apply. The full implementation handles the redirect; the
example script keeps it simple.

## Ratios that don't reduce to clean integers

A few real splits use non-integer ratios that brokers express as
fractions. Massive's response always uses integers, so a 3-for-2
forward split arrives as `split_to: 3, split_from: 2`. The math works
the same: 100 shares becomes 150. The render layer should display the
ratio as written in the response (`3-for-2`), not reduce it.

## Currency-redenominated cost basis

When a foreign-listed security's cost basis is recorded in one
currency and the corporate action settles in another, FX conversion
becomes part of the reconciliation. The reconciler does NOT handle
FX; it operates on whatever currency the input file records. If the
operator's cost basis is in USD and the company pays the dividend in
GBP, the cash side is the operator's problem to reconcile against
their settlement system. The reconciler will flag the dividend
informationally but won't attempt the conversion.
