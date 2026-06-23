# Dividends methodology

How to apply a dividend record to a recorded position. Most cash
dividends don't change anything in the position file: shares stay the
same, cost basis stays the same. The reconciler still surfaces them so
the operator can confirm settled cash matches, but they're informational
unless the operator opted into cost-basis tracking for non-qualified or
special distributions.

## Cash dividends: regular

`dividend_type: "CD"` from `GET /v3/reference/dividends`. Default
behavior: no break unless cost-basis-adjusted reconciliation is on.

A regular cash dividend mechanically:

```
shares (after)     = shares (before)
cost_basis (after) = cost_basis (before)
cash               = cash + shares * dividend_amount
```

The cash side is the operator's settlement reconciliation, not the
position file's. The reconciler logs the dividend as a SOURCE entry
(for the audit trail) but does NOT generate a BREAK.

## Cash dividends: special

`dividend_type: "SC"` (special cash) is the operator's tripwire. Brokers
sometimes adjust cost basis on special distributions because they're
treated as return-of-capital for tax purposes. The IRS rule:

- If the distribution is from current/accumulated earnings →
  taxable income, no cost basis change.
- If the distribution is from invested capital → return of capital,
  cost basis goes down by the distribution amount.

The reconciler can't tell which without the company's 1099-DIV
classification. Default behavior: when `dividend_type == "SC"`, emit a
soft break (`kind: cash_dividend_basis`) with the dividend amount and a
note that the operator should confirm tax treatment before adjusting
basis. This is informational; the operator decides.

If the run config sets `treat_special_as_roc = true`, the reconciler
applies:

```
expected_cost_basis = current_cost_basis - dividend_amount
```

per share, and emits a hard break with the adjusted basis. Use this
when the operator has confirmed the company's 1099-DIV classification
ahead of time.

## Stock dividends

Treat as a small forward split. See
[`splits-methodology.md`](./splits-methodology.md#stock-dividends).

A 5% stock dividend on 100 shares with $10 basis:

```
expected_shares     = 100 * 1.05 = 105
expected_cost_basis = 1000 / 1.05 = 9.524 per share
```

Massive sometimes lists the same event in both the splits and
dividends endpoints. The reconciler de-dupes by ex-date and prefers the
splits record (it carries the cleaner ratio).

## Return-of-capital distributions

`dividend_type: "RC"` from Massive. These always adjust cost basis
downward by the distributed amount:

```
expected_cost_basis = current_cost_basis - (amount per share)
```

The reconciler emits a hard break for these by default since RoC is
unambiguous about the cost-basis adjustment. If the basis goes negative
(distribution exceeds basis), the excess is treated as a capital gain
for tax purposes; the reconciler flags `basis_exhausted = true` so the
operator's tax team gets a heads-up.

## Timing rule

Same as splits: apply when `ex_dividend_date > as_of_date`. The Massive
field is `ex_dividend_date` (note: NOT `ex_date`; the splits endpoint
uses `execution_date`; the dividends endpoint uses `ex_dividend_date`).
The reconciler aliases both to `ex_date` in the output JSON to keep the
schema clean for downstream consumers.

```python
if div["ex_dividend_date"] > as_of_date:
    consider(div)
```

A position dated on the ex-date is already past the distribution: the
shares trade ex-div that day. No adjustment.

## Sample sizes worth remembering

When checking a portfolio against the dividends endpoint, expect a lot
of records. A typical dividend-paying name pays 4 times a year, so a
ticker held since 2020 will have 24+ dividend records by mid-2026. The
reconciler defaults to `informational` mode for cash dividends: it
counts them in the source trail but doesn't bloat the BREAKS list. If
the operator wants every distribution surfaced, set
`include_informational = true` in the run config.

## Frequency field

Massive's `frequency` enum is a useful sanity check. The mapping:

| Code | Meaning |
|---|---|
| 0 | One-time (likely special) |
| 1 | Annual |
| 2 | Semi-annual |
| 4 | Quarterly |
| 12 | Monthly |

If a `CD` (regular cash) record arrives with `frequency: 0`, the
reconciler treats it as a one-time special, not a regular quarterly,
even if the type code says otherwise. The frequency field is the more
reliable signal.
