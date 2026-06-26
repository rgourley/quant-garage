# T+1 settlement cycle

## What T+1 means

US equities settle one business day after the trade date. The SEC
adopted this on May 28, 2024, replacing the T+2 cycle that had been
in place since 2017. The rule covers stocks, ETFs, corporate bonds
that trade like equities, ADRs, and the equity leg of options
exercises. US Treasuries and most mutual funds are on different
cycles.

T+1 reduces counterparty risk and unlocks margin, but it compresses
the operations window. Under T+2 an ops desk had two business days to
resolve fails, breaks, locates, and FX. Under T+1 they have one.

## Business day counting

The settlement calendar uses US equity-market business days only.

- Saturdays and Sundays are not business days.
- US equity-market holidays are not business days. This is a smaller
  set than US federal holidays. Columbus Day and Veterans Day are
  federal holidays but the equity markets are open. Good Friday is not
  a federal holiday but the equity markets are closed.
- Half-day sessions (early close) ARE business days for settlement
  counting purposes. DTCC processes settlement on a half-day session;
  the cutoff is just earlier than on a full day (typically 12:30 ET
  instead of 15:00 ET).

A trade dated Wednesday settles Thursday. A trade dated Friday
settles Monday. A trade dated the Wednesday before Thanksgiving
settles Friday (the half day after Thanksgiving). A trade dated July 2
when July 4 falls on a Saturday settles July 6 (Friday July 3 is the
observed federal holiday and the equity markets close).

## Walking the calendar

The skill's algorithm:

1. Set `candidate = trade_date + 1 calendar day`.
2. While `candidate` is Saturday, Sunday, or a closed US equity-market
   day, step `candidate += 1 calendar day` and re-check.
3. The first `candidate` that survives the check is the computed
   settlement date.

Two flags emit from the walk:

- `weekend_crossing` if the naive (calendar) T+1 landed on Sat or Sun.
- `holiday_adjacency` if the naive T+1 landed on a market-closed day
  OR the walk pushed the candidate past one. These overlap (long
  weekend = both), and that's intentional; treasury teams want both
  flags.

## Holiday calendar source

Massive's `/v1/marketstatus/upcoming` returns the next ~90 days of
NYSE and NASDAQ events. NYSE and NASDAQ track each other on US equity
holidays (this hasn't always been true historically but is true post-
2007). The skill treats them as equivalent and de-dupes by `date`.

The response shape is:

```
[
  {"date": "2026-07-03", "exchange": "NYSE", "name": "Independence Day", "status": "closed"},
  {"date": "2026-07-03", "exchange": "NASDAQ", "name": "Independence Day", "status": "closed"},
  {"date": "2026-11-27", "exchange": "NYSE", "name": "Thanksgiving", "status": "early-close",
   "open": "2026-11-27T14:30:00.000Z", "close": "2026-11-27T18:00:00.000Z"},
  ...
]
```

`close` is in UTC; `2026-11-27T18:00:00.000Z` is 13:00 ET. The skill
parses the UTC close, converts to ET, and stores the `HH:MM` ET string
for the rendered output.

## Half-day flagging

Half-day sessions are valid business days. DTCC still settles trades
that day; the cutoff is earlier. The skill flags
`half_day_settlement` so cash-management knows to tighten the funding
window. The specific cutoffs:

- Full day: DTCC fed-funds cutoff is 15:00 ET; payment cutoff 16:00 ET
- Half day: DTCC fed-funds cutoff is 12:30 ET; payment cutoff 13:30 ET

These are publicly documented by DTCC and the Fed; they don't come
from the Massive API. The skill hard-codes the half-day cutoffs.

The half-day sessions in scope (NYSE/NASDAQ schedule):

- Day after Thanksgiving (Friday): early close 13:00 ET
- Christmas Eve when 12/24 is a weekday: early close 13:00 ET
- Day before Independence Day when 7/3 is a weekday and not the
  observed holiday: early close 13:00 ET

## Cross-reference: NYSE and NASDAQ

These exchanges publish the same US equity holiday calendar. If they
ever diverged on a specific date (vanishingly unlikely now), the more
restrictive (closed) calendar wins, because a security listed on the
closed exchange can't print and therefore can't settle. The
de-duplication logic in the skill assumes alignment; revisit if NYSE
and NASDAQ publish a divergent date.

## Why this matters for operations

A trade dated Thursday July 2, 2026 looks like a normal T+1 trade.
Cash is needed Friday July 3. But Friday July 3, 2026 is the observed
Independence Day holiday (because July 4 falls on a Saturday).
Settlement actually lands Monday July 6. If the cash forecast assumes
Friday funding, treasury moves money one business day too early and
holds it idle through a long weekend. On a $50M trade that's not
material; on a $500M institutional book it costs noticeable carry.

The skill catches this and flags it.
