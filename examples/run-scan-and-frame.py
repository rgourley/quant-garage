#!/usr/bin/env python3
"""CLI wrapper for scan-and-frame."""
from __future__ import annotations

import argparse, json, os, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.scan_and_frame import run, render


def main() -> int:
    ap = argparse.ArgumentParser(prog="run-scan-and-frame")
    ap.add_argument("--candidate-source", default="curated",
                    choices=["curated", "reference"])
    ap.add_argument("--min-mcap", type=float, default=10e9)
    ap.add_argument("--max-mcap", type=float, default=None)
    ap.add_argument("--include-sectors", default=None)
    ap.add_argument("--exclude-sectors", default=None)
    ap.add_argument("--top-n-rank", type=int, default=15)
    ap.add_argument("--include-factor-research", action="store_true", default=False)
    ap.add_argument("--factor-universe-size", type=int, default=200)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    try:
        payload = run(
            candidate_source=args.candidate_source,
            min_mcap=args.min_mcap,
            max_mcap=args.max_mcap,
            include_sectors=args.include_sectors,
            exclude_sectors=args.exclude_sectors,
            top_n_rank=args.top_n_rank,
            include_factor_research=args.include_factor_research,
            factor_universe_size=args.factor_universe_size,
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
            f.write(f"# scan-and-frame output\n\n{payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
