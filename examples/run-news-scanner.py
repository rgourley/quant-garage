#!/usr/bin/env python3
"""
CLI wrapper for the news-scanner skill.

Compute lives in quant_garage/skills/news_scanner.py.

    from quant_garage.skills.news_scanner import run, render
    payload = run(["NVDA","TSLA","AAPL"], hours=24, top_n=15)

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

from quant_garage.skills.news_scanner import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-news-scanner",
        description="Watchlist news scanner: sentiment + novelty + reaction + impact.",
    )
    ap.add_argument("--watchlist", default="NVDA,TSLA,AAPL,SPY,META,NFLX",
                    help="Comma-separated tickers. Default: NVDA,TSLA,AAPL,SPY,META,NFLX")
    ap.add_argument("--hours", type=int, default=24,
                    help="Lookback window in hours. Default 24.")
    ap.add_argument("--top", type=int, default=15,
                    help="Max events to emit. Default 15.")
    ap.add_argument("--sentiment-mode", choices=["auto", "keyword"], default="auto",
                    help="auto = prefer Benzinga insights; keyword = force keyword scorer.")
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
            hours=args.hours,
            top_n=args.top,
            sentiment_mode=args.sentiment_mode,
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
            f.write(f"# news-scanner run\n\n")
            f.write(f"Generated: {payload['run_at']}\n")
            f.write(f"Watchlist: {', '.join(payload['scan_params']['watchlist'])}\n")
            f.write(f"Window: last {payload['scan_params']['window_hours']}h\n")
            f.write(f"Tier: {payload['tier']}\n\n")
            f.write("## Take\n\n")
            f.write(payload["take"] + "\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
