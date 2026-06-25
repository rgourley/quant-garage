# Rendering: event-study

The skill emits canonical JSON matching `output-schema.json`. The
`mode` discriminant on the JSON picks the rendered shape:

| mode | rendered shape | analog |
|---|---|---|
| `single` | sell-side note | `earnings-drilldown` SKILL.md |
| `cross_section` | table + footer | `pitch-comps`, `universe-builder` |
| `aggregate` | summary stats block | new in this skill |

The Claude Code default is the rendered form. A custom UI consumes
the JSON and builds its own visual layer.

## Mode: single

A one-page sell-side note. Bold take, event window block, historical
comparison block. The reader scans the take in 2 seconds and finds
any supporting number in 5 more.

### Header

```
{TICKER} · {event_label} · {event_date} {session}
```

Where `event_label` is class-specific:
- `earnings`: `{fiscal_period} {fiscal_year} earnings` (e.g. `Q1 FY2027 earnings`)
- `dividend_changes`: `dividend {direction} ${prior_amount} -> ${new_amount}` (e.g. `dividend hike $0.04 -> $0.05`)
- `large_volume_spike`: `volume spike (z={z_score:.1f})`

Session uses `BMO`, `AMC`, `Intraday`, or omits if `unknown`.

### Take

Single bold line, derived from the strongest signal:

```
Take: {take}
```

Patterns:
- **Reaction extreme** (|z_score| >= 1.5 AND prior_n >= 8):
  `+{ar_t5_pct}pp abnormal return over T+1 to T+5; t-stat {z}, significant.`
- **Reaction concurs with prior pattern**:
  `+{ar_t5_pct}pp abnormal return, in line with prior {prior_n}-event mean of +{prior_mean}%.`
- **Reaction inverts prior pattern**:
  `+{ar_t5_pct}pp abnormal return, inverts prior {prior_n}-event mean of -{prior_mean}% (z={z}).`
- **Underpowered**:
  `+{ar_t5_pct}pp abnormal return; {prior_n} prior events too few for significance test.`

### Event window block

```
Event window (SPY-adjusted)
- T0 close:    ${t0_close}
- T+1 close:   ${close_t1} ({raw_t1}, market {spy_t1}, abnormal {ar_t1})
- T+3 close:   ${close_t3} ({raw_t3}, market {spy_t3}, abnormal {ar_t3})
- T+5 close:   ${close_t5} ({raw_t5}, market {spy_t5}, CAR {car_t5})
```

All return columns formatted as signed percentages with one decimal.

### Historical comparison block

Only render when `t_stat_vs_history` is non-null.

```
Historical comparison (last {prior_n} {ticker} {event_class} reactions)
- Mean T+5 CAR:        {prior_mean}
- Median:              {prior_median}
- Std dev:             {prior_std}
- This event:          {this_t5_car} ({percentile:.0%} pct, {z}σ {above|below} mean)
```

When `direction_concurrence` is non-null (earnings only):

```
- Direction concur:    {direction_concurrence} priors aligned with surprise sign
```

When `underpowered: true`, append a single line:

```
Note: prior_n < 8, distribution test is underpowered.
```

## Mode: cross_section

A comparison table (one row per ticker), then a cross-section footer.
Matches the `universe-builder` and `factor-research` tabular style.

### Header

```
Event study: {ticker_list} {event_class} · {period_label} · {n_subjects} events
```

### Table

```
| Ticker | {Class column} | T+1 Abn | T+5 CAR | t-stat (vs history) | Concur |
|--------|---------------:|--------:|--------:|--------------------:|-------:|
```

The `Class column` varies:
- `earnings` Tier A: `Surprise` (formatted `+5.2%`)
- `earnings` Tier B: `Reaction sign` (`pos`/`neg`)
- `dividend_changes`: `Change` (`+25%`)
- `large_volume_spike`: `Volume z`

The `Concur` column shows `direction_concurrence` for earnings;
omitted for other classes.

When `t_stat_vs_history.underpowered` is true for a row, suffix the
t-stat with `*` and add a footer note: `* underpowered (n<8)`.

Rows are sorted by `ticker` alphabetically. Different shops sort by
`T+5 CAR` descending; a UI can re-sort. The default alphabetical
order is reproducible.

### Cross-section footer

```
Cross-section
- Avg T+1 abnormal:    {mean_ar_t1}
- Avg T+5 CAR:         {mean_t5_car}
- Median:              {median_t5_car}
- t-stat (avg vs 0):   {t_stat_avg_vs_zero:.2f} ({significant ? 'significant' : 'not significant'} at n={n_subjects})
- Surprise vs reaction ρ: {rho:.2f} (R² = {r_squared:.0%})
```

The last line is omitted when `surprise_reaction_correlation` is
null (non-earnings classes or Tier B fallback).

### Footer take

A single paragraph (1-2 sentences) at the bottom:

```
Take: {take}
```

Pattern: cite the strongest cross-sectional signal. If
`surprise_reaction_correlation.rho > 0.5 AND n >= 5`, lead with
that. If `significant AND mean > 0`, cite the average. If neither
fires, default to a factual non-call.

## Mode: aggregate

Only the summary stats. No per-subject detail in the rendered output
(it's all in the JSON for UIs).

### Header

```
Event study: {ticker_list} · {event_class} · {from_date} to {to_date} · {n_subjects} events
```

### Summary block

```
Aggregate abnormal returns (SPY-adjusted)
- T+1 avg:   {mean_ar_t1} (median {median_ar_t1}, t-stat {t_t1:.2f}, n={n})
- T+3 avg:   {mean_ar_t3} (median {median_ar_t3}, t-stat {t_t3:.2f}, n={n})
- T+5 avg:   {mean_t5_car} (median {median_t5_car}, t-stat {t_t5:.2f}, n={n})
- T+5 distribution: p10={p10} p25={p25} p50={p50} p75={p75} p90={p90}
```

### Regime check (when populated)

```
Regime check
- Recent (last 4 events): {recent_mean_t5_car}
- Full window mean: {full_mean_t5_car}
- Delta: {delta_pp} ({regime_shift_flag ? 'REGIME SHIFT' : 'within 1 SE'})
```

When flagged, the take cites the recent regime number.

### Surprise correlation (earnings, Tier A only, when populated)

```
Surprise vs reaction
- Pearson ρ: {rho:.2f}
- R²:        {r_squared:.0%}
- n:         {n}
```

### Take

```
Take: {take}
```

For aggregate mode the take generator picks from:
- `significant cross-section`: `Event class has tradeable signal: avg CAR +{x}%, t-stat {t}, n={n}.`
- `regime shift`: `Regime has shifted: recent 4 events avg {x}% vs full-window {y}%. Cite recent, not headline.`
- `not significant`: `No tradeable signal at n={n}: avg CAR {x}%, t-stat {t}.`
- `correlation only`: `Surprise explains {rsq}% of cross-section variation, but mean reaction itself isn't significant.`

## Tier caveats (all modes)

When `tier == "B"`, append at the bottom:

```
Tier B caveats
- {caveat 1}
- {caveat 2}
```

The caveats come from the JSON's `tier_caveats[]` array verbatim.

## Number formatting rules (all modes)

- All percentages have an explicit sign (`+1.4%`, `-0.3%`). Use the
  ASCII minus `-`, not the en-dash or unicode minus.
- T-stats and z-scores: 2 decimal places (`1.83`, `-0.42`).
- Prices: 2 decimal places, comma thousands (`$1,234.56`).
- N values: integer, no comma below 1000 (`n=8`, `n=1,250`).
- No em-dashes anywhere. Use colons, parentheses, or periods.
- No emojis.

## Why hybrid

Event studies have three legitimate consumption surfaces. A PM
asking about one print needs a note. A trader scanning a basket
needs a table. A quant testing whether the class is a real factor
needs the aggregate. One JSON, three renderers, the same compute
underneath.
