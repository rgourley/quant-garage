# macro-event-calendar

Forward calendar of the macro releases that move the whole tape.
Sibling to earnings-blackout: earnings covers single names, this
covers FOMC / CPI / NFP / ISM / GDP / PCE / JOLTS / claims / retail
sales / sentiment.

## Quick start

```bash
python3 examples/run-macro-event-calendar.py --format render
python3 examples/run-macro-event-calendar.py --window-days 60 --format render
python3 examples/run-macro-event-calendar.py --events "FOMC,CPI,NFP" --format render
```

## What you get back

```
Macro Event Calendar — 2026-07-02
Forward window: 30d · Benchmark SPY · Historical reactions: 730d lookback

17 events across 13 dates

Date             ET  Event                        Impact   Mean|Δ|   Median     p90     n
2026-07-03~  08:30  Non-Farm Payrolls              ★★★★     1.15%    0.92%   1.92%    22
2026-07-08~  08:30  CPI                            ★★★★     0.98%    0.53%   1.58%    24
2026-07-29   14:00  FOMC Rate Decision             ★★★★     0.67%    1.25%   1.40%     4

Crowded days (3):
  2026-07-03: 2 events — Non-Farm Payrolls, ISM Services PMI
  2026-07-08: 2 events — CPI, FOMC Minutes
```

`~` marks pattern-derived dates that should be verified against the
official calendar.

## Methodology

- FOMC dates hardcoded from the official 2026 schedule.
- Other events pattern-derived (NFP = 1st Friday, CPI = 2nd Wednesday,
  ISM Mfg = 1st business day, PCE = last business day - 5, etc.).
- Historical reactions are unconditional |1-day SPY move|. Regime-
  dependent behavior (CPI hits harder in inflation regimes) not
  reflected in v1.

## Plan requirement

Stocks Basic. One SPY range-aggs call for the history window. See
top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
