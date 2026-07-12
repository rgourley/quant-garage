#!/usr/bin/env python3
"""CLI wrapper for hurst-exponent.

    from quant_garage.skills.hurst_exponent import run, render
    payload = run("AAPL", lookback_days=504)

Reads MASSIVE_API_KEY from env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.hurst_exponent import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-hurst-exponent",
        description="Rescaled-range Hurst exponent estimator; classifies name as mean-reverting, random-walk, or trending.",
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--lookback-days", type=int, default=504)
    ap.add_argument("--n-bootstrap", type=int, default=100,
                    help="Block-bootstrap iterations for the confidence band. 0 to skip. Default 100.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.ticker,
            lookback_days=args.lookback_days,
            n_bootstrap=args.n_bootstrap,
            seed=args.seed,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rendered = render(payload)
    if fmt == "render":
        print(rendered)
    elif fmt == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(rendered); print(); print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write("# hurst-exponent run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
