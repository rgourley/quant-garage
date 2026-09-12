# cash-secured-puts rendering (Layer 2)

Output mode: table. The skill emits canonical JSON matching
`output-schema.json`; this reference shows how that JSON renders as
the Claude Code default view.

No em-dashes anywhere. Colons, parentheses, periods.

Order of blocks:

1. Header (spot, cash, stance, DTE window, IV context, events)
2. One table per analyzed expiry, in date order, with liquidity flags
3. Picks (three stances)
4. Take
5. Caveats footer

## Block 1: header

```
Cash-Secured Puts: {TICKER} ({AS_OF})
Spot ${SPOT} ({SPOT_SOURCE}) · cash ${CASH} · stance: {STANCE} · DTE window {MIN}-{MAX}d
IV context: ATM IV {IV}% vs realized 20d {RV20}% = {RATIO}x ({LABEL}) · realized 60d {RV60}%
Earnings: {DATE} {SESSION} (source: {benzinga|edgar_8k})
Ex-dividend: {DATE} ${AMOUNT}[, ...]
```

- `SPOT_SOURCE` is the price-fallback step that won. `prevDay.c` means
  the tape is closed or stale; read yields accordingly.
- `cash not specified` replaces the cash clause when `--cash` was
  omitted; sizing fields are then null.
- IV context falls back to `IV context: unknown` when ATM IV or the
  underlying history is missing.
- Earnings line variants: a date with session and source; `none found
  in window`; `calendar not fetched (check manually)`.
- The ex-dividend line is omitted when no ex-date falls in the window.

## Block 2: expiry tables

One per expiry in `expiries`, date order.

```
Expiry {DATE} ({DTE}d, {TD} trading days) · {N} candidates of {M} puts · OI {TOTAL}[ · EARNINGS INSIDE][ · ex-div inside]
  Strike    OTM    Mid   Prem  Yield    Ann      B/E   |d|  P(ITM)  Hist<K   E[PnL]      OI  Sprd
-------------------------------------------------------------------------------------------------
     220  -4.4%   2.55    255  1.16%  12.1%   217.45  0.27     30%     22%      +91   9,118    2%
  ! 205P: thin OI 64 (below 100 floor)
```

Columns, left to right, from each `candidates[]` row:

- `Strike`: `strike`.
- `OTM`: `-otm_pct`, signed percent. Negative means the strike is below
  spot; a positive value is a slightly ITM strike (allowed up to 2%).
- `Mid`: `mid`, the chain mid used to price the premium.
- `Prem`: `premium_usd`, dollars per contract.
- `Yield`: `static_yield`, two decimals.
- `Ann`: `annualized_yield`, one decimal. Simple scaling by 365/DTE.
- `B/E`: `breakeven` = strike minus mid.
- `|d|`: `delta_abs`. Suffix `*` when `delta_source` is
  `black_scholes` (chain greeks absent); the footer explains the mark.
- `P(ITM)`: `prob_itm_bs`, Black-Scholes N(-d2), zero decimals.
- `Hist<K`: `empirical.p_below_strike`, zero decimals; `n/a` when the
  history has fewer than 20 windows.
- `E[PnL]`: `empirical.expected_pnl_usd`, signed, per contract.
- `OI`: `open_interest`, thousands-separated.
- `Sprd`: `(ask - bid) / mid` as a percent; `n/a` without a two-sided
  quote.

Rows are sorted by strike descending (closest to spot first), which is
also the order of rising yield and rising assignment odds.

Under each table, one `!` line per candidate whose `caveats` array is
non-empty, joining the flags with `; `. Flags: thin OI below the floor,
bid-ask wider than the ceiling, no two-sided quote, premium sourced
from `day_close` or `fmv` rather than a live quote.

## Block 3: picks

```
Picks (from the {PRIMARY_EXPIRY} expiry, nearest the {HORIZON}d horizon; other expiries only when it has no fit):
  conservative (~0.15d): {EXPIRY} {STRIKE}P @ ${MID} mid: {STATIC}% static, {ANN}% annualized, breakeven ${BE} ({CUSHION}% below spot), |delta| {D}, hist {H}% (n = {N}) · {K} contract(s)
  balanced     (~0.25d): ...
  aggressive   (~0.35d): ...
```

- A stance with no candidate within 0.08 of its target delta renders
  `none within range`.
- The `hist` clause is omitted when the empirical block is null; the
  contract clause is omitted when cash was not given.

## Block 4: take

One paragraph for the stated stance. Premium and yields, breakeven and
cushion, the three assignment odds (whichever are available),
historical expected P&L, sizing, IV label when expensive or cheap,
earnings placement relative to the pick's expiry, and the first thin-OI
or wide-spread flag on the pick. Always ends with `Not advice.`

```
Take: For a balanced stance, the 2026-10-17 220 put (35d) collects $255 per contract: 1.16% on $22,000 secured, 12.1% annualized, breakeven $217.45 (5.5% below spot). Assignment odds: |delta| 0.27, N(-d2) 30%, history 22% of 35d windows finished below the strike (n = 480). Expected P&L per contract under the historical distribution: +$91. Cash covers 2 contract(s). Earnings 2026-10-30 is after this expiry. Not advice.
```

When the stance has no pick: `No {stance} candidate (target |delta|
{X}) could be priced on {TICKER}; widen the DTE window or check the
chain. Not advice.`

## Block 5: caveats footer

```
Caveats:
  - {caveat}
  * delta from Black-Scholes on chain IV (chain greeks absent)
```

`tier_caveats` in order. The trailing `*` legend appears only when at
least one candidate used the Black-Scholes delta fallback. The rate-
limit caveat, when present, is the one that changes the read: realized
vol and the empirical columns are unknown, not zero.

## Error renders

When `error` is present (no spot, chain not entitled), the render is
three lines: the skill and ticker, the error, and the caveats.

## What UI devs do instead

A custom UI consumes the JSON directly: a strike-by-expiry grid with
yield on one axis and the three assignment measures as overlaid
markers, the picks highlighted, and the earnings date drawn as a
vertical line across the expiry axis. The table format is the Claude
Code default; UIs build their own visual layer from the same JSON.
