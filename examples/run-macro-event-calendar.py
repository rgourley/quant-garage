#!/usr/bin/env python3
"""
CLI wrapper for the macro-event-calendar skill.

Compute lives in quant_garage/skills/macro_event_calendar.py.

    from quant_garage.skills.macro_event_calendar import run, render
    payload = run(window_days=30)

CLI usage:
    python3 examples/run-macro-event-calendar.py --format render
    python3 examples/run-macro-event-calendar.py --window-days 60 --format render
    python3 examples/run-macro-event-calendar.py --events "FOMC,CPI,NFP" --format render

Reads MASSIVE_API_KEY from env (for SPY reaction history).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.macro_event_calendar import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-macro-event-calendar",
        description=(
            "Upcoming macro release calendar (FOMC, CPI, PPI, NFP, ISM, "
            "GDP, PCE, etc.) with historical |1-day SPY move| per event type."
        ),
    )
    ap.add_argument("--window-days", type=int, default=30,
                    help="Forward window in calendar days. Default 30.")
    ap.add_argument("--events", default=None,
                    help="Comma-separated event keys to filter "
                         "(e.g. FOMC,CPI,NFP).")
    ap.add_argument("--benchmark", default="SPY",
                    help="Reaction benchmark. Default SPY.")
    ap.add_argument("--history-days", type=int, default=730,
                    help="Historical lookback for reaction stats. Default 730.")
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
            window_days=args.window_days,
            events=args.events,
            benchmark=args.benchmark,
            history_days=args.history_days,
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
            f.write(f"# macro-event-calendar output\n\n")
            f.write(f"Window: {payload['scan_params']['window_days']}d "
                    f"as of {payload['scan_params']['as_of']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
