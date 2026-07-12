#!/usr/bin/env python3
"""CLI wrapper for vs-benchmark-audit."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.vs_benchmark_audit import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-vs-benchmark-audit",
        description="Full performance audit of a book vs benchmark with deflated Sharpe + rolling IC.",
    )
    ap.add_argument("--positions", required=True,
                    help="'TKR=w,TKR=w,...' weights.")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--lookback-days", type=int, default=504)
    ap.add_argument("--ic-window", type=int, default=63)
    ap.add_argument("--n-trials-dsr", type=int, default=1)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.positions,
            benchmark=args.benchmark,
            lookback_days=args.lookback_days,
            ic_window=args.ic_window,
            n_trials_dsr=args.n_trials_dsr,
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
        print(rendered); print(); print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write("# vs-benchmark-audit run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
