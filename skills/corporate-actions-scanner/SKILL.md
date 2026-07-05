---
name: corporate-actions-scanner
description: Scan for material 8-K corporate actions over a lookback window. For a ticker or watchlist, pulls SEC EDGAR 8-K filings, filters to material items (offerings, private placements, splits, spin-offs, buybacks, M&A, restatements), cross-references Massive news for the headline, and computes T+1 and T+5 price reactions. Complements news-scanner (general) and earnings-drilldown (item 2.02 only) by giving material corporate actions a dedicated surface. Use when running a portfolio review, sanity-checking why a name moved months ago, or asking "what happened to this stock?" that news-scanner's 24-hour default missed.
---

# corporate-actions-scanner

You hand over a ticker or watchlist and a lookback window (default 180
days). The skill pulls every 8-K filing from SEC EDGAR in the window,
classifies each into a materiality bucket, cross-references Massive
news for the headline, and computes the T+1 and T+5 price reactions.

Motivated by a live portfolio review that missed an ALLO public
offering (87.5M shares at $2, 34% dilution) because news-scanner
defaulted to a 24-hour window and the offering was 78 days old.
Nothing else in the toolkit surfaced it.

## When to invoke

- The operator is running a portfolio review and wants retrospective
  corporate-action coverage across the book
- "Why did X move" for an unexplained gap that predates the news-
  scanner default window
- Pre-trade hygiene on a name: is there a pending offering, spin-off,
  or M&A announcement in the last quarter that changes the setup
- The user says "material events", "corporate actions", "8-K scan",
  "offerings", "buybacks", "M&A on my book"

## What you need

- `MASSIVE_API_KEY` for news + reactions (Stocks Basic is sufficient)
- SEC EDGAR is public and requires no key

## What you get back

**Layer 1 canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-event: filing date, 8-K items, materiality buckets, matched news
headline (when found), T+1 and T+5 close-to-close reactions.

**Layer 2 rendered stream** ranked by |T+5 reaction|. Header per event
with ticker + date + items + buckets, headline underneath when matched,
reaction line, optional URL to the source article. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Fetch ticker metadata** for the CIK (SEC EDGAR is CIK-indexed).
2. **Pull 8-K filings** from `data.sec.gov/submissions/CIK{CIK}.json`,
   filter to the lookback window, drop non-material items.
3. **Fetch news window** via `/v2/reference/news` for the same window,
   used to cross-reference the 8-K with a headline.
4. **Pull daily aggs** covering the filing dates + a T+5 buffer.
5. **Match news to filing** within +/- 2 days, preferring same-day and
   the article with the most flavor-keyword hits.
6. **Detect flavor** (public_offering, atm_offering, private_placement,
   share_repurchase, special_dividend, stock_split, spin_off,
   acquisition_announcement, acquisition_target, restatement) via a
   curated keyword list in the news title + description.
7. **Compute T+1 and T+5 reactions** close-to-close from the filing
   date.
8. **Dedupe** filings with the same ticker, date, and item-bucket set
   (SEC sometimes emits duplicate 8-Ks for the same corporate action).

## Item taxonomy

Material items surfaced by default (see `ITEM_TAXONOMY` in the
implementation): 1.01, 1.02, 1.03, 2.01, 2.03, 2.04, 2.05, 2.06, 3.01,
3.02, 3.03, 4.01, 4.02, 5.01, 5.03, 7.01, 8.01.

Excluded from default surface (routine 8-Ks that outnumber material
ones ~10:1): 2.02 (earnings — earnings-drilldown handles), 5.02
(officer/director change), 5.05 (ethics code), 5.07 (vote results),
5.08 (shareholder nomination). Pass `material_only=False` to include
these.

## Endpoints used

- `GET https://data.sec.gov/submissions/CIK{CIK}.json` (SEC EDGAR)
- `GET /v2/reference/news?ticker={T}&published_utc.gte={from}` (Massive)
- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}` (Massive)
- `GET /v3/reference/tickers/{T}` (Massive, for CIK)

## Doesn't handle (yet)

- **Reactions are not SPY-adjusted.** T+1 and T+5 mix name-specific
  and market moves. event-study does this properly; port pending.
- **Match ambiguity on multi-hit windows.** When multiple news articles
  hit within the +/- 2 day window and all contain flavor keywords, the
  match picks the closest by date. Rare but real.
- **Private-placement lag.** Some material actions surface in 8-K days
  or weeks after they happened; the tool reports the filing date, not
  the effective date.
