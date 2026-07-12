# Methodology: risk-factor-delta

The compute layer sits in `quant_garage/skills/risk_factor_delta.py`.
This file explains the choices, not the code.

## Why the taxonomy is trustworthy

The endpoint returns each risk factor tagged with a three-tier category
(`primary_category`, `secondary_category`, `tertiary_category`) plus a
`supporting_text` snippet from the filing that motivated the label.
The taxonomy comes from Massive's published research paper (linked in
the endpoint docs at `https://arxiv.org/pdf/2601.15247`).

For this skill we treat the labels as authoritative. That means the
diff logic operates on category tuples, not on raw text similarity.
Two consequences:

1. **Rephrases in the same category don't count as "added."** If AAPL
   swaps out the exact wording of its "supply chain concentration"
   risk in the 10-K, the primary/secondary/tertiary tuple is
   unchanged, and the skill classifies it as either "retained
   unchanged" or "materially changed" depending on the length delta.
2. **A genuinely new category is a real signal.** Management rarely
   adds a wholly new risk category unless something changed in the
   business. New categories are the headline of every run.

We surface the supporting text so the reader can confirm the label
quickly. If a label looks off, the reader has the source snippet
inline; no need to open the 10-K.

## Filing selection

Default: two most recent filings on record for the ticker, sorted by
`filing_date` descending. This is almost always the last two 10-K
annual filings. Every row for a given filing shares one `filing_date`
value, so grouping is a one-liner.

Override: pass `--current-filing-date` and/or `--prior-filing-date`
(YYYY-MM-DD) to pin specific filings. Useful for backfilling analysis
of a historical restatement or for comparing this year's 10-K to
an earlier filing that skipped a year.

When only one 10-K is on record (new IPOs, recently-covered issuers),
the skill degrades to a single-filing category catalog with a caveat.
It still emits structured JSON so a downstream agent can pick up the
change later.

## Diff logic

For each unique `(primary_category, secondary_category, tertiary_category)`
tuple across both filings:

- Present only in current → **added**
- Present only in prior → **removed**
- Present in both:
  - `|current_len - prior_len| / max(current_len, prior_len) >= 0.25`
    → **materially_changed**
  - otherwise → **retained_unchanged**

The 25% length threshold is judgment, not statistics. It caught most
of the genuine edits in back-of-envelope testing on mega caps
(guidance-related rewrites, new regulatory concerns, expanded
liquidity language). Setting the threshold higher (e.g., 40%) missed
edits that a reader would notice on skim; lower (e.g., 15%) flagged
paraphrase drift.

Callers who need finer control can filter the JSON on
`changes.materially_changed[].length_delta_pct` directly.

## Grouping by primary_category

The render sections all group by `primary_category` and sort primary
sections by count descending. The primary axis is the headline shape
of the change:

- "Every new category is `financial_and_market`" says something
  different than "new categories are spread across five primaries."

Secondary and tertiary render as breadcrumbs under each primary
section, humanized at render time (underscore → space). JSON keeps
underscored form for stability.

## What the endpoint doesn't cover

- **10-Q Item 1A amendments.** Companies can update Item 1A in a
  quarterly filing. The endpoint returns rows from every filing
  (including 10-Qs when they carry risk-factor updates), but the
  skill's diff logic assumes the two most recent filings are both
  10-Ks. For issuers with 10-Q amendments in the mix, the
  auto-selection may compare a 10-K to a 10-Q amendment; caller can
  pin dates explicitly if needed.
- **Historical trajectory.** Two filings only. A "risk factor
  trajectory over N years" view would surface chronic vs newly-
  appearing categories; queued as a separate skill.
- **Peer set comparison.** No "what risks does AAPL cite that MSFT
  doesn't?" The taxonomy makes this trivially composable; queued as
  `peer-risk-comparison`.
- **Word-level diff.** Length delta is a proxy for material change,
  not the change itself. A sentence-level or word-level diff on the
  supporting text would be a clean PR extension. In the meantime the
  JSON carries `prior_supporting_text` and `current_supporting_text`
  side-by-side so a caller who wants a real diff can compute one.

## Reading list

- Massive research paper on the risk-factor taxonomy:
  <https://arxiv.org/pdf/2601.15247>
- SEC Item 1A guidance: <https://www.sec.gov/rules/final/33-8591.pdf>
- Kravet & Muslu (2013), *Textual Risk Disclosures and Investors'
  Risk Perceptions*: the empirical foundation for treating risk
  factor changes as informative.
