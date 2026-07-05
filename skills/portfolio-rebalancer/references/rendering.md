# portfolio-rebalancer rendering

Table mode. Header, before/after summary block, trade table, status
line, caveats.

## Header

```
Portfolio Rebalance Recommendation — {AS_OF}
Book value: {DOLLAR_FMT(BOOK_VALUE)} · {N_POSITIONS} positions · caps: variance-share <= {PCT}, weight <= {PCT}, churn <= {PCT}
```

## Before / After summary

```
Before / After:
  Vol (ann):              {PCT} -> {PCT}
  Top-3 variance share:   {PCT} -> {PCT}
  Max single-name var:    {PCT} ({NAME}) -> {PCT} ({NAME})
  Herfindahl:             {N.NNN} -> {N.NNN}
  Actual churn:           {PCT}
```

## Trade table

Sort by absolute dollar amount (biggest change first).

```
Recommended trades ({N_TRADES}):

Ticker  Action        Dollar     Δ wt              Weight           Var Share
------------------------------------------------------------------------------
{TICKER}  {BUY|SELL}  {DOLLAR}  {SIGNED_PCT}  {PCT} -> {PCT}   {PCT} -> {PCT}
```

Skip trades below `min_trade_dollar` (default $100).

## Status line

If all variance shares within cap:

```
STATUS: All variance shares within cap after rebalance.
```

Else:

```
STATUS: Constraints partially satisfied. Names still over variance-share cap after churn limit:
  {TICKER}: variance share {PCT}, weight {PCT}
  ...
  To fully resolve, raise --max-churn or run again after the first rebalance settles.
```

If the churn cap was binding, append:

```
  (Churn cap was binding — trades scaled down proportionally.)
```

## Caveats footer

Standard block: not tax-aware, not liquidity-aware, descriptive not
return-maximizing, covariance regime-sensitive.
