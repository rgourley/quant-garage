# Methodology: 8-k-scanner

The compute layer sits in `quant_garage/skills/eight_k_scanner.py`.
This file explains the choices, not the code.

## Why signal buckets, not raw taxonomy

Massive's disclosure taxonomy is three-tier (primary / secondary /
tertiary) and comprehensive. Comprehensive is good for machine
consumption; it's noisy for a human reading a Monday morning scan.

The bucket ranking collapses similar primary categories into a
shorter list ordered by trader/analyst importance:

| Priority | Bucket | Primary categories mapped |
|---|---|---|
| 1 | M&A / Strategic | strategic_transactions |
| 2 | Restatement / Restructuring | accounting_and_restatement, restructuring_and_bankruptcy |
| 3 | Material agreement | material_agreements |
| 4 | Regulatory / Legal | regulatory_actions, legal_and_regulatory, litigation |
| 5 | Leadership change | leadership_and_governance |
| 6 | Capital / Debt | capital_and_financing, debt_and_credit_agreements |
| 7 | Earnings / Guidance | financial_results |
| 8 | Corporate housekeeping | corporate_governance, shareholder_matters, auditor_matters |
| 9 | Other | other |

The bucket a filing lands in is determined by its **first** hit
against the ranked list, walking top to bottom. A filing that
mixes M&A language with a leadership change (common when a deal
carries a co-CEO announcement) sorts as M&A because that's the
higher-priority signal.

Ordering rationale:
- M&A tops the list because it triggers price rerating and it's
  the classic "read the 8-K right now" event.
- Restatements come next because they retroactively invalidate
  prior financials. A 10-Q restatement is a top-of-the-inbox
  event.
- Material agreements (long-term supply, license) sit above
  regulatory because they change the business's cash-flow
  profile.
- Regulatory/legal above leadership because a consent decree or
  major lawsuit reprices faster than a CFO swap.
- Leadership change matters (especially CFO/CEO) but doesn't
  usually reprice as fast as the top four.
- Capital/debt sits below leadership because most debt filings
  are routine refinancings. When they're not (going-concern
  warnings, cross-default), the underlying primary category
  usually maps to restructuring, which is higher.
- Earnings/guidance is deprioritized in this ranking because
  8.01 items around earnings are usually a subset of the
  quarterly-release flow, and the reader already has
  earnings-drilldown / event-study for that lane.

## Filing grouping

A single 8-K carries multiple tagged Items. The endpoint returns one
row per Item (unique tertiary_category). The skill groups by
`accession_number` and represents each filing as one entry with a
`categories[]` array of the deduplicated tuples.

Deduplication rule: within a filing, unique
`(primary, secondary, tertiary)` triples. The first supporting_text
seen for a triple wins; subsequent duplicates are dropped. This
matches how Massive tags items when a single Item contains multiple
disclosure snippets: only the first supports the tuple.

## Supporting text length

220-character truncation on quotes. Long enough to convey the
substance (party names, dollar amounts, dates), short enough that a
watchlist scan doesn't scroll off. The full text is in the JSON at
`filings[].categories[].supporting_text` for downstream consumers
that want more.

Full 8-K bodies live at the `/8-K/vX/text` endpoint. A
"read-the-whole-filing" companion skill is queued as a natural
extension.

## Silent tickers

The scanner names tickers in the watchlist that returned zero
disclosures ("silent") in the tier_caveats. This matters: a silent
result is a real result (nothing happened), not an omission. The
JSON lists them at `tickers[]` unconditionally so downstream
consumers can rebuild the exception-report themselves.

## What this doesn't do

- **Full-text 8-K analysis.** Uses only the categorized disclosures
  endpoint. Full bodies live at `/8-K/vX/text` and would be a
  natural companion.
- **Sentiment scoring per filing.** No pos/neg label. Loughran-
  McDonald finance dictionary scoring would be a clean PR extension
  as a `filing-sentiment` skill.
- **Base rate context.** No per-name "typical 8-K cadence over the
  last year." A serial 8-K filer (activist target, restructuring
  situation) looks different from a quiet mega cap.
- **Price reaction overlay.** Not chained to `event-study`. A
  workflow composite that combines this scanner with `event-study`
  would compute CARs around each filing by bucket.
- **Cross-ticker taxonomy correlation.** No "3 names in my basket
  just filed material agreements in the same sector." That's a
  workflow composite, not a primitive.

## Reading list

- SEC Form 8-K filing instructions:
  <https://www.sec.gov/about/forms/form8-k.pdf>
- Loughran, McDonald (2011), *When Is a Liability Not a Liability?*
  the empirical foundation for finance-domain textual analysis.
- Ben-Rephael, Da, Israelsen (2017), *It Depends on Where You
  Search: Institutional and Individual Attention on the SEC*: on
  how quickly 8-K events reprice.
