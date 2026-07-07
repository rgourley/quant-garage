# options-structure-analyzer

Given a view (direction, vol, hedge), a horizon, and a target move,
enumerate candidate options structures and rank by payoff-at-target.
A structured comparison, not a recommendation.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.options_structure_analyzer import run, render
payload = run("NVDA", view="direction_bullish",
               horizon_days=30, target_move_pct=0.08)
print(render(payload))
```

### CLI

```bash
python3 examples/run-options-structure-analyzer.py \
  --ticker NVDA --view direction_bullish \
  --horizon-days 30 --target-move-pct 0.08 --format render
```

### Claude Code / LLM tool use

Discovered at `skills/options-structure-analyzer/`. In a Claude
Code session, ask "how do I express bullish NVDA into a 30-day 8%
move with options" and Claude invokes the skill. Tool-use LLMs
consume the `run()` payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
Options Structure Analyzer — NVDA · view=direction_bullish
As of 2026-07-03 · Spot $194.83 · Target $210.42 (+8.0% move)
Expiry: 2026-07-31 (28d) · 2 structures evaluated

### Bull Call Spread
  Buy the 195 call, sell the 210 call. Cheaper than long call but caps upside at $945.
    BUY 1 x call @ $195  (O:NVDA260731C00195000) @ $8.62
    SELL 1 x call @ $210  (O:NVDA260731C00210000) @ $3.08
    Net debit: $554.90  ·  Max profit: $945.10  ·  Max loss: $554.90
    Breakeven(s): $200.55  ·  Capital req: $554.90
    P&L at target: +$945.10  (+170.3% of capital)

### Long Call
  Long call at 195. Unbounded upside above 203.62 at expiry, capped loss at premium paid ($862).
    BUY 1 x call @ $195  @ $8.62
    Net debit: $862.50  ·  Max profit: unbounded  ·  Max loss: $862.50
    Breakeven(s): $203.62  ·  Capital req: $862.50
    P&L at target: +$679.14  (+78.7% of capital)
```

## Views

- `direction_bullish` — long call, bull call spread
- `direction_bearish` — long put, bear put spread
- `vol_long` — long straddle, long strangle
- `vol_short` — short iron condor
- `hedge` — protective put, collar

## Methodology

- Entry price = chain snapshot `day.close` (falls back to `fmv`).
- Payoff-at-target evaluated at expiration assuming underlying = target.
- Ranking is payoff / capital, except hedge structures where the
  payoff line shows "vs unhedged" delta (net premium is a
  meaningless denominator for hedges).

## Plan requirement

Options Developer add-on (chain endpoint access). See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
