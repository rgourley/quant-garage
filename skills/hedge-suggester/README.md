# hedge-suggester

`risk-report` flags positions carrying most of the book's variance.
`options-flow` shows what other traders are doing. Neither one proposes
what to do about a concentrated long. This one takes one long position
and returns five live-priced option overlays, ranked by cost per dollar
of downside protected.

## Quick start

### Python library

```python
from quant_garage.skills.hedge_suggester import run, render
payload = run(ticker="ALLO", shares=1000, horizon_days=90, risk_tolerance="medium")
print(render(payload))
```

### CLI

```bash
python3 examples/run-hedge-suggester.py --ticker ALLO --shares 1000 \
  --horizon-days 90 --risk-tolerance medium
```

### Claude Code

Discovered at `skills/hedge-suggester/`. Ask "hedge my ALLO position
over 90 days, medium risk" and it prices five structures against the
live chain.

## Structures priced

Each is priced from chain mids, with liquidity flags per leg (OI floor
100, spread ceiling 15%):

- **covered_call**: sell an OTM call per 100 shares
- **protective_put**: buy an ATM/slightly-OTM put per 100 shares
- **collar**: protective put financed by a covered call
- **put_spread**: buy near-ATM put, sell a lower put
- **ratio_put_spread**: buy 1 put, sell 2 further-OTM puts (tail risk flagged)

Structures are ranked by cost per dollar of downside protected. IV
context comes from ATM implied vol vs 20-day realized vol (a variance
risk premium proxy, not a true IV percentile).

## What you get back

A structures table (net cost, % notional, cost/$ protected, breakeven,
max loss/gain), per-structure legs and greeks, an IV context line, a
one-sentence take fitted to the stated risk tolerance, and per-leg
caveats. Full JSON contract in [`output-schema.json`](./output-schema.json).

## Not investment advice

Mid-price fills are optimistic (real fills cross the spread). Greeks
are point-in-time. Short legs carry assignment risk. Options Developer
tape is 15-min delayed. This proposes, it does not advise.

## Runs on

Stocks Starter + Options Developer add-on (chain + greeks + OI + IV).
Details in [`requires.yml`](./requires.yml).
