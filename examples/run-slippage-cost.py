#!/usr/bin/env python3
"""
CLI wrapper for the slippage-cost skill.

    from quant_garage.skills.slippage_cost import run, render
    payload = run("examples/sample-fills.csv")
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.slippage_cost import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-slippage-cost",
        description="Fill-vs-NBBO slippage analysis with exception report.",
    )
    ap.add_argument("csv_path", help="Path to fills CSV.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(args.csv_path)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
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
            f.write("# slippage-cost\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
