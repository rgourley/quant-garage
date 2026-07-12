---
name: risk-factor-delta
description: Diff Item 1A Risk Factors between two 10-K filings for a name using Massive's pre-parsed and taxonomy-classified risk-factor endpoint. Reports categories added, categories removed, and categories where the supporting text materially changed (>= 25% length delta) year-over-year. Groups by primary category so the reader sees the shape of what changed, not a flat diff. Use when a PM, credit analyst, or fundamental researcher asks "what did management add to Item 1A this year?" Requires Stocks Basic. Runs on the free tier.
---

# risk-factor-delta

You hand over a ticker. The skill pulls the most recent 10-K risk-factor
disclosures and the one before it, diffs the standardized category
taxonomy, and reports what management added, dropped, and materially
rewrote year-over-year.

This is the "what changed in Item 1A?" read that fundamental analysts
do by hand. It works because Massive already parses and categorizes
risk factors from every 10-K into a three-tier taxonomy (primary,
secondary, tertiary). No NLP on our end. No EDGAR text scraping.

## When to invoke

- A fundamental analyst asks "did AAPL add anything new to Item 1A?"
- A credit analyst wants a heads-up on new balance-sheet or liquidity
  risks flagged for the first time this cycle
- A macro-driven investor scanning for regulatory / tariff / geopolitical
  risk-factor additions across a basket
- The user says "risk factor delta", "10-K diff", "what's new in Item 1A",
  "compare risk factors YoY"

Not for: single-filing risk catalog (fine as a fallback but the primary
value is the delta). Not for prose-level word-for-word diff (this is a
category-level diff with supporting text quoted for confirmation).

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Basic plan minimum. The `/stocks/filings/vX/risk-factors`
  endpoint is included on every Stocks plan.

Optional:

- `--current-filing-date` (YYYY-MM-DD): pin a specific "current" filing.
  Defaults to the most recent on record.
- `--prior-filing-date` (YYYY-MM-DD): pin a specific "prior" filing.
  Defaults to the second-most-recent on record.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Top-level: `filings.current`, `filings.prior`, and `summary` counts
(added, removed, materially changed, retained unchanged). `changes.added[]`,
`changes.removed[]`, `changes.materially_changed[]` each carry per-entry
`{primary_category, secondary_category, tertiary_category, supporting_text}`.
Materially-changed entries also include `prior_supporting_text`, both
lengths, and `length_delta_pct`.

**Layer 2: rendered narrative**. Header with the delta counts, three
sections (NEW / DROPPED / MATERIALLY CHANGED) grouped by primary
category, each with the supporting-text quote so the reader can
confirm the taxonomy call, followed by a one-line Take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull risk factors** for the ticker via
   `GET /stocks/filings/vX/risk-factors?ticker={T}&limit=50000&sort=filing_date.desc`.
   Massive returns one row per unique (primary, secondary, tertiary)
   category per filing, with a supporting-text snippet.
2. **Group by filing_date.** Each 10-K filing produces N rows all
   sharing the same `filing_date`. Sort dates descending; pick the two
   most recent as `current` and `prior` (or use the caller-supplied
   dates).
3. **Diff by category tuple.** For every (primary, secondary,
   tertiary):
   - In current only → added
   - In prior only → removed
   - In both → check supporting_text length delta. `>= 25%` flip →
     materially changed. Otherwise retained unchanged.
4. **Group results by primary category.** The primary axis is the
   headline shape ("all the new categories are financial_and_market").
   Secondary/tertiary render as bullets under it.
5. **Take.** One sentence summarizing counts and the concentration of
   new categories.

Massive's taxonomy comes from a published research paper linked in
the endpoint docs; see [`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and pagination on the filings endpoint.

## Output mode: note

Narrative note. This is a category-level diff on a small number of
rows (10-K risk factors typically 15-40 per filing); a wide table
would waste space. The rendered format optimizes for a fundamental
analyst reading the delta once, then quoting the supporting text into
a note or a call.

## Endpoints used

- `GET /stocks/filings/vX/risk-factors?ticker={T}&limit=50000&sort=filing_date.desc`
  All categorized risk factors for the ticker across every 10-K on
  record. One paginated call.

## Doesn't handle (yet)

- **Sentence-level text diff.** The skill reports a length delta as a
  "materially changed" proxy and quotes the current supporting text.
  A proper word-level diff (highlighting added/removed phrases)
  would be a clean PR extension.
- **Cross-ticker roll-ups.** A watchlist mode ("scan my 30 names for
  new regulatory risk factors YoY") would compose this skill and
  aggregate by primary_category. Queued.
- **10-Q updates.** Item 1A can be amended in a 10-Q. The endpoint
  covers annual 10-K disclosures only for the diff. 10-Q updates are
  a separate lane.
- **Historical trends.** Only diffs two filings. A "risk factor
  trajectory over N years" view would surface which categories are
  chronic vs newly-appearing; queued.
- **Peer comparison.** No "what risks does AAPL cite that MSFT doesn't?"
  yet. The taxonomy makes this trivially composable; queued as a
  separate `peer-risk-comparison` skill.

These are clean PR extensions. The output schema is forward-compatible.
