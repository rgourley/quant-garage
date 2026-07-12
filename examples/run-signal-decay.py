#!/usr/bin/env python3
"""CLI wrapper for signal-decay.

    from quant_garage.skills.signal_decay import run, render
    payload = run("SPY", signal_kind="momentum")

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

from quant_garage.skills.signal_decay import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-signal-decay",
        description="Estimate the half-life of a candidate signal via rolling IC decay fit.",
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--signal-kind", choices=["momentum", "mean_reversion", "vol_expansion", "trend_break"],
                    default="momentum")
    ap.add_argument("--signal-window", type=int, default=20)
    ap.add_argument("--forward-horizon", type=int, default=5)
    ap.add_argument("--ic-window", type=int, default=63)
    ap.add_argument("--lookback-days", type=int, default=1260)
    ap.add_argument("--format", choices=["render", "json", "both"], default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        return 2

    try:
        payload = run(
            args.ticker,
            signal_kind=args.signal_kind,
            signal_window=args.signal_window,
            forward_horizon=args.forward_horizon,
            ic_window=args.ic_window,
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
        print(rendered); print(); print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w") as f:
            f.write("# signal-decay run\n\n## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
