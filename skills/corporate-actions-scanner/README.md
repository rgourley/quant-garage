# corporate-actions-scanner

Scans SEC EDGAR for material 8-K filings on a ticker or watchlist and
reports each with a matched news headline (when available) plus T+1
and T+5 price reactions. Fills the retrospective-catalyst gap that
news-scanner's 24-hour default window misses.

## Quick start

Three ways to invoke. Same code, three surfaces.

### Python library

```python
from quant_garage.skills.corporate_actions_scanner import run, render
payload = run("ALLO", lookback_days=180)
print(render(payload))
```

### CLI

```bash
python3 examples/run-corporate-actions-scanner.py --ticker ALLO --lookback-days 180 --format render
python3 examples/run-corporate-actions-scanner.py --watchlist "NVDA,AAPL,ALLO,SOFI" --lookback-days 90 --format render
```

### Claude Code / LLM tool use

Discovered automatically at `skills/corporate-actions-scanner/`. In
a Claude Code session, ask "scan ALLO for material 8-Ks over the
last 6 months" and Claude invokes the skill. For any LLM that
supports tool use, the `run()` function returns JSON matching
[`output-schema.json`](./output-schema.json) — wire it in as a
tool and the agent gets the same output your CLI does.

## What you get back

```
Corporate Actions Scanner — 2026-07-02
Tickers: ALLO · Lookback 120d · material-only

4 events across 1 tickers · ranked by |T+5 reaction|

ALLO  2026-04-13  8-K item 8.01  ·  other material event  ·  flavor: public offering
  HEADLINE (GlobeNewswire Inc.): Allogene Therapeutics Announces Pricing of Public Offering of Common Stock
  REACTION: T+1 -25.5%  ·  T+5 -20.9%
  ↳ https://www.globenewswire.com/news-release/...
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Methodology

- 8-K item taxonomy is deterministic; keyword-based flavor detection
  is heuristic (title + description scan, +/- 2 day match window).
- Reactions are close-to-close; SPY-adjusted (abnormal) columns
  subtract the same-window SPY return so the signal is name-specific.
- Deduplicates same-day filings with identical item buckets.

## Plan requirement

Stocks Basic covers this end-to-end. SEC EDGAR is public, no key
needed. Massive news + daily aggs come free on the base stocks tier.
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
