# Vol-target sizing

Inverse-vol weights scaled to a target portfolio vol. The reflex
answer when a risk team asks "how do I keep the high-vol names from
eating the whole risk budget?"

## The math

Raw per-name weight: inverse of the name's annualized vol.

```
w_i_raw = 1 / σ_i
```

Normalize so Σ w_i = 1 (long-only book):

```
w_i = (1 / σ_i) / Σ_j (1 / σ_j)
```

The normalized inverse-vol book is the unscaled "vol-balanced"
allocation. Every name contributes the same per-unit-weight standard
deviation, before correlation effects.

Then scale to hit a target portfolio vol:

```
σ_port_unscaled = sqrt(w' Σ w)
scale           = target_vol / σ_port_unscaled
w_final         = scale * w
```

`σ_port_unscaled` is the portfolio vol at the normalized (Σw=1)
weights. The scale factor brings the actual portfolio vol to the
target.

## When the binding constraints actually matter

Three caps can bind on the final book, in this precedence:

1. **`leverage_cap`** (default 1.0x). If `scale > leverage_cap`,
   the target vol demands more gross than the cap allows. The book
   gets rescaled to Σ|w| = leverage_cap and the binding is reported
   as `leverage_cap`. Portfolio vol comes in below the requested
   target. This is the dominant signal that "your target_vol is
   unreachable at this cap."
2. **`max_weight`** (per-name). Hit BEFORE scaling, on the
   normalized inverse-vol weights. The low-vol names get their
   weight clipped at the cap; the excess redistributes proportionally
   to the uncapped names. Then the target_vol scaling runs on the
   capped book. The binding is reported as `max_weight` only when
   the final scaled weight on at least one name still sits at the
   cap. If target_vol scaling brought every name below the cap, the
   binding flips to `target_vol`.
3. **`target_vol`**. The clean "everything worked" case. The book
   hits the requested portfolio vol without any cap binding.

## Why inverse-vol, not inverse-variance

Inverse-vol weights every name by `1/σ`. Inverse-variance weights by
`1/σ²`. The variance-weighted version concentrates the book even more
aggressively in low-vol names, which most PMs find too defensive.
Inverse-vol is the practical compromise: low-vol names get the
larger weight, but a 2x vol difference produces a 2x weight
difference, not a 4x.

When every name has roughly the same vol, vol-target collapses to
equal weight. The method's signal value rises with the dispersion of
per-name vols across the basket.

## Why this is conservative

Vol-target ignores expected returns. If you've got a high-conviction
name that also happens to be the highest-vol one in the basket, the
method cuts your weight on that name. PMs with strong directional
views will find this frustrating; PMs with no view (or who want the
sizing to be agnostic to the view) will find it disciplined.

Pair vol-target with a separate alpha overlay if you want to
restore the directional tilt: size via vol-target, then bump the
high-conviction names up by 10-20% before reweighting.

## Edge cases

- **All-equal vols**: collapses to equal weight; target_vol scaling
  still applies.
- **One name with very low vol** (e.g., a money-market substitute at
  σ = 2%): without `max_weight`, vol-target gives that name a very
  large weight. Always set `max_weight` when basket has a wide vol
  spread.
- **`leverage_cap < 1.0`**: if `scale * Σw > leverage_cap`, the book
  is shrunk and the portfolio vol comes in BELOW target. The script
  reports `leverage_cap` as the binding constraint so the PM knows
  the target wasn't reached.
- **Target vol = 0**: raises ValueError at the lib layer. The script
  flags it as a bad CLI input before reaching the sizing call.

## What the rendered output looks like

The column header is `Vol-Target`; the footer rows show Σ|w|, port
vol, and the binding. If port vol matches target_vol exactly, no cap
bound. If it falls below, leverage_cap probably bound. If a per-name
weight sits at the max_weight cap, max_weight bound.
