---
name: single-name-vs-sector
description: Split one name's relative strength into two clean legs, name vs its sector ETF and sector vs benchmark, so you can tell whether the name is strong because its sector is strong or because it is pulling away from its own sector. Maps the ticker to its SPDR sector ETF (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) with a --sector override for any name. Computes name-vs-sector, sector-vs-benchmark, and name-vs-benchmark RS in basis points across 5/20/60/120-day windows, a divergence score, and a take that classifies the name as leading its sector, lagging its sector, or diverging. Use when a single name looks strong or weak vs SPY and you need to know if it is name-specific or sector-driven. Requires Stocks Starter (Free Basic works, only three series per run).
---

# single-name-vs-sector

relative-strength tells you a name is a stable laggard versus SPY. It does
not tell you why. SOFI reading -4100bp versus SPY over 120 days looks like
a broken name until you notice its sector XLF was a leader (+377bp over
20d). The weakness was name-specific, not a financials problem. Reading two
separate relative-strength runs and doing the subtraction in your head is
exactly the kind of eyeballing this tool removes.

You hand over one ticker. The skill maps it to its SPDR sector ETF, then
measures the name against its sector and the sector against the benchmark
over several windows. The take says whether the name is leading its sector,
lagging its sector, or diverging (moving opposite to its sector), with the
magnitude and the windows driving it.

This is not alpha. It is descriptive math that separates a name-specific
move from a sector move, so an LLM does not have to guess whether "SOFI is
weak" means SOFI or means financials.

## When to invoke

- A single name looks strong or weak versus SPY and the question is
  whether that is the name or its whole sector
- Following up a `relative-strength` run where one name stands out and you
  want to attribute the move
- The user says "is this the name or the sector", "is NVDA leading or is
  it just XLK", "why is SOFI lagging", "is the weakness name-specific",
  "how is this name doing versus its peers"

For ranking a whole watchlist versus one benchmark, use
[`relative-strength`](../relative-strength). This skill is the focused
single-name attribution; relative-strength is the watchlist ranker.
relative-strength with `--include-sectors` surfaces sector context
alongside the names; this skill isolates one name against its own sector
and states the divergence explicitly.

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported in the environment
- Any stocks tier (three US-listed series per run: name, sector ETF,
  benchmark)

Optional:

- `--benchmark` (default `SPY`): the sector-vs-market leg denominator
- `--windows` (default `5,20,60,120`): RS lookback windows in trading days
- `--sector` (e.g. `XLK`): sector ETF override. Required when the ticker
  is not in the built-in map; also lets you re-map a name to a different
  sector proxy.
- `--sleep`: seconds between calls. Rarely needed for three series; use
  `--sleep 13` when batching many names on Free Basic.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-window `name_vs_sector_bps`, `sector_vs_benchmark_bps`, and
`name_vs_benchmark_bps` (all basis points), the per-leg five-bucket trend
labels, a `divergence` block (signed score, unsigned composite, sector and
name averages), a `classification`, and a composed `take`. UIs and
downstream agents consume this.

**Layer 2: rendered note**: a three-row RS table (name vs sector, sector
vs benchmark, name vs benchmark), then the divergence block, then the
take. See [`references/rendering.md`](./references/rendering.md).

## How it works

1. **Map the ticker to its sector ETF** from the built-in map of large US
   names, or from `--sector`. Unknown ticker with no override is a clear
   error telling the user to pass `--sector`.
2. **Pull daily aggregates** for the name, the sector ETF, and the
   benchmark over `max(windows) * 1.6 + 14` calendar days, via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
3. **Compute three RS legs** per window in basis points:
   `name_vs_sector = (name_return - sector_return) * 10_000`,
   `sector_vs_benchmark = (sector_return - spy_return) * 10_000`,
   `name_vs_benchmark = (name_return - spy_return) * 10_000`. Each leg
   also gets a five-bucket trend label (the relative-strength scheme).
4. **Score the divergence**: the name-vs-sector RS averaged across windows
   (signed) is the divergence score; the mean of its absolute values is
   the composite magnitude.
5. **Classify and compose the take**: `diverging` when the name-vs-sector
   move and the sector-vs-benchmark move point opposite ways; otherwise
   the sign of the divergence score (`leading its sector` /
   `lagging its sector`). The map, the score, and the rule live in
   [`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, and the `/v2/aggs` daily endpoint conventions.

## Output mode: note

The deliverable is a short attribution read, not a wide sortable table.
One name, three RS legs, a divergence score, and a one-sentence take: a
note is the right canvas. For the many-names table, use relative-strength.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily closes per series. Three calls per run (name, sector ETF,
  benchmark).

## Doesn't handle (yet)

- **Custom peer baskets.** The sector ETF is a cap-weighted proxy for the
  peer group, not a hand-picked peer set. A name can read as diverging
  from its sector when it is really diverging from the ETF's largest
  holdings. Per-name custom peer baskets are queued.
- **Multi-sector names.** Each ticker maps to one sector ETF. Conglomerates
  that straddle two sectors get a single best-fit mapping; a blended
  sector benchmark is queued.
- **Volume and risk adjustment.** Same gaps as relative-strength: no volume
  confirmation and no vol-adjusted RS. Clean PR extensions; the schema is
  forward-compatible.
