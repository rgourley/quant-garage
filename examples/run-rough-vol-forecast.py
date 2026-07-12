#!/usr/bin/env python3
"""CLI wrapper for rough-vol-forecast."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.rough_vol_forecast import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-rough-vol-forecast",
        description="Rough-volatility-scaled vol forecast (Bayer-Friz-Gatheral 2016) across multiple horizons.",
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--horizons", default="1,5,20,60,120",
                    help="Comma-separated horizon days. Default 1,5,20,60,120.")
    ap.add_argument("--lookback-days", type=int, default=504)
    ap.add_argument("--hurst", type=float, default=None,
                    help="Override Hurst exponent. Default uses Livieri et al. 2018 empirical 0.14.")
    ap.add_argument("--ewma-lambda", type=float, default=0.94)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    except ValueError as e:
        print(f"ERROR: bad --horizons: {e}", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.ticker,
            horizons_days=horizons,
            lookback_days=args.lookback_days,
            hurst=args.hurst,
            ewma_lambda=args.ewma_lambda,
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
            f.write("# rough-vol-forecast run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
