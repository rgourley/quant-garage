---
name: t+1-settlement-prep
description: Take a trade file (recent trades that haven't settled yet) and walk every line against the business-day calendar plus the corporate-action calendar to flag trades with settlement risk. Catches holiday adjacency, weekend crossing, short-sale locate prompts, ex-dividend in the settlement window, mid-settlement splits, and half-day DTCC cutoffs. Exception-report mode. Runs on a free Stocks Basic key.
---

# t+1-settlement-prep

You hand over a trade file. The skill computes the T+1 settlement date
for each row, walks the next 90 days of holidays + corporate actions,
and emits an exception report listing only the trades that need
operations attention before settlement.

This is the operations-desk workflow that runs at end of trading day,
between T and T+1. SEC moved US equities to T+1 in May 2024; the
margin for error on settlement is now one business day, not three.
A trade that crosses a long weekend or settles on a half-day session
can break cash forecasting if nobody flagged it.

## When to invoke

- Operations says "prep tonight's trades for settlement" or "run T+1
  checks on this blotter"
- Treasury asks "is cash going to be where it needs to be tomorrow"
- A long weekend is coming up (3-day weekend, July 4, Thanksgiving)
  and ops wants visibility into which trades push into the following
  week
- A corporate action calendar shows an ex-date inside the next two
  business days and you want to know which trades are exposed

## What you need

- A trade CSV with columns: `ticker`, `side` (BUY/SELL/SHORT/COVER),
  `qty`, `trade_date` (YYYY-MM-DD)
- `MASSIVE_API_KEY` exported. Stocks Basic is enough; the three
  endpoints this skill uses (`/v1/marketstatus/upcoming`,
  `/v3/reference/dividends`, `/v3/reference/splits`) are all on the
  free tier.

## What this skill flags

Six categories. A trade can hit more than one.

1. **Holiday adjacency** (`holiday_adjacency`). T+1 lands on a US
   equity-market holiday; settlement pushes to the next business day.
   Operations needs to refund cash forecasts and notify treasury for
   the adjusted date.

2. **Weekend crossing** (`weekend_crossing`). T+1 lands on a Saturday
   or Sunday; settlement pushes to Monday. Treated as a separate flag
   from holiday_adjacency because cash desks model them differently
   (regular weekend vs irregular three-day weekend).

3. **Short-sale locate** (`short_sale_locate`). Side is SHORT. Reg
   SHO requires a locate before the trade can settle. The skill cannot
   read the prime broker's locate file, so this flag is a confirmation
   prompt rather than a hard break.

4. **Ex-dividend in window** (`ex_dividend_in_window`). The ticker has
   an ex-dividend date between trade_date and computed_settlement_date.
   Under T+1, ex-date and record-date are the same day, so the buyer's
   dividend entitlement turns on whether they purchased before the
   ex-date. The skill computes the dollar impact (qty * cash_amount)
   so operations can confirm the dividend allocation flag on the trade
   ticket.

5. **Corporate action overlap** (`corp_action_overlap`). A split or
   reverse split has an ex-date in the settlement window. DTCC handles
   the share-count adjustment automatically, but the post-action
   delivered quantity differs from the recorded trade quantity. Flagged
   as informational so settlement and position-reporting teams aren't
   surprised. Not a hard break.

6. **Half-day settlement** (`half_day_settlement`). Settlement date is
   a US equity early-close session (Thanksgiving Friday, Christmas Eve,
   July 3 when 7/4 falls on Saturday). DTCC's cutoff is earlier than
   the normal 15:00 ET deadline, typically 12:30 ET. Cash-management
   teams tighten their windows accordingly.

## What you get back

Two output layers.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per flagged trade: ticker, side, qty, trade_date, computed settlement
date, reason codes, impact text, suggested next action, plus typed
substructures for `dividend`, `corp_action`, and `session_info` when
those reasons fire. Per run: scan_params, settlement window dates,
counts by reason. UI dashboards and downstream agents consume this.

**Layer 2: rendered exception report**. Header with run metadata. One
BREAK block per flagged trade. Summary block with counts by reason and
the settlement-window date range. See [`references/rendering.md`](./references/rendering.md)
for the full format.

A short example:

```
T+1 settlement prep: 12 trades checked · 4 BREAKs flagged · run 2026-06-25 16:42 UTC

BREAK 1: NVDA SHORT 200 · trade 2026-06-25 · settlement 2026-06-26
  Reason:        Short sale; locate confirmation required
  Impact:        Trade may fail without locate on file before T+1 cutoff
  Suggest:       Confirm locate ticket with prime broker before EOD
```

## How it works

1. **Pull the holiday calendar** from `/v1/marketstatus/upcoming` once
   per run. Cache the response; the API returns one row per exchange
   per event (NYSE and NASDAQ each emit their own row for the same
   holiday). The skill de-dupes by `date` and treats NYSE as canonical.
   Half-day sessions appear as `status: "early-close"` with `open` and
   `close` UTC timestamps. Full closures are `status: "closed"` with no
   open/close.
2. **For each trade**, compute the naive settlement date as
   `trade_date + 1 calendar day`. Then walk forward: if the candidate
   is a weekend or a holiday, step one day and re-check. The first
   business day that survives is the computed settlement date.
3. **Set flag codes**:
   - `weekend_crossing` if naive settlement was Sat or Sun
   - `holiday_adjacency` if naive settlement was a closed market day
     (or computed settlement was pushed past one). These are tracked
     separately because cash desks model them differently.
   - `short_sale_locate` if `side == "SHORT"`
4. **Pull the dividend calendar** for each ticker via
   `/v3/reference/dividends?ticker={t}&ex_dividend_date.gte=...`. If
   any ex-date falls inside `[trade_date, computed_settlement_date]`,
   flag `ex_dividend_in_window` and populate the `dividend` substructure
   with `cash_amount`, `pay_date`, and dollar impact (qty * amount).
5. **Pull the splits calendar** for each ticker via
   `/v3/reference/splits?ticker={t}&execution_date.gte=...`. If any
   ex-date falls in the window, flag `corp_action_overlap` and
   populate the `corp_action` substructure with ratio and the
   post-adjustment quantity. This is informational; DTCC handles the
   share-count delivery automatically.
6. **Check half-day settlement**. If `computed_settlement_date` is in
   the holiday calendar with `status: "early-close"`, flag
   `half_day_settlement` and populate `session_info` with the close
   time and the DTCC cutoff time (typically 12:30 ET).
7. **Emit JSON and rendered output**. Only trades with at least one
   reason code appear. Summary block tallies counts by reason and
   reports the settlement window date range.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth and
  rate limiting

## Endpoints used

- `GET /v1/marketstatus/upcoming` (holiday + half-day calendar, one
  pull per run, cached)
- `GET /v3/reference/dividends` (one paginated call per ticker;
  filtered to ex-dates in the settlement window)
- `GET /v3/reference/splits` (one paginated call per ticker; same
  filter)

All three are in Stocks Basic. The free tier's 5-calls-per-minute cap
makes a 20-trade file take roughly 10 minutes; any paid tier eliminates
the wait.

## Example

```bash
# Trade file with 12 rows
cat examples/sample-trades.csv

# Invoke from Claude Code
# > /t+1-settlement-prep examples/sample-trades.csv
```

The skill streams findings as it walks each trade, so the operator
sees BREAK blocks as they're computed instead of waiting for the full
report.

## Doesn't handle (yet)

- **Symbol changes and delistings.** A trade in a ticker that changes
  symbol or delists between trade and settlement still settles
  correctly, but operations needs the new identifier. Detecting this
  requires reading `/v3/reference/tickers` over the window and
  comparing `last_updated_utc` to the trade date. Deferred to v2 of
  the skill.
- **Cash-vs-margin reconciliation.** The skill flags ex-dividend
  exposure as a dollar impact but does not project the account-level
  cash delta. That's the cash-management system's job.
- **Locate-file ingest.** The skill flags every SHORT row for locate
  confirmation. An operator with a CSV export of the prime broker's
  locate file can extend the skill with a second optional input to
  auto-confirm; today's v1 is a prompt, not a check.
- **International equities.** Settlement calendars in EU (T+1 from
  October 2027), Japan (T+1 since 2024), India (T+0 in pilot) are
  different. The skill assumes US equities only.
- **Options assignment cash flow.** OCC delivery on an assigned
  option settles T+1 like the underlying, but the workflow is
  different (premium debit, share delivery, exercise notice). A
  future `options-settlement-prep` skill would handle that.

Add these in a PR if you need them. The calendar walking and
ref-data pulls carry over.
