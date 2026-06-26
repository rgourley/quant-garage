# Ex-dividend timing under T+1

## The entitlement rule

A buyer who purchases shares **on or before** the day before the
ex-dividend date is entitled to the dividend. A buyer who purchases
**on or after** the ex-dividend date is not.

Under T+1, the ex-date and the record date are the same day. This is
a change from the T+2 era, when ex-date was T+2's worth of days before
record date.

## Why the skill flags this

A trade that executes on or just before the ex-date and settles in
the window around it can create a dividend allocation question on the
trade ticket. Operations needs to confirm the entitlement flag,
because:

- The DTCC delivers the dividend cash to whoever is the holder of
  record on the record date.
- Under T+1, the trade settling on or before the ex-date means the
  buyer becomes the holder of record on the ex-date, which is the
  record date.
- The buyer's entitlement is computed against the record date by the
  DTCC; the operator just needs the trade ticket's dividend flag set
  correctly so the firm's books match what DTCC delivers.

## What the skill flags

`ex_dividend_in_window` fires when the dividend's `ex_dividend_date`
falls in `[trade_date, computed_settlement_date]` inclusive.

The skill populates the `dividend` substructure with:

- `ex_date`: from the Massive response
- `pay_date`: from the Massive response (when the cash hits)
- `cash_amount_per_share`: from the Massive response
- `currency`: from the Massive response (usually USD)
- `dollar_impact`: `qty * cash_amount_per_share`

The dollar impact is the headline number ops cares about. A $0.27/sh
dividend on a 1,000-share trade is $270; trivial in isolation,
material across a 5,000-trade institutional blotter.

## Side handling

- **BUY**: the entitlement question is real. Flag.
- **SELL** (long sale): the entitlement question is the inverse: did
  the seller already collect the dividend or will the buyer? Still
  worth flagging because the trade ticket needs a clear flag either
  way.
- **SHORT**: short sellers owe the dividend to whoever lent them the
  shares (the "payment in lieu" rule). The skill flags this as the
  short-sale flag handles the broader concern; the ex-dividend flag
  duplicates it. v1 flags both; the dollar-impact line correctly
  shows the cash flow direction.
- **COVER**: the buy-to-close has the same entitlement rules as a
  regular BUY. Flag.

## Special dividends

`dividend_type` distinguishes the cash-flow type. Massive uses:

| Code | Meaning |
|---|---|
| `CD` | Regular cash dividend (quarterly, monthly, annual) |
| `SC` | Special cash (one-off; larger than regular) |
| `RC` | Return of capital (reduces cost basis, not income) |
| `ST` | Stock dividend (shares delivered instead of cash) |

For settlement prep, the skill flags all four types when the ex-date
is in the window. The `dividend_type` is passed through in the
JSON so downstream consumers can branch:

- `CD` and `SC`: standard cash entitlement question, dollar_impact in
  USD
- `RC`: dollar_impact still applies; the bookkeeping is different
  (basis reduction, not income) but the cash flow on settlement day
  is the same
- `ST`: dollar_impact will be 0 (no cash); operations needs to know
  the share-count delivery is increasing. The skill renders the ratio
  in the impact line.

## Stock dividends specifically

A stock dividend (`dividend_type == "ST"`) acts like a small forward
split. A 5% stock dividend means 5 new shares per 100 held delivered
at the ex-date. DTCC handles the delivery, but the settlement
quantity for the trade ticket is the pre-ex-date number; the share
delivery and the trade settlement are separate events.

This overlaps with the `corp_action_overlap` flag conceptually but
fires off the dividends endpoint. The skill flags it as
`ex_dividend_in_window` (with `dividend_type: "ST"`) rather than
`corp_action_overlap` because the data source is dividends, not
splits.

## Edge case: the dividend gets reversed

Occasionally a company announces a dividend, the trade settles, and
then the dividend is cancelled or modified. The Massive API will
update the record but the trade has already settled. This is a
broker-system reconciliation problem, not a settlement-prep
problem; the skill doesn't address it.

## Edge case: foreign-domiciled issuers

ADRs and foreign-domiciled issuers may have ex-dates in their home
market that don't match the US ADR ex-date. The skill uses the
Massive ex-date, which is the US ex-date for ADRs. The home-market
ex-date is not surfaced. For European ADRs this matters less under
T+1 since both sides are converging on T+1, but for emerging-market
ADRs it can matter.

## Pre-fetch optimization

The skill fetches the dividend calendar once per ticker, not once per
trade. If three rows in the trade file are all AAPL, the skill makes
one `/v3/reference/dividends?ticker=AAPL` call and applies the result
to all three rows. The same caching applies to splits.
