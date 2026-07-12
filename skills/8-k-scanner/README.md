# 8-k-scanner

You have a watchlist. You want to know what material events hit those
names this week: M&A, restatements, executive departures, credit
agreements, guidance changes. This skill pulls SEC 8-K filings via
Massive's pre-parsed disclosure taxonomy, groups by filing, ranks by
signal bucket, and surfaces the highest-signal filings at the top with
the supporting text quoted.

## Quick start

### Python library

```python
from quant_garage.skills.eight_k_scanner import run, render
payload = run("NVDA,RKLB,AAPL,MSFT", lookback_days=30)
print(render(payload))
```

### CLI

```bash
python3 examples/run-8-k-scanner.py \
  --tickers NVDA,RKLB,AAPL,MSFT \
  --lookback-days 30
```

Add `--categories strategic_transactions,leadership_and_governance`
to filter to a specific bucket.

### Claude Code / LLM tool use

Discovered at `skills/8-k-scanner/`. In a Claude Code session, ask
"any material 8-Ks on my mega-cap basket this week?" Tool-use LLMs
consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
8-K scan: NVDA,RKLB,AAPL,MSFT · 30d lookback · 4 filings (12 disclosure rows)
By signal: M&A / Strategic: 1 · Leadership change: 1 · Earnings / Guidance: 2

[M&A / Strategic]
  2026-06-29 · RKLB · accession 0001753926-26-001085
    · strategic transactions > deal agreements > merger agreement
      "On June 28, 2026, Rocket Lab Corporation ... entered into an
       Agreement and Plan of Merger with Iridium Communications Inc..."
    · strategic transactions > deal agreements > acquisition agreement
      "..."

[Leadership change]
  2026-07-02 · NVDA · accession 0001045810-26-000060
    · leadership and governance > executive leadership > executive officer appointment
      "On July 1, 2026, the Company appointed Nicholas Parker, age 55,
       as Executive Vice President, Worldwide Field Operations..."
    · leadership and governance > executive leadership > executive officer departure
      "..."
...

Take: 1 strategic-transaction filing in the window (top of the report).
1 leadership-change filing.
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the note in Claude
Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`methodology.md`](./references/methodology.md): signal-bucket
  ranking, filing grouping rules, quote length choice
- [`rendering.md`](./references/rendering.md): section order, bucket
  headers, supporting-text truncation

## Plan requirement

Runs on Stocks Basic (free tier). The
`/stocks/filings/8-K/vX/disclosures` endpoint is included on every
Stocks plan. One paginated call per run regardless of watchlist size
(uses `tickers.any_of` filter). See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
