# t+1-settlement-prep

You're an ops manager looking at tonight's trades. Which ones have
settlement risk crossing this weekend? Which ones need a short-sale
locate confirmed before tomorrow's cutoff? Which ones cross an ex-
dividend date? The tool walks each trade against the US holiday and
corporate-action calendar and flags six failure modes, with a
suggested next action per flag.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.t1_settlement_prep import run, render
payload = run("examples/sample-trades.csv")
print(render(payload))
```

### CLI

```bash
python3 examples/run-t1-settlement-prep.py examples/sample-trades.csv
```

### Claude Code / LLM tool use

Discovered at `skills/t+1-settlement-prep/`. In a Claude Code
session, ask "walk tonight's trades for settlement risk". Tool-use
LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
6 of 12 trades flagged · 1 short-locate · 1 ex-div · 1 split overlap · 1 half-day

BREAK 1: AAPL BUY 1,000 · trade 2026-07-02 · settlement 2026-07-03 → 2026-07-06
  Reason:  Settlement crosses Independence Day; pushed to next business day
  Suggest: Update cash forecast; notify treasury for adjusted funding

BREAK 2: NVDA SHORT 200 · trade 2026-06-25 · settlement 2026-06-26
  Reason:  Short sale; locate confirmation required
  Suggest: Confirm locate ticket with prime broker before EOD

BREAK 4: NVDL BUY 300 · settlement 2026-06-26 · split ex-date 2026-06-26
  Reason:  3-for-1 forward split with ex-date in settlement window
  Suggest: Confirm position-reconciliation system reflects the split
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`t-plus-one-cycle.md`](./references/t-plus-one-cycle.md) — May 2024 SEC rule and the business-day walk
- [`holiday-calendar.md`](./references/holiday-calendar.md) — `/v1/marketstatus/upcoming` with NYSE/NASDAQ de-dupe
- [`ex-dividend-timing.md`](./references/ex-dividend-timing.md) — buyer entitlement under T+1 and the allocation-question flag
- [`corp-action-overlap.md`](./references/corp-action-overlap.md) — split ex-date inside the settlement window
- [`short-sale-locate-flagging.md`](./references/short-sale-locate-flagging.md) — heuristic prompt; skill can't see the locate ticket
- [`rendering.md`](./references/rendering.md) — exception-report with reason / impact / suggest per flag

## Plan requirement

Runs on free Stocks Basic. Logic-heavy, light API (one holiday call
plus two reference calls per ticker). A 20-trade file is ~41 calls,
about 10 minutes on free, seconds on any paid tier. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
