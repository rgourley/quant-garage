# cash-secured-puts methodology

The IP is stating a put seller's yield next to three independent
measures of the assignment risk, and refusing to show one without the
other. Everything is priced from the chain snapshot; nothing is
fabricated. No em-dashes: colons, parentheses, periods.

## Conventions

- Spot `S0` from the underlying snapshot via the shared best-price
  chain. Zero is rejected at every step.
- Contract multiplier 100. One short put obligates you to buy 100
  shares at the strike.
- Premium `P` is the chain mid (`last_quote.midpoint`, else the
  bid-ask average, else `day.close`, else `fmv`; the source is recorded
  per candidate). The bid is carried alongside because a seller
  receives the bid, not the mid.
- `DTE` is calendar days from the run's `as_of` to the expiry.

## Expiry selection

Pull puts with expiries in `[min_dte, max_dte]` (default `[7, horizon +
30]`) and strikes in `[0.70, 1.02] x S0`. Sum open interest per expiry
and keep the top `expiries` (default 3) in date order. Summed OI picks
monthlies over thin weeklies without a hardcoded calendar; the count of
puts per expiry and the total OI are reported so the choice is
auditable.

## Strike selection

Per expiry, one put per delta target in `{0.10, 0.15, 0.20, 0.25,
0.30, 0.35, 0.40}`, taking the listed put whose |delta| is nearest the
target and within 0.05 of it. Duplicates collapse (two targets landing
on the same strike produce one row). Delta comes from the chain greeks;
when the chain carries none, from Black-Scholes on the chain IV; when
neither is available, the fallback is fixed moneyness targets `{0.97,
0.95, 0.92, 0.90, 0.87, 0.85, 0.80} x S0` within 3%.

## Yield math

Per contract, strike `K`:

```
cash_secured_usd   = K * 100
premium_usd        = P * 100
static_yield       = premium_usd / cash_secured_usd
annualized_yield   = static_yield * 365 / DTE
breakeven          = K - P
cushion_pct        = (S0 - breakeven) / S0
otm_pct            = (S0 - K) / S0
max_loss_usd       = (K - P) * 100          (stock to zero)
```

The same yields at the bid are `static_yield_at_bid` and
`annualized_yield_at_bid`. Annualization is simple scaling, not
compounding, and assumes the premium can be re-earned at the same rate
every cycle, which it cannot. Treat `Ann` as a comparison axis across
expiries, not a forecast of yearly income.

## Assignment odds, three ways

Each measure answers "how likely is it the stock is below `K` at
expiry" from a different premise. They are reported side by side so the
disagreement is visible.

**Chain delta (`delta_abs`, `delta_source = chain`).** The market's
own risk-neutral first derivative, from the snapshot greeks. The
standard desk shorthand for assignment probability. It embeds the
market's IV, so in a fear regime it is high because puts are expensive,
not because the stock is more likely to fall.

**Black-Scholes N(-d2) (`prob_itm_bs`).** The risk-neutral probability
of finishing below `K`:

```
d1 = [ln(S0/K) + (r + 0.5 * iv^2) * T] / (iv * sqrt(T))
d2 = d1 - iv * sqrt(T)
P(S_T < K) = N(-d2)
```

with `iv` the chain implied vol for that contract, `T = DTE / 365`, and
`r = risk_free_rate` (default 0, stated in the caveats). N(-d2) is
always a little below |delta| for a put; the gap is the vol drag term.
When greeks are absent, `delta_abs` is `N(d1) - 1` from the same inputs
and `delta_source = black_scholes`.

**Empirical base rate (`empirical.p_below_strike`).** What the stock
actually did. From ~2 years of daily closes (760 calendar days
requested), build every overlapping window of `TD = round(DTE * 252 /
365)` trading days and compute the simple return over each. The base
rate is the share of windows with return below `K / S0 - 1`, i.e.
windows where a strike at today's moneyness would have finished in the
money. `p_below_breakeven` uses `(K - P) / S0 - 1` instead.
`n_windows` is reported; overlapping windows mean the effective sample
is smaller than `n`, and the caveat says so. Fewer than 20 windows
yields null, not a number.

## Expected P&L under history

For each historical window return `r`:

```
S_T   = S0 * (1 + r)
pnl   = (P - max(K - S_T, 0)) * 100
```

`expected_pnl_usd` is the mean over windows, `avg_loss_when_assigned_usd`
the mean intrinsic loss over windows that finished ITM, and
`worst_window_pnl_usd` the minimum. This is the premium collected minus
the historical average cost of being assigned. It is a base rate under
the trailing two years' distribution and inherits that period's drift;
a stock that rallied for two years shows a flattering number.

## IV context

`atm_iv` is the IV of the put nearest spot on the first analyzed
expiry. `realized_vol_20d` and `realized_vol_60d` are the annualized
standard deviations of daily log returns over the trailing 20 and 60
closes. The label thresholds are the same as hedge-suggester's: ratio
>= 1.30 `expensive`, <= 1.00 `cheap`, otherwise `fair`. For a put
seller, `expensive` is the favorable side of the variance risk premium,
as long as realized stays where it has been. This is not an IV
percentile; that needs the name's IV history.

## Events

- **Earnings**: `earnings-blackout.run([ticker], window_days = max_dte
  + 7)` supplies the next date and session (Benzinga when entitled,
  EDGAR 8-K dates otherwise). Each expiry block carries
  `earnings_inside`; the render tags the expiry and the take says
  whether the pick's expiry contains the print. Selling through a
  print collects the event premium and takes the gap risk; the tool
  labels it, it does not forbid it.
- **Ex-dividend**: `/v3/reference/dividends` with `ex_dividend_date`
  inside the window. Spot steps down by the dividend on the ex-date,
  which moves the stock toward the strike by that much.

## Picks

The primary expiry is the analyzed expiry with DTE nearest
`horizon_days`. Per stance (conservative 0.15, balanced 0.25,
aggressive 0.35), the pick is the primary-expiry candidate with |delta|
nearest the target, ties broken by DTE closeness then annualized yield.
A stance falls back to all expiries only when the primary has nothing
within 0.08 of its target, and reports null when nothing does.

## Liquidity flags

Per candidate: open interest below 100, bid-ask wider than 15% of the
mid, no two-sided quote, premium sourced from `day_close` or `fmv`.
Flags are informational; a flagged candidate can still be the pick, and
the take repeats the first thin-OI or wide-spread flag so it is not
missed.

## What the caveats always say

Mids not fills; a short put is long the stock to zero; the earnings
placement (inside, after, not found, not fetched); ex-dividend nudges;
IV label when not fair; overlapping-window and risk-free-rate
disclosure; American early assignment and tax; not advice.
