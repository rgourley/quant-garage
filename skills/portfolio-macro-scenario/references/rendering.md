# portfolio-macro-scenario rendering (Layer 2)

Output mode: table. The core deliverable is a sensitivity grid (position x
factor beta) with R^2 and per-position P&L. Around it sit a header, the
factor-shock line, the book P&L with its CI band, the two
dominant-contributor lists, the take, and a caveats footer.

No em-dashes. Colons, parentheses, periods.

## Header

Two lines:

- Line 1: `Portfolio Macro Scenario ({scenario phrase}) as of {as_of}`
- Line 2: `Lookback Nd · P positions · Book $X`

The scenario phrase is the non-zero shocks joined with ` / `, for example
`+50bp rates / +2% DXY`. When every shock is zero it reads `a flat
scenario (all shocks zero)`.

## Factor-shock line

One line stating each factor's translated ETF return shock (signed
percent), so the reader sees exactly what was pushed through the betas:

```
Factor shocks (ETF return): TLT -8.5%  UUP +2.0%  USO +0.0%  GLD +0.0%
```

## Sensitivity table

One row per surviving position. Columns: position ticker, one beta column
per factor (`b_TLT`, `b_UUP`, `b_USO`, `b_GLD`, two decimals), R^2 (two
decimals), the expected position return under the scenario (signed
percent), and the position P&L (signed dollars, no cents).

```
Position    b_TLT   b_UUP   b_USO   b_GLD    R^2   ExpRet         P&L
------------------------------------------------------------------------
AAPL        -0.30    0.45    0.05    0.10   0.42   -1.4%      -$2,100
NVDA        -0.55    0.30   -0.02    0.05   0.51   -2.1%      -$4,300
```

Betas are the OLS coefficients; a negative `b_TLT` means the name falls
when TLT falls (rates rise), i.e. positive duration exposure.

## Book P&L with CI band

Two lines. The first is the aggregate expected P&L in dollars and as a
percent of book value. The second is the `~90%` band:

```
Book expected P&L: -$14,400 (-3.1%)
  ~90% band: -$26,900 to -$1,900 (+/- $12,500, 1.64 sigma, independence assumed)
```

The band is `expected_pnl +/- 1.64 * ci_std_usd`. State the independence
assumption inline so the reader knows the true band is wider.

## Dominant contributors

Two short lists. Positions first (ranked by absolute P&L contribution, top
5), then factors (all four, ranked by absolute aggregate contribution).
Each row shows the P&L and its share of book P&L when book P&L is non-zero:

```
Dominant positions (by absolute P&L contribution):
  NVDA          -$4,300 (+29.9% of book P&L)
  AAPL          -$2,100 (+14.6% of book P&L)
  ...

Dominant factors (by aggregate P&L contribution):
  TLT   rates (20+ year Treasuries)     -$9,800 (+68.1% of book P&L)
  UUP   dollar (US Dollar Index)        -$3,200 (+22.2% of book P&L)
  ...
```

Contribution share is signed against book P&L: a position that loses money
when the book loses money shows a positive share (it adds to the loss).

## Take

One sentence summarizing the book's net exposure under the scenario:

```
Take: Under +50bp rates / +2% DXY, the book loses an estimated 3.1%
(-14.4K), driven by NVDA's -duration beta and the rates-sensitive names.
```

The verb is `loses` or `gains` by the sign of expected P&L; the driver is
the top position and the factor that moved it most; the tail names the
dominant book-level factor.

## Excluded positions

When any position was dropped (no price or insufficient history), list them
under an `Excluded positions:` heading with the reason and `n_obs`.

## Caveats footer

When `tier_caveats` is non-empty, render:

```
Caveats:
  - {caveat}
```

The rate-limit caveat, when present, always sorts first: a partial pull can
drop a factor or a position and distort the scenario, so the reader must
see it before trusting the numbers.
