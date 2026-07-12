---
name: hurst-exponent
description: Estimate the Hurst exponent for a single ticker's daily log returns using rescaled-range (R/S) analysis, and classify the series as mean_reverting (H < 0.45), random_walk (H in [0.45, 0.55]), or trending (H > 0.55). Reports per-block R/S values and a block-bootstrap confidence band around H. Companion to pairs-scanner: pairs handles two-name cointegration, hurst handles single-name persistence. Answers "is this name a mean-reversion setup or a momentum setup?" Requires Stocks Basic. Runs on the free tier.
---

# hurst-exponent

You hand over a ticker. The skill pulls 2 years of daily closes,
computes log returns, runs R/S analysis across a log-spaced set of
block sizes, and fits `log(R/S) = c + H * log(n)` by OLS. H is the
slope. Classifies the series based on where H falls and adds a
bootstrap confidence band so the reader can judge whether the
classification is robust.

## Interpretation

- **H < 0.45**: **mean-reverting**. Prices push back toward a
  centerline. Pair strategies, range trading, and z-score entries
  historically have structural edge. Utilities and staples names
  tend here.
- **H in [0.45, 0.55]**: **random walk**. No persistence. Neither
  trend nor mean-reversion strategies have edge from the tape alone.
- **H > 0.55**: **trending / momentum**. Prices tend to keep going.
  Breakout strategies and trend-following have structural edge.
  Growth names in a strong run often show this.

## When to invoke

- "Is AAPL trending or reverting right now?"
- Deciding whether to use pairs-scanner or a breakout entry on a
  name
- Screening a watchlist for mean-reversion candidates before running
  z-score entries
- The user says "Hurst", "R/S", "persistence", "mean reverting or
  trending"

Not for: cross-sectional pair analysis (that's pairs-scanner). Not
for regime detection at higher frequencies (this uses daily returns;
intraday persistence would need tick data).

## What you need

- A ticker (`--ticker`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--lookback-days` (default 504, ~2 years). Longer = tighter H but
  more risk of masking a recent regime shift. Minimum 80.
- `--n-bootstrap` (default 100): block-bootstrap iterations for the
  confidence band. Set to 0 to skip.
- `--seed` (default 42): RNG seed.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON**.
`hurst_exponent`, `classification` (mean_reverting / random_walk /
trending), `reasoning`, `bootstrap` with p5/p50/p95 and n_valid,
`per_block_rs` with (block_size, rs_mean) entries showing how R/S
scales with block size, plus lookback and n_returns.

**Layer 2: rendered note**. Header + H + classification tag,
bootstrap band, per-block R/S table, one-line Take with strategy
implication.

## How it works

1. **Pull daily closes** for the ticker over `lookback_days * 1.6`
   calendar days.
2. **Log returns** = diff of log(close).
3. **Block sizes**: 12 log-spaced values from min_block=10 to
   max_block=N/4. N/4 is the standard upper bound; going higher gives
   fewer blocks per size and destabilizes the regression.
4. **R/S per block size n**:
   - Partition returns into non-overlapping blocks of length n.
   - For each block: center by mean, take cumulative sum, R = max -
     min of the cumsum, S = sample std. R/S = R/S.
   - Mean R/S across blocks.
5. **OLS on log-log**: fit `log(R/S(n)) = c + H * log(n)`. H is the
   slope.
6. **Bootstrap**: block-bootstrap (block length 20) 100 times, refit
   H each iteration, report p5/p50/p95 of the H distribution.
7. **Classify** by fixed thresholds (0.45 and 0.55) so the buckets
   are stable across runs.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and daily aggs.

## Output mode: note

Narrative note with a small per-block table. A single number (H)
plus its confidence band and per-block trace reads better as a
short structured note than a table.

## Endpoints used

- `GET /v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`
  One call per run.

## Doesn't handle (yet)

- **Multi-scale Hurst**. Only one H per run. A rolling Hurst over
  N-day windows would show regime changes; queued as a companion.
- **Detrended fluctuation analysis (DFA)**. R/S is the classic
  method; DFA is more robust to non-stationarities. Queued.
- **Fractional differencing**. If you want to trade on the estimate,
  the natural next step is fractional integration order d = H - 0.5.
  Beyond this skill's scope.
- **Cross-asset Hurst comparison**. No "AAPL's H vs sector median H."
  Queued.

These are clean PR extensions.
