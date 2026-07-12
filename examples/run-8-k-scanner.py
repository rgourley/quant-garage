#!/usr/bin/env python3
"""
CLI wrapper for the 8-k-scanner skill.

Compute lives in quant_garage/skills/eight_k_scanner.py.

    from quant_garage.skills.eight_k_scanner import run, render
    payload = run("NVDA,RKLB,AAPL", lookback_days=30)

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

from quant_garage.skills.eight_k_scanner import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-8-k-scanner",
        description="Scan SEC 8-K disclosures across a single ticker or watchlist using Massive's pre-parsed taxonomy.",
    )
    ap.add_argument("--tickers", required=True,
                    help="Comma-separated tickers or a single ticker.")
    ap.add_argument("--lookback-days", type=int, default=30,
                    help="Calendar-day window back from today. Default 30.")
    ap.add_argument("--categories", default=None,
                    help="Optional comma-separated list of primary_category values to filter to.")
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
            args.tickers,
            lookback_days=args.lookback_days,
            categories=args.categories,
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
            f.write("# 8-k-scanner run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Tickers: {','.join(payload['tickers'])}\n")
            f.write(f"Lookback: {payload['lookback_days']} days\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
