#!/usr/bin/env python3
"""CLI wrapper for prediction-market-monitor.

    from quant_garage.skills.prediction_market_monitor import run, render
    payload = run(series="KXFED")

Kalshi's public read-only API. No auth required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.prediction_market_monitor import run, render, COMMON_SERIES


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-prediction-market-monitor",
        description="Kalshi prediction market monitor for Fed decisions, CPI, GDP, NFP, etc.",
    )
    ap.add_argument("--series", default=None,
                    help=f"Kalshi series ticker or shortcut. Shortcuts: {', '.join(sorted(COMMON_SERIES))}")
    ap.add_argument("--keyword", default=None,
                    help="Keyword filter applied to market titles / tickers.")
    ap.add_argument("--event-ticker", default=None,
                    help="Pin a specific Kalshi event (e.g. KXFED-27APR).")
    ap.add_argument("--max-events", type=int, default=5)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            series=args.series,
            keyword=args.keyword,
            event_ticker=args.event_ticker,
            max_events=args.max_events,
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
        print(rendered); print(); print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write("# prediction-market-monitor run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
