#!/usr/bin/env python3
"""
CLI wrapper for the commodity-cycle skill.

Compute lives in quant_garage/skills/commodity_cycle.py.

    from quant_garage.skills.commodity_cycle import run, render
    payload = run(ticker="GLD", window=60)

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

from quant_garage.skills.commodity_cycle import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-commodity-cycle",
        description="Macro cycle read for one commodity ETF.",
    )
    ap.add_argument("--ticker", default="GLD",
                    help="Target commodity ETF. Default GLD.")
    ap.add_argument("--window", type=int, default=60,
                    help="Lookback in trading days. Default 60.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Use --sleep 13 on Free Basic.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    args = ap.parse_args()
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")
    if args.window <= 0:
        raise SystemExit("--window must be > 0")

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(ticker=args.ticker, window=args.window, sleep=args.sleep)
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
