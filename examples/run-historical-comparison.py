#!/usr/bin/env python3
"""CLI wrapper for historical-comparison."""
from __future__ import annotations

import argparse, json, os, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.historical_comparison import run, render


def main() -> int:
    ap = argparse.ArgumentParser(prog="run-historical-comparison")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--event-class", default="earnings",
                    choices=["earnings", "dividend_changes", "large_volume_spike"])
    ap.add_argument("--event-date", default=None)
    ap.add_argument("--period", default=None,
                    help="e.g. 'most_recent'")
    ap.add_argument("--analog-k", type=int, default=20)
    ap.add_argument("--analog-horizons", default="30,60,90,252")
    ap.add_argument("--skip-event", action="store_true", default=False)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    try:
        horizons = [int(h.strip()) for h in args.analog_horizons.split(",") if h.strip()]
    except ValueError:
        print("ERROR: --analog-horizons must be comma-separated integers",
              file=sys.stderr)
        return 2

    try:
        payload = run(
            ticker=args.ticker,
            event_class=args.event_class,
            event_date=args.event_date,
            period=args.period,
            analog_k=args.analog_k,
            analog_horizons_days=horizons,
            include_event=not args.skip_event,
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
            f.write(f"# historical-comparison output\n\n{payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
