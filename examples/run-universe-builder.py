#!/usr/bin/env python3
"""
CLI wrapper for the universe-builder skill.

    from quant_garage.skills.universe_builder import run, render
    payload = run(min_mcap=5e9, max_week_return=-0.05)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.universe_builder import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-universe-builder",
        description="Filtered, ranked equity universe from a candidate pool.",
    )
    ap.add_argument("--candidate-source", choices=["curated", "reference", "grouped"], default="curated")
    ap.add_argument("--candidate-cap", type=int, default=15000)
    ap.add_argument("--min-mcap", type=float, default=10e9)
    ap.add_argument("--max-mcap", type=float, default=None)
    ap.add_argument("--include-sectors", default=None)
    ap.add_argument("--exclude-sectors", default=None)
    ap.add_argument("--no-mom-filter", action="store_true")
    ap.add_argument("--min-mom-3m", type=float, default=None)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--min-adv", type=float, default=None)
    ap.add_argument("--max-week-return", type=float, default=None)
    ap.add_argument("--include-types", default="CS")
    ap.add_argument("--ocf-yield-min", type=float, default=None)
    ap.add_argument("--skip-financials", action="store_true")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--lookback-days", type=int, default=63)
    ap.add_argument("--rank-by", choices=["composite", "pullback"], default="composite")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            candidate_source=args.candidate_source,
            candidate_cap=args.candidate_cap,
            min_mcap=args.min_mcap,
            max_mcap=args.max_mcap,
            include_sectors=args.include_sectors,
            exclude_sectors=args.exclude_sectors,
            no_mom_filter=args.no_mom_filter,
            min_mom_3m=args.min_mom_3m,
            min_price=args.min_price,
            min_adv=args.min_adv,
            max_week_return=args.max_week_return,
            include_types=args.include_types,
            ocf_yield_min=args.ocf_yield_min,
            skip_financials=args.skip_financials,
            top_n=args.top_n,
            lookback_days=args.lookback_days,
            rank_by=args.rank_by,
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
            f.write("# universe-builder\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
