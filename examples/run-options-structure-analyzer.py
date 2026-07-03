#!/usr/bin/env python3
"""
CLI wrapper for the options-structure-analyzer skill.

    from quant_garage.skills.options_structure_analyzer import run, render
    payload = run("NVDA", view="direction_bullish", horizon_days=30,
                  target_move_pct=0.08)

CLI usage:
    python3 examples/run-options-structure-analyzer.py \\
      --ticker NVDA --view direction_bullish \\
      --horizon-days 30 --target-move-pct 0.08 --format render

Reads MASSIVE_API_KEY from env. Requires options entitlement.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage.skills.options_structure_analyzer import run, render, VALID_VIEWS


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-options-structure-analyzer",
        description=(
            "Rank options structures for a specified directional, "
            "volatility, or hedge view. Payoff-at-target comparison."
        ),
    )
    ap.add_argument("--ticker", required=True,
                    help="Underlying ticker.")
    ap.add_argument("--view", required=True, choices=list(VALID_VIEWS),
                    help="Trade thesis: direction_bullish, "
                         "direction_bearish, vol_long, vol_short, or hedge.")
    ap.add_argument("--horizon-days", type=int, default=30,
                    help="Preferred days to expiration. Default 30.")
    ap.add_argument("--target-move-pct", type=float, default=0.05,
                    help="Thesis on how much the underlying moves (decimal). "
                         "Default 0.05 (5%%).")
    ap.add_argument("--otm-pct-for-wings", type=float, default=0.05,
                    help="How far OTM to place spread/strangle wings. "
                         "Default 0.05.")
    ap.add_argument("--wing-width-pct", type=float, default=0.05,
                    help="Iron-condor wing width past the shorts. "
                         "Default 0.05.")
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
            ticker=args.ticker,
            view=args.view,
            horizon_days=args.horizon_days,
            target_move_pct=args.target_move_pct,
            otm_pct_for_wings=args.otm_pct_for_wings,
            wing_width_pct=args.wing_width_pct,
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
            f.write(f"# options-structure-analyzer output\n\n")
            f.write(f"{args.ticker} · view={args.view}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
