# hedge-suggester methodology

The IP is the structure construction, the payoff math, the
cost-per-protection ranking, and the honest accounting of what each hedge
does and does not do. Everything is priced from live chain mids against a
LONG position of `shares` shares (`n = shares / 100` contracts per leg,
floored). No em-dashes: colons, parentheses, periods.

## Conventions

- Spot `S0` from the underlying snapshot via the shared best-price chain.
- Contract multiplier is 100 shares per contract.
- Sign of net cost: a DEBIT is positive (you pay), a CREDIT is negative
  (you receive). `net_cost_usd = sum over legs of sign * mid * 100 * qty`,
  where `sign` is +1 for a bought leg and -1 for a sold leg.
- Strikes are chosen at fixed moneyness targets, then snapped to the
  nearest listed strike (below-spot for puts, above-spot for the OTM call):
  - covered-call OTM call: nearest listed strike at or above `S0 * 1.05`
  - protective / spread-upper put: nearest listed strike at or below `S0`
    (ATM / slightly OTM)
  - put-spread short put: nearest listed strike at or below `S0 * 0.90`
  - ratio short puts: nearest listed strike at or below `S0 * 0.85`

## Expiry selection

Pull the chain for expiries in `[horizon - 14, horizon + 45]` calendar days
so the horizon is bracketed even when the exact day is not a listed expiry.
The chosen expiry is the nearest listed expiry AT OR BEYOND
`today + horizon_days`; if none is at or beyond, fall back to the latest
listed expiry (and the horizon is under-covered, which the days-to-expiry
field makes visible).

## The five structures

All are overlays on a long stock position. Payoff at expiry is stated per
share; multiply by `shares`.

**Covered call.** Sell `n` OTM calls at `Kc` (~5% OTM).

- Net cost: a credit (premium received).
- The premium is a thin downside cushion; there is no floor. In a large
  drawdown the stock loss dominates.
- Upside is capped at `Kc`: above it the short call gives back gains
  one-for-one.
- Combined max gain: `(Kc - S0 + premium_per_share) * shares`, finite.
- Combined max loss: near `(S0 - premium_per_share) * shares` (stock to
  zero, cushioned by the premium).

**Protective put.** Buy `n` ATM/slightly-OTM puts at `Kp`.

- Net cost: a debit.
- Full downside floor: below `Kp` the put offsets further stock losses, so
  the combined position stops losing.
- Upside uncapped (you still own the stock): max gain reported as
  `uncapped`.
- Combined max loss: `((S0 - Kp) + put_premium_per_share) * shares` (the
  deductible from `S0` down to `Kp`, plus the premium).

**Collar.** Buy protective put at `Kp` and sell covered call at `Kc` to
finance it.

- Net cost: `put_debit - call_credit`, often near zero (a zero-cost collar
  when the call premium pays for the put).
- Downside floored at `Kp`; upside capped at `Kc`. The price of the cheap
  floor is the surrendered upside above `Kc`.

**Put spread (debit).** Buy a near-ATM put at `Kp`, sell a lower put at
`Kp_low` (~10% OTM).

- Net cost: a debit, cheaper than the outright put because the short put
  offsets part of the premium.
- Protection only over the band `[Kp_low, Kp]`. Below `Kp_low` the short
  put cancels the long put and the position is unhedged again: a crash
  blows through it.
- Combined max loss: below `Kp_low`, `((S0 - Kp_low) + net_debit_per_share)
  * shares` and growing with the stock toward zero.

**Ratio put spread.** Buy 1 put at `Kp`, sell 2 further-OTM puts at
`Kp_low` (~15% OTM).

- Net cost: cheapest of the protective structures, often a CREDIT.
- Protection over `[Kp_low, Kp]`, best right at `Kp_low`.
- TAIL RISK: below `Kp_low` the position is net SHORT one put, so it loses
  on the stock AND on the naked short put. Max loss is large and grows
  toward zero price. This is flagged as a per-structure caveat, always.

## Payoff math

Combined-position P&L at expiry at price `S`:

```
stock_pnl        = (S - S0) * shares
options_intrinsic = sum over legs of sign * intrinsic(leg, S) * 100 * qty
  where intrinsic(call, S) = max(S - K, 0)
        intrinsic(put,  S) = max(K - S, 0)
        sign = +1 bought, -1 sold
pnl(S) = stock_pnl + options_intrinsic - net_cost_usd
```

`max_loss_usd` and `max_gain_usd` are the min and max of `pnl(S)` over a
grid `S in [0, 3*S0]`. Upside is reported as `uncapped` when `pnl(S)` is
still strictly rising at the top of the grid (a short call flattens the top,
so a flat top is a CAP, not uncapped: the code tests the slope of the last
step, not whether the top equals the max).

`breakeven` is the conventional one: `S0 + net_cost_per_share`. For a debit
structure this is above spot (the stock must rise by the premium paid); for
a credit structure it is below spot (the premium collected cushions the
downside to that point).

## Greeks at open

Position greeks are stock plus option legs, in the units below. Per-contract
greeks from the chain are per-share-of-underlying, so scale by `100 * qty`
and the leg sign.

- `net_delta` (share-equivalent): `shares + sum(sign * delta * 100 * qty)`.
  A well-hedged long has net delta well below `shares`.
- `net_gamma` (per $1 move): `sum(sign * gamma * 100 * qty)`.
- `net_theta` (dollars per day): `sum(sign * theta * 100 * qty)`. Long
  options bleed theta (negative); credit structures collect it.

Greeks are point-in-time snapshots and drift as spot, vol, and time move.
They describe the position at open, not through the horizon.

## Cost per unit of downside protection (the ranking)

Structures are ranked by how cheaply they buy protection:

```
downside_protected_usd     = (protection_ceiling - protection_floor) * shares
cost_per_dollar_protected  = net_cost_usd / downside_protected_usd
```

`downside_protected_usd` is the dollar size of the INSURED region, the price
band over which the structure pays off on the way down:

- protective put, collar: `[0, Kp]` (a full floor down to zero).
- put spread, ratio: `[Kp_low, Kp]` (the band only).
- covered call: no put payoff region, so the insured amount is the premium
  cushion (`protection_ceiling = S0`, `protection_floor = S0 -
  premium_per_share`).

Lower `cost_per_dollar_protected` is cheaper insurance and ranks higher. A
credit structure has a negative value and ranks first: it pays you, but it
protects the least (covered call) or carries tail risk (ratio), so the
ranking is always read alongside the per-structure tradeoff and max-loss.
The task phrasing is "cost per dollar protected between current price and
the protection strike"; this implementation uses the insured-region width
as that denominator, which is the region where the hedge actually pays, and
documents the choice here rather than leaving it implicit.

The take line does NOT just pick the top of the ranking. It picks by risk
tolerance: low tolerance prefers a collar or protective put (a real floor);
high tolerance prefers a put spread or covered call (cheaper, partial); then
it reports the cheapest-per-protection structure and the collar's upside cap
for contrast.

## Liquidity checks

Per leg, two floors, reported as caveats (the structure is still priced so
the reader sees the cost, but flagged so they do not trust the mid):

- Open interest below `100`: thin OI, the mid may not be a real market.
- Bid-ask spread wider than `15%` of mid: `(ask - bid) / mid > 0.15`. A
  wide quote means the mid is optimistic and a real fill is worse.
- No two-sided quote: the mid falls back to the day close, flagged as an
  estimate.

## IV context (is protection expensive)

True IV percentile needs a historical IV series, which this run does not
fetch. As a proxy it compares ATM implied vol to trailing 20-day realized
vol (the variance risk premium):

```
ratio = atm_iv / realized_vol_20d
ratio >= 1.30 : expensive  (options price much more vol than delivered)
ratio <= 1.00 : cheap
otherwise     : fair
```

`realized_vol_20d` is annualized close-to-close vol from the underlying
daily aggregates (pulled with the rate-limit-aware pattern; a rate-limited
pull leaves the context unknown and is flagged loudly). When protection is
expensive, credit and spread structures that SELL vol look relatively
better; when cheap, outright protective puts look relatively better. This is
context, not a signal.

## Honest caveats

- **Mid-price fills are optimistic.** Every price is a chain mid. Real fills
  cross the spread, so live cost is worse than shown, most on the
  wide-quote legs.
- **Delayed tape.** On Options Developer the chain is ~15-min delayed.
  Quotes, OI, IV, and greeks are point-in-time snapshots, not live.
- **Earnings not fetched.** The run does not pull the earnings calendar. It
  says so and names the expiry to check against. An earnings event inside
  the horizon inflates IV and can gap the stock straight through a hedge.
- **Assignment and early exercise.** Short legs (the calls in covered
  calls and collars, the extra put in the ratio) can be assigned; American
  options can be exercised early, especially puts near or through the strike
  and around dividends.
- **Greeks are point-in-time** and drift through the horizon.
- **Ratio tail risk is real,** not theoretical: below the short strike the
  position is net short a put and losses accelerate.
- **Not advice.** This is a costed menu of standard structures, not a
  recommendation to trade. It does not know your mandate, taxes, existing
  options, or view on the stock.
