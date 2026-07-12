#!/usr/bin/env python3
"""
CLI wrapper for the risk-factor-delta skill.

Compute lives in quant_garage/skills/risk_factor_delta.py.

    from quant_garage.skills.risk_factor_delta import run, render
    payload = run("AAPL")

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

from quant_garage.skills.risk_factor_delta import run, render


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-risk-factor-delta",
        description="Diff Item 1A Risk Factors between two 10-K filings for a name via Massive's pre-parsed taxonomy.",
    )
    ap.add_argument("--ticker", required=True, help="Single stock ticker.")
    ap.add_argument("--current-filing-date", default=None,
                    help="Force a specific 'current' filing (YYYY-MM-DD). Default: most recent on record.")
    ap.add_argument("--prior-filing-date", default=None,
                    help="Force a specific 'prior' filing (YYYY-MM-DD). Default: second-most-recent on record.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: json.")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = args.format or os.environ.get("QUANT_GARAGE_OUTPUT_FORMAT", "json")
    if fmt not in ("render", "json", "both"):
        print(f"ERROR: --format must be render|json|both, got {fmt!r}", file=sys.stderr)
        return 2

    try:
        payload = run(
            args.ticker,
            current_filing_date=args.current_filing_date,
            prior_filing_date=args.prior_filing_date,
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
            f.write("# risk-factor-delta run\n\n")
            f.write(f"Generated: {payload['fetched_at']}\n")
            f.write(f"Ticker: {payload['ticker']}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## Canonical JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")
        print(f"Wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
