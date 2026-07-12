#!/usr/bin/env python3
"""CLI wrapper for zero-dte-gamma."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.zero_dte_gamma import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-zero-dte-gamma",
        description="Dealer gamma exposure aggregation for 0DTE / near-expiry options.",
    )
    ap.add_argument("--underlying", default="SPY")
    ap.add_argument("--expiration-date", default=None,
                    help="YYYY-MM-DD target expiry. Default: nearest listed expiration to today.")
    ap.add_argument("--risk-free-rate", type=float, default=0.045)
    ap.add_argument("--default-iv", type=float, default=0.15)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.underlying,
            expiration_date=args.expiration_date,
            risk_free_rate=args.risk_free_rate,
            default_iv=args.default_iv,
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
            f.write("# zero-dte-gamma run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
