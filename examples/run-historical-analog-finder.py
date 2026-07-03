#!/usr/bin/env python3
"""
CLI wrapper for the historical-analog-finder skill.

    from quant_garage.skills.historical_analog_finder import run, render
    payload = run(k=20, horizon_days=[30, 60, 90, 252])

CLI usage:
    python3 examples/run-historical-analog-finder.py --format render
    python3 examples/run-historical-analog-finder.py --k 30 --horizons 30,60,90,252 --format render

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

from quant_garage.skills.historical_analog_finder import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-historical-analog-finder",
        description=(
            "Find K historical periods most similar to the current market "
            "regime and report forward SPY return distributions."
        ),
    )
    ap.add_argument("--k", type=int, default=20,
                    help="Number of nearest analogs. Default 20.")
    ap.add_argument("--horizons", default="30,60,90,252",
                    help="Comma-separated forward horizons in trading days. "
                         "Default 30,60,90,252.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark ticker. Default SPY.")
    ap.add_argument("--history-years", type=int, default=20,
                    help="Years of history to search. Default 20.")
    ap.add_argument("--min-gap-days", type=int, default=30,
                    help="Minimum calendar gap between accepted analogs "
                         "to prevent one window from dominating. Default 30.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}",
              file=sys.stderr)
        return 2

    try:
        horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    except ValueError:
        print(f"ERROR: --horizons must be comma-separated integers",
              file=sys.stderr)
        return 2

    try:
        payload = run(
            k=args.k,
            horizon_days=horizons,
            benchmark=args.benchmark,
            history_years=args.history_years,
            min_gap_days=args.min_gap_days,
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
            f.write(f"# historical-analog-finder output\n\n")
            f.write(f"K={payload['scan_params']['k']} analogs "
                    f"as of {payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
