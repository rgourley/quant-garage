# news-scanner

You want today's notable news cross-referenced against the price
reaction and sentiment. Each event ships with sentiment score,
novelty score (is this a re-run or a new angle), and a price-vs-
sentiment divergence flag. A "positive" article that the stock sold
off on means the market already knew; that's a flag worth surfacing.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.news_scanner import run, render
payload = run(watchlist="NVDA,TSLA,AAPL", hours=24)
# Retrospective analysis (ignore time window):
payload = run(watchlist="ALLO", last_n=5)
print(render(payload))
```

### CLI

```bash
python3 examples/run-news-scanner.py
python3 examples/run-news-scanner.py --watchlist "ALLO" --last-n 5 --format render
```

### Claude Code / LLM tool use

Discovered at `skills/news-scanner/`. In a Claude Code session,
ask "what news moved my watchlist overnight" or "surface the last
5 material news items on ALLO". Tool-use LLMs consume the `run()`
payload matching [`output-schema.json`](./output-schema.json).

## What you get back

```
15 events surfaced from 5 tickers · window: last 24h

NVDA  2026-06-22 22:05 ET  The Motley Fool
HEADLINE: Could Investing $10,000 in SpaceX Make You a Millionaire?
SENTIMENT: +0.70 · NOVELTY: high · REACTION: -2.4% · 1.3x baseline vol
↳ DIVERGENCE: positive sentiment, -2.4% reaction. Likely priced in.

META  2026-06-23 08:35 ET  Investing.com
HEADLINE: Google Tests Major Support as AI Spending Fears Return
SENTIMENT: -0.70 · NOVELTY: high · REACTION: +1.8% · 0.7x baseline vol
↳ DIVERGENCE: negative sentiment, +1.8% reaction. Tape says 'not as bad.'
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`news-sources-and-coverage.md`](./references/news-sources-and-coverage.md) — Massive's `/v2/reference/news` and the Benzinga firehose
- [`sentiment-scoring.md`](./references/sentiment-scoring.md) — Benzinga insights vs keyword fallback, with honest caveats
- [`novelty-detection.md`](./references/novelty-detection.md) — new angles vs the third rewrite of a 4-hour-old story
- [`price-news-divergence.md`](./references/price-news-divergence.md) — when reaction disagrees with the headline
- [`impact-ranking.md`](./references/impact-ranking.md) — the scalar score that orders the stream
- [`rendering.md`](./references/rendering.md) — stream-mode format (Bloomberg tape, Benzinga Pro, Reuters Eikon)

## Plan requirement

Stocks Basic plus Benzinga News (~$99/mo) for sentiment + 5-min
reaction. Stocks Starter unlocks 1-min reaction windows and Benzinga
insights as the precision sentiment source. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
