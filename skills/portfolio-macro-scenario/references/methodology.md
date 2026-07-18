# portfolio-macro-scenario methodology

The IP is the factor set, the regression spec, and the shock-to-return
conversions. Every number is grounded in real macro ETF prices, so the
book's response to a macro scenario is estimated from history rather than
assumed.

## The four macro factors

Each factor is a liquid, US-listed ETF that isolates one macro variable.
They form the regression design matrix in this fixed column order:

| Ticker | Factor | Proxy for |
|--------|--------|-----------|
| TLT | rates | 20+ year Treasuries (long-duration rate direction) |
| UUP | dollar | US Dollar Index (broad dollar / DXY) |
| USO | oil | WTI crude oil |
| GLD | gold | gold |

These four capture the macro moves an operator most often wants to stress:
rates, the dollar, oil, and gold. They are proxies, not the cash market:
UUP tracks the dollar index with fees and roll, USO tracks front-month WTI
with meaningful roll cost, and TLT is a bond fund, not the yield itself.

## The regression

For each position, align its close-to-close simple daily returns with the
four factor return series over their common dates, then keep the last
`lookback` observations (default 252 trading days). Positions with fewer
than 60 aligned observations, or any run where a factor ETF itself has
insufficient history, are excluded and flagged.

Fit a multivariate OLS with an intercept using `numpy.linalg.lstsq`:

```
y = a + b_TLT * r_TLT + b_UUP * r_UUP + b_USO * r_USO + b_GLD * r_GLD + e
```

where `y` is the position's daily return and `r_*` are the factor daily
returns. The design matrix is `[1, r_TLT, r_UUP, r_USO, r_GLD]`; the four
factor betas are the slope coefficients. Two more quantities come out of
the fit:

- **R^2** = `1 - SS_res / SS_tot`, how much of the name's daily variance
  the four factors explain. Low R^2 means the name is mostly idiosyncratic
  and the scenario read on it is weak.
- **Residual std** = `sqrt(SS_res / (n - k))` with `k = 5` parameters
  (intercept plus four factors). This is the daily standard deviation of
  what the factors do not explain, and it drives the confidence band.

## Shock to factor return

The scenario flags are translated into factor ETF return shocks:

- `dxy_pct` -> UUP return = `dxy_pct / 100`
- `oil_pct` -> USO return = `oil_pct / 100`
- `gld_pct` -> GLD return = `gld_pct / 100`

Three of the four are direct: a `+2` on `--dxy-pct` is a `+2%` UUP return,
applied straight through `b_UUP`.

### The rate shock and the duration assumption

Rates are different. `--rates-bp` is a parallel yield shock in basis
points, but the regression is against TLT's price return. A bond's price
moves by approximately `-duration * dyield`, so:

```
TLT_return = -(rates_bp / 10000) * TLT_DURATION
```

with `TLT_DURATION = 17` years. `rates_bp / 10000` converts basis points to
a decimal yield change; the negative sign is the inverse price/yield
relationship. A `+50bp` shock therefore maps to a TLT return of
`-(50 / 10000) * 17 = -0.085`, i.e. `-8.5%`.

`TLT_DURATION ~= 17` is an assumption, not a measured value. TLT's
effective duration has hovered near 16-18 years; 17 is a round midpoint.
It also assumes a parallel shift (TLT sits at the long end, so it responds
to long rates, not the short end or the curve shape) and a first-order
(linear) price response with no convexity. For large rate moves convexity
would soften the loss; this model does not capture it. If a book's rate
sensitivity is the whole point of the analysis, treat the TLT leg as
directionally right, not precise.

## Position and book P&L

- Expected position return = `sum(b_factor * factor_shock)` across the four
  factors.
- Position value = `shares * latest close`.
- Position P&L = `position_value * expected_return`.
- Per-factor P&L contribution = `position_value * b_factor * factor_shock`,
  so the four contributions sum to the position P&L.
- Book expected P&L = sum of position P&L across surviving positions.
- Book expected return = book P&L / book value.

## The confidence band (a simplification)

Each position carries regression residual risk: the part of its return the
factors do not explain. The scenario is a single instantaneous shock, so we
treat the horizon as one step and use the one-step (daily) residual std
directly, with no `sqrt(horizon)` scaling.

Per-position P&L uncertainty is `position_value * residual_std`. The
book-level P&L std assumes the residuals are **independent** across names:

```
book_std = sqrt( sum_i (position_value_i * residual_std_i)^2 )
```

The reported band is `expected_pnl +/- 1.64 * book_std`, a nominal ~90%
interval under a normal approximation.

This is deliberately rough. The independence assumption ignores residual
correlation across names (real books have common idiosyncratic factors), so
the true band is wider than reported. The `1.64` multiplier assumes
normality, which understates tail risk. Read the band as an order-of-
magnitude spread around the point estimate, not a calibrated interval.

## Dominant contributors

- **Positions** are ranked by the absolute value of their P&L
  contribution, surfacing which names carry the scenario.
- **Factors** are ranked by the absolute value of their aggregate P&L
  contribution across the book, surfacing which macro variable the book is
  most exposed to under this scenario.

Contribution shares are signed against book P&L: a name that loses money
while the book loses money shows a positive share (it adds to the loss).

## Honest caveats

- **Betas are historical and unstable.** They are estimated over the
  lookback and will not hold exactly out of sample; a name's macro
  sensitivity drifts with its business mix and positioning.
- **The factors are collinear.** Rates, the dollar, oil, and gold co-move
  (a strong dollar often coincides with falling gold and rising real
  rates). Collinear regressors make individual betas noisy and can flip
  their signs even when the aggregate fit is good. Read the betas together,
  not one at a time.
- **Shocks are linear.** Betas are applied linearly, so a large move is
  extrapolated straight off small daily co-movements. Convexity, gap risk,
  and regime shifts are not captured.
- **The TLT duration is assumed.** ~17 years is a round midpoint that
  drifts and assumes a parallel long-rate shift.
- **The CI band assumes independent residuals**, so the true ~90% band is
  wider than reported.
- **ETF proxies, not the cash market.** UUP, USO, and TLT carry fees, roll
  cost, and tracking error. Directionally right, not a cash-market
  substitute.
