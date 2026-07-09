#!/usr/bin/env python3
"""CLI wrapper for fixed-income-context."""
from __future__ import annotations

import argparse, json, os, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.fixed_income_context import run, render


def main() -> int:
    ap = argparse.ArgumentParser(prog="run-fixed-income-context")
    ap.add_argument("--lookback-days", type=int, default=252)
    ap.add_argument("--correlation-window", type=int, default=60)
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    try:
        payload = run(
            lookback_days=args.lookback_days,
            correlation_window=args.correlation_window,
            benchmark=args.benchmark,
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
            f.write(f"# fixed-income-context output\n\nAs of {payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
