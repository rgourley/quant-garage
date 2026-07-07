# stock-one-pager

Beginner-friendly single-name snapshot. Composes technical-briefing +
earnings-blackout + market-regime into a plain-language card. The
retail-tier answer to "what should I know before buying this thing I
saw on social."

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.stock_one_pager import run, render
payload = run("NVDA")
print(render(payload))
```

### CLI

```bash
python3 examples/run-stock-one-pager.py --ticker NVDA --format render
```

### Claude Code / LLM tool use

Discovered at `skills/stock-one-pager/`. In a Claude Code session,
ask "what should I know about NVDA" or "give me a snapshot on
ALLO" — Claude runs the composite and returns the plain-language
card. Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
NVDA — plain-language snapshot
As of 2026-07-07 · Price $194.83

Trend:   In a fragile uptrend. Above the 200-day but below the 50-day.
Range:   Sitting mid-range vs Bollinger bands.
Vol:     Normal for this name.
Liquid:  Extremely liquid — one of the most-traded names.

Key levels
  Support:     $185 (20-day SMA), $178 (Bollinger lower)
  Resistance:  $205 (Bollinger upper), $210 (50-day SMA)

Next catalyst: Earnings on 2026-07-31 AMC (24 days out).

Market context: The overall market is leaning risk-on but
confirmation is incomplete.

What could go wrong:
- Trend is fragile; a break below $185 opens $178.
- Earnings 24 days out; expect volatility around the print.
- Broader tape has not confirmed risk-on; a regime flip hits growth
  names first.
```

## Methodology

Pure composition. Every fact comes from a sub-skill; the skill
translates each into plain language via lookup tables (`TREND_PLAIN`,
`MOMENTUM_PLAIN`, `REGIME_PLAIN`, `ADV_PLAIN`).

Sub-skill chain shares a single MassiveClient so daily-aggs fetches
cache across the run.

## Plan requirement

Stocks Starter — the sub-skills' union of daily-aggs + snapshot +
earnings resolution calls. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
