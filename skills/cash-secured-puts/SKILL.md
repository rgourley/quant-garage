---
name: cash-secured-puts
description: Screens cash-secured put candidates on one underlying and states what you are paid against what you risk. Pulls the put chain for the most liquid expiries in a DTE window, picks one strike per delta target (0.10 to 0.40), and reports per candidate the premium at the chain mid and at the bid, static and annualized yield on the cash secured, breakeven and cushion, and assignment odds three ways (chain delta, Black-Scholes N(-d2), and the empirical share of same-length historical windows that finished below the strike), plus expected P&L under the stock's own return history. Flags earnings and ex-dividend dates inside each expiry, thin open interest, and wide spreads. Three picks (conservative ~0.15 delta, balanced ~0.25, aggressive ~0.35) from the expiry nearest the horizon, and a take fitted to the stated stance. Use when the question is "which put do I sell on X, at what strike and expiry, and what am I actually being paid to take the stock." Needs Stocks Starter plus the Options Developer add-on. Not advice.
---

# cash-secured-puts

hedge-suggester prices protection against a long you already own.
options-structure-analyzer picks a structure for a directional view.
Neither answers the income seller's question: which put do I sell, at
what strike and expiry, and what am I actually being paid to take the
stock? This skill does, and it refuses to quote a yield without the
assignment odds next to it.

The assignment odds come three ways because each one lies differently.
Chain delta is the market's risk-neutral guess. Black-Scholes N(-d2)
is the same guess with the drift term made explicit. The empirical
base rate is what the stock itself did over every same-length window
in the trailing two years. When the three disagree, that gap is the
read.

## When to invoke

- The operator wants to get paid to buy a stock below the current
  price and needs the strike and expiry chosen with the risk stated
- Comparing yield across expiries on the same name (monthly vs the
  next monthly) without hand-computing annualized returns
- A wheel or income overlay where the entry leg is a short put
- The user says "cash secured put", "sell puts on", "get paid to buy",
  "CSP", "what put should I sell", "put writing", "wheel strategy"

For the covered-call side of a wheel on stock already held, use
[`hedge-suggester`](../hedge-suggester) (its covered_call structure
prices the call leg). For the options-flow read on the name, use
[`options-flow`](../options-flow). For whether now is a bad time to be
long the stock at all, chain into `preflight-trade`.

## What you need

- `--ticker` (required): the underlying
- `MASSIVE_API_KEY` exported in the environment
- Stocks Starter (underlying snapshot and daily aggregates) plus the
  Options Developer add-on (put chain snapshot with bid/ask, open
  interest, implied vol, and greeks). Same options entitlement
  options-flow and hedge-suggester need.

Optional:

- `--cash`: cash available to secure puts. Sizes contracts per
  candidate (`cash // (strike * 100)`) and totals the premium
- `--stance` (`conservative` / `balanced` / `aggressive`, default
  `balanced`): target |delta| 0.15 / 0.25 / 0.35; drives the take
- `--horizon-days` (default `45`): preferred DTE. Picks come from the
  analyzed expiry nearest this
- `--min-dte` (default `7`) and `--max-dte` (default `horizon + 30`):
  the chain window
- `--expiries` (default `3`): how many expiries to analyze, chosen by
  summed put open interest (which selects monthlies over thin weeklies)
- `--risk-free-rate` (default `0`): feeds only the Black-Scholes
  N(-d2) and the delta fallback; stated in the caveats, never assumed
- `--sleep` (default `0`): seconds between API calls; `13` on a
  rate-limited key
- `--format` (`render` / `json` / `both`)

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-expiry blocks (DTE, trading days, total OI, earnings_inside,
ex_dividends_inside), per-candidate rows (premium at mid and bid,
cash_secured_usd, static and annualized yield at mid and at bid,
breakeven, cushion_pct, max_loss_usd, delta_abs with its source,
prob_itm_bs, the `empirical` block with p_below_strike,
p_below_breakeven, expected_pnl_usd, n_windows, and per-candidate
liquidity caveats), the three `picks`, `iv_context`, `events`, the
composed `take`, `tier_caveats`, and `sources` with fetched_at per
endpoint.

**Layer 2: rendered table**: header, one table per expiry, liquidity
flags under each table, the three picks, the take, caveats. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Resolve spot** via `/v2/snapshot/locale/us/markets/stocks/tickers/{T}`
   and the shared best-price fallback chain (zero is never a price).
2. **Pull the put chain** via `/v3/snapshot/options/{T}` filtered to
   `contract_type=put`, expiries in `[min_dte, max_dte]`, strikes in
   `[0.70, 1.02] x spot`. Paginates up to 2,500 contracts.
3. **Choose expiries**: the `expiries` with the most summed open
   interest, in date order.
4. **Choose strikes**: per expiry, the put nearest each delta target
   in `{0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40}` (within 0.05).
   Chain delta when present, Black-Scholes delta from the chain IV
   when not, fixed moneyness targets when neither is available.
5. **Price and measure** each candidate: yields, breakeven, cushion,
   N(-d2), and the empirical base rate from ~2 years of daily closes
   over windows of the same trading-day length. Math in
   [`references/methodology.md`](./references/methodology.md).
6. **Context**: ATM IV vs 20-day realized (variance-risk-premium
   proxy), next earnings date via `earnings-blackout` (Benzinga with
   EDGAR fallback), ex-dividend dates inside the window.
7. **Picks and take**: per stance, the candidate nearest the stance
   delta on the expiry nearest the horizon. The take states premium,
   yields, breakeven, the three assignment odds, historical expected
   P&L, sizing, IV context, earnings placement, and the worst
   liquidity flag. Always ends "Not advice."

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, the best-price fallback chain, and the daily
  aggregates conventions.

## Output mode: table

One row per candidate with the same columns across expiries is the
whole point: yield goes up as you walk toward spot, and the
assignment columns go up with it. A stream would hide that tradeoff.

## Endpoints used

- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: spot.
- `GET /v3/snapshot/options/{ticker}`: put chain with `last_quote`,
  `open_interest`, `implied_volatility`, `greeks`.
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`: ~2 years of
  daily closes for realized vol and the empirical base rates.
- `GET /benzinga/v1/earnings` (via `earnings-blackout`, EDGAR
  fallback): next earnings date.
- `GET /v3/reference/dividends`: ex-dividend dates inside the window.

## Doesn't handle (yet)

- **Live fills.** Premiums are chain mids on a 15-min delayed tape.
  The `*_at_bid` fields carry the conservative number; real fills sit
  between.
- **True IV percentile.** IV context is ATM IV vs 20-day realized,
  not the name's own IV history.
- **The covered-call leg.** This is the put side of the wheel only.
- **Margin treatment.** Cash secured means the full strike is set
  aside. Broker margin relief on short puts is not modeled.
- **Multi-name screening.** One underlying per run. A watchlist mode
  that ranks names by yield-per-delta is a clean extension.
- **Early assignment modeling.** American-style early exercise is
  caveated, not priced.

## Not advice

The skill prices what the chain offers and states the risk in three
independent ways. It does not know your tax lot situation, your
mandate, or your view on the stock. A short put is long the stock
below the strike all the way to zero; the yield is compensation for
that exposure, not free income.
