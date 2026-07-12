#!/usr/bin/env python3
"""CLI wrapper for manager-portfolio-diff.

    from quant_garage.skills.manager_portfolio_diff import run, render
    payload = run(filer="berkshire")
    payload = run(filer_cik="0001067983")

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

from quant_garage.skills.manager_portfolio_diff import run, render, FILER_ALIASES


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-manager-portfolio-diff",
        description="Diff the two most recent quarterly 13-F filings for a filer.",
    )
    ap.add_argument("--filer", default=None,
                    help=f"Alias for a well-known filer. Known: {', '.join(sorted(FILER_ALIASES.keys()))}")
    ap.add_argument("--filer-cik", default=None,
                    help="10-digit zero-padded SEC CIK. Takes precedence over --filer.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    if not args.filer and not args.filer_cik:
        print("ERROR: provide --filer or --filer-cik.", file=sys.stderr)
        return 2

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(filer=args.filer, filer_cik=args.filer_cik)
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
            f.write("# manager-portfolio-diff run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
