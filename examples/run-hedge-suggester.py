#!/usr/bin/env python3
"""
CLI wrapper for the hedge-suggester skill.

Compute lives in quant_garage/skills/hedge_suggester.py.

    from quant_garage.skills.hedge_suggester import run, render
    payload = run(ticker="ALLO", shares=1000, horizon_days=90)

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

from quant_garage.skills.hedge_suggester import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-hedge-suggester",
        description="Propose five option overlays ranked by cost per dollar of downside protected.",
    )
    ap.add_argument("--ticker", required=True, help="Underlying ticker of the long position.")
    ap.add_argument("--shares", type=int, default=None,
                    help="Position size in shares. Provide this or --notional.")
    ap.add_argument("--notional", type=float, default=None,
                    help="Position size in dollars (converted to shares at spot).")
    ap.add_argument("--risk-tolerance", choices=["low", "medium", "high"], default="medium",
                    help="Drives the recommended structure. Default medium.")
    ap.add_argument("--horizon-days", type=int, default=45,
                    help="Protection horizon in calendar days. Default 45.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Use --sleep 13 on Free Basic.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    args = ap.parse_args()

    if args.shares is None and args.notional is None:
        raise SystemExit("provide --shares or --notional")
    if args.shares is not None and args.shares <= 0:
        raise SystemExit("--shares must be > 0")
    if args.notional is not None and args.notional <= 0:
        raise SystemExit("--notional must be > 0")
    if args.horizon_days <= 0:
        raise SystemExit("--horizon-days must be > 0")
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            ticker=args.ticker,
            shares=args.shares,
            notional=args.notional,
            risk_tolerance=args.risk_tolerance,
            horizon_days=args.horizon_days,
            sleep=args.sleep,
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
