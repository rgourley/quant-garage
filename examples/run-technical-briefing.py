#!/usr/bin/env python3
"""
CLI wrapper for the technical-briefing skill.

The compute lives in quant_garage/skills/technical_briefing.py — this
file is a thin arg-parser that calls it. Agents and downstream skills
should import `run()` directly:

    from quant_garage.skills.technical_briefing import run, render
    payload = run("NVDA")

CLI usage:

    # Default: JSON to stdout (agent-friendly)
    python3 examples/run-technical-briefing.py --ticker NVDA

    # Rendered briefing to stdout
    python3 examples/run-technical-briefing.py --ticker NVDA --format render

    # Both, and also write a markdown file
    python3 examples/run-technical-briefing.py --ticker NVDA --format both --out /tmp/nvda.md

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

from quant_garage import resolve_output_format
from quant_garage.skills.technical_briefing import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-technical-briefing",
        description="Single-name technical briefing: trend regime, RSI, MACD, MAs, Bollinger, ATR, ADV.",
    )
    ap.add_argument("--ticker", required=True, help="US ticker (e.g. NVDA)")
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="Trading days of daily OHLC. Min 60. Default 252.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    # New default: JSON. Agent-first. Env var still respected.
    fmt = resolve_output_format(args.format) if args.format else \
          os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(args.ticker, lookback_days=args.lookback_days)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rendered = render(payload)
    if fmt == "render":
        print(rendered)
    elif fmt == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:  # both
        print(rendered)
        print()
        print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write(f"# technical-briefing: {payload['ticker']}\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
