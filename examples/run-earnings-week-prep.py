#!/usr/bin/env python3
"""CLI wrapper for earnings-week-prep."""
from __future__ import annotations

import argparse, json, os, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.earnings_week_prep import run, render


def main() -> int:
    ap = argparse.ArgumentParser(prog="run-earnings-week-prep")
    ap.add_argument("--watchlist", required=True)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--top-n-drilldown", type=int, default=5)
    ap.add_argument("--skip-technicals", action="store_true", default=False)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    try:
        payload = run(
            watchlist=args.watchlist,
            window_days=args.window_days,
            top_n_drilldown=args.top_n_drilldown,
            include_technicals=not args.skip_technicals,
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
            f.write(f"# earnings-week-prep output\n\n{payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
