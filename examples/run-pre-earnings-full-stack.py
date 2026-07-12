#!/usr/bin/env python3
"""CLI wrapper for pre-earnings-full-stack."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.pre_earnings_full_stack import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-pre-earnings-full-stack",
        description="Full pre-earnings prep on a single ticker: blackout, event-study, guidance, analyst, MC sizing.",
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--proposed-weight", type=float, default=0.10)
    ap.add_argument("--n-prior-quarters", type=int, default=8)
    ap.add_argument("--horizon-days", type=int, default=10)
    ap.add_argument("--n-paths", type=int, default=10_000)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.ticker,
            proposed_weight=args.proposed_weight,
            n_prior_quarters=args.n_prior_quarters,
            horizon_days=args.horizon_days,
            n_paths=args.n_paths,
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
            f.write("# pre-earnings-full-stack run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
