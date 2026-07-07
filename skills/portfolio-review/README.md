# portfolio-review

Composite skill. Chains market-regime, sector-rotation-signal, risk-
report, earnings-blackout, macro-event-calendar, corporate-actions-
scanner, and portfolio-rebalancer into one call. Turns the manual
6-command portfolio-review workflow into a single invocation.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.portfolio_review import run, render
payload = run(
    positions="JEPI=0.305,ALLO=0.183,BRK.B=0.163,GLD=0.145,SOFI=0.070",
    book_value=650000,
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-portfolio-review.py \
  --positions "JEPI=0.305,ALLO=0.183,BRK.B=0.163,GLD=0.145,SOFI=0.070" \
  --book-value 650000 --format render
```

### Claude Code / LLM tool use

Discovered at `skills/portfolio-review/`. In a Claude Code session,
say "review my portfolio" and provide your positions — Claude runs
the full 8-tool chain in one call. Tool-use LLMs consume the
`run()` payload matching [`output-schema.json`](./output-schema.json),
including the per-sub-skill sections.

## What you get back

```
Portfolio Review — 2026-07-05
Book: $650,000 across 5 positions (3 equities, 2 ETFs)

HEADLINE
────────────────────────────────────────────────────────────
Regime:        MIXED_RISK_ON
Rotation:      Rate-sensitive rotation: REIT/Utilities/Financials rotating in.
Portfolio vol: 24.1% · ALLO drives 68% of variance
Next earnings: SOFI (2026-07-29, 24d), BRK.B (2026-08-03, 29d)
Next macro:    CPI on 2026-07-08 (3d, very_high)
Top 8-K:       ALLO 2026-04-13 · public_offering · abn T+5 -24.2%
Rebalance:     vol 24.1% -> 15.5% · 5 trades
               Biggest trim: ALLO -$65,748 (18.3% -> 8.2%)
```

Below the headline: each section rendered in full with a titled divider.

## Methodology

Pure composition. Every fact comes from a sub-skill; nothing new is
computed. Sub-skill chain shares a single MassiveClient so daily-aggs
fetches cache across the run.

## Plan requirement

Stocks Starter minimum (for risk-report, sector-rotation-signal,
portfolio-rebalancer, options-structure-analyzer isn't in the chain).
Corporate-actions-scanner also hits SEC EDGAR (free). See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
