# filing-sentiment

Score 10-K narrative sections (Business, Risk Factors) with the
Loughran-McDonald finance sentiment dictionary and report year-over-year
tone shifts. Answers "did management's language get more defensive,
uncertain, or litigious this year?"

## Quick start

```bash
python3 examples/run-filing-sentiment.py --ticker AAPL
```

```python
from quant_garage.skills.filing_sentiment import run, render
payload = run("AAPL")
print(render(payload))
```

## What you get back

```
Filing sentiment: AAPL · 2024-11-01 → 2025-10-31

[business] prior 2,200 → current 2,300 tokens (length +5%)
  Category       Prior  Current    Δ   Δ%   Shift
  negative        45.0    48.0   +3.0   +7%  flat
  uncertain       28.0    35.0   +7.0  +25%  material up
  litigious       11.0    14.0   +3.0  +27%  material up
  ...

[risk factors] prior 9,600 → current 11,200 tokens (length +17%)
  Category       Prior  Current    Δ   Δ%   Shift
  negative        88.0    95.0   +7.0   +8%  flat
  ...

Take: Material tone shifts detected: business uncertain up 25%; business litigious up 27%.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
