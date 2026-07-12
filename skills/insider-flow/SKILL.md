---
name: insider-flow
description: Aggregate SEC Form 4 insider activity for a ticker over a caller-supplied lookback window, classify each transaction by SEC transaction code and Rule 10b5-1 status, separate signal (conviction buys, discretionary sales) from noise (grants, exercises, tax withholding, 10b5-1 sales), detect cluster buys (>= 2 insiders in a 14-day window worth >= $100k), and emit a sentiment label backed by the underlying dollar flow. Use when a PM or fundamental analyst asks "are insiders buying or selling this name?" Uses Massive's pre-parsed Form 4 endpoint. Requires Stocks Basic. Runs on the free tier.
---

# insider-flow

You hand over a ticker. The skill pulls every Form 4 filed against the
issuer over the lookback window, classifies each transaction, separates
the signal (open-market buys, non-scheduled sales) from the noise
(grants, option exercises, 10b5-1 scheduled sales), detects cluster
buys, and emits a sentiment label backed by the underlying dollar flow.

The point is to answer the question a PM actually asks: **are insiders
buying or selling this name, and does it matter?** The default read
ignores 10b5-1 sales (pre-committed months in advance) and comp-related
grants because they carry no signal about what management thinks of
the current price.

## When to invoke

- A PM asks "any insider activity on NVDA in the last 6 months?"
- A fundamental analyst wants a heads-up on the CEO's stock
  transactions over a cycle
- Screening a watchlist for cluster buys as a bullish overlay on
  weakness
- The user says "insider flow", "Form 4 activity", "who's buying /
  selling", "insider signal"

Not for: real-time (Form 4 is filed within 2 business days, so this
is days-fresh, not tick-fresh). Not for 13-F holdings (institutional
ownership is a separate skill).

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Basic plan minimum. The `/stocks/filings/vX/form-4`
  endpoint is included on every Stocks plan.

Optional:

- `--lookback-days` (default 180): calendar-day window back from today.
- `--exclude-directors`: drop pure-director rows (`is_director` AND
  NOT `is_officer` AND NOT `is_ten_percent_owner`). Useful for names
  with VC or PE board reps unwinding a fund position, which
  structurally look bearish but carry no operator signal. Executives
  who also sit on the board are kept (they carry operator signal).

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Top-level `summary` (sentiment label + reasoning, conviction buy /
discretionary sale / scheduled sale / routine comp counts and dollars,
net conviction flow), `clusters[]` (detected 14-day cluster buy
windows sorted by dollar volume), `notable_buys[]` (top 5 open-market
buys by dollar), `notable_sales[]` (top 5 discretionary sales),
`by_role` (aggregate flow keyed on officer / director / 10% owner).

**Layer 2: rendered note**. Sentiment label at the top, transaction-
flow block, cluster buys (when detected), notable individual
transactions, by-role aggregation, one-line Take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull Form 4 rows** via
   `GET /stocks/filings/vX/form-4?tickers={T}&filing_date.gte={from_date}&limit=1000&sort=filing_date.desc`.
   Massive returns one row per transaction leg (a single Form 4
   filing can have multiple non-derivative + derivative legs).
2. **Classify each row** by SEC transaction code + Rule 10b5-1
   status. See [`references/transaction-codes.md`](./references/transaction-codes.md)
   for the full mapping:
   - `conviction_buy`: code P (open-market purchase, non-derivative)
   - `discretionary_sale`: code S with `aff_10b5_one=false`
   - `scheduled_sale`: code S with `aff_10b5_one=true`
   - `routine_comp`: codes A (grant), M (derivative exercise), F
     (tax withholding)
   - `non_informative`: everything else (gifts, expiries, swaps)
3. **Detect cluster buys.** Rolling 14-day windows where >= 2 distinct
   insiders made open-market purchases (code P) summing to >= $100k.
   One entry per detected cluster.
4. **Aggregate by role.** Officer / director / 10% owner net flow.
   Officers dominate signal typically; 10% owners can be activists
   or founders with idiosyncratic reasons.
5. **Emit sentiment** on the net conviction flow (buys minus
   discretionary sales, 10b5-1 sales excluded). Buckets:
   - `strong_bullish`: cluster detected + positive net, or net > $250k
   - `bullish`: net > $50k
   - `neutral`: between -$250k and +$50k
   - `bearish`: net < -$250k
   - `strong_bearish`: net < -$1M

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and pagination on the filings endpoint.

## Output mode: note

Narrative note. Insider activity for a single name is a small number
of rows (typically 50-300 in six months for an active name); a wide
table would waste space. The rendered format optimizes for a PM
reading the note once and either dismissing (no signal), noting a
cluster buy, or noting a specific insider's discretionary sale.

## Endpoints used

- `GET /stocks/filings/vX/form-4?tickers={T}&filing_date.gte={D}`
  Every Form 4 row for the ticker since `D`. Paginated; one call
  per page.

## Doesn't handle (yet)

- **Cross-ticker roll-up.** A watchlist mode ("scan my 30 names for
  cluster buys this week") would compose this skill and aggregate.
  Queued.
- **Base rate context.** No per-name "typical monthly volume of
  discretionary sales" percentile. A $2M sale is different for JPM
  than for MU. Queued.
- **Price context.** No overlay of transaction date vs price. Insider
  buys near 52-week lows are stronger signal; sales at highs are
  weaker signal. Queued as a chain with `technical-briefing`.
- **10b5-1 plan adoption date.** The endpoint returns
  `aff_10b5_one` as a boolean but not when the plan was adopted.
  Plans adopted right before a material corporate event are
  themselves a red flag; that data is in footnotes but requires
  text parsing.
- **13-D / 13-G triggers.** Not covered; those are institutional
  filings on a different form.

These are clean PR extensions. The output schema is forward-compatible.
