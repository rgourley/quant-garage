# market-regime

Daily macro context. Run the tool; it returns the regime label
(risk_on, risk_off, mixed_risk_on, mixed_risk_off, neutral) backed by
four evidence blocks: SPY trend via the 20/50/200-day SMA stack, VIX
state with percentile rank vs the trailing year, breadth proxy from
11 sector ETFs above their own 50-day / 200-day, and 20-day relative-
strength sector leadership.

Anchor every research session with this. The closest competitor (Jow
Dones) leads with the same idea; the difference here is proper sample
sizes, percentile context, no opinionated tone.

## Quick start

```bash
python3 examples/run-market-regime.py
python3 examples/run-market-regime.py --lookback-days 252 --format render
python3 examples/run-market-regime.py --format json
```

## What you get back

```
Market Regime — 2026-06-29
RISK_ON

SPY: $555.20 (+0.23% today, +1.2% 5d, +4.5% 20d) — uptrend_strong
  Above 20/50/200-day MAs

VIX: 18.4 (42nd %ile of trailing year) — normal
  20-day avg 17.9. No stress signal.

Breadth (sector ETF proxy): 8 of 11 above 50-day MA (73%)
  10 of 11 above 200-day (91%). Broad participation.

Sector leadership (20-day RS vs SPY):
  Leaders:  XLK +215bp  ·  XLC +142bp  ·  XLY +98bp
  Laggards: XLE -187bp  ·  XLP -94bp   ·  XLU -53bp

Take: Risk-on regime. SPY uptrend with broad participation and growth
sector leadership; VIX at the median signals no immediate fear. Watch
for VIX > 22 or breadth dropping below 50% as the first sign of
regime change.
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered
briefing in Claude Code or wire the JSON into your own UI / agent.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`regime-taxonomy.md`](./references/regime-taxonomy.md) — the five
  composite regimes and the evidence rules that map four block
  readings to a single label
- [`breadth-methodology.md`](./references/breadth-methodology.md) — why
  the sector-ETF proxy is good enough for a regime read, what it
  misses vs the full advance/decline line, and when to suspect it
- [`rendering.md`](./references/rendering.md) — header + four stanzas
  + adaptive take format rules

## Plan requirement

Stocks Starter ($29/mo) covers this end-to-end. 13 daily-aggs calls
per run, well under the Starter rate limit. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md). That
file is what Claude reads to decide when and how to invoke this tool.
