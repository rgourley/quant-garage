#!/usr/bin/env python3
"""
CLI wrapper for the relative-strength skill.

Compute lives in quant_garage/skills/relative_strength.py.

    from quant_garage.skills.relative_strength import run, render
    payload = run(["NVDA","AMD"], benchmark="SPY", windows=[5,20,60,120])

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

from quant_garage.skills.relative_strength import run, render


def _parse_int_list(raw: str, name: str) -> list[int]:
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            v = int(chunk)
        except ValueError as exc:
            raise SystemExit(f"--{name} entry not an integer: {chunk!r}") from exc
        if v <= 0:
            raise SystemExit(f"--{name} entry must be > 0: {chunk!r}")
        out.append(v)
    if not out:
        raise SystemExit(f"--{name} requires at least one value")
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-relative-strength",
        description="Rank a watchlist by relative strength vs benchmark across multiple windows.",
    )
    ap.add_argument("--watchlist", required=True,
                    help="Comma-separated tickers to rank.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark ticker. Default SPY.")
    ap.add_argument("--windows", default="5,20,60,120",
                    help="Comma-separated trading-day windows. Default 5,20,60,120.")
    ap.add_argument("--include-sectors", action="store_true",
                    help="Also rank the 11 SPDR sector ETFs alongside.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.watchlist,
            benchmark=args.benchmark,
            windows=_parse_int_list(args.windows, "windows"),
            include_sectors=args.include_sectors,
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

    if args.out:
        with open(args.out, "w") as f:
            f.write(f"# relative-strength run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Watchlist: {','.join(payload['watchlist'])}\n")
            f.write(f"Benchmark: {payload['benchmark']}  ")
            f.write(f"Windows: {payload['windows_days']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
