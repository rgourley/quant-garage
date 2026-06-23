# Decile analysis

The IC tells you whether a factor predicts forward returns in rank
order. The decile spread tells you how much money the prediction is
worth. Both matter; report both.

## Method

Per rebalance month t:

1. Rank the universe by factor score
2. Cut into 10 equal-size deciles (D1 = lowest, D10 = highest)
3. Equal-weight names within each decile
4. Record each decile's forward return at horizon h

The decile portfolio return at month t, decile d, horizon h:

```
decile_return_t_d_h = (1/n_d) * sum_{i in D_d} forward_return_i_t_to_t+h
```

The long-short spread:

```
spread_t_h = decile_return_t_10_h - decile_return_t_1_h
```

## Annualization

Decile returns at a 1M horizon compound 12x per year, 3M compounds 4x,
12M doesn't compound. Annualized spread:

```
annualized_spread_h = mean(spread_t_h) * (12 / months(h))
```

Where `months(h)` is 1, 3, 6, or 12. This is the arithmetic
annualization, which slightly overstates the compounded return when
returns are volatile but is the convention for cross-sectional factor
research (so the numbers are comparable to FactSet, Axioma, MSCI Barra
reports).

Geometric annualization (more accurate for high-vol spreads) is a
clean PR extension. The schema records `annualization_method` so the
consumer knows which one was used.

## Typical magnitudes

Real factor spreads on US equity universes:

- Momentum (12-1M): 4-12% annualized in trending regimes; 0 to -4%
  in mean-reverting regimes (post-2009, post-March 2020)
- Value (1/(P/B)): 2-8% annualized over multi-year windows; can be
  flat or negative in growth regimes (2010-2020 was rough)
- Quality (ROE): 2-6% annualized, more stable than the others;
  rarely produces a big year but rarely a big drawdown either
- Low-vol (1/realized_vol): 3-8% annualized in risk-off regimes;
  negative in risk-on rips (recent regime)

Numbers above 15% annualized for a single factor on a top-500
universe should be treated with suspicion: usually a data error
(unwinsorized outlier), a survivorship-bias artifact, or
overfitting in a small sub-window.

## Hit rate

Fraction of months where D10 beats D1 at the 12M forward horizon:

```
hit_rate_12m = (1/T) * sum_t (1 if spread_t_12m > 0 else 0)
```

The hit rate is the discretionary-PM-friendly metric. A factor with
a 65% hit rate produces a "wins more often than it loses" story even
when the magnitudes are small. The IC and the hit rate are
complementary: IC is the magnitude-weighted view; hit rate is the
frequency view.

Typical hit rates for real factors:

- 55-65%: a workable factor
- 50%: noise
- Below 50%: the factor is mispriced in this regime (sign-flip
  candidate)
- Above 70%: suspicious, usually a backtest-overfit artifact or
  survivorship bias

## D10 minus D1 vs D10 alone

The long-short spread (D10 - D1) is the standard quant report because
it controls for market beta. Both deciles move with the market; the
spread isolates the factor signal.

The long-only D10 return is what a long-only mutual fund actually
captures. It's usually 30-60% of the spread (because the short leg
contributes the other half). The schema records D10 and D1 returns
separately so the consumer can compute either view.

## Monotonicity

A clean factor produces monotone decile returns: D1 < D2 < D3 < ... <
D10. A factor with a U-shaped pattern (D1 and D10 both high, middle
flat) is often a volatility factor in disguise. A factor with a
saturation pattern (D8, D9, D10 all the same) suggests the signal
runs out at the tails.

v1 doesn't render the full decile-by-decile curve in the table; the
schema reserves space (`decile_returns`) for a future UI extension
that draws the line chart.

## Equal-weight vs cap-weight within deciles

The skill uses equal-weight within deciles. Cap-weighted deciles are
closer to what a long-only manager actually trades but are dominated
by the top few names (NVDA alone is 5% of the top decile by cap in
2026). Equal-weight is the academic convention and matches
FactSet/Axioma reports. The schema records `decile_weighting` so the
consumer knows which one was used.

## Why monthly rebalance

Same reasoning as monthly IC: convention for quant factor research at
this scale. Weekly turnover eats gross return through transaction
costs; quarterly understates real-time factor opportunity. Monthly
hits the practical sweet spot. The take in the rendered output should
note when a factor's IC decay suggests a different rebalance
frequency would be optimal.
