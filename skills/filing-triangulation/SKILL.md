---
name: filing-triangulation
description: Workflow composite that runs five filing / ownership skills on a single ticker (8-k-scanner + risk-factor-delta + filing-sentiment + insider-flow + analyst-tracker) and returns a unified fundamental report with a cross-source verdict (predominantly_constructive / predominantly_concerning / mixed / no_clear_signal). Handles entitlement gaps gracefully. Requires Stocks Basic; analyst-tracker section skipped without Benzinga Analyst Ratings.
---

# filing-triangulation

You hand over a ticker. The workflow runs five fundamental skills in
sequence and returns a unified report with a cross-source verdict:

- **`8-k-scanner`** — material events over the last 90 days
- **`risk-factor-delta`** — 10-K risk factor YoY changes
- **`filing-sentiment`** — LM tone shift on 10-K narrative sections
- **`insider-flow`** — Form 4 signal-vs-noise + cluster buys
- **`analyst-tracker`** — sell-side ratings and consensus PT

Answers "what is the full fundamental picture on this name?" — the
integrated view I've been building manually every time you asked me
to look at ALLO or NVDA.

## When to invoke

- Deep-dive on a single name before a position decision
- Pre-portfolio review triage
- Fundamental analyst prep for a call
- The user says "full fundamental read on X", "triangulate X",
  "everything you know about X"

Not for: watchlist scans (one ticker at a time; this is 5+ API
call sequences). Not for real-time (all data sources are
snapshot / filing-based).

## What you need

- A ticker (`--ticker`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum
- Optional: Benzinga Analyst Ratings entitlement (analyst-tracker
  section will emit NOT_AUTHORIZED without it and skip gracefully)

Optional:

- `--lookback-days-8k` (default 90)
- `--lookback-days-insider` (default 180)
- `--lookback-days-analyst` (default 180)
- `--exclude-directors` (pass through to insider-flow)

## What you get back

**Layer 1: canonical JSON** with the full output of each sub-skill
nested under `eight_k_scanner`, `risk_factor_delta`,
`filing_sentiment`, `insider_flow`, `analyst_tracker`. Plus a
top-level `triangulation` block with `verdict`, `bullish_signals`,
`concerns`, `other_signals`.

**Layer 2: rendered note**. Verdict header + concerns / bullish
signals list + per-sub-skill summary blocks.

## How it works

1. Runs each sub-skill in sequence with error catching. If any
   sub-skill raises (entitlement, empty data, etc.), records the
   error and moves on.
2. Cross-reads the outputs to build a triangulation:
   - Bullish signals: insider cluster buys, analyst upgrades,
     ensemble consensus PT premium, positive M&A / leadership news
   - Concerns: negative tone shift, new risk categories, insider
     selling, analyst downgrades, restatement filings
3. Verdict:
   - `predominantly_constructive`: >=2 bullish, <=1 concern
   - `predominantly_concerning`: >=3 concerns, 0 bullish
   - `mixed`: >=1 bullish AND >=2 concerns
   - `no_clear_signal`: everything else

## Fallbacks

If a sub-skill fails, the `sub_skill_errors` block reports the
failure and the triangulation is built from what remains. Missing
Benzinga analyst-tracker just drops the sell-side lens.

## Endpoints used (via sub-skills)

- `/stocks/filings/8-K/vX/disclosures`
- `/stocks/filings/vX/risk-factors`
- `/stocks/filings/10-K/vX/sections`
- `/stocks/filings/vX/form-4`
- `/benzinga/v1/ratings`

## Doesn't handle (yet)

- **Batch mode.** One ticker per run. A watchlist mode would
  iterate + summarize, but the per-ticker output is already dense.
- **Time-series triangulation.** A single point-in-time verdict;
  no rolling history.
- **Price context.** No overlay of the fundamental verdict against
  the current price / RS / vol regime.
- **Options overlay.** Not chained to options-flow or
  zero-dte-gamma.

These are clean composite extensions.
