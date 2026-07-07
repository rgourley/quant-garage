#!/usr/bin/env python3
"""CLI wrapper for preflight-trade."""
from __future__ import annotations

import argparse, json, os, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.preflight_trade import run, render


def main() -> int:
    ap = argparse.ArgumentParser(prog="run-preflight-trade")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--action", default="buy",
                    choices=["buy", "sell", "add", "reduce", "exit"])
    ap.add_argument("--news-last-n", type=int, default=5)
    ap.add_argument("--earnings-window-days", type=int, default=14)
    ap.add_argument("--corp-lookback-days", type=int, default=90)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    try:
        payload = run(
            ticker=args.ticker,
            action=args.action,
            news_last_n=args.news_last_n,
            earnings_window_days=args.earnings_window_days,
            corp_lookback_days=args.corp_lookback_days,
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
            f.write(f"# preflight-trade output\n\n{payload['scan_params']['ticker']} · {payload['scan_params']['action']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
