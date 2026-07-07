# portfolio-mark

You need end-of-day marks for a position book. Run the tool. It pulls
the snapshot per name, walks the fallback chain (last trade →
snapshot last → minute close → day close → prior close), reports
per-position confidence (high/medium/low), and flags any name where
the mark looks stale or the spread is wide enough to need a manual
check. Two modes: delayed REST for end-of-day reports, live
WebSocket for intraday.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.portfolio_mark import run, render
payload = run("examples/sample-book.csv")
print(render(payload))
```

### CLI

```bash
python3 examples/run-portfolio-mark.py examples/sample-book.csv
```

### Claude Code / LLM tool use

Discovered at `skills/portfolio-mark/`. In a Claude Code session,
ask "mark my book to current fair value" and pass your positions.
Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
Book marked: 2026-06-24 04:27 UTC · 7 positions · Tier A (live)

| Ticker | Shares |    Mark | Source     | Confidence |  Unrealized P&L |
|--------|--------|---------|------------|------------|-----------------|
| AAPL   |    100 | $294.28 | last_trade | low        |      +$14,428   |
| NVDA   |    200 | $200.04 | last_trade | low        |      +$34,308   |
| TSLA   |     50 | $381.54 | last_trade | low        |      +$10,077   |
| SPY    |    200 | $733.58 | last_trade | low        |      +$44,716   |

Book value: $316,776 · Unrealized P&L: +$122,226

FLAGGED: AAPL · $294.28 · 638bps spread · last trade 8h 27m stale
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`snapshot-fallback-chain.md`](./references/snapshot-fallback-chain.md) — last_trade → snapshot.last → minute_close → day_close → prev_close
- [`confidence-scoring.md`](./references/confidence-scoring.md) — three inputs, three buckets
- [`book-value-and-pnl.md`](./references/book-value-and-pnl.md) — mark math, shorts, missing cost basis, mixed-source books
- [`live-vs-delayed.md`](./references/live-vs-delayed.md) — when to use each mode and what each gives up
- [`websocket-mark-updates.md`](./references/websocket-mark-updates.md) — the mark-keeping state machine on top of the socket
- [`rendering.md`](./references/rendering.md) — hybrid-mode marked table + exception block + live trailer

## Plan requirement

Stocks Starter ($29/mo) runs delayed mode end-to-end. Live mode
needs Stocks Advanced ($199/mo) for T-channel ticks, or Stocks
Business for the FMV / AM fallback channels. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
