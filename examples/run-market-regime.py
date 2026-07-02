#!/usr/bin/env python3
"""
CLI wrapper for the market-regime skill.

Compute lives in quant_garage/skills/market_regime.py.
Callers composing this skill should import run() directly:

    from quant_garage.skills.market_regime import run, render
    payload = run(lookback_days=252)

CLI usage:
    python3 examples/run-market-regime.py
    python3 examples/run-market-regime.py --format render
    python3 examples/run-market-regime.py --lookback-days 180 --benchmark SPY --format both

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

from quant_garage.skills.market_regime import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-market-regime",
        description="Daily-use macro context: SPY trend + VIX state + sector breadth + leadership.",
    )
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="Trading-day lookback for percentile/RS windows. Default 252.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Trend + RS denominator. Default SPY.")
    ap.add_argument("--vix-ticker", default="VIX",
                    help="Primary VIX ticker; falls back to I:<TICKER>. Default VIX.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    payload = run(
        lookback_days=args.lookback_days,
        benchmark=args.benchmark,
        vix_ticker=args.vix_ticker,
    )
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
            f.write("# market-regime run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"As of: {payload['as_of']}\n")
            f.write(f"Benchmark: {payload['benchmark']} · Lookback: {payload['lookback_days']} trading days\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
