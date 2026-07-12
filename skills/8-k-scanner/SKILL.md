---
name: 8-k-scanner
description: Scan SEC 8-K disclosures across a single ticker or a watchlist using Massive's pre-parsed disclosure taxonomy. Groups the underlying rows by filing (one 8-K carries N tagged Items), ranks by signal bucket (M&A / Restatement / Material agreement / Regulatory / Leadership change / Capital / Earnings / Corporate housekeeping / Other), and surfaces the highest-signal filings at the top with the supporting text quoted. Use when a PM or analyst asks "what material events hit my names this week?" Requires Stocks Basic. Runs on the free tier.
---

# 8-k-scanner

You hand over a ticker or a watchlist. The skill pulls every 8-K
disclosure filed against those issuers over the lookback window,
groups the taxonomy rows into filings (one 8-K = many tagged Items,
one accession number), ranks by signal bucket (M&A / Restatement /
Material agreement / Regulatory / Leadership change / Capital /
Earnings / Corporate housekeeping / Other), and surfaces the
high-signal filings first with the supporting text quoted.

This is the "what materially happened this week" read a PM does by
skimming SEC filings each morning. It works because Massive already
parses and taxonomically classifies every Item in every 8-K into a
three-tier taxonomy (primary, secondary, tertiary). No text NLP on
our side.

## When to invoke

- A PM asks "any material 8-Ks on my watchlist this week?"
- A trader wants a Monday-morning M&A scan across a sector basket
- A credit analyst wants to catch restatements, going-concern
  disclosures, or debt-covenant events across a portfolio
- The user says "8-K scan", "material events", "any deals or
  leadership changes", "did anyone in my book file an 8-K"

Not for: single-item drill-down on one specific 8-K (use the
underlying `/8-K/vX/text` endpoint or read the filing on EDGAR).
Not for the full 8-K narrative (this quotes supporting text at
~220 chars per Item).

## What you need

- A ticker or watchlist (`--tickers`, required, comma-separated)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Basic plan minimum. The
  `/stocks/filings/8-K/vX/disclosures` endpoint is included on
  every Stocks plan.

Optional:

- `--lookback-days` (default 30): calendar-day window back from today.
- `--categories`: comma-separated primary_category values to filter
  to (e.g. `strategic_transactions,leadership_and_governance`).

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-filing block includes `accession_number`, `filing_date`,
`tickers`, `filing_url`, and every tagged `categories[]` tuple
(`primary`, `secondary`, `tertiary`, `supporting_text`). Top level
gives `by_bucket` (M&A / Leadership / etc counts), `by_ticker`
(per-name filing count with bucket breakdown), and
`by_primary_category` (raw category counts).

**Layer 2: rendered note**. Header + by-signal one-liner + filings
grouped by signal bucket in priority order, most-recent-first
within a bucket. Each filing lists its tagged Items with the
supporting text quoted. One-line Take at the end. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull disclosures** for the watchlist via
   `GET /stocks/filings/8-K/vX/disclosures?tickers.any_of={T1,T2,...}&filing_date.gte={D}&limit=1000&sort=filing_date.desc`.
   Massive returns one row per (accession, tagged Item) so a single
   8-K with three Items produces three rows sharing an
   `accession_number`.
2. **Group by accession_number.** Union of tagged tuples per filing;
   deduplicated `(primary, secondary, tertiary)` triples with the
   supporting text preserved.
3. **Assign a headline signal bucket** based on the first primary
   category the filing hits from the ranked bucket list. Buckets in
   descending priority: M&A / Strategic → Restatement / Restructuring
   → Material agreement → Regulatory / Legal → Leadership change →
   Capital / Debt → Earnings / Guidance → Corporate housekeeping →
   Other.
4. **Sort filings** by bucket priority first, then filing date
   descending within a bucket. The reader sees the highest-signal
   filings up top and can stop reading once they hit routine items.
5. **Take.** One line summarizing which signal buckets fired.

Taxonomy reference: full primary/secondary/tertiary list at
`/stocks/taxonomies/vX/disclosures`. See
[`references/methodology.md`](./references/methodology.md) for the
signal-bucket ranking.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and pagination on the filings endpoint.

## Output mode: note

Narrative note. A watchlist-scale scan produces a small number of
filings (typically 5-50 for 30 days on 5-15 tickers); a wide table
would lose the supporting-text quotes that let a reader triage the
filing without opening EDGAR.

## Endpoints used

- `GET /stocks/filings/8-K/vX/disclosures?tickers.any_of={T}&filing_date.gte={D}`
  All 8-K disclosure rows for the ticker set in the window.
  Paginated; one call per page.

## Doesn't handle (yet)

- **Full 8-K text.** The skill uses the `disclosures` endpoint
  (categorized Item excerpts). Full plain-text 8-K bodies live at
  `/stocks/filings/8-K/vX/text` and would be a natural companion
  for a "read the whole filing" flow.
- **Sentiment scoring on 8-K text.** No positive/negative label per
  filing. Loughran-McDonald finance dictionary scoring would be a
  clean PR extension for a `filing-sentiment` skill; queued.
- **Base rate context.** No per-name "typical 8-K cadence." An
  activist target that files 3 8-Ks in a week is different from
  AAPL doing the same. Queued.
- **Cross-reference to price reaction.** A chain with `event-study`
  would compute the abnormal-return distribution around each 8-K by
  category. Queued.
- **Watchlist-of-watchlists.** No group naming or per-group summary
  yet. Callers who want to run against 3 sector baskets do 3 runs.

These are clean PR extensions. The output schema is
forward-compatible.
