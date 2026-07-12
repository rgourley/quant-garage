#!/usr/bin/env python3
"""
CLI wrapper for the position-sizer skill.

Compute lives in quant_garage/skills/position_sizer.py.

    from quant_garage.skills.position_sizer import run, render
    payload = run(["NVDA","AMZN","GOOGL","META"], target_vol=0.12)

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

from quant_garage.skills.position_sizer import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-position-sizer",
        description="Four canonical sizing methods side-by-side on a fixed basket.",
    )
    ap.add_argument("--tickers", required=True, help="Comma-separated tickers.")
    ap.add_argument("--target-vol", type=float, default=0.12)
    ap.add_argument("--leverage-cap", type=float, default=1.0)
    ap.add_argument("--max-weight", type=float, default=None)
    ap.add_argument("--lookback-days", type=int, default=252)
    ap.add_argument("--kelly-edges", default=None,
                    help="Comma-separated TICKER=edge pairs.")
    ap.add_argument("--kelly-scale", type=float, default=0.25)
    ap.add_argument("--methods", default="vol_target,kelly,risk_parity,equal_weight")
    ap.add_argument("--shrinkage", type=float, default=0.05)
    ap.add_argument("--vol", choices=["realized", "ewma"], default="realized",
                    help="Vol estimator. 'realized' = trailing-window std; 'ewma' = RiskMetrics EWMA (responds faster to recent regime). Default realized.")
    ap.add_argument("--ewma-lambda", type=float, default=0.94,
                    help="EWMA decay when --vol ewma. 0.94 is the RiskMetrics daily convention. Default 0.94.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.tickers,
            target_vol=args.target_vol,
            leverage_cap=args.leverage_cap,
            max_weight=args.max_weight,
            lookback_days=args.lookback_days,
            kelly_edges=args.kelly_edges,
            kelly_scale=args.kelly_scale,
            methods=args.methods,
            shrinkage=args.shrinkage,
            vol_method=args.vol,
            ewma_lambda=args.ewma_lambda,
        )
    except (ValueError, RuntimeError) as e:
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
            f.write("# position-sizer\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
