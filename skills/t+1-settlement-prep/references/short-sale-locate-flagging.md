# Short-sale locate flagging

## What this skill checks

Every trade row with `side == "SHORT"` gets a `short_sale_locate`
flag. That's it. The skill does not verify the locate exists; it
prompts operations to confirm.

## Why a locate matters

Reg SHO (SEC Rules 200-204) requires a broker-dealer to either
borrow, arrange to borrow, or have reasonable grounds to believe a
security can be borrowed before executing a short sale. The
documentation of that borrow is the "locate ticket" and lives in the
prime broker's stock loan system.

If the locate isn't on file by T+1 settlement:

- The trade may fail (no shares to deliver).
- If it fails for 13 consecutive settlement days, Reg SHO Rule 204
  triggers a forced buy-in and the broker reports it on Form CNS.
- Persistent fails draw SEC examination scrutiny.

Under T+2 the desk had two business days to confirm the locate. Under
T+1 there's one. Operations needs visibility into every SHORT row
before EOD on trade date.

## What this skill does NOT check

The skill cannot read the prime broker's locate file. It does not know
whether a ticket exists for the trade in question. The flag is a
prompt, not a confirmation.

If the operator has a CSV export of the locate file, a future extension
of the skill could accept it as a second optional input and clear the
flag automatically. v1 is the prompt.

## Side codes

| Side | Locate check |
|---|---|
| `BUY` | No flag |
| `SELL` | No flag (long sale; closing an existing position) |
| `SHORT` | Flag `short_sale_locate` |
| `COVER` | No flag (buying to close a short; settles like a BUY) |

If the input CSV uses a different convention (e.g. `SELL_SHORT`,
`SS`, signed quantities), the parser maps it. The schema expects one
of the four codes above.

## Bona fide market making exemption

Reg SHO has a market-maker exemption: a bona fide market maker can
short without a pre-borrow if the short is in the course of their
market-making activity. The exemption is narrow; most operators
don't qualify. The skill flags every SHORT row by default; an
operator running on a market-maker book can ignore the flag or filter
it upstream.

## What the impact line should say

The skill renders a one-line impact statement on the flagged trade.
For short_sale_locate it should always be:

```
Trade may fail without locate on file before T+1 cutoff
```

And the suggested action:

```
Confirm locate ticket with prime broker before EOD
```

These are templated, not computed. The skill doesn't have the data to
say more.

## Edge case: hard-to-borrow names

Some tickers (low-float, recently IPO'd, in a squeeze) are on the
prime broker's hard-to-borrow list. Those typically carry higher
borrow fees and lower locate availability. The skill does not know
which names are HTB. An ops desk that wants HTB-specific flagging
should layer it on top: pull the prime's HTB feed, intersect with the
SHORT rows from this skill's output, and surface the overlap as a
higher-priority subset.

## Edge case: ETF creation/redemption

Short positions in ETFs created by an authorized participant via
in-kind creation don't follow the standard locate path. This is
rare in normal flow and isn't worth special-casing in v1.
