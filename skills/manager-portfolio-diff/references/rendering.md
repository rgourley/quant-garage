# Rendering: manager-portfolio-diff

Note-mode skill. Layout:

1. Header (identity + periods)
2. Portfolio-value + activity counts line
3. Four buckets (NEW POSITIONS / EXITED / ADDS / TRIMS) capped at 10 each
4. Take + caveats

## Header

```
Manager portfolio diff: Berkshire Hathaway (Warren Buffett) · 2026-03-31 → 2026-06-30
Holdings: 41 → 39 · Portfolio value: $282.30B → $291.10B (+3.1%)
Activity: +2 init  -4 exit  ^3 add  v6 trim  ~28 unchanged
```

Line 1: display name (from alias resolution or "CIK {N}") + prior
and current period.
Line 2: holdings-count delta + portfolio value delta.
Line 3: activity counts with fixed prefix symbols so a scanner can
parse quickly.

## Buckets

Each populated bucket renders. Empty buckets skip entirely (avoids
empty-line clutter for filers with no exits, etc).

```
[NEW POSITIONS] (2)
  · CONSTELLATION BRANDS INC                    $1.20B (11,500,000 sh)
  · OXY (OCCIDENTAL PETROLEUM)                  $850.0M (14,500,000 sh)

[ADDS (>= 25%)] (3)
  · CHEVRON CORP                                $2.10B (55,000,000 sh, +32.0%)
  · APPLE INC                                   $180.00B (900,000,000 sh, +25.0%)
  ...
```

Columns:
- Issuer name (truncated to 40 chars).
- Market value (right-aligned): current for initiations/adds, prior
  for exits/trims.
- Shares in parens.
- For adds/trims: additionally the share-change percentage.

Cap: 10 per bucket. Overflow shows `... and N more`. Full array in
JSON.

## Take

- Both initiations and exits: "Biggest new position: X at $Y.
  Biggest exit: Z ($W)."
- Only initiations: "Biggest new position: X at $Y."
- Only exits: "Biggest exit: X ($Y)."
- Neither: "No initiations or exits this quarter; only add/trim
  adjustments."

## What UI devs do instead

- Sankey diagram of dollars flowing between positions QoQ.
- Per-holding time series across N quarters.
- Overlay against SPY / benchmark to show sector rotation shape.
