#!/usr/bin/env python3
"""CLI wrapper for filing-triangulation."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.filing_triangulation import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-filing-triangulation",
        description="Chain 8-K, risk-factor-delta, filing-sentiment, insider-flow, analyst-tracker.",
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--lookback-days-8k", type=int, default=90)
    ap.add_argument("--lookback-days-insider", type=int, default=180)
    ap.add_argument("--lookback-days-analyst", type=int, default=180)
    ap.add_argument("--exclude-directors", action="store_true")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.ticker,
            lookback_days_8k=args.lookback_days_8k,
            lookback_days_insider=args.lookback_days_insider,
            lookback_days_analyst=args.lookback_days_analyst,
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
        print(rendered); print(); print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write("# filing-triangulation run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
