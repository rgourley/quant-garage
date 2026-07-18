#!/usr/bin/env python3
"""
CLI wrapper for the portfolio-macro-scenario skill.

Compute lives in quant_garage/skills/portfolio_macro_scenario.py.

    from quant_garage.skills.portfolio_macro_scenario import run, render
    payload = run(book_path="examples/sample-book.csv", rates_bp=50, dxy_pct=2)

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

from quant_garage.skills.portfolio_macro_scenario import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-portfolio-macro-scenario",
        description="Shock a book by rates/DXY/oil/gold and estimate book P&L.",
    )
    ap.add_argument("--book", required=True,
                    help="Path to a book CSV (ticker,shares[,cost_basis,as_of_date]).")
    ap.add_argument("--rates-bp", type=float, default=0.0,
                    help="Parallel rate shock in basis points. +50 = rates up 50bp.")
    ap.add_argument("--dxy-pct", type=float, default=0.0,
                    help="Dollar shock in percent, applied as the UUP return.")
    ap.add_argument("--oil-pct", type=float, default=0.0,
                    help="Oil shock in percent, applied as the USO return.")
    ap.add_argument("--gld-pct", type=float, default=0.0,
                    help="Gold shock in percent, applied as the GLD return.")
    ap.add_argument("--lookback", type=int, default=252,
                    help="Trading days of daily returns for the regression. Default 252.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Use --sleep 13 on Free Basic.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    args = ap.parse_args()
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")
    if args.lookback <= 0:
        raise SystemExit("--lookback must be > 0")

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            book_path=args.book,
            rates_bp=args.rates_bp,
            dxy_pct=args.dxy_pct,
            oil_pct=args.oil_pct,
            gld_pct=args.gld_pct,
            lookback=args.lookback,
            sleep=args.sleep,
        )
    except (ValueError, FileNotFoundError) as e:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
