# event-study

You want to measure abnormal returns around any event class:
earnings, dividend changes, large volume spikes. Single event for
one ticker (gets you a note), the same event across many tickers
(cross-section table), or all events in a window (aggregate stats
with t-stats). Last month's run on mega-cap tech Q1 prints surfaced
that the cross-section average is negative despite all five beating
on EPS. Guidance is dominating headlines this regime.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.event_study import run, render
payload = run(
    tickers="AAPL,NVDA,MSFT,GOOGL,META",
    event_class="earnings",
    period="most_recent",
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-event-study.py --mode cross_section \
  --tickers AAPL,NVDA,MSFT,GOOGL,META --event earnings
```

### Claude Code / LLM tool use

Discovered at `skills/event-study/`. In a Claude Code session,
ask "run an event study on the mega-cap tech Q1 prints" or
"measure NVDA's abnormal return around the last earnings". Tool-use
LLMs consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Event study: AAPL,GOOGL,META,MSFT,NVDA · earnings · 5 events

| Ticker | Surprise | T+5 CAR | t-stat (vs hist) | Concur |
|--------|---------:|--------:|-----------------:|-------:|
| AAPL   |    +3.6% |   +4.1% |            +0.48 |  30/55 |
| GOOGL  |   +92.1% |  +10.6% |            +1.78 |  25/42 |
| META   |    +9.6% |  -11.5% |            -0.37 |  14/19 |
| MSFT   |    +4.9% |   -5.6% |            -1.11 |  31/55 |
| NVDA   |    +6.2% |   -5.9% |            -0.82 |  33/55 |

Cross-section: avg -1.7%, t-stat -0.42 (not sig at n=5) · ρ=+0.74 (R²=55%)
Take: Surprise explains 55% of T+5 CAR variation. Cross-section avg isn't significant.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`abnormal-returns.md`](./references/abnormal-returns.md) — AR and CAR with SPY-beta adjustment to strip market moves
- [`event-class-definitions.md`](./references/event-class-definitions.md) — resolvers for earnings, dividend changes, volume spikes
- [`cross-section-methodology.md`](./references/cross-section-methodology.md) — aggregating per-subject CARs across events
- [`regime-stability.md`](./references/regime-stability.md) — flagging when a recent regime has flipped a long-run pattern
- [`t-stat-significance.md`](./references/t-stat-significance.md) — n≥8 reliability threshold, underpowered marker
- [`rendering.md`](./references/rendering.md) — hybrid (note / table / summary) keyed off the `mode` discriminant

## Plan requirement

Stocks Starter ($29/mo) runs Tier B (reaction-sign bucketing).
Add Benzinga Earnings (~$99/mo) for Tier A surprise % and beat/miss
bucketing. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
