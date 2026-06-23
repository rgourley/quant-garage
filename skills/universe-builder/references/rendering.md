# Rendering: universe-builder

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in table mode. Table mode is the
format Bloomberg EQS, FactSet screeners, and quant blotters use to
compare a ranked set of names: monospaced columns (or aligned markdown
table), a small separate survival funnel table, a concentration callout
block, and a single-line survivorship note at the bottom.

This is the canonical reference for any table-mode skill in the suite
(factor-research, pitch-comps). Match this format.

## Mode: table

Three distinct blocks, separated by blank lines:

1. **Filter chain header**, one line.
2. **Main results table**, monospaced or markdown.
3. **Survival funnel table**.
4. **Concentration block** (only when at least one finding exceeds the
   2σ threshold).
5. **Survivorship line**, one line.

No prose intros. No "here are some interesting findings." The reader
opens the file expecting a screen result; deliver one.

## Header

One line at the top, then a blank line before the main table:

```
Filter chain → {N} names from {M}
```

Where `{N}` is the final survivor count and `{M}` is the starting
universe size. When tier == B, append the caveat on a second line:

```
Tier B run (free Basic, curated 100-name seed). Re-run on Stocks Starter for the full pool.
```

## Main results table

Markdown table or monospaced text columns. Default columns, in order:

| Column     | Source field               | Format |
|------------|----------------------------|--------|
| `Ticker`   | `ticker`                   | Uppercase, left-aligned |
| `MCap($B)` | `market_cap / 1e9`         | One decimal, comma thousands |
| `3M Mom`   | `factors.mom_3m`           | Signed %, one decimal |
| `OCF Yld`  | `factors.ocf_yield`        | %, one decimal |
| `Opt ADV`  | `factors.opt_adv_contracts`| Humanized (`8.4M`, `120K`); blank when unavailable |
| `Z-score`  | `composite_zscore`         | Signed, two decimals |

Render the top 20 rows by default. The JSON carries the full ranked
list. Right-align all numeric columns. Cap any column header at the
width of its widest data row so values line up under the header.

If a factor wasn't included in the filter chain (e.g. `Opt ADV` on a
Stocks-only plan), drop the column entirely rather than leaving it
blank for every row. The user shouldn't have to wonder whether the
data is missing or the factor wasn't requested.

Worked column-width example:

```
Ticker   MCap($B)  3M Mom  OCF Yld  Opt ADV   Z-score
NVDA      2,840    +18.2%   1.2%    8.4M     +2.81
AVGO      1,120    +14.7%   2.1%    1.2M     +2.43
AAPL      3,100    +12.1%   3.4%    2.8M     +2.18
```

Header underscored with hyphens is **not** required; column alignment
carries the same signal in monospaced output. When emitting markdown,
use the standard `|---|` separator row.

## Survival funnel

A second small table immediately after the main results. Two columns:

```
Survival by step
| Filter                            | Survivors |
|-----------------------------------|-----------|
| Starting: US stocks ≥ $10B mcap   | 1,243     |
| + 3M momentum top quartile        | 311       |
| + OCF yield > 3%                  | 142       |
| + Options ADV > 50k contracts     | 88        |
```

Each row is one step in `filter_chain[]`, in order. The `Survivors`
column renders `cumulative_count`, not the per-step survivor count.
The starting row prepends `Starting:` to the first filter's predicate.
Subsequent rows prepend `+ ` to the predicate.

Right-align the survivor counts. Comma thousands separators.

## Concentration block

Render only when `concentration[]` contains entries that crossed the
2σ flag threshold. Skip the section entirely if the array is empty;
don't pad with "no concentration findings."

Format:

```
Concentration check
- Top 20 by Z-score: {count} {sector_or_industry} (+{std_devs}σ vs starting weight)
- Top 20 by Z-score: 3 software, 2 healthcare, 9 other
- Worth knowing before regressing on this set
```

The first bullet flags the most overweight group with its sigma count.
The second bullet enumerates the rest of the top 20 for context. The
third bullet is the standing call-out: "worth knowing before regressing"
or "watch for sector beta" depending on the screen direction; pick one,
keep it short.

## Survivorship line

Single line at the bottom:

```
Survivorship: clean. Delisted names retained for the lookback window.
```

When `survivorship.mode == 'biased'`:

```
Survivorship: biased. Current-snapshot only; backtests over this set will overstate returns.
```

When the input cohort is current-day only (no historical lookback in
the chain), the line still renders but flags `mode: clean` because the
absence of a lookback means there's nothing to bias.

## Sort order

Always sort the main results table by `composite_zscore` descending.
If the user invoked with `--sort-by mcap` or similar, the JSON's
`results[]` is re-ordered but the funnel and concentration are still
computed against the z-score-ranked top 20.

## What UI devs do instead

A custom UI consumes the JSON payload directly. A screener dashboard
typically shows the main table as a sortable, filterable grid with
click-through to each name's detail view, the survival funnel as a
sticky sidebar, and concentration as a colored callout. The rendered
format here is the Claude Code default; UIs build their own visual
layer from the same JSON.

## Why this format

Bloomberg EQS, FactSet, Koyfin, and TIKR all converged on the
columned table because:

- The columns expose the factors the screen was filtered on; the
  reader knows exactly which inputs produced the rank
- The funnel makes each step auditable: someone questioning the
  result can see how many names died at each filter
- The concentration callout prevents the most common screen mistake
  (regressing on a set that's secretly one sector)

This format is the floor for any future table-mode skill. Don't invent
a new layout; this one matches what the workflow's users already read.
