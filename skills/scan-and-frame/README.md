# scan-and-frame

Research-tier idea generation with regime framing. market-regime +
universe-builder + relative-strength, optionally factor-research.

## Quick start

Three ways to invoke. Add the factor pass with the
`include_factor_research=True` / `--include-factor-research` flag.

### Python library

```python
from quant_garage.skills.scan_and_frame import run, render
payload = run(
    candidate_source="curated",
    min_mcap=10e9,
    top_n_rank=15,
)
print(render(payload))
```

### CLI

```bash
python3 examples/run-scan-and-frame.py --candidate-source curated --min-mcap 10e9 --top-n-rank 15 --format render
```

### Claude Code / LLM tool use

Discovered at `skills/scan-and-frame/`. In a Claude Code session,
ask "find me the strongest large-caps in this regime" — Claude
runs the universe + regime + RS chain. Tool-use LLMs consume the
`run()` payload matching [`output-schema.json`](./output-schema.json).

## Plan requirement

Stocks Starter minimum. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
