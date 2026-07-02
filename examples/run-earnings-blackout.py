#!/usr/bin/env python3
"""
CLI wrapper for the earnings-blackout skill.

Compute lives in quant_garage/skills/earnings_blackout.py. Callers
composing this skill should import run() directly:

    from quant_garage.skills.earnings_blackout import run, render
    payload = run(["NVDA","AAPL"], window_days=7, include_past_days=3)

CLI usage:
    # Default: JSON to stdout (agent-friendly)
    python3 examples/run-earnings-blackout.py --watchlist NVDA,AAPL,MSFT

    # Rendered scan to stdout
    python3 examples/run-earnings-blackout.py --watchlist NVDA,AAPL,MSFT --format render

    # Write markdown to file too
    python3 examples/run-earnings-blackout.py --watchlist ALLO --window-days 14 \\
        --include-past-days 90 --format render --out /tmp/allo-blackout.md

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

from quant_garage.skills.earnings_blackout import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-earnings-blackout",
        description="Watchlist scanner: earnings status + consensus (Tier A) or 8-K date (Tier B).",
    )
    ap.add_argument("--watchlist", required=True,
                    help="Comma-separated tickers (e.g. NVDA,TSLA,AAPL)")
    ap.add_argument("--window-days", type=int, default=7,
                    help="Forward window in calendar days. Default 7.")
    ap.add_argument("--include-past-days", type=int, default=3,
                    help="Include earnings within past N days. Default 3.")
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
            args.watchlist,
            window_days=args.window_days,
            include_past_days=args.include_past_days,
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
            f.write(f"# earnings-blackout run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Watchlist: {payload['n_tickers']} tickers · Tier: {payload['tier']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
