# Rendering: pairs-scanner

The skill emits canonical JSON matching `output-schema.json`. This
reference describes how that JSON renders in table mode.

Table-mode conventions in this repo are inherited from
[`../../universe-builder/references/rendering.md`](../../universe-builder/references/rendering.md);
this file documents the pairs-scanner-specific layout.

## Block order

Up to four blocks, separated by blank lines:

1. Header (two lines)
2. TRADEABLE table (only when `tradeable[]` is non-empty)
3. CONSIDERED BUT REJECTED table (only when non-empty)
4. SKIPPED count line + Caveats

No prose intros. The reader opens the output expecting a screen;
deliver one.

## Header

```
Pairs Scanner: 5 names, 10 pairs, 2 tradeable (2026-07-10)
Lookback: 252 trading days (251 aligned bars) · Engle-Granger + OU half-life
```

Line 1: `Pairs Scanner: {n_basket} names, {n_pairs_total} pairs, {n_pairs_tradeable} tradeable ({as_of})`.
Line 2: `Lookback: {lookback_days} trading days ({n_aligned_bars} aligned bars) · Engle-Granger + OU half-life`.

## TRADEABLE table

Sorted by `abs(z_current)` descending. Widest spreads first: the pair
most likely to revert on the biggest move sits at the top.

```
TRADEABLE (widest spreads first)
Pair              Beta   ADF-t     p  Half-life       Z      Stability
----------------------------------------------------------------------
KO-PEP           0.912   -4.12    1%       8.4d   +2.34         stable
MO-PM            1.184   -3.58    5%      14.1d   -2.08         stable
```

Columns:

- `Pair`: `{dependent}-{independent}`. Left-aligned, width 14.
- `Beta`: `hedge_ratio_beta`. 3 decimals, right-aligned.
- `ADF-t`: `adf_tstat`. 2 decimals, right-aligned. More negative =
  more stationary.
- `p`: `cointegration_bucket` mapped to `1%` / `5%` / `10%` / `n.s.`.
- `Half-life`: `half_life_days` with `d` suffix. `n/a` when null.
- `Z`: `z_current`, signed, 2 decimals. Positive = spread wide with
  dependent above the fitted line; negative = below.
- `Stability`: `stable_ / regime_shift / insufficient_os_sample`.

When `tradeable[]` is empty, render one line instead of the table:

```
TRADEABLE: none. All considered pairs failed one or more filters (see below).
```

## CONSIDERED BUT REJECTED table

Sorted by `adf_tstat` ascending (most stationary first). The
transparency section: "here's what the scan looked at and why it
didn't recommend those." Same columns as TRADEABLE with the last two
replaced by a `Reason` column carrying the concatenated
`tradeable_rejections[]`.

```
CONSIDERED BUT REJECTED (sorted by ADF t-stat, most-stationary first)
Pair             ADF-t     p  Half-life       Z Reason
------------------------------------------------------
MDLZ-KO          -3.11   10%      42.3d   +1.20 z +1.20 below |2.0| entry threshold
```

Reason is truncated to 40 chars. Full reasons live in the JSON at
`considered_but_rejected[].tradeable_rejections`.

## SKIPPED count line

Single line:

```
SKIPPED (3 pairs failed the |rho| >= 0.6 prefilter or lacked history)
```

The full skip list lives in the JSON at `skipped_correlation[]`. The
rendered layer only surfaces the count so the reader isn't buried in
non-cointegrated pairs.

## Caveats

```
Caveats:
- Cointegration is a linear property that breaks in regime shifts (M&A, earnings, sector rotation). Half-life is the historical mean-reversion tempo, not a forecast.
- One or more pairs flagged 'regime_shift' via 70/30 out-of-sample residual std ratio. Treat those with extra scepticism even when the ADF bucket is significant.
```

From `tier_caveats`. The first line is always present. The regime-shift
line fires only when at least one pair carries the `regime_shift`
label.

## What UI devs do instead

A custom UI consumes the JSON and renders:

- A scatter of every pair with ADF t-stat on x and |z_current| on y,
  quadrants shaded by tradeable / rejected.
- A time-series chart per pair of the residual with the ±z_entry
  bands overlaid.
- A hedge-ratio table with click-through to a mock P&L over the
  in-sample window.

The rendered table here is the Claude Code default.

## Why this format

Two-section (tradeable + rejected) is the honest read for a stat-arb
screen. Showing only the tradeable pairs would hide the base rate:
the reader wouldn't know if 2 tradeable out of 10 is normal for the
basket or a suspiciously high hit rate. The rejection section shows
"here's what almost made it" and the specific filter that killed
each one, so the caller can dial thresholds knowingly.
