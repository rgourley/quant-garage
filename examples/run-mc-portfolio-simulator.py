#!/usr/bin/env python3
"""CLI wrapper for mc-portfolio-simulator.

    from quant_garage.skills.mc_portfolio_simulator import run, render
    payload = run("NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25", simulation_days=60)

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

from quant_garage.skills.mc_portfolio_simulator import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-mc-portfolio-simulator",
        description="Monte Carlo forward P&L simulator for a book.",
    )
    ap.add_argument("--positions", required=True,
                    help="'TKR=w,TKR=w,...' weights.")
    ap.add_argument("--simulation-days", type=int, default=60)
    ap.add_argument("--n-paths", type=int, default=10_000)
    ap.add_argument("--tail", choices=["normal", "student_t"], default="normal")
    ap.add_argument("--tail-df", type=float, default=4.0)
    ap.add_argument("--lookback-days", type=int, default=252)
    ap.add_argument("--vol", choices=["realized", "ewma"], default="realized",
                    help="Vol estimator. Default realized.")
    ap.add_argument("--ewma-lambda", type=float, default=0.94)
    ap.add_argument("--shrinkage", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.positions,
            simulation_days=args.simulation_days,
            n_paths=args.n_paths,
            tail=args.tail,
            tail_df=args.tail_df,
            lookback_days=args.lookback_days,
            vol_method=args.vol,
            ewma_lambda=args.ewma_lambda,
            shrinkage=args.shrinkage,
            seed=args.seed,
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
            f.write("# mc-portfolio-simulator run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
