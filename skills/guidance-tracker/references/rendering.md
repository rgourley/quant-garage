# Rendering: guidance-tracker

Note-mode skill. Layout:

1. Header (identity + count) and "By action" summary
2. Timeline (most-recent-first, capped at 20)
3. Take + caveats

## Header

```
Guidance tracker: NVDA · 540d lookback · 12 guidance event(s)
By action: raised: 6 · reaffirmed: 4 · initiation: 2
```

The "By action" line only includes labels with non-zero count and
walks the fixed priority order (raised, lowered, reaffirmed,
initiation, mixed) so a scanning reader always sees raises first.

## NOT_AUTHORIZED case

When the account lacks the Benzinga Corporate Guidance add-on:

```
Guidance tracker: NVDA — ENTITLEMENT REQUIRED

- This key is NOT entitled to the Benzinga Corporate Guidance add-on...
```

Only the header, the caveat, and no timeline.

## Timeline

Each event renders as up to three lines:

```
  2026-05-28 · FY2026 Q2 · RAISED
    EPS 1.14 raised (+3.6%) · Rev $45.20B raised (+4.5%)
    note: "..."
```

Line 1: `{date} · FY{year} {period} · {LABEL_TAG}`. Tags:
- `RAISED` (uppercase for emphasis)
- `LOWERED` (uppercase)
- `reaffirmed`
- `initiated`
- `mixed`
- `unclear`

Line 2: EPS + Revenue on same line. Metric rendered when the current
midpoint is known. Direction shows the per-metric label (raised /
lowered / reaffirmed / initiated). Delta_pct in parentheses when
prior is known.

Line 3: `note:` line only when the event carries a `notes` field.
Truncated to ~140 chars.

Timeline cap: first 20 events. Overflow shows `... and N more`.
Full array in JSON.

## Take

- All raises, no cuts: "Management has been raising guidance
  consistently (N raise(s), 0 cuts)."
- All cuts, no raises: "Management has been cutting guidance..."
- Mixed: "Mixed guidance trajectory..."
- Only reaffirmations: "Management has been reaffirming..."
- Only initiations: "Only initiations on record..."

Followed by "Most recent event ({date}) was a {label}." when there
is a last event.

## What UI devs do instead

- Timeline chart with markers colored by label.
- Per-fiscal-period card showing trajectory as an arrow chain.
- Alert on the "just cut" event with the delta_pct as the callout.
