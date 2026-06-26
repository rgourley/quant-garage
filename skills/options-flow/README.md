# options-flow

You're scanning a watchlist for unusual options activity. Premium
size, volume vs open interest, above-ask vs below-bid, repeat-strike
clustering. Output is a tight stream of the top 10-20 prints with a
sentiment tag per block. Yesterday's run surfaced a TSLA bullish read
where someone sold $400 puts on the bid AND bought $385 calls above
the ask. Two trades, same direction.

## Quick start

```bash
python3 examples/run-options-flow.py
```

## What you get back

```
5 tickers scanned · 20 prints surfaced · Tier B (15-min delayed)

SPY   2026-06-23  $737C  SWEEP  @ $15.54
462,666 vol · $56.8M prem · >100x avg · ABOVE ASK · BULLISH

TSLA  2026-06-24  $385C  OTHER  @ $16.64
50,804 vol · $22.6M prem · >100x avg · BID side · BEARISH

NVDA  2026-06-24  $202.5C OTHER  @ $8.15
85,814 vol · $15.2M prem · >100x avg · ABOVE ASK · BULLISH
```

Every output also ships as canonical JSON matching
[`output-schema.json`](./output-schema.json). Read the rendered view
in Claude Code or wire the JSON into your own UI.

## Methodology

[`references/`](./references/) covers the analytical depth:

- [`unusual-activity-detection.md`](./references/unusual-activity-detection.md) — anomaly score, not raw volume
- [`sweep-vs-block.md`](./references/sweep-vs-block.md) — urgency vs negotiated and the read each carries
- [`directional-inference.md`](./references/directional-inference.md) — price-vs-NBBO into bullish/bearish per option type
- [`opening-vs-closing.md`](./references/opening-vs-closing.md) — new interest vs housekeeping
- [`dealer-positioning.md`](./references/dealer-positioning.md) — GEX and gamma flip (documented, not computed in v1)
- [`rendering.md`](./references/rendering.md) — stream-mode format (Cheddar Flow, FlowAlgo, Unusual Whales)

## Plan requirement

Options Developer ($79/mo) plus Stocks Starter ($29/mo) runs Tier B
(15-min delayed). Options Business and Stocks Business unlock Tier A
real-time tape with sub-second sweep detection. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

The Claude Code skill entry point is at [`SKILL.md`](./SKILL.md).
That file is what Claude reads to decide when and how to invoke this
tool.
