# options-structure-analyzer rendering

Table mode. Header block, one block per structure (ranked by
payoff/capital), caveats footer.

## Header

```
Options Structure Analyzer — {TICKER} · view={VIEW}
As of {AS_OF} · Spot ${SPOT} · Target ${TARGET_PRICE} ({SIGNED_PCT} move)
Expiry: {EXPIRY_DATE} ({DAYS}d) · {N_STRUCTURES} structures evaluated
```

## Structure block

```
### {STRUCTURE_NAME}
  {PLAIN_ENGLISH_READ}
    {ACTION} {QTY} x {TYPE} @ ${STRIKE}  ({OCC_TICKER}) @ ${PREMIUM}
    ...
    Net debit: {DOLLAR}  ·  Max profit: {DOLLAR|unbounded}  ·  Max loss: {DOLLAR}
    Breakeven(s): ${LEVEL}, ${LEVEL}  ·  Capital req: {DOLLAR}
    P&L at target: {SIGNED_DOLLAR}  [({PCT} of capital) | ({SIGNED_DOLLAR} vs unhedged)]
```

## Legs

- For share legs: `HOLD {qty} shares`
- For option legs: `{ACTION} {qty} x {type} @ ${strike}  ({occ_ticker}) @ ${premium}`

## Net line

- Positive `net_debit`: `Net debit: ${amount}`
- Negative `net_debit`: `Net credit: ${amount}` (absolute value)
- `unbounded` when `max_profit` is null

## P&L at target line

Two variants based on `structure_type`:

- **Non-hedge**: append `({signed pct} of capital)` when
  `capital_required > $100`.
- **Hedge**: append `({signed dollar} vs unhedged)` where
  `unhedged = (target_price - spot) * 100` shares. The unhedged
  delta shows what the hedge actually saved (positive = hedge helped).

## Sort order

Descending by `payoff_at_target / capital_required` for non-hedge
views; the hedge view sorts by same but the render layer masks the
capital ratio.

## Caveats footer

Standard block: delayed-data caveat, expiration-only-payoff caveat,
ranking-not-selection caveat, greeks-omitted caveat.
