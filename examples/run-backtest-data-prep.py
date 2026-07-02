#!/usr/bin/env python3
"""
CLI wrapper for the backtest-data-prep skill.

    from quant_garage.skills.backtest_data_prep import run, render
    payload = run(universe="top100",
                  window=("2022-06-25","2026-06-25"),
                  out_dir="./backtest-data/")

CLI:
    python3 examples/run-backtest-data-prep.py --universe top100 \\
        --window 2022-06-25..2026-06-25 --out ./backtest-data/
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.backtest_data_prep import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-backtest-data-prep",
        description="Build a clean OHLCV parquet dataset for a US equity universe over a window.",
    )
    ap.add_argument("--universe", default="top100",
                    help="top100 | top500 | top1000 | sp500 | custom:path/to/tickers.csv")
    ap.add_argument("--window", required=True,
                    help="YYYY-MM-DD..YYYY-MM-DD inclusive both ends")
    ap.add_argument("--out", required=True, help="Output directory.")
    ap.add_argument("--survivorship", choices=["biased"], default="biased")
    ap.add_argument("--interface", choices=["auto", "flat-files", "rest"], default="auto")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        a, b = args.window.split("..")
    except ValueError:
        print("ERROR: --window must be YYYY-MM-DD..YYYY-MM-DD", file=sys.stderr)
        return 2

    try:
        payload = run(
            universe=args.universe,
            window=(a.strip(), b.strip()),
            out_dir=args.out,
            survivorship=args.survivorship,
            interface=args.interface,
        )
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

    print(f"\nDONE. Files in {args.out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
