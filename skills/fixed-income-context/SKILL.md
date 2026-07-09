---
name: fixed-income-context
description: Rates and credit view via ETF proxies (SHV, SHY, IEF, TLT, TIP, LQD, HYG, AGG). Reports returns across 1/5/20/60/120 day windows, price percentile vs trailing year, HYG-LQD credit spread delta and TLT-IEF duration spread delta, plus HYG-benchmark correlation. Derives a regime label (risk_off, credit_stress, goldilocks, reflation, rate_pressure, neutral). Every equity valuation implicitly assumes something about rates; this skill closes the equity-only gap without needing FRED.
---

# fixed-income-context

You hand over nothing. The skill returns the rates and credit picture
via a fixed panel of ETF proxies plus derived spread reads and a
plain-English regime label.

ETF proxies rather than raw yields so the whole thing runs on any
Massive Stocks plan. When you want actual yields, wire in FRED as
the primary source and keep this as fallback.

## When to invoke

- Any equity workflow that implicitly cares about rates or credit
  (portfolio-review, valuation-sanity-check, scan-and-frame)
- User asks "what are rates doing", "credit stress", "curve",
  "spreads widening"
- Sanity-check before a directional bond ETF trade (TLT, HYG, LQD)

## What you need

- `MASSIVE_API_KEY` (Stocks Basic minimum; 9 range-aggs calls)

## What you get back

**Layer 1 JSON** matching [`output-schema.json`](./output-schema.json).
Per-proxy returns and percentiles, spread deltas, HYG-benchmark
correlation, regime label + read, caveats.

**Layer 2 rendered brief**. Regime line + proxy table + spread block
+ correlation read + caveats. See
[`references/rendering.md`](./references/rendering.md).

## Regime labels

- `risk_off`: credit widening (HYG lagging LQD) AND TLT rallying
  (long duration bid). Classic flight-to-quality.
- `credit_stress`: HY underperforming IG, no rates confirmation yet.
- `goldilocks`: rates rallying + HY leading. Easing bid, no fear.
- `reflation`: rates selling off + HY leading. Growth on, rates hot.
- `rate_pressure`: long end selling off. Watch equity multiple
  compression.
- `neutral`: no clean signal.

## Doesn't handle (yet)

- **Not raw yields.** ETF total-return prices move inversely to
  yields for duration ETFs. FRED integration would give both.
- **HYG-LQD is a return-delta proxy for credit stress, not an OAS
  spread.** Directionally correct; not tradeable as a spread quote.
- **Regime label is heuristic.** Six-bucket classifier. A real
  regime engine is a bigger build.
