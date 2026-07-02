#!/usr/bin/env python3
"""
CLI wrapper for the earnings-drilldown skill.

Tier B implementation: SEC EDGAR 8-K item 2.02 for print dates + Massive
financials for GAAP EPS actuals + options straddle for implied-vs-realized.

    from quant_garage.skills.earnings_drilldown import run, render
    payload = run("AAPL")
    payload = run("ALLO", peers=["BEAM","NTLA","CRSP","EDIT"])

CLI:
    python3 examples/run-earnings-drilldown.py --ticker AAPL --format render
    python3 examples/run-earnings-drilldown.py --ticker ALLO \\
        --peers BEAM,NTLA,CRSP,EDIT --format render

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

from quant_garage.skills.earnings_drilldown import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-earnings-drilldown",
        description="Full-fidelity earnings drilldown: implied vs realized, print history, PEAD, peer reaction.",
    )
    ap.add_argument("--ticker", required=True, help="US ticker (e.g. AAPL)")
    ap.add_argument("--peers", default=None,
                    help="Comma-separated peer override. Falls back to curated map when absent.")
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
        payload = run(args.ticker, peers=args.peers)
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
            f.write(f"# earnings-drilldown: {payload['ticker']}\n\n")
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
