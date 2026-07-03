#!/usr/bin/env python3
"""
CLI wrapper for the corporate-actions-scanner skill.

Compute lives in quant_garage/skills/corporate_actions_scanner.py.
Callers composing this skill should import run() directly:

    from quant_garage.skills.corporate_actions_scanner import run, render
    payload = run("ALLO", lookback_days=180)

CLI usage:
    python3 examples/run-corporate-actions-scanner.py --ticker ALLO
    python3 examples/run-corporate-actions-scanner.py --watchlist "ALLO,SOFI,ROKU" --lookback-days 90 --format render
    python3 examples/run-corporate-actions-scanner.py --ticker NVDA --lookback-days 365 --format both --out /tmp/nvda-corp.md

Reads MASSIVE_API_KEY from env. SEC EDGAR is public and does not need a key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.corporate_actions_scanner import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-corporate-actions-scanner",
        description=(
            "Scan for material 8-K corporate actions (offerings, splits, "
            "spin-offs, buybacks, M&A, restatements) over a lookback window."
        ),
    )
    ap.add_argument("--ticker", default=None,
                    help="Single ticker. Mutually exclusive with --watchlist.")
    ap.add_argument("--watchlist", default=None,
                    help="Comma-separated tickers.")
    ap.add_argument("--lookback-days", type=int, default=180,
                    help="How far back to scan. Default 180.")
    ap.add_argument("--material-only", action="store_true", default=True,
                    help="Filter out routine 8-K items (default: on).")
    ap.add_argument("--all-items", action="store_true", default=False,
                    help="Include all 8-K items with a known bucket, "
                         "including routine ones like officer changes.")
    ap.add_argument("--top", type=int, default=30,
                    help="Max events to surface. Default 30.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    if not args.ticker and not args.watchlist:
        print("ERROR: pass --ticker or --watchlist", file=sys.stderr)
        return 2
    if args.ticker and args.watchlist:
        print("ERROR: --ticker and --watchlist are mutually exclusive",
              file=sys.stderr)
        return 2

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}",
              file=sys.stderr)
        return 2

    watchlist = args.ticker if args.ticker else args.watchlist
    material_only = not args.all_items

    try:
        payload = run(
            watchlist,
            lookback_days=args.lookback_days,
            material_only=material_only,
            top_n=args.top,
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
            f.write(f"# corporate-actions-scanner output\n\n")
            f.write(f"Tickers: {', '.join(payload['scan_params']['tickers'])}\n")
            f.write(f"Lookback: {payload['scan_params']['lookback_days']}d\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
