# single-name-vs-sector

`relative-strength` ranks a name versus SPY. That conflates two things:
is the name strong because its whole sector is strong, or is the name
pulling away from (or falling behind) its own sector. This one splits
them explicitly.

Surfaced when SOFI showed as a stable laggard (-4100bp vs SPY over
120d) while its sector XLF was a leader (+377bp over 20d). The name
was diverging from its sector: the weakness was name-specific, not a
financials problem.

## Quick start

### Python library

```python
from quant_garage.skills.single_name_vs_sector import run, render
payload = run(ticker="SOFI")
print(render(payload))
```

### CLI

```bash
python3 examples/run-single-name-vs-sector.py --ticker SOFI
python3 examples/run-single-name-vs-sector.py --ticker ANET --sector XLK
```

### Claude Code

Discovered at `skills/single-name-vs-sector/`. Ask "is SOFI diverging
from its sector?" and the take line classifies name-specific vs
sector-driven.

## Method

For one ticker, map to its SPDR sector ETF (11 GICS sectors, override
via `sector=`), then over each window compute three RS legs in bps:

- name vs sector
- sector vs benchmark
- name vs benchmark

The divergence score is the name-vs-sector RS averaged across windows.
The take classifies as **leading**, **lagging**, or **diverging**
(name and sector pointing opposite ways) with the magnitude and driving
window.

## What you get back

A 3-row RS table (bps by window, with trend labels), a divergence
block (score, composite, sector-vs-benchmark average, classification),
and a one-sentence take. Full JSON contract in
[`output-schema.json`](./output-schema.json).

## Runs on

Any stocks tier (only 3 daily aggs pulled). Details in
[`requires.yml`](./requires.yml).
