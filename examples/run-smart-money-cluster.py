#!/usr/bin/env python3
"""CLI wrapper for smart-money-cluster."""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.smart_money_cluster import run, render, DEFAULT_COHORT


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-smart-money-cluster",
        description="Cross-fund 13-F initiations / adds / exits clustering across a cohort of well-known filers.",
    )
    ap.add_argument("--aliases", default=None,
                    help=f"Comma-separated filer aliases. Default: {','.join(DEFAULT_COHORT)}")
    ap.add_argument("--min-funds", type=int, default=2)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    aliases = None
    if args.aliases:
        aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]

    try:
        payload = run(aliases=aliases, min_funds=args.min_funds)
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
            f.write("# smart-money-cluster run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
