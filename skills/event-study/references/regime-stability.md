# Regime stability: rolling average vs recent

A common failure mode in event studies is reading a 10-year average
and acting on it when the most recent quarter has flipped the sign.
Post-earnings drift after beats was a real factor through 2015; it
flattened to near-zero by 2022 as systematic strategies arbitraged
it away. A skill that reports the 10-year number without flagging
the regime change actively misleads the user.

## When the check runs

Only in aggregate mode, only when `n_subjects >= 8`. Cross-section
mode operates on a single period and has nothing to compare against.
Single mode handles the per-name version via the
`t_stat_vs_history` block.

## The arithmetic

```
sort subjects by event_date ascending
recent_window = last 4 subjects
recent_mean = mean(recent_window.car_t5)
full_mean = mean(all.car_t5)
full_std = std(all.car_t5)
se_full = full_std / sqrt(n)
delta_pp = recent_mean - full_mean
regime_shift = abs(delta_pp) > se_full
```

`se_full` is the standard error of the full-window mean. The
threshold "delta exceeds 1 SE of the full mean" is loose; a stricter
shop would require 2 SE. The loose threshold is deliberate: this is
a flag for the reader to look closer, not a trade signal.

## Why 4 events is "recent"

For earnings, 4 events is one full year of quarterly prints. That's
the standard rolling window in academic event studies of corporate
events. For dividend changes, 4 events is a much longer real-time
window (mature dividend payers change their dividend infrequently);
the 4-event window still works for the regime check but the
calendar timespan can be 2-4 years.

For `large_volume_spike`, 4 events is whatever the resolver returned
last. Could be 4 weeks (for a name in a hype cycle) or 4 months (for
a stable name). The check works regardless of calendar span.

## What the rendered output shows

The aggregate-mode rendered output prints both numbers:

```
Recent regime (last 4): +1.4%
Full-window mean: +0.8% (n=20)
Delta: +0.6pp (within 1 SE; no regime shift flagged)
```

or, when flagged:

```
Recent regime (last 4): -2.1%
Full-window mean: +0.8% (n=20)
Delta: -2.9pp (exceeds 1 SE; REGIME SHIFT flagged)
Take should reference recent regime, not full mean.
```

The take generator reads the flag and switches which number it
cites in the headline sentence.

## Single mode equivalent

In single mode, the per-subject `t_stat_vs_history` block plays the
same role: it tells the reader whether THIS event is consistent with
the name's prior reaction distribution. The cross-section regime
check is the same idea applied across the full event set.

## What this catches

- PEAD decay: the classic example. Post-earnings drift after beats
  ran ~1% over the academic samples (1980-2010); the post-2020
  realization is closer to zero. A 10-year average masks this.
- Dividend cut reactions: in the QE era (2010-2021) the market
  forgave dividend cuts quickly; in a higher-rate regime (post-2022)
  the same cuts produced larger and longer-lasting drawdowns.
- Volume spike resolution: in a low-vol regime, volume spikes mean-
  revert quickly; in a high-vol regime they often persist.

## What it does NOT catch

- Regime shifts at a horizon shorter than 4 events.
- Regime shifts driven by composition (e.g. the most recent 4 events
  are all from the same sector).
- Regime shifts where the magnitude changed but the sign didn't.

These need richer treatments (rolling t-stat with confidence bands,
sector-stratified comparison). v1 keeps the check simple and visible.
