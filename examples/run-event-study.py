#!/usr/bin/env python3
"""
CLI wrapper for the event-study skill.

Compute lives in quant_garage/skills/event_study.py.

    from quant_garage.skills.event_study import run, render
    payload = run(ticker="NVDA", event_date="2026-05-20", event_class="earnings")

CLI examples:
    # Single event
    python3 examples/run-event-study.py --ticker NVDA \\
        --event-date 2026-05-20 --event-class earnings

    # Cross-section: most-recent print for each name
    python3 examples/run-event-study.py --tickers AAPL,NVDA,MSFT,GOOGL,META \\
        --event-class earnings --period most_recent

    # Aggregate over a date window
    python3 examples/run-event-study.py --tickers AAPL,NVDA,MSFT \\
        --event-class earnings --window 2025-06-01..2026-06-24

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

from quant_garage.skills.event_study import run, render, RESOLVERS


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-event-study",
        description="Event-study: single / cross-section / aggregate across earnings, dividend changes, or volume spikes.",
    )
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--event-class", required=True, choices=list(RESOLVERS.keys()))
    ap.add_argument("--event-date", default=None, help="YYYY-MM-DD (single event)")
    ap.add_argument("--window", default=None, help="YYYY-MM-DD..YYYY-MM-DD (aggregate)")
    ap.add_argument("--period", default=None, help="'most_recent' picks most recent event per ticker")
    ap.add_argument("--with-base-rate", action="store_true",
                    help="Single mode: pull universe base rate to anchor the reaction. Heavy.")
    ap.add_argument("--base-rate-universe", default=None,
                    help="Comma-separated universe. Default: 15 mega-caps.")
    ap.add_argument("--debug-resolver", action="store_true",
                    help="Dump per-step resolver diagnostics to stderr.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    if not args.ticker and not args.tickers:
        ap.error("Provide --ticker or --tickers")

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    # Convert window string to tuple if present
    window = None
    if args.window:
        try:
            a, b = args.window.split("..")
            window = (a.strip(), b.strip())
        except ValueError:
            print(f"ERROR: --window must be YYYY-MM-DD..YYYY-MM-DD, got {args.window!r}", file=sys.stderr)
            return 2

    try:
        payload = run(
            ticker=args.ticker,
            tickers=args.tickers,
            event_class=args.event_class,
            event_date=args.event_date,
            window=window,
            period=args.period,
            with_base_rate=args.with_base_rate,
            base_rate_universe=args.base_rate_universe,
            debug_resolver=args.debug_resolver,
        )
    except (ValueError, RuntimeError) as e:
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
            f.write(f"# event-study {payload['mode']} run\n\n")
            f.write(f"Event class: {payload['event_class']} · Tier: {payload['tier']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
