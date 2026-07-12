---
name: regime-audit
description: Workflow composite that runs change-point-detector + hurst-exponent on SPY plus the 11 SPDR sector ETFs. Reports per-name the last detected regime shift, current persistence classification (mean_reverting / random_walk / trending), and cross-sector summary (broad_regime_shift / localized_regime_shift / trend_dominated / mean_reversion_dominated / mixed_stable). Requires Stocks Basic. Runs on the free tier.
---

# regime-audit

Runs `change-point-detector` and `hurst-exponent` on SPY + 11 SPDR
sector ETFs. Reports a matrix view: for each name, when the last
regime shift happened, current annualized return + vol per segment,
and the Hurst persistence classification.

Answers "where has the market regime shifted, and which sectors are
in what regime right now?"

## When to invoke

- Weekly market context review
- Sector rotation prep
- "Is this a trending or mean-reverting environment?"
- The user says "regime audit", "sector regimes", "regime shift map"

## What you need

- `MASSIVE_API_KEY` exported
- Stocks Basic minimum

Optional:

- `--tickers` (default: SPY + 11 SPDR sector ETFs)
- `--lookback-days` (default 504)
- `--lambda-run` (default 250) — change-point prior mean run length

## What you get back

**Layer 1: JSON** with per-ticker `hurst`, `hurst_classification`,
`n_change_points`, `last_change_point_date`,
`last_change_point_confidence`, `current_segment` (annualized return
+ vol), `n_segments`. Top-level `by_regime` counts,
`n_shifted_recently`, `summary_verdict`.

**Layer 2: rendered note**. Header verdict + summary counts, per-name
table, one-line Take.

## Foundations used

- Composes `change-point-detector` and `hurst-exponent`
- Uses `massive-api-patterns` transitively.
