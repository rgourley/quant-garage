# commodity-cycle

Is this commodity in a winning or losing macro setup right now, and
which macro driver dominates? Where `fixed-income-context` paints the whole
cross-asset tape, this one zooms in on a single commodity ETF and
reads the specific drivers that push it around.

Would have flagged the gold drawdown two weeks early: a strengthening
dollar plus rising real yields against a negatively-correlated
commodity is a headwind long before price confirms.

## Quick start

### Python library

```python
from quant_garage.skills.commodity_cycle import run, render
payload = run(ticker="GLD", window=60)
print(render(payload))
```

### CLI

```bash
python3 examples/run-commodity-cycle.py
python3 examples/run-commodity-cycle.py --ticker SLV --window 90
```

### Claude Code

Discovered at `skills/commodity-cycle/`. Ask "what's the setup for gold
right now" and get the constructive/neutral/headwind read.

## Signals

- **DXY correlation**: rolling corr of the commodity vs UUP
- **Real-yield corr**: rolling corr vs the TIP-minus-IEF spread
- **Miner divergence**: GLD vs GDX relative return (GLD only)
- **Silver co-movement**: rolling corr GLD vs SLV (GLD only)
- **Momentum quintile**: window return ranked vs trailing-year windows

The take reads **constructive / neutral / headwind** and names the
dominant macro variable.

## What you get back

Signal block, take line, tier caveats. Full JSON contract in
[`output-schema.json`](./output-schema.json).

## Runs on

Any stocks tier (5-6 daily ETF pulls). Details in
[`requires.yml`](./requires.yml).
