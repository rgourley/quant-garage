#!/usr/bin/env python3
"""
CLI wrapper for the insider-flow skill.

Compute lives in quant_garage/skills/insider_flow.py.

    from quant_garage.skills.insider_flow import run, render
    payload = run("NVDA", lookback_days=180)

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

from quant_garage.skills.insider_flow import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-insider-flow",
        description="Aggregate SEC Form 4 insider activity for a ticker with signal-vs-noise classification.",
    )
    ap.add_argument("--ticker", required=True, help="Single stock ticker.")
    ap.add_argument("--lookback-days", type=int, default=180,
                    help="Calendar-day window back from today. Default 180.")
    ap.add_argument("--exclude-directors", action="store_true",
                    help="Drop pure-director rows (is_director AND NOT is_officer AND NOT is_ten_percent_owner). "
                         "Useful for names with VC/PE board reps unwinding fund positions.")
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
            args.ticker,
            lookback_days=args.lookback_days,
            exclude_directors=args.exclude_directors,
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
            f.write("# insider-flow run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Ticker: {payload['ticker']}\n")
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
