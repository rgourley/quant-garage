# stock-one-pager rendering

Note mode. Retail-tier plain-language card. No em-dashes, no jargon.

## Header

```
{TICKER} — plain-language snapshot
As of {AS_OF} · Price {DOLLAR_FMT(CURRENT_PRICE)}
```

## Body sections

Each section is a single-line label followed by the plain-language
read. Skip a section entirely when the underlying data is missing
(better than filling with "n/a").

```
Trend:   {TREND_PLAIN}
Range:   {RECENT_RANGE_POSITION}
Vol:     {VOLATILITY_PLAIN_FROM_COMPONENTS}
Liquid:  {LIQUIDITY_PLAIN}
```

## Key levels

Two-column block. Support first (below price), resistance second
(above price). Include the basis in parens.

```
Key levels
  Support:     ${LEVEL} ({BASIS}), ${LEVEL} ({BASIS})
  Resistance:  ${LEVEL} ({BASIS}), ${LEVEL} ({BASIS})
```

## Next catalyst

```
Next catalyst: {CATALYST_READ}
```

If null, print: `Next catalyst: none known in the next 30 days.`

## Market context

```
Market context: {MARKET_CONTEXT_PLAIN}
```

Skip entirely when `include_market_context=False`.

## What could go wrong

```
What could go wrong:
- {RISK_1}
- {RISK_2}
- {RISK_3}
```

Bullets come from `_risks()` which reads the component signals; the
list is generated, not templated. Skip the section when there are
zero risks (rare — usually there's at least one honest caveat).

## Voice rules

- Reading-level: high-school to first-year college.
- No abbreviations without their meaning (SMA, RSI, ATR appear only
  in the `components` block, never in prose).
- No hedging weasel words ("arguably", "potentially", "some might
  say"). If the signal isn't clear, say "unclear."
- No em-dashes. Use colons, parentheses, periods.
