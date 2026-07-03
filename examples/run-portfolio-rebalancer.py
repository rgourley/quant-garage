#!/usr/bin/env python3
"""
CLI wrapper for the portfolio-rebalancer skill.

Compute lives in quant_garage/skills/portfolio_rebalancer.py.

    from quant_garage.skills.portfolio_rebalancer import run, render
    payload = run(positions="ALLO=0.18,SOFI=0.07,...", book_value=650000,
                  max_variance_share=0.25)

CLI usage:
    python3 examples/run-portfolio-rebalancer.py \\
      --positions "JEPI=0.305,ALLO=0.183,BRK.B=0.163,GLD=0.145,SOFI=0.070" \\
      --book-value 650000 --format render

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

from quant_garage.skills.portfolio_rebalancer import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-portfolio-rebalancer",
        description=(
            "Recommend a rebalance that brings every name's variance share "
            "under a cap, respecting weight and churn caps."
        ),
    )
    ap.add_argument("--positions", default=None,
                    help="Comma-separated 'TICKER=WEIGHT' string.")
    ap.add_argument("--book", default=None,
                    help="Path to JSON book file (same shape as risk-report).")
    ap.add_argument("--book-value", type=float, default=100000.0,
                    help="Dollar value of the book. Default 100,000.")
    ap.add_argument("--max-variance-share", type=float, default=0.25,
                    help="Per-name variance-share cap. Default 0.25.")
    ap.add_argument("--max-weight", type=float, default=0.15,
                    help="Per-name weight cap. Default 0.15.")
    ap.add_argument("--max-churn", type=float, default=0.10,
                    help="Max one-way turnover per rebalance. Default 0.10.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark ticker. Default SPY.")
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="Covariance lookback. Default 252.")
    ap.add_argument("--shrinkage", type=float, default=0.05,
                    help="Correlation shrinkage. Default 0.05.")
    ap.add_argument("--min-trade-dollar", type=float, default=100.0,
                    help="Skip trades below this dollar amount. Default 100.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}",
              file=sys.stderr)
        return 2

    try:
        payload = run(
            positions=args.positions,
            book=args.book,
            book_value=args.book_value,
            max_variance_share=args.max_variance_share,
            max_weight=args.max_weight,
            max_churn=args.max_churn,
            benchmark=args.benchmark,
            lookback_days=args.lookback_days,
            shrinkage=args.shrinkage,
            min_trade_dollar=args.min_trade_dollar,
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
        print(rendered)
        print()
        print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write(f"# portfolio-rebalancer output\n\n")
            f.write(f"Book value: {payload['scan_params']['book_value']}\n")
            f.write(f"Caps: variance-share <= {payload['scan_params']['max_variance_share']}, "
                    f"weight <= {payload['scan_params']['max_weight']}, "
                    f"churn <= {payload['scan_params']['max_churn']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
