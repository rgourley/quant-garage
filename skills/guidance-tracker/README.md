# guidance-tracker

Track how management's own view of the fiscal year has evolved. This
skill pulls Benzinga Corporate Guidance history, classifies each event
as raised / lowered / reaffirmed / initiation, and groups by fiscal
period so the trajectory reads legibly.

## Quick start

### Python library

```python
from quant_garage.skills.guidance_tracker import run, render
payload = run("NVDA", lookback_days=540)
print(render(payload))
```

### CLI

```bash
python3 examples/run-guidance-tracker.py --ticker NVDA --lookback-days 540
```

## What you get back

```
Guidance tracker: NVDA · 540d lookback · 12 guidance event(s)
By action: raised: 6 · reaffirmed: 4 · initiation: 2

Timeline (most recent first)
  2026-05-28 · FY2026 Q2 · RAISED
    EPS 1.14 raised (+3.6%) · Rev $45.20B raised (+4.5%)
  2026-02-26 · FY2026 Q1 · RAISED
    EPS 0.95 raised (+2.1%) · Rev $37.40B raised (+2.7%)
...

Take: Management has been raising guidance consistently (6 raise(s), 0 cuts).
Most recent event (2026-05-28) was a raised.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Requires **Stocks Basic + Benzinga Corporate Guidance add-on** (approx
$99/mo). Without the add-on the endpoint returns NOT_AUTHORIZED and
the skill emits a clean tier caveat.

## Skill spec

[`SKILL.md`](./SKILL.md).
