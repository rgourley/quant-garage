# analyst-tracker

Track sell-side positioning on a name. Pulls Benzinga analyst ratings,
classifies each event, aggregates the latest per firm, and reports
consensus PT + buy/hold/sell distribution.

## Quick start

```bash
python3 examples/run-analyst-tracker.py --ticker NVDA --lookback-days 180
```

```python
from quant_garage.skills.analyst_tracker import run, render
payload = run("NVDA", lookback_days=180)
print(render(payload))
```

## What you get back

```
Analyst tracker: NVDA · 180d lookback · 47 rating event(s)
By action: upgrade: 6 · downgrade: 2 · reiteration: 30 · PT: 8 · initiation: 1
Latest per firm (32): Buy 28 (88%) · Hold 3 (9%) · Sell 1 (3%)
Consensus PT (median of latest per firm): $195.00 (low $120.00, high $425.00)

Timeline (most recent first)
  2026-06-05 · China Renaissance (Jack Zhou) · initiated · buy
    PT $319.00
  2026-06-02 · Needham (N. Quinn Bolton) · reiterated · buy
    PT $270.00 → $270.00 (+0.0%)
  ...

Take: Sell-side net-bullish over the window: 6 upgrades vs 2 downgrades.
Consensus PT $195.00 across 32 firms.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic + Benzinga Analyst Ratings entitlement. Returns a clean
NOT_AUTHORIZED caveat when the entitlement is missing.

## Skill spec

[`SKILL.md`](./SKILL.md).
