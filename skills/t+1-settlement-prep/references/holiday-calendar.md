# Holiday calendar

## Source: `/v1/marketstatus/upcoming`

Massive's holiday endpoint returns the next ~90 days of US equity-
market events. One row per exchange per event, so NYSE and NASDAQ each
emit a row for the same date. Verified 2026-06-25 on a Stocks Business
key; the endpoint is also available on Stocks Basic (free).

Request:

```
GET /v1/marketstatus/upcoming
```

No query parameters. The response is a JSON array of events:

```json
[
  {
    "date": "2026-07-03",
    "exchange": "NYSE",
    "name": "Independence Day",
    "status": "closed"
  },
  {
    "date": "2026-11-27",
    "exchange": "NYSE",
    "name": "Thanksgiving",
    "status": "early-close",
    "open": "2026-11-27T14:30:00.000Z",
    "close": "2026-11-27T18:00:00.000Z"
  }
]
```

## Fields

| Field | Meaning |
|---|---|
| `date` | YYYY-MM-DD, the date of the event |
| `exchange` | "NYSE" or "NASDAQ" (the skill de-dupes on date) |
| `name` | Human-readable holiday name |
| `status` | `"closed"` (full holiday) or `"early-close"` (half day) |
| `open` | UTC ISO timestamp; only on early-close rows |
| `close` | UTC ISO timestamp; only on early-close rows |

`open` and `close` are absent on full holidays. On early-close days,
both are present and the skill converts `close` to Eastern Time for
display.

## De-duplication

NYSE and NASDAQ emit the same date with the same status (verified
2026-06-25). The skill keeps the first occurrence per date and
ignores the second. If the two ever disagree (extremely unlikely),
the closed status wins.

## Half-day flagging

`status: "early-close"` is the half-day signal. The skill stores:

- `close_et`: the ET wall-clock time derived from `close` UTC
- `reason`: the holiday name (e.g. "Thanksgiving", "Christmas Eve")
- `dtcc_cutoff_et`: hard-coded to `"12:30"` (DTCC's published fed-
  funds cutoff for half-day sessions)

The DTCC cutoff is NOT in the Massive response; it comes from DTCC's
operations bulletin and is a property of US settlement
infrastructure, not the trading exchange.

## US equity holidays in the next 90 days

Verified 2026-06-25 against the live endpoint:

| Date | Holiday | Status |
|---|---|---|
| 2026-07-03 | Independence Day (observed; 7/4 is Saturday) | closed |
| 2026-09-07 | Labor Day | closed |
| 2026-11-26 | Thanksgiving | closed |
| 2026-11-27 | Thanksgiving Friday | early-close 13:00 ET |
| 2026-12-24 | Christmas Eve | early-close 13:00 ET |
| 2026-12-25 | Christmas | closed |

The endpoint also returned 2027 holidays (New Year's Day,
MLK Day, Washington's Birthday, Good Friday, Memorial Day,
Juneteenth) within the same response. The skill loads everything it
returns and walks the relevant subset for each trade.

## Half-day session rule

US equity half-day sessions occur on three predictable days:

1. **Day after Thanksgiving** (Friday): always 13:00 ET close.
2. **Christmas Eve (December 24)** when it falls on a weekday: 13:00 ET
   close. If 12/24 is a weekend, the markets follow the normal
   weekend schedule.
3. **Day before Independence Day (July 3)** when 7/3 is a weekday AND
   7/4 falls on a weekday: 13:00 ET close. When 7/4 falls on a
   Saturday (as in 2026), 7/3 is the observed holiday and is fully
   closed, NOT a half day.

The Massive endpoint encodes this directly. The skill trusts the
response and doesn't try to reconstruct the rule.

## Why the calendar is bounded

`/v1/marketstatus/upcoming` returns "upcoming" events without a
date filter. In practice the response covers ~6-9 months out. For a
T+1 settlement skill that only cares about the next 1-2 business days
per trade this is over-coverage; for trades dated up to 90 days back
it's still enough. If the skill is ever extended to walk historical
trades older than 6 months, it would need a separate historical
calendar.

## Fallback: hard-coded NYSE 2026-2027 calendar

If `/v1/marketstatus/upcoming` returns a non-200 response, the skill
falls back to a hard-coded NYSE holiday calendar. This is the
verified list:

```python
NYSE_HOLIDAYS_FALLBACK = {
    # 2026
    "2026-01-01": ("New Year's Day", "closed"),
    "2026-01-19": ("Martin Luther King, Jr. Day", "closed"),
    "2026-02-16": ("Washington's Birthday", "closed"),
    "2026-04-03": ("Good Friday", "closed"),
    "2026-05-25": ("Memorial Day", "closed"),
    "2026-06-19": ("Juneteenth", "closed"),
    "2026-07-03": ("Independence Day (observed)", "closed"),
    "2026-09-07": ("Labor Day", "closed"),
    "2026-11-26": ("Thanksgiving", "closed"),
    "2026-11-27": ("Day after Thanksgiving", "early-close"),
    "2026-12-24": ("Christmas Eve", "early-close"),
    "2026-12-25": ("Christmas", "closed"),
    # 2027
    "2027-01-01": ("New Year's Day", "closed"),
    "2027-01-18": ("Martin Luther King, Jr. Day", "closed"),
    "2027-02-15": ("Washington's Birthday", "closed"),
    "2027-03-26": ("Good Friday", "closed"),
    "2027-05-31": ("Memorial Day", "closed"),
    "2027-06-18": ("Juneteenth", "closed"),
    "2027-07-05": ("Independence Day (observed)", "closed"),
    "2027-09-06": ("Labor Day", "closed"),
    "2027-11-25": ("Thanksgiving", "closed"),
    "2027-11-26": ("Day after Thanksgiving", "early-close"),
    "2027-12-23": ("Christmas Eve", "early-close"),
    "2027-12-24": ("Christmas (observed)", "closed"),
}
```

The fallback is exercised only when the API call fails. In normal
operation the live endpoint is the source of truth and the fallback
is dormant.
