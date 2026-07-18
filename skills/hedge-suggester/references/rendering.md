# hedge-suggester rendering (Layer 2)

Output mode: table. The skill emits canonical JSON matching
`output-schema.json`; this reference shows how that JSON renders as the
Claude Code default view.

No em-dashes anywhere. Colons, parentheses, periods.

Order of blocks:

1. Header (position and context)
2. Structures comparison table (ranked cheapest insurance first)
3. Per-structure detail (legs, greeks, tradeoff, caveats)
4. Take line
5. Not-priced line (when any structure was skipped)
6. Caveats footer

## Block 1: header

Four lines: the name and date, the position, the horizon and expiry, and
the IV context.

```
Hedge Suggester: ALLO (2026-07-18)
Spot $100.00 (lastTrade) · 1,000 shares ($100,000 notional) · 10 contracts/leg
Horizon 45d · expiry 2026-09-02 (46d out) · risk tolerance: medium
IV context: ATM IV 60% vs realized 42% = 1.43x (expensive)
```

- The spot source in parens is the price-fallback step that won
  (`lastTrade` is a live trade; `prevDay.c` is stale, flag it).
- `contracts/leg` is `shares / 100` (floor). When the position is below one
  round lot, the run caveats that the option over-hedges the stock.
- The IV-context line reports ATM IV, trailing realized vol, their ratio,
  and a label (`expensive` / `fair` / `cheap`). When the ratio is
  unavailable, render `IV context: unknown` with the reason.

## Block 2: structures table

One row per priced structure, sorted by `cost_per_dollar_protected`
ascending (cheapest insurance per dollar protected first, matching the
`ranking` array).

```
Structures (ranked cheapest insurance per $ protected first):

Structure            Net cost  % notl  Cost/$prot  Breakeven   Max loss   Max gain
----------------------------------------------------------------------------------
covered call           -$2.2K   -2.2%      -1.000     $97.80    -$97.8K      $7.2K
collar                  $1.1K   +1.1%      +0.011    $101.15     -$1.1K      $3.9K
protective put          $3.4K   +3.4%      +0.034    $103.35     -$3.4K   uncapped
ratio put spread        $2.1K   +2.1%      +0.140    $102.10   -$172.1K   uncapped
put spread              $2.2K   +2.2%      +0.223    $102.22    -$92.2K   uncapped
```

- `Net cost` is `net_cost_usd`, humanized (`$1.1K`, `$2.2M`). A credit is
  negative (`-$2.2K`).
- `% notl` is `net_cost_pct_of_notional`, signed, one decimal.
- `Cost/$prot` is `cost_per_dollar_protected`, signed, three decimals.
  Negative means the structure pays you (a credit); it ranks first but
  protects the least, so read its tradeoff.
- `Breakeven` is the conventional combined-position breakeven price.
- `Max loss` / `Max gain` are the worst / best combined-position P&L at
  expiry over a 0..3x-spot grid. `Max gain` renders `uncapped` when
  `max_gain_uncapped` is true (any structure that leaves stock upside open:
  protective put, put spread, ratio).

Note that the covered call and ratio show large max losses: neither floors
the downside. Ranking by cost alone would mislead, which is why the
tradeoff column and the per-structure caveats below the table exist.

## Block 3: per-structure detail

For each structure, in the same ranked order: the name, its legs, the net
greeks at open, the tradeoff, then any caveats prefixed `!`.

```
RATIO PUT SPREAD
  buy 10x 100P @ $3.35 · OI 900
  sell 20x 85P @ $0.62 · OI 300
  net delta 820.0 · gamma -6.0 · theta/day $0
  tradeoff: Cheapest (often a credit) over the 100 to 85 band, but the extra
  short put re-opens downside below 85: losses accelerate in a crash.
  ! TAIL RISK: net short 1 put below 85. Below that strike the position loses
  on both the stock and the naked short put; max loss is large.
  ! sell 85P: wide bid-ask 24% (above 15% ceiling); mid fill unlikely
```

- Each leg line is `{action} {qty}x {strike}{C|P} @ ${mid} · OI {oi}`.
- `net delta` is share-equivalent (stock delta is +shares). `theta/day` is
  dollars per day, humanized.
- Caveats are the per-structure `caveats` array: liquidity flags (thin OI
  below the floor, bid-ask wider than the ceiling, no two-sided quote) and,
  for the ratio, the tail-risk flag. Render each on its own `!` line.

## Block 4: take

One or two sentences recommending the structure that fits the stated risk
tolerance, with its cost and protection, the cheapest-per-protection note,
a collar/upside contrast, and the IV context. Always ends with
`Not advice.`

```
Take: For medium risk over 45d, the put spread costs 2.2% of notional and
covers the 100 to 90 band. Cheapest insurance per dollar protected is the
covered call. The collar is 1.1% cost but caps upside at 105. Protection is
expensive right now (ATM IV vs realized vol). Not advice.
```

## Block 5: not-priced line

When `skipped_structures` is non-empty, one line naming each skipped
structure and its reason:

```
Not priced: put_spread (no distinct lower put strike for spread)
```

Omit the line entirely when every structure priced.

## Block 6: caveats footer

When `tier_caveats` is non-empty:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, sorts first: a rate-limited aggregate
pull leaves the IV context unknown, which changes how to read the take.

## What UI devs do instead

A custom UI consumes the JSON directly: a comparison grid of structures
with a payoff-diagram thumbnail per row (from `protection_floor`,
`protection_ceiling`, `upside_cap`, `max_loss_usd`, `max_gain_usd`),
color-coded by whether the structure floors the downside, with the take as
a callout. The table format is the Claude Code default; UIs build their own
visual layer from the same JSON.
