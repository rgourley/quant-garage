## Book value and P&L

How to compute book value from marks, and unrealized P&L from marks
plus cost basis. The math is straightforward; the rules below cover
the edge cases (short positions, missing cost basis, mixed marks)
that produce wrong numbers when ignored.

## Book value

```
book_value_usd = sum(shares * mark_price for position in positions
                     if mark_price is not None)
```

A position with `mark_price = null` (the fallback chain returned
nothing) is excluded from book value. The skill flags this in the
exception block; the operator can override with a manual mark or
remove the position.

Negative-share positions (shorts) contribute negative book value.
A book that is net-short overall has a negative book_value_usd. This
is correct: book value is the dollar amount the position would
liquidate for at current marks, and a short liquidates at a loss to
the holder (you owe the borrowed shares).

## Unrealized P&L

When the input CSV carries a `cost_basis` column:

```
unrealized_pnl_usd = (mark_price - cost_basis) * shares
```

The sign flips naturally for short positions:
- Long, mark > basis: `(110 - 100) * 100 = +1000` (gain)
- Long, mark < basis: `(90 - 100) * 100 = -1000` (loss)
- Short, mark > basis: `(110 - 100) * -100 = -1000` (loss; price went up against you)
- Short, mark < basis: `(90 - 100) * -100 = +1000` (gain; price went down for you)

The book-level total is the sum across positions that have a
non-null cost basis. Positions without cost basis contribute null to
the per-position P&L and are excluded from the sum.

If no position in the input has a cost basis, the output's
`unrealized_pnl_usd` is null and the rendered output omits the P&L
column entirely.

## Cost basis semantics

The skill treats `cost_basis` as **per-share cost basis** in USD,
not total cost. So a 100-share AAPL position acquired at $150/share
total cost $15,000 has `cost_basis: 150.00` in the CSV.

If the operator's source system stores total cost, they convert
upstream: `cost_basis = total_cost / shares`. The skill doesn't
attempt to detect or normalize.

## Mixed-confidence book value

The skill computes book value across all marked positions regardless
of confidence. A low-confidence mark still contributes to the total;
the operator sees the contribution and the warning side-by-side. The
alternative (excluding flagged positions) silently understates the
book, which is the worse failure mode for a NAV.

The rendered output footer includes the count of flagged positions
and the dollar value at risk if those marks are wrong. Example:

```
Book value: $361,584.50 · 1 flagged ($1,209 exposure at <high confidence)
```

The "exposure at <high confidence" is `sum(abs(shares) * mark_price)`
across flagged rows.

## Currency

V1 assumes USD throughout. The output schema includes the unit
explicitly (`book_value_usd`) so a future FX-overlay version can
extend cleanly.

## Day P&L (not implemented)

V1 does not compute day P&L (mark vs prior close). That requires
either a prior-close field in the input or a second snapshot pull,
and the operator's pricing system usually already has prior close.
The output schema is extensible: a future field would be
`day_pnl_usd` per position and at book level, sourced from
`snapshot.prevDay.c`.
