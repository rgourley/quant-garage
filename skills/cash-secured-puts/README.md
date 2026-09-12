# cash-secured-puts

Which put do I sell, at what strike and expiry, and what am I actually
being paid to take the stock? One underlying, the most liquid expiries
in your DTE window, one strike per delta target, and the yield stated
next to the assignment odds three ways: chain delta, Black-Scholes
N(-d2), and the stock's own history over same-length windows.

## Quick start

Three ways to invoke.

### Python library

```python
from quant_garage.skills.cash_secured_puts import run, render
payload = run("AAPL", cash=50_000, stance="balanced", horizon_days=45)
print(render(payload))
```

### CLI

```bash
python3 examples/run-cash-secured-puts.py \
  --ticker AAPL --cash 50000 --stance balanced --horizon-days 45 \
  --format render
```

`--format json` for the canonical payload only. `--sleep 13` on a
rate-limited key.

### Claude Code / LLM tool use

Discovered at `skills/cash-secured-puts/`. In a Claude Code session,
ask "what put should I sell on AAPL with $50k" and Claude invokes the
skill. Tool-use LLMs consume the `run()` payload matching
[`output-schema.json`](./output-schema.json).

## What you get back

```
Cash-Secured Puts: AAPL (2026-09-12)
Spot $230.10 (lastTrade) · cash $50,000 · stance: balanced · DTE window 7-75d
IV context: ATM IV 28% vs realized 20d 22% = 1.27x (fair) · realized 60d 25%
Earnings: 2026-10-30 AMC (source: benzinga)

Expiry 2026-10-17 (35d, 24 trading days) · 6 candidates of 41 puts · OI 184,220
  Strike    OTM    Mid   Prem  Yield    Ann      B/E   |d|  P(ITM)  Hist<K   E[PnL]      OI  Sprd
-------------------------------------------------------------------------------------------------
     225  -2.2%   4.10    410  1.82%  19.0%   220.90  0.38     41%     33%     +112  12,406    2%
     220  -4.4%   2.55    255  1.16%  12.1%   217.45  0.27     30%     22%      +91   9,118    2%
     215  -6.6%   1.52    152  0.71%   7.4%   213.48  0.18     21%     13%      +68   7,502    3%
     ...

Picks (from the 2026-10-17 expiry, nearest the 45d horizon; other expiries only when it has no fit):
  conservative (~0.15d): 2026-10-17 210P @ $0.92 mid: 0.44% static, 4.6% annualized, breakeven $209.08 (9.1% below spot), |delta| 0.13, hist 8% (n = 480) · 2 contract(s)
  balanced     (~0.25d): 2026-10-17 220P @ $2.55 mid: 1.16% static, 12.1% annualized, breakeven $217.45 (5.5% below spot), |delta| 0.27, hist 22% (n = 480) · 2 contract(s)
  aggressive   (~0.35d): ...

Take: For a balanced stance, the 2026-10-17 220 put (35d) collects $255 per contract: 1.16% on $22,000 secured, 12.1% annualized, breakeven $217.45 (5.5% below spot). Assignment odds: |delta| 0.27, N(-d2) 30%, history 22% of 35d windows finished below the strike (n = 480). Expected P&L per contract under the historical distribution: +$91. Cash covers 2 contract(s). Earnings 2026-10-30 is after this expiry. Not advice.
```

Numbers above are illustrative of the shape; every real run cites its
own `sources` with fetched_at timestamps.

## Reading the columns

- **Yield / Ann**: premium at the mid over the cash secured
  (`strike x 100`), and that yield scaled by `365 / DTE`. The JSON
  also carries both at the bid.
- **|d|**: absolute put delta from the chain. A trailing `*` means
  Black-Scholes from the chain IV because the chain carried no greeks.
- **P(ITM)**: Black-Scholes `N(-d2)`, the risk-neutral probability
  of finishing below the strike.
- **Hist<K**: the share of trailing overlapping windows of the same
  trading-day length in which the stock fell below the strike's
  moneyness. `n` is in the JSON and the picks line.
- **E[PnL]**: premium minus the average intrinsic loss across those
  historical windows, per contract. A base rate, not a forecast.

## Methodology

See [`references/methodology.md`](./references/methodology.md) for
the yield math, the three assignment measures and how they differ,
the empirical window construction, expiry and strike selection, and
the caveats that ship with every run.

## Plan requirement

Stocks Starter plus the Options Developer add-on. See top-level
[PLAN-MATRIX.md](../../PLAN-MATRIX.md).

## Skill spec

Claude Code invocation contract at [`SKILL.md`](./SKILL.md).
