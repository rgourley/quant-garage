# sector-rotation-signal

Change-detection on top of market-regime. Tracks 20-day RS rank per
SPDR sector ETF over a rotation window and flags sectors moving up
or down the leadership order. Bundled with a growth/value/defensive/
rate-sensitive theme read.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.sector_rotation_signal import run, render
payload = run(rotation_window=30)
print(render(payload))
```

### CLI

```bash
python3 examples/run-sector-rotation-signal.py --format render
python3 examples/run-sector-rotation-signal.py --rotation-window 60 --format render
```

### Claude Code / LLM tool use

Discovered at `skills/sector-rotation-signal/`. In a Claude Code
session, ask "what sectors are rotating" or "is the market
rotating right now" and Claude invokes the skill. Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Sector Rotation Signal — 2026-07-02
Rotation window: 30d (2026-06-02 -> 2026-07-02) · Primary RS: 20d · Secondary RS: 60d

Theme: Risk-off rotation: defensives (Utilities, Staples, Healthcare) taking share from growth (Tech, Discretionary, Communications). Late-cycle or risk-averse positioning signal.

Sector  Name                      Rank  Δ Rank    20d RS   Δ 20d RS    60d RS  Rotation
XLV     Healthcare                   1      +3   +1223bp    +1686bp    -126bp  ↑↑ rotating in
XLF     Financials                   2      +4   +1059bp    +1661bp    -147bp  ↑↑ rotating in
XLU     Utilities                    4      +7    +594bp    +1706bp   -1408bp  ↑↑ rotating in
XLK     Technology                  10      -9    -672bp    -2324bp   +1843bp  ↓↓ rotating out
```

## Methodology

- 2+ position rank moves classify as rotation; 1-position moves are
  inside the noise floor.
- Category tags: growth (XLK/XLY/XLC), defensive (XLP/XLU/XLV),
  value_cyclical (XLE/XLI/XLB/XLF), rate_sensitive (XLRE/XLU/XLF).
- Theme read scans inflow vs outflow across categories.

## Plan requirement

Stocks Starter — SPY + 11 sector ETFs, one range-aggs call each. See
top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
