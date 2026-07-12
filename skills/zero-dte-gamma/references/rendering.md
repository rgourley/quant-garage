# Rendering: zero-dte-gamma

Note-mode. Layout:

1. Header (3 lines: identity + net gamma + flip)
2. Top gamma pins table
3. Take + caveats

## Header

```
0DTE gamma flow: SPY · exp 2026-07-13 (0DTE) · spot $754.95 · 148 strikes
Net dealer gamma: -$1.25B → SHORT GAMMA (destabilizing)
Gamma flip strike: $500.00
```

Line 1: underlying + expiry + DTE tag + spot + strike count.
Line 2: net dealer GEX + regime tag.
Line 3: gamma flip strike (skipped when None).

Regime tags:
- `LONG GAMMA (stabilizing)`
- `SHORT GAMMA (destabilizing)`

## Top pins table

Fixed 6-column layout:

```
Top gamma pins (strikes with largest total notional gamma)
    Strike   Distance     Call γ ($)     Put γ ($)    Call OI    Put OI
    759.00    +0.54%       $326.2M         $1.2M       9,412        34
    ...
```

## Take

- Short gamma: "Dealers are net-short gamma at these strikes; expect
  intraday moves to accelerate (dealers hedge with the market)."
- Long gamma: "Dealers are net-long gamma; expect intraday range
  compression."

Followed by: "Gamma flip at ${flip}; a break past this level shifts
the dealer hedging regime."

## Empty case

```
0DTE gamma flow: SPY exp {date} — ENTITLEMENT REQUIRED
```

Or when no contracts:

```
0DTE gamma flow: {under} · exp {date}
- No contracts with open interest for {under} exp {date}.
```
