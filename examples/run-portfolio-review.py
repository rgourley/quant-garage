#!/usr/bin/env python3
"""
CLI wrapper for the portfolio-review composite skill.

    from quant_garage.skills.portfolio_review import run, render
    payload = run(positions="ALLO=0.18,SOFI=0.07,...", book_value=650000)

CLI usage:
    python3 examples/run-portfolio-review.py \\
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

from quant_garage.skills.portfolio_review import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-portfolio-review",
        description=(
            "Composite portfolio review: market-regime + sector-rotation "
            "+ risk-report + earnings-blackout + macro-event-calendar + "
            "corporate-actions-scanner + portfolio-rebalancer in one call."
        ),
    )
    ap.add_argument("--positions", required=True,
                    help="Comma-separated 'TICKER=WEIGHT' string.")
    ap.add_argument("--book-value", type=float, default=100000.0,
                    help="Dollar value of the book. Default 100,000.")
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="Shared history for risk-report + rotations. Default 252.")
    ap.add_argument("--max-variance-share", type=float, default=0.25,
                    help="Rebalancer's variance-share cap. Default 0.25.")
    ap.add_argument("--max-weight", type=float, default=0.15,
                    help="Rebalancer's per-name weight cap. Default 0.15.")
    ap.add_argument("--max-churn", type=float, default=0.10,
                    help="Rebalancer's max one-way turnover. Default 0.10.")
    ap.add_argument("--corp-actions-lookback-days", type=int, default=180,
                    help="How far back to scan 8-Ks. Default 180.")
    ap.add_argument("--earnings-window-days", type=int, default=30,
                    help="Forward earnings-blackout window. Default 30.")
    ap.add_argument("--macro-window-days", type=int, default=30,
                    help="Forward macro-calendar window. Default 30.")
    ap.add_argument("--rotation-window-days", type=int, default=30,
                    help="Sector-rotation lookback. Default 30.")
    ap.add_argument("--skip-rebalance", action="store_true", default=False,
                    help="Skip the rebalancer section (context only).")
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
            book_value=args.book_value,
            lookback_days=args.lookback_days,
            max_variance_share=args.max_variance_share,
            max_weight=args.max_weight,
            max_churn=args.max_churn,
            corp_actions_lookback_days=args.corp_actions_lookback_days,
            earnings_window_days=args.earnings_window_days,
            macro_window_days=args.macro_window_days,
            rotation_window_days=args.rotation_window_days,
            include_rebalance=not args.skip_rebalance,
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
        print(rendered)
        print()
        print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write(f"# portfolio-review output\n\n")
            f.write(f"Book value: {payload['scan_params']['book_value']}\n")
            f.write(f"As of: {payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
