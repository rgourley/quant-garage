---
name: preflight-trade
description: Before-you-execute sanity check on a single ticker + intended action (buy, sell, add, reduce, exit). Composes technical-briefing + earnings-blackout (14d) + news-scanner (last N) + corporate-actions-scanner (90d) into a verdict (go, wait, review) plus red/green flag lists. Use when the operator is about to execute a trade and wants a fast honest read on whether now is a bad time.
---

# preflight-trade

Single-ticker preflight check. You hand over ticker + action, get
back a verdict (go, wait, review) plus red/green flags derived from
four sub-skills.

Not a recommendation to trade or not trade. A structured "is now
obviously a bad time" gate. If the tool says WAIT, the flags explain
why. If GO, the flags list what's supporting.

## When to invoke

- Before hitting buy/sell/reduce on a specific name
- "Preflight NVDA buy", "should I trim ALLO", "any red flags on X"
- Fills the "is now a bad moment" moment

## Actions supported

`buy`, `sell`, `add`, `reduce`, `exit`. The action tilts the verdict
threshold: `buy` and `add` require fewer red flags to warrant WAIT
than `sell` or `exit` (bias toward defense on entry).

## Verdict logic

- **GO**: 2+ green flags AND <=1 red flag
- **WAIT**: 3+ red flags AND <=1 green flag, OR (buy/add + 2+ reds)
- **REVIEW**: everything else (mixed signals, human judgment needed)

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Verdict, red/green flag arrays, four sub-skill payloads.

**Layer 2 rendered note**. Verdict block on top, flags, then each
sub-skill's own render below. See
[`references/rendering.md`](./references/rendering.md).

## How it works

Runs 4 sub-skills for the ticker, then applies deterministic rules
in `_build_verdict()`:
- technical-briefing: trend regime, RSI, ATR
- earnings-blackout: pending print
- news-scanner: recent sentiment tilt
- corporate-actions-scanner: material 8-K in the last 90d
