# pairs-scanner

You have a basket. You want to know which two names are statistically
tethered, how wide the spread is right now, and how quickly the spread
has historically closed. This skill runs the cointegration test on
every pair, estimates the mean-reversion tempo, and flags the tradeable
ones.

This is a screen, not a strategy. It surfaces candidates with the
statistics that back the claim. Sizing, execution, and stops are yours.

## Quick start

### Python library

```python
from quant_garage.skills.pairs_scanner import run, render
payload = run(
    basket="KO,PEP,MDLZ,MO,PM",
    lookback_days=252,
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-pairs-scanner.py \
  --basket KO,PEP,MDLZ,MO,PM \
  --lookback-days 252 \
  --min-halflife 2 --max-halflife 60 \
  --z-entry 2.0
```

### Claude Code / LLM tool use

Discovered at `skills/pairs-scanner/`. In a Claude Code session, ask
"find cointegrated pairs in my consumer staples basket". Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Pairs Scanner: 5 names, 10 pairs, 2 tradeable (2026-07-10)
Lookback: 252 trading days (251 aligned bars) · Engle-Granger + OU half-life

TRADEABLE (widest spreads first)
Pair              Beta   ADF-t     p  Half-life       Z      Stability
----------------------------------------------------------------------
KO-PEP           0.912   -4.12    1%       8.4d   +2.34         stable
MO-PM            1.184   -3.58    5%      14.1d   -2.08         stable

CONSIDERED BUT REJECTED (sorted by ADF t-stat, most-stationary first)
Pair             ADF-t     p  Half-life       Z Reason
------------------------------------------------------
MDLZ-KO          -3.11   10%      42.3d   +1.20 z below entry
...

Caveats:
- Cointegration is a linear property that breaks in regime shifts.
- Half-life is the historical mean-reversion tempo, not a forecast.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the table in Claude
Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`methodology.md`](./references/methodology.md): Engle-Granger
  direction selection, MacKinnon critical values, OU half-life,
  70/30 stability split
- [`rendering.md`](./references/rendering.md): two-section table
  layout, sort rules, rejection reason surfacing

## Plan requirement

Stocks Starter ($29/mo) for unlimited REST. A 5-name basket is 5
calls per run; a 20-name basket is 20. Pair count grows as n(n-1)/2
so 20 names = 190 pair tests, all local (one API call per ticker,
not per pair). See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md). That
file is what Claude reads to decide when and how to invoke this tool.
