---
name: filing-sentiment
description: Score 10-K narrative sections (Business, Risk Factors) for a ticker using the Loughran-McDonald finance sentiment dictionary and report year-over-year tone shifts by category (negative, uncertain, litigious, modal-weak, modal-strong, constraining). Answers "did management's language get more defensive this year?" Uses Massive's pre-parsed 10-K sections endpoint. Requires Stocks Basic. Runs on the free tier.
---

# filing-sentiment

You hand over a ticker. The skill pulls the last two 10-K narrative
sections (Business, Risk Factors), tokenizes each, applies the
Loughran-McDonald finance sentiment dictionary (curated 900-word
negative set, 550-word litigious set, etc), and reports the tone
shift per category per section year-over-year.

The output tells you whether management's language got more
defensive, more uncertain, more litigious, or held steady. Not
clause-level meaning — a bag-of-words score with the tone shifts
flagged so a reader knows where to focus when reading the actual
section text.

## When to invoke

- A fundamental analyst asks "did AAPL's 10-K get more defensive
  this year?"
- Screening a watchlist for issuers whose litigious language jumped
  (a proxy for undisclosed legal exposure)
- Cross-reference with `risk-factor-delta`: this scores the tone,
  that identifies category-level structural changes
- The user says "10-K tone", "filing sentiment", "language shift",
  "management is getting defensive"

Not for: clause-level or sentence-level meaning. Not for sell-side
sentiment (that's news + analyst commentary). Not for 10-Q amendments.

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported
- Stocks Basic plan minimum. The
  `/stocks/filings/10-K/vX/sections` endpoint is included on every
  Stocks plan.

Optional:

- `--current-filing-date` (YYYY-MM-DD): pin the "current" filing.
  Default: most recent 10-K on record.
- `--prior-filing-date` (YYYY-MM-DD): pin the "prior" filing.
  Default: second most recent.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
`sections.current` and `sections.prior` each carry per-section
`n_tokens`, `counts` per LM category, and `rates_per_10k` (words per
10,000-word normalization). `yoy_deltas` reports per-section
per-category `prior_rate`, `current_rate`, delta, delta_pct, and a
shift label (`flat` / `noticeable` / `material` / `dramatic`).

**Layer 2: rendered note**. Per-section header with token counts +
length delta, six-row table of category scores prior vs current with
labels. One-line Take highlighting material shifts. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull 10-K sections** via
   `GET /stocks/filings/10-K/vX/sections?ticker={T}&limit=100&sort=filing_date.desc`.
   Massive returns pre-parsed plain-text extracts for Business,
   Risk Factors, and other Item 1/1A/7 sections.
2. **Group by filing_date.** Two most recent 10-Ks (or the caller-
   supplied dates) become current and prior.
3. **Score each section per filing** with the LM dictionary. Tokenize
   with `[A-Za-z][A-Za-z\-']+`, lowercase, count occurrences in each
   of six category sets: negative, uncertain, litigious, modal-strong,
   modal-weak, constraining. Normalize to words per 10,000 tokens so
   sections of different lengths are comparable.
4. **Compute YoY deltas.** Per category: absolute delta in rate,
   delta as % of prior rate, and a shift label based on the
   |delta|/current_rate ratio:
   - `flat`: |ratio| < 10%
   - `noticeable`: 10-25%
   - `material`: 25-50%
   - `dramatic`: >= 50%
   Any category whose current rate is under 10 per 10k gets `n/a`
   (sample too small to trust).
5. **Take.** Summarizes material shifts. When nothing shifted, says so.

Methodology detail in
[`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, pagination.

## Output mode: note

Narrative note with a per-section score table. A 10-K sentiment
diff is a small number of numbers (2 sections × 6 categories × 2
filings = 24 cells). Table renders cleanly.

## Endpoints used

- `GET /stocks/filings/10-K/vX/sections?ticker={T}` — pre-parsed
  narrative sections for the ticker. One paginated call.

## Doesn't handle (yet)

- **MD&A section.** Endpoint may return MD&A (Item 7); the current
  skill focuses on Business + Risk Factors. Adding MD&A is a
  drop-in change (already in the sections union).
- **Sentence-level pinpointing.** Bag-of-words. A future extension
  could highlight the top 5 sentences responsible for each category
  shift.
- **Cross-ticker peer comparison.** No "is AAPL's uncertain language
  above peer median?" Requires a peer set and a normalized score.
  Queued.
- **Trend across N filings.** Only diffs two. A three-year or
  five-year tone trajectory is a clean composite.

These are clean PR extensions. Output schema is forward-compatible.
