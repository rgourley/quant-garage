# manager-portfolio-diff

Diff the two most recent quarterly 13-F filings for a fund manager.
Answers "what did Buffett / Klarman / Burry do last quarter?" Reports
initiations, exits, adds (>= 25% share change), and trims (<= -25%),
sorted by market value.

## Quick start

### Python library

```python
from quant_garage.skills.manager_portfolio_diff import run, render
payload = run(filer="berkshire")
print(render(payload))
```

### CLI

```bash
python3 examples/run-manager-portfolio-diff.py --filer berkshire
python3 examples/run-manager-portfolio-diff.py --filer-cik 0001067983
```

## What you get back

```
Manager portfolio diff: Berkshire Hathaway (Warren Buffett) · 2026-03-31 → 2026-06-30
Holdings: 41 → 39 · Portfolio value: $282.30B → $291.10B (+3.1%)
Activity: +2 init  -4 exit  ^3 add  v6 trim  ~28 unchanged

[NEW POSITIONS] (2)
  · CONSTELLATION BRANDS INC                    $1.20B (11,500,000 sh)
  · OXY (OCCIDENTAL PETROLEUM)                  $850.0M (14,500,000 sh)

[EXITED POSITIONS] (4)
  · PARAMOUNT GLOBAL                            $410.0M (63,300,000 sh)
  ...

Take: Biggest new position: CONSTELLATION BRANDS INC at $1.20B.
Biggest exit: PARAMOUNT GLOBAL ($410.0M).
```

Every run also ships canonical JSON matching
[`output-schema.json`](./output-schema.json).

## Known aliases

Berkshire, Baupost/Klarman, Renaissance, Bridgewater, Third
Point/Loeb, Pershing Square/Ackman, Tiger Global/Coleman, Scion/Burry,
Appaloosa/Tepper. For anyone else use `--filer-cik` with the CIK.

## Plan requirement

Stocks Basic (free tier).

## Skill spec

[`SKILL.md`](./SKILL.md).
