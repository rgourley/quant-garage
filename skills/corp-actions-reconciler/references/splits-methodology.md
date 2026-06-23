# Splits methodology

How to apply a forward or reverse split to a recorded position. This is
the math the reconciler runs for every `kind: split` or
`kind: reverse_split` action. Get this right and the reconciler stops
flagging false positives; get it wrong and every reverse split looks
like a position blowup.

## The math

For each split record from `GET /v3/reference/splits`:

```
ratio = split_to / split_from
expected_shares     = current_shares * ratio
expected_cost_basis = current_cost_basis / ratio
```

Examples (all real records from Massive's reference data):

| Ticker | Ex-date | split_to | split_from | Pre 100 sh | Post |
|---|---|---|---|---|---|
| AAPL | 2020-08-31 | 4 | 1 | 100 | 400 |
| GOOGL | 2022-07-18 | 20 | 1 | 50 | 1000 |
| NVDA | 2024-06-10 | 10 | 1 | 100 | 1000 |
| TSLA | 2022-08-25 | 3 | 1 | 50 | 150 |
| GE | 2021-08-02 | 1 | 8 | 800 | 100 |

The last row is a reverse split: 1-for-8 means `split_to=1,
split_from=8`, so 800 shares become 100. Cost basis goes the other way:
`$10 * 8/1 = $80` per share so the position dollar value stays the
same. The reconciler classifies this as `kind: reverse_split` for
clearer rendering, but the math is identical to a forward split:
`shares * (split_to / split_from)`.

## Classifying forward vs reverse

The Massive splits endpoint doesn't tag forward vs reverse. Derive it:

```python
if split["split_to"] > split["split_from"]:
    kind = "split"           # forward
else:
    kind = "reverse_split"
```

This matters for the rendered output (operators read "reverse split" as
a different risk shape: it's often a delisting precursor) and for the
ratio formatting: forward renders as `"4-for-1"`, reverse renders as
`"1-for-8"`. Always write the larger number first in the ratio string
so a reader sees the share-count direction at a glance.

## Compounding multiple splits

When a ticker has multiple splits between `as_of_date` and today, apply
them in chronological order (ascending execution_date). Each split
operates on the share count from the previous step:

```
shares_after_split_n = shares_after_split_(n-1) * (split_to_n / split_from_n)
```

NVDA is the live example. Held since 2019, the position has gone through
two splits:

```
1000 shares purchased 2019-01-15
* (4/1)  for 2021-07-20 split → 4000
* (10/1) for 2024-06-10 split → 40000
```

The reconciler always sorts splits by `execution_date` ascending before
applying. Out-of-order application gives the same mathematical answer
in this case (multiplicative), but cost basis tracking and intermediate
states will be wrong if you ever need to break the chain.

## Timing rule

Apply a split when `execution_date > as_of_date`. The execution_date
field on Massive's response is the ex-date: the first session the stock
trades at the new ratio. If the operator's position is dated on or
after the ex-date, the position already reflects the split and the
reconciler should NOT apply it again.

```python
if split["execution_date"] > as_of_date:
    apply_split(position, split)
```

Strict greater-than. A position with `as_of_date = 2020-08-31` (the
AAPL ex-date) is on the post-split system already and should reconcile
clean against 100 shares. A position with `as_of_date = 2020-08-30`
should expect the split to apply.

## Fractional share results

When `current_shares * (split_to / split_from)` isn't a whole number,
the broker pays cash-in-lieu (CIL) for the fractional. The reconciler's
default rounding rule:

- Forward splits: brokers typically round down and pay CIL on the
  fractional. The reconciler emits `expected_shares` floored to the
  whole share, and flags `cash_in_lieu_expected = true` in the break.
- Reverse splits: same: round down, CIL on the fractional. Reverse
  splits are more likely to produce fractionals (1-for-8 turns 7
  shares into 0.875).

The reconciler does NOT attempt to value the CIL: it would need the
ex-date close from `/v2/aggs/ticker/{ticker}/range/1/day/{ex_date}/{ex_date}`
and broker-specific rounding rules. Operators handle CIL value
reconciliation separately, usually against their broker's confirm.

If the operator wants exact CIL flagging, set
`flag_fractional_results = true` in the run config: every position
that would result in a fractional gets flagged as a soft break
(`kind: cash_in_lieu_expected`) so it gets a manual review pass.

## Stock dividends

A "stock dividend" (cash_amount = 0 with a share count distribution) is
mechanically the same as a small forward split. If the source data
expresses it as a percentage (`5% stock dividend`), convert to a
ratio:

```
split_to = 100 + pct   # e.g. 105 for a 5% stock div
split_from = 100
```

Massive's dividends endpoint does NOT cleanly distinguish stock
dividends from cash dividends; the `dividend_type` field carries codes
like `CD` (regular cash), `SC` (special cash), `LT` (long-term). When
the reconciler sees a `dividend_type` outside the cash set, it
inspects the record for share-distribution fields before falling back
to treating it as cash. Stock dividends are also routinely
double-listed in the splits endpoint, so the reconciler prefers the
splits record when both exist for the same ex-date.

## Edge cases worth flagging

See [`edge-cases.md`](./edge-cases.md) for the full list: fractional
shares, cash-in-lieu, foreign domiciles, ADR splits, and ratios that
don't reduce to a clean number.
