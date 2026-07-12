#!/usr/bin/env python3
"""
CLI wrapper for the risk-report skill.

    from quant_garage.skills.risk_report import run, render
    payload = run("NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25")
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.risk_report import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-risk-report",
        description="PM-style risk report: vol/Sharpe/beta/VaR/ES/DD/stress/attribution.",
    )
    ap.add_argument("--positions", default=None)
    ap.add_argument("--book", default=None)
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--lookback-days", type=int, default=252)
    ap.add_argument("--var-confidence", default="0.95,0.99")
    ap.add_argument("--stress-n", type=int, default=5)
    ap.add_argument("--shrinkage", type=float, default=0.05)
    ap.add_argument("--vol", choices=["realized", "ewma"], default="realized",
                    help="Vol estimator. 'realized' = trailing-window std; 'ewma' = RiskMetrics EWMA (responds faster to recent regime). Default realized.")
    ap.add_argument("--ewma-lambda", type=float, default=0.94,
                    help="EWMA decay when --vol ewma. 0.94 is the RiskMetrics daily convention. Default 0.94.")
    ap.add_argument("--mc", action="store_true",
                    help="Enable Monte Carlo path VaR simulation from the fitted covariance matrix.")
    ap.add_argument("--mc-simulation-days", type=int, default=20,
                    help="MC horizon in trading days. Default 20 (~1 month).")
    ap.add_argument("--n-paths", type=int, default=10_000,
                    help="MC path count. Default 10,000.")
    ap.add_argument("--mc-tail", choices=["normal", "student_t"], default="normal",
                    help="Innovation distribution. 'student_t' gives fatter tails than 'normal'.")
    ap.add_argument("--mc-tail-df", type=float, default=4.0,
                    help="Student-t degrees of freedom (ignored for --mc-tail normal). Default 4.")
    ap.add_argument("--mc-seed", type=int, default=42, help="MC RNG seed. Default 42.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            positions=args.positions,
            book=args.book,
            benchmark=args.benchmark,
            lookback_days=args.lookback_days,
            var_confidence=args.var_confidence,
            stress_n=args.stress_n,
            shrinkage=args.shrinkage,
            vol_method=args.vol,
            ewma_lambda=args.ewma_lambda,
            mc=args.mc,
            mc_simulation_days=args.mc_simulation_days,
            mc_n_paths=args.n_paths,
            mc_tail=args.mc_tail,
            mc_tail_df=args.mc_tail_df,
            mc_seed=args.mc_seed,
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
            f.write("# risk-report\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
