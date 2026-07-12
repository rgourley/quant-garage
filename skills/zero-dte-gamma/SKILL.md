---
name: zero-dte-gamma
description: Estimate net dealer gamma exposure (GEX) for same-day-expiry (or nearest-expiry) SPY / SPX / QQQ / IWM options and identify gamma pins. Uses Black-Scholes gamma applied to reported open interest with a standard dealer positioning assumption (short customer calls, long customer puts). Reports net dealer gamma, gamma regime (long / short), gamma flip strike, and top 5 gamma pin strikes with per-side notional gamma. Motivated by 2024-25 research on how 0DTE options now drive systematic intraday moves through market-maker delta hedging pressure. Requires Options Developer.
---

# zero-dte-gamma

You hand over an underlying (default SPY). The skill pulls the options
chain snapshot for the nearest expiry, computes per-contract gamma
exposure using Black-Scholes greeks and reported open interest,
aggregates by strike, and reports the net dealer gamma regime along
with the top pins.

Motivated by 2024-25 research (Baltussen-Terhorst-Van Vliet 2024,
Bhattacharya 2024, others) documenting that 0-day-to-expiration
options now drive systematic intraday moves through market-maker
delta hedging pressure. This phenomenon didn't exist meaningfully
before 2022 (when CBOE expanded 0DTE availability); by 2024-25 it's
a first-order intraday factor.

## Interpretation

- **Long gamma regime** (net dealer gamma > 0): dealers hedge
  against price moves, compressing intraday range. Late-day chop
  typical. Sell-vol strategies favored.
- **Short gamma regime** (net dealer gamma < 0): dealers hedge with
  the market, amplifying moves. Trend days more likely, especially
  in the last hour. Buy-vol / breakout strategies favored.
- **Gamma flip strike**: the level where cumulative dealer GEX
  crosses zero. Break past it and the hedging regime changes.
- **Gamma pins**: strikes with the largest concentrated open
  interest gamma. Spot tends to gravitate toward these on expiry day.

## When to invoke

- "What's the gamma regime on SPY today?"
- Pre-market prep on an SPX / QQQ options trader's watchlist
- Sizing risk for a 0DTE strategy
- The user says "gamma flip", "gamma pins", "0DTE",
  "dealer positioning"

Not for: single-name equity options (this is calibrated to index /
ETF flow assumptions). Not for real-time (this is snapshot-based;
end-of-day is fine, intraday drift can be substantial).

## What you need

- Underlying ticker (`--underlying`, default SPY)
- `MASSIVE_API_KEY` exported
- **Options Developer** or higher entitlement. Returns a clean
  NOT_AUTHORIZED tier caveat without it.

Optional:

- `--expiration-date` (YYYY-MM-DD): pin a specific expiry. Default:
  nearest listed expiration to today.
- `--risk-free-rate` (default 0.045)
- `--default-iv` (default 0.15): fallback when Massive's greeks
  or IV field is missing on a contract.

## What you get back

Two output layers.

**Layer 1: canonical JSON**. Per-strike `call_gamma_notional`,
`put_gamma_notional`, `dealer_gex`, `cum_dealer_gex`, `call_oi`,
`put_oi`. Top-level `net_dealer_gex`, `gamma_regime`,
`gamma_flip_strike`, `top_gamma_pins` (top 5 by absolute notional),
plus `spot`, `days_to_expiration`, and full `gamma_by_strike` for
downstream consumers.

**Layer 2: rendered note**. Header + regime label + gamma flip level,
top 5 pin table, one-line Take.

## How it works

1. **Pick nearest expiry** from
   `/v3/reference/options/contracts?underlying_ticker={U}`.
2. **Fetch chain snapshot** from
   `/v3/snapshot/options/{U}?expiration_date={D}`. Massive returns
   per-contract greeks + open interest + IV.
3. **Compute gamma** per contract. Prefer Massive's returned gamma;
   fall back to Black-Scholes with the reported IV (or `default_iv`
   when missing).
4. **Cash gamma** per contract = gamma × OI × 100 × spot² / 100.
   This is dollar-gamma per 1% underlying move.
5. **Dealer positioning assumption**: short customer calls, long
   customer puts. So `dealer_gex(call) = -cash_gamma`, `dealer_gex(put)
   = +cash_gamma`. This is the standard 0DTE convention; not exact for
   any given book, but consistent across time.
6. **Aggregate per strike**, compute cumulative GEX walking from
   lowest to highest strike, find gamma-flip strike where cumulative
   crosses zero.
7. **Top pins** = strikes with the largest total notional gamma
   (call + put), sorted descending.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, and options chain snapshot.

## Output mode: note

Narrative note with a per-strike table. A single expiry chain
produces 50-200 strikes; the top-5 pin table is the digestible view.

## Endpoints used

- `GET /v3/reference/options/contracts?underlying_ticker={U}`
  (list expirations)
- `GET /v3/snapshot/options/{U}?expiration_date={D}`
  (chain snapshot with greeks + OI)
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{U}`
  (spot fallback chain)

## Doesn't handle (yet)

- **Intraday updates.** Snapshot only. For live updates, wire the
  same aggregation onto the options WebSocket.
- **Vanna and charm.** Only gamma. Second-order greeks (vanna =
  d²/dS/dσ, charm = d²/dS/dt) are the natural next layer for a
  full "dealer hedging response" model.
- **Actual dealer books.** The short-calls / long-puts assumption
  is retail-flow convention. On event days (Fed, CPI, earnings),
  actual dealer books can invert.
- **Non-index underlyings.** Calibrated for SPY / SPX / QQQ / IWM
  where the flow assumption holds. Single-name equity gamma has
  different flow dynamics.
- **rBergomi-consistent IV.** Uses reported IV as-is; a rough-vol-
  consistent IV surface would be a real research extension.

These are clean PR extensions.
