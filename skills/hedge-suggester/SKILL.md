---
name: hedge-suggester
description: Proposes concrete, live-priced option hedges against a single long position and ranks them by cost per dollar of downside protected. Takes a ticker and a position size (shares or notional), pulls the underlying price and the options chain around the horizon expiry, and constructs five standard overlays (covered call, protective put, collar, put spread, ratio put spread) priced from chain mids with net cost, breakeven, max loss, max gain, and net delta/gamma/theta at open. The take line recommends the structure that fits the stated risk tolerance. Use when a concentrated long needs a hedge and you want the actual structures and their live cost, not just "you are over-exposed." Needs Stocks Starter plus the Options Developer add-on. Not advice.
---

# hedge-suggester

risk-report tells you that ALLO is carrying most of your book's variance.
options-flow tells you what other traders are doing on ALLO. Neither one
tells you what to actually do about it. hedge-suggester closes that loop:
you hand it the position, it hands back concrete option overlays, each one
priced live off the chain and ranked by how cheaply it buys downside
protection.

This is the "so what do I do" layer. It does not forecast the stock and it
does not give advice. It prices the standard hedges a desk would reach for
against a long, states each one's cost and payoff bounds honestly, and
recommends the one that matches the risk tolerance you state.

## When to invoke

- risk-report or a concentration check has flagged a single long as the
  dominant risk in a book, and the next question is "how do I hedge it"
- A PM holds a large single-name long into an uncertain window (a horizon
  of weeks) and wants the actual protective structures priced
- The user says "hedge my ALLO", "how much would a collar on NVDA cost",
  "what's the cheapest downside protection on this position", "protect my
  gains without selling", "put spread vs protective put on TSLA"
- You want the concrete follow-through to options-flow or risk-report
  rather than another read-only diagnostic

For what other traders are doing on the name, use
[`options-flow`](../options-flow). For the book-level risk that motivates
the hedge, use [`risk-report`](../risk-report). For how the hedged book
behaves under macro shocks, chain into `portfolio-macro-scenario`.

## What you need

- `--ticker` (required): the underlying of the long position
- `--shares` OR `--notional`: the position size (notional is converted to
  shares at spot)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter (underlying snapshot and daily aggregates) plus the
  Options Developer add-on (options chain snapshot with bid/ask, open
  interest, implied vol, and greeks). This is the same options entitlement
  options-flow needs.

Optional:

- `--risk-tolerance` (`low` / `medium` / `high`, default `medium`): drives
  which structure the take line recommends
- `--horizon-days` (default `45`): protection horizon; the chosen expiry is
  the nearest listed expiry at or beyond this
- `--sleep` (default `0`): seconds between aggregate calls; use `--sleep 13`
  on a rate-limited key
- `--format` (`render` / `json` / `both`)

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-structure `legs`, `net_cost_usd` (debit positive / credit negative),
`breakeven`, `max_loss_usd`, `max_gain_usd`, `protection_floor` /
`protection_ceiling` / `upside_cap`, `downside_protected_usd`,
`cost_per_dollar_protected`, and `net_delta` / `net_gamma` / `net_theta`
at open, plus a `ranking`, an `iv_context` block, and the composed `take`.
UIs and downstream agents consume this.

**Layer 2: rendered table**: the structures sorted cheapest-insurance-first,
then per-structure legs, greeks, tradeoff, and caveats, then the take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Resolve spot** via `/v2/snapshot/locale/us/markets/stocks/tickers/{T}`
   and the shared best-price fallback chain. Size the position into shares
   and round-lot contracts.
2. **Pull the chain** around the horizon expiry via
   `/v3/snapshot/options/{T}` filtered to a strike band and an expiry
   window, then pick the nearest listed expiry at or beyond the horizon.
   Read bid/ask, open interest, implied vol, and greeks per contract (the
   same endpoint and fields options-flow uses).
3. **Construct and price five structures** against the long from chain
   mids. The structure definitions and payoff math are in
   [`references/methodology.md`](./references/methodology.md).
4. **Rank** by cost per dollar of downside protected (cheaper ranks
   higher) and label the tradeoff.
5. **Caveat** every structure (liquidity floors, tail risk) and the run
   (delayed tape, mid-price optimism, earnings not fetched, assignment /
   early-exercise, IV-vs-realized context).
6. **Compose the take**: recommend the structure that fits the stated risk
   tolerance, with its cost and protection, and a not-advice disclaimer.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, the best-price fallback chain for spot, and the
  `/v2/aggs` daily endpoint conventions.

## Output mode: table

The deliverable is a set of structures compared on the same fields (cost,
breakeven, max loss, max gain, protection), which is a table. The take
line sits under the table as the one-line recommendation. A stream would
lose the side-by-side comparison that is the whole point.

## Endpoints used

- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: underlying
  spot with the best-price fallback chain.
- `GET /v3/snapshot/options/{ticker}`: options chain snapshot with
  per-contract bid/ask (`last_quote`), open interest, implied vol, and
  greeks (delta/gamma/theta). Filtered by `expiration_date` and
  `strike_price`.
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`: underlying daily
  closes for the realized-vol / IV-context proxy. Pulled with the
  rate-limit-aware pattern.

## Doesn't handle (yet)

- **Earnings proximity.** The run does not fetch the earnings calendar; it
  says so and tells the reader to check whether an earnings date falls
  before the expiry. A Benzinga-earnings pull would close this; queued.
- **True IV percentile.** The IV context is a proxy (ATM IV vs 20-day
  realized vol), not a percentile of the stock's own historical IV, which
  needs a historical IV series. Queued.
- **Multi-expiry / calendar structures.** One expiry per run. Diagonals
  and calendars are a clean extension.
- **Optimal strike search.** Strikes are picked at fixed moneyness targets
  (5% OTM call, ATM put, 10%/15% OTM short puts), not solved for a target
  cost or delta. A solver is a v2 candidate.
- **Live fills.** Everything is priced at chain mids; real fills cross the
  spread. The output flags this loudly rather than pretending otherwise.

These are clean PR extensions. The output schema is forward-compatible.

## Not advice

This skill proposes structures and prices them from live data. It does not
know your tax situation, your mandate, your existing options, or your view
on the stock. Mid-price fills are optimistic, greeks are point-in-time,
short legs carry assignment and early-exercise risk, and the ratio put
spread carries real tail risk that the output flags explicitly. Treat the
output as a costed menu, not a recommendation to trade.
