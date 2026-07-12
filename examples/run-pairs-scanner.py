#!/usr/bin/env python3
"""
CLI wrapper for the pairs-scanner skill.

Compute lives in quant_garage/skills/pairs_scanner.py.

    from quant_garage.skills.pairs_scanner import run, render
    payload = run(["KO","PEP","MDLZ","MO"], lookback_days=252)

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

from quant_garage.skills.pairs_scanner import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-pairs-scanner",
        description="Screen every pair in a basket for cointegration and mean reversion.",
    )
    ap.add_argument("--basket", required=True,
                    help="Comma-separated tickers to scan pairwise.")
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="Trading-day window for the cointegration fit. Default 252 (~1 year).")
    ap.add_argument("--min-correlation", type=float, default=0.6,
                    help="Absolute log-return correlation prefilter. Default 0.6.")
    ap.add_argument("--min-pvalue", type=float, default=0.05,
                    help="Engle-Granger p-value ceiling for the tradeable flag. Default 0.05.")
    ap.add_argument("--min-halflife", type=float, default=2.0,
                    help="Minimum OU half-life in days. Default 2.")
    ap.add_argument("--max-halflife", type=float, default=60.0,
                    help="Maximum OU half-life in days. Default 60.")
    ap.add_argument("--z-entry", type=float, default=2.0,
                    help="Minimum |z_current| to flag a pair tradeable. Default 2.0.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.basket,
            lookback_days=args.lookback_days,
            min_correlation=args.min_correlation,
            min_pvalue=args.min_pvalue,
            min_halflife_days=args.min_halflife,
            max_halflife_days=args.max_halflife,
            z_entry=args.z_entry,
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
            f.write("# pairs-scanner run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Basket: {','.join(payload['basket'])}\n")
            f.write(f"Lookback: {payload['lookback_days']} trading days\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
