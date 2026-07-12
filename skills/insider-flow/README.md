# insider-flow

You want to know what insiders have been doing on a name. This skill
pulls SEC Form 4 filings via Massive, classifies each transaction,
separates open-market buys and non-scheduled sales from grants,
exercises, and 10b5-1 scheduled sales, detects cluster buys, and emits
a sentiment label backed by dollar flow.

The default read ignores routine comp and pre-committed 10b5-1 sales.
Those aren't signal about what management thinks of the current price.

## Quick start

### Python library

```python
from quant_garage.skills.insider_flow import run, render
payload = run("NVDA", lookback_days=180)
print(render(payload))
```

### CLI

```bash
python3 examples/run-insider-flow.py --ticker NVDA --lookback-days 180
```

### Claude Code / LLM tool use

Discovered at `skills/insider-flow/`. In a Claude Code session, ask
"any insider buying on NVDA in the last six months?" Tool-use LLMs
consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
Insider flow: NVDA · 180-day lookback · 128 Form 4 rows
Sentiment: Bearish (net conviction -$4.20M)

Transaction flow
  Conviction buys (P):          2 txns    $85.0k
  Discretionary sales (S):      6 txns    $4.29M
  Scheduled sales (10b5-1):    35 txns   $92.10M  (filtered out)
  Routine comp (A/M/F):        45 txns  (grants + exercises)

Notable open-market buys
  · 2026-05-14 · KRESS COLETTE (Officer (EVP, CFO)) · 500 sh @ $85.00 = $42.5k
  ...

Take: Insider read is Bearish: discretionary sales of $4.29M vs $85.0k in open-market buys.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the note in Claude
Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`methodology.md`](./references/methodology.md): signal vs noise,
  cluster window and threshold choices, sentiment bucket cutoffs
- [`transaction-codes.md`](./references/transaction-codes.md): SEC
  transaction code taxonomy and how each maps to signal category
- [`rendering.md`](./references/rendering.md): section order,
  sentiment label formatting, notable-transactions cap

## Plan requirement

Runs on Stocks Basic (free tier). The
`/stocks/filings/vX/form-4` endpoint is included on every Stocks
plan. One paginated call per ticker per run. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
