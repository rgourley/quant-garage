#!/usr/bin/env python3
"""
CLI wrapper for the portfolio-mark skill.

    from quant_garage.skills.portfolio_mark import run, render
    payload = run("examples/sample-book.csv", mode="delayed")

CLI:
    python3 examples/run-portfolio-mark.py examples/sample-book.csv --format render
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.portfolio_mark import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-portfolio-mark",
        description="Mark a CSV of positions to current fair value (delayed REST or live WebSocket).",
    )
    ap.add_argument("csv_path", help="Positions CSV.")
    ap.add_argument("--mode", choices=["delayed", "live"], default="delayed")
    ap.add_argument("--listen", type=int, default=30, help="Live mode window (seconds).")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(args.csv_path, mode=args.mode, listen=args.listen)
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
            f.write("# portfolio-mark\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
