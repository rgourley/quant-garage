# macro-basket methodology

The IP is the pair choices and the label thresholds. Each derived signal
is a relative return between two ETFs that isolates one macro variable, or
a single-ETF direction. All are proxies for the cash market, chosen for
liquidity and clean exposure.

## The basket

| Ticker | Exposure | Role |
|--------|----------|------|
| TLT | 20+ year Treasuries | long-duration rate direction |
| IEF | 7-10 year Treasuries | belly of the curve |
| SHY | 1-3 year Treasuries | short end |
| HYG | high yield credit | risk appetite in credit |
| LQD | investment grade credit | duration + high-quality credit |
| TIP | inflation-protected Treasuries | real yields / breakevens |
| BND | total bond market | aggregate fixed income |
| GLD | gold | real-rate + fear hedge |
| SLV | silver | higher-beta precious metal |
| UUP | US dollar index | dollar direction |
| DBC | broad commodities | commodity carry |

## Relative strength

Per instrument per window: `RS_bps = (etf_return - spy_return) * 10_000`.
Basis points keep magnitudes comparable across windows. Trend label is
the five-bucket relative-strength scheme (improving, deteriorating,
stable_leader, stable_laggard, mixed) applied to RS across windows.

`curve_position_pct` is the percentile of the latest close within the
trailing max-window close series: a quick read on where the instrument
sits in its own range, independent of the benchmark.

## Derived signals

All computed over `signal_window_days` (default 60).

**Rates direction (TLT).** Bond price up means yield down. TLT rising
means long rates falling: easing. Threshold: +/-1% over the window. Below
that, stable.

**Curve shape (SHY vs TLT).** The 2s10s proxy. If the short end (SHY)
outperforms the long end (TLT), long yields rose more than short:
steepening. If long outperforms short: flattening. Combine with the rate
direction (TLT up = rates falling = "bull", TLT down = "bear") for the
four-way taxonomy:

- rates falling + flattening: bull flattening
- rates falling + steepening: bull steepening
- rates rising + flattening: bear flattening
- rates rising + steepening: bear steepening

**Real yields (TIP vs IEF).** TIP is real (inflation-protected), IEF is
nominal at a similar duration. TIP outperforming IEF means real yields
falling or breakevens (inflation expectations) widening. IEF
outperforming means real yields rising. Threshold: +/-25bp of relative
return.

**Credit stress (HYG vs LQD).** High yield versus investment grade. HY
underperforming IG means credit spreads widening: stress. HY
outperforming means spreads tightening: risk appetite. Threshold: +/-25bp.

**Dollar (UUP).** Direct direction of the broad dollar. Threshold +/-1%.

**Commodity carry (DBC).** Direct direction of broad commodities.
Threshold +/-1%.

**Gold/silver ratio (GLD/SLV).** The classic risk-appetite gauge in
metals. A rising ratio (gold leading silver) is defensive; a falling
ratio (silver leading) is risk-seeking. State is set by the ratio's
percentile within the trailing year: >=60th defensive, <=40th
risk-seeking, else neutral.

**Gold vs dollar beta (GLD vs UUP).** Rolling 60-day beta of GLD daily
returns to UUP daily returns:
`beta = cov(gld_ret, uup_ret) / var(uup_ret)`. Normally negative (gold
falls when the dollar rallies). `<= -0.5` is dollar-sensitive; `> -0.2`
is decoupled; in between is moderately sensitive. When gold is decoupled
from a rising dollar, something else (real rates, fear) is driving it.

## Honest caveats

- Every signal is an ETF-return proxy, not the cash rate, spread, or
  index. ETFs carry expense ratios, roll cost (DBC especially), and
  tracking error. Directionally right, not a cash-market substitute.
- Thresholds are judgment calls, not estimated. They are set to filter
  noise at the 60-day window; a different window wants different
  thresholds.
- A rate-limited pull can drop an ETF and flip a signal. The runtime
  flags rate-limited series loudly; never read the take without checking
  the caveats.
