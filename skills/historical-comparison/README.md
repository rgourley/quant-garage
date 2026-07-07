# historical-comparison

Twin decision-support. event-study (event-specific) + historical-
analog-finder (market-wide). Both anchors together instead of one.

## Quick start

```bash
python3 examples/run-historical-comparison.py --ticker NVDA --event-class earnings --period most_recent --format render
```

Analog-only mode:

```bash
python3 examples/run-historical-comparison.py --skip-event --format render
```

## Plan requirement

Stocks Starter. See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
