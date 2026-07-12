# Rendering: filing-sentiment

Note-mode skill. Layout:

1. Header (identity + filing dates or "only filing on record")
2. Per-section block: header + score table
3. Take + caveats

## Header

```
Filing sentiment: AAPL · 2024-11-01 → 2025-10-31
```

`{ticker} · {prior_date} → {current_date}`. When only one filing is
on record: `{ticker} · {current_date} (only filing on record)`.

## Per-section block

```
[risk factors] prior 9,600 → current 11,200 tokens (length +17%)
  Category       Prior  Current    Δ   Δ%   Shift
  negative        88.0    95.0   +7.0   +8%  flat
  uncertain       28.0    35.0   +7.0  +25%  material up
  litigious       11.0    14.0   +3.0  +27%  material up
  constraining    52.0    50.0   -2.0   -4%  flat
  modal_weak      18.0    22.0   +4.0  +22%  noticeable up
  modal_strong     3.0     3.0    0.0    0%  n/a
```

Categories always render in this order: negative, uncertain, litigious,
constraining, modal_weak, modal_strong. Consistent across sections and
runs.

Columns:
- `Prior`: prior filing's rate per 10,000 tokens.
- `Current`: current filing's rate.
- `Δ`: absolute difference in rate points, signed.
- `Δ%`: relative to prior rate. `n/a` when prior rate is 0.
- `Shift`: label + direction. `flat` / `noticeable up` / `material up` /
  `dramatic up` / same with `down`. `n/a` when the current rate is
  under 10 per 10k (sample too small).

## Single-filing fallback

```
Filing sentiment: LUNR · 2024-04-05 (only filing on record)

[business] n_tokens=2,150
  negative        42.0 per 10k words
  uncertain       31.5 per 10k words
  ...
```

Same six-category order. No YoY deltas.

## Take

- Material shifts detected: lists up to 6 as
  `{section} {category} {direction} {abs_pct}%`.
- No material shifts: "No material tone shifts across sections.
  Management's language held steady year-over-year."

## What UI devs do instead

- Side-by-side stacked-bar chart of category rates prior vs current
  per section, colored by category.
- Sentence-level heatmap on the raw text with LM matches highlighted.
- Peer overlay: this ticker's rates vs sector median.

The rendered note here is the Claude Code default.
