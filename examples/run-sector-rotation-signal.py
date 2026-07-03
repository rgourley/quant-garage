#!/usr/bin/env python3
"""
CLI wrapper for the sector-rotation-signal skill.

    from quant_garage.skills.sector_rotation_signal import run, render
    payload = run(rotation_window=30)

CLI usage:
    python3 examples/run-sector-rotation-signal.py --format render
    python3 examples/run-sector-rotation-signal.py --rotation-window 60 --format render

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

from quant_garage.skills.sector_rotation_signal import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-sector-rotation-signal",
        description=(
            "Detect leadership rotation across the 11 SPDR sector ETFs. "
            "Complements market-regime (state) with a change-detection layer."
        ),
    )
    ap.add_argument("--rotation-window", type=int, default=30,
                    help="Days over which to compute rank change. Default 30.")
    ap.add_argument("--rs-window", type=int, default=20,
                    help="Primary RS window in trading days. Default 20.")
    ap.add_argument("--rs-secondary-window", type=int, default=60,
                    help="Secondary RS window for context. Default 60.")
    ap.add_argument("--lookback-days", type=int, default=252,
                    help="History for RS baseline. Default 252.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}",
              file=sys.stderr)
        return 2

    try:
        payload = run(
            rotation_window=args.rotation_window,
            rs_window=args.rs_window,
            rs_secondary_window=args.rs_secondary_window,
            lookback_days=args.lookback_days,
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
            f.write(f"# sector-rotation-signal output\n\n")
            f.write(f"As of {payload['scan_params']['as_of']}, "
                    f"rotation window {payload['scan_params']['rotation_window_days']}d\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
