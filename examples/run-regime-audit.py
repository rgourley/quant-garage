#!/usr/bin/env python3
"""CLI wrapper for regime-audit."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.regime_audit import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-regime-audit",
        description="change-point-detector + hurst-exponent on SPY + 11 SPDR sector ETFs.",
    )
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated tickers. Default: SPY + 11 SPDR sector ETFs.")
    ap.add_argument("--lookback-days", type=int, default=504)
    ap.add_argument("--lambda-run", type=float, default=250.0)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    try:
        payload = run(
            tickers=tickers,
            lookback_days=args.lookback_days,
            lambda_run=args.lambda_run,
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
            f.write("# regime-audit run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
