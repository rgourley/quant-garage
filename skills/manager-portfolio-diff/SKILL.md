---
name: manager-portfolio-diff
description: Diff the two most recent quarterly 13-F filings for an institutional investment manager (Berkshire, Baupost, Renaissance, Pershing Square, Tiger Global, Appaloosa, Scion, etc.) using Massive's pre-parsed 13-F endpoint. Reports initiations, exits, adds (>= 25% share change), trims (<= -25%), and portfolio value change. Answers "what did Buffett/Klarman/Burry do last quarter?" Requires Stocks Basic. Runs on the free tier.
---

# manager-portfolio-diff

You hand over a filer (an alias like "berkshire" or a raw CIK). The
skill pulls the two most recent quarterly 13-F filings for that
manager, aggregates holdings by CUSIP (handles multi-manager joint
filings), and reports the quarter-over-quarter change: initiations,
exits, adds, trims, and portfolio value delta.

The Massive endpoint doesn't support an issuer-oriented lookup ("who
holds AAPL?"), so this is the correct shape for 13-F work: pick the
filer whose decisions you want to see and diff their book.

## When to invoke

- "What did Berkshire do this quarter?"
- "Is Baupost still in that name?"
- "Did Pershing Square initiate anything new?"
- Any smart-money-following workflow across a curated cohort of
  funds

Not for: "who owns AAPL?" (endpoint doesn't support issuer filter at
scale). Not for real-time positioning (13-F is quarterly and lagged
~45 days after quarter-end).

## What you need

- Either `--filer` (an alias) OR `--filer-cik` (10-digit CIK)
- `MASSIVE_API_KEY` exported
- Stocks Basic plan minimum

Known aliases:
- `berkshire` / `buffett`
- `baupost` / `klarman`
- `renaissance` / `rentech`
- `bridgewater`
- `third point` / `loeb`
- `pershing` / `pershing square` / `ackman`
- `tiger global` / `coleman`
- `scion` / `burry`
- `appaloosa` / `tepper`

For anyone else, look up their CIK at
<https://www.sec.gov/cgi-bin/browse-edgar>.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Top-level `filer_cik`, `filer_display_name`, `periods`, `summary`
(holding counts, portfolio value, activity counts), and `changes`
with per-bucket entries (initiation, exit, add, trim, unchanged).
Each entry carries `issuer_name`, `cusip`, `prior_shares`,
`current_shares`, `prior_market_value`, `current_market_value`,
`delta_shares_pct`.

**Layer 2: rendered note**. Header + activity counts, then four
buckets (NEW POSITIONS / EXITED / ADDS / TRIMS) capped at 10 entries
per bucket, sorted by market value. One-line Take highlighting the
biggest new position and biggest exit. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull all 13-F rows** for the filer via
   `GET /stocks/filings/vX/13-F?filer_cik={CIK}&limit=1000&sort=filing_date.desc`.
2. **Group by `period`** (quarter-end YYYY-MM-DD). Take the two
   most recent periods as current and prior.
3. **Aggregate by CUSIP** within each period. Sum shares and market
   value across multiple manager-of-record rows (joint filings
   produce one row per manager).
4. **Classify each CUSIP:**
   - Only in current → initiation
   - Only in prior → exit
   - In both, share change >= +25% → add
   - In both, share change <= -25% → trim
   - Otherwise → unchanged
5. **Sort each bucket by market value.** New positions and adds by
   current value; exits and trims by prior value. Renders top 10 per
   bucket with overflow count.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, pagination.

## Output mode: note

Narrative note. A single fund's 13-F is 30-300 rows typically; a
grouped-by-bucket note reads better than one wide table.

## Endpoints used

- `GET /stocks/filings/vX/13-F?filer_cik={CIK}&limit=1000&sort=filing_date.desc`
  All 13-F rows for the filer. Paginated.

## Doesn't handle (yet)

- **Fund-of-funds cohort scan.** No "top 20 hedge funds this
  quarter." A workflow composite could iterate over aliases.
- **Multi-quarter trajectory.** Only diffs two quarters. A rolling
  4-quarter cluster (add-add-add-add vs churn) would be a great
  extension.
- **Shorts / derivatives.** 13-F is longs + long options + a few
  other instruments only. Shorts don't appear.
- **Marked-to-market values.** Uses filer-reported market_value at
  the period date. Not marked to today's price.
- **Position-in-portfolio-percent.** Adds are currently ranked on
  absolute market value, not share-of-portfolio. Both matter.

These are clean PR extensions. Output schema is forward-compatible.
