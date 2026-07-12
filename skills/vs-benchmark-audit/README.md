# vs-benchmark-audit

Full performance audit of a book vs benchmark with deflated Sharpe
correction and rolling IC. Answers "is this actually alpha?"

## Quick start

```bash
python3 examples/run-vs-benchmark-audit.py \
  --positions NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25 \
  --benchmark SPY
```

## Plan requirement

Stocks Basic.

## Skill spec

[`SKILL.md`](./SKILL.md).
