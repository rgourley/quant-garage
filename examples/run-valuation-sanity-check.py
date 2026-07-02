#!/usr/bin/env python3
"""
CLI wrapper for the valuation-sanity-check skill.

Compute lives in quant_garage/skills/valuation_sanity_check.py.

    from quant_garage.skills.valuation_sanity_check import run, render
    payload = run("NVDA", target_price=250, assumed_growth=0.28,
                  assumed_margin=0.60, horizon=5)

CLI usage:
    python3 examples/run-valuation-sanity-check.py NVDA \\
        --target-price 250 --assumed-growth 0.28 --assumed-margin 0.60 \\
        --horizon 5 --format render

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

from quant_garage.skills.valuation_sanity_check import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-valuation-sanity-check",
        description="Sell-side flash-note sanity check on an analyst's target/growth/margin thesis.",
    )
    ap.add_argument("ticker", nargs="?", default="NVDA")
    ap.add_argument("--target-price", type=float, required=True)
    ap.add_argument("--assumed-growth", type=float, required=True)
    ap.add_argument("--assumed-margin", type=float, required=True)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--peers", default=None, help="Comma-separated peer override.")
    ap.add_argument("--mc", action="store_true",
                    help="Run Monte Carlo fair-value distribution.")
    ap.add_argument("--mc-samples", type=int, default=10000)
    ap.add_argument("--mc-distribution", choices=["peer", "normal"], default="peer")
    ap.add_argument("--mc-seed", type=int, default=None)
    ap.add_argument("--multiple", choices=["ev_ebitda", "ev_sales", "auto"], default="auto")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.ticker,
            target_price=args.target_price,
            assumed_growth=args.assumed_growth,
            assumed_margin=args.assumed_margin,
            horizon=args.horizon,
            peers=args.peers,
            mc=args.mc,
            mc_samples=args.mc_samples,
            mc_distribution=args.mc_distribution,
            mc_seed=args.mc_seed,
            multiple=args.multiple,
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
            f.write(f"# valuation-sanity-check: {payload['subject']['ticker']}\n\n")
            f.write(f"Generated: {payload['run_at']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
