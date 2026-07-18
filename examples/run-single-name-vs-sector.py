#!/usr/bin/env python3
"""
CLI wrapper for the single-name-vs-sector skill.

Compute lives in quant_garage/skills/single_name_vs_sector.py.

    from quant_garage.skills.single_name_vs_sector import run, render
    payload = run(ticker="SOFI")

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

from quant_garage.skills.single_name_vs_sector import run, render


def _parse_windows(raw: str) -> list[int]:
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            v = int(chunk)
        except ValueError as exc:
            raise SystemExit(f"--windows entry not an integer: {chunk!r}") from exc
        if v <= 0:
            raise SystemExit(f"--windows entry must be > 0: {chunk!r}")
        out.append(v)
    if not out:
        raise SystemExit("--windows requires at least one value")
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-single-name-vs-sector",
        description="Compare a name to its sector ETF and to a benchmark across windows.",
    )
    ap.add_argument("--ticker", required=True,
                    help="The single name to measure against its sector.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark for the sector-vs-market leg. Default SPY.")
    ap.add_argument("--windows", default="5,20,60,120",
                    help="Comma-separated lookback windows in trading days.")
    ap.add_argument("--sector", default=None,
                    help="SPDR sector ETF override (e.g. XLK). Required when "
                         "the ticker is not in the built-in map.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Use --sleep 13 on Free Basic.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    args = ap.parse_args()
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            ticker=args.ticker,
            benchmark=args.benchmark,
            windows=_parse_windows(args.windows),
            sector=args.sector,
            sleep=args.sleep,
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
