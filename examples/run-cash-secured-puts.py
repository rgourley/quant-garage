#!/usr/bin/env python3
"""
CLI wrapper for the cash-secured-puts skill.

    from quant_garage.skills.cash_secured_puts import run, render
    payload = run("AAPL", cash=50_000, stance="balanced", horizon_days=45)

CLI usage:
    python3 examples/run-cash-secured-puts.py \\
      --ticker AAPL --cash 50000 --stance balanced --horizon-days 45 \\
      --format render

Reads MASSIVE_API_KEY from env. Requires Stocks Starter plus the
Options Developer add-on (put chain snapshot).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from quant_garage import resolve_output_format, emit_to_stdout
from quant_garage.skills.cash_secured_puts import run, render, VALID_STANCES


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run-cash-secured-puts",
        description=(
            "Screen cash-secured put candidates on one underlying: yield vs "
            "assignment odds per strike and expiry, three stance picks, a take."
        ),
    )
    ap.add_argument("--ticker", required=True, help="Underlying ticker.")
    ap.add_argument("--cash", type=float, default=None,
                    help="Cash available to secure puts (USD). Sizes contracts per candidate.")
    ap.add_argument("--stance", choices=list(VALID_STANCES), default="balanced",
                    help="conservative (~0.15 delta), balanced (~0.25), aggressive (~0.35). "
                         "Drives the take line. Default balanced.")
    ap.add_argument("--horizon-days", type=int, default=45,
                    help="Preferred days to expiration. Default 45.")
    ap.add_argument("--min-dte", type=int, default=7,
                    help="Earliest expiry considered, in calendar days. Default 7.")
    ap.add_argument("--max-dte", type=int, default=None,
                    help="Latest expiry considered. Default horizon + 30.")
    ap.add_argument("--expiries", type=int, default=3,
                    help="How many expiries to analyze (most open interest first). Default 3.")
    ap.add_argument("--risk-free-rate", type=float, default=0.0,
                    help="Annual rate for the Black-Scholes N(-d2) fallback. Default 0.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between API calls. Use 13 on a rate-limited key.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Default: render (or QUANT_GARAGE_OUTPUT_FORMAT).")
    ap.add_argument("--out", type=str, default=None,
                    help="Optional path to write a markdown file with JSON + rendered layers.")
    args = ap.parse_args()

    fmt = resolve_output_format(args.format)

    try:
        payload = run(
            ticker=args.ticker,
            cash=args.cash,
            stance=args.stance,
            horizon_days=args.horizon_days,
            min_dte=args.min_dte,
            max_dte=args.max_dte,
            expiries=args.expiries,
            risk_free_rate=args.risk_free_rate,
            sleep=args.sleep,
        )
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rendered = render(payload)
    emit_to_stdout(rendered, payload, fmt)

    if args.out:
        with open(args.out, "w") as f:
            f.write("# cash-secured-puts output\n\n")
            f.write(f"{args.ticker.upper()} · stance={args.stance}\n\n")
            f.write("## Rendered\n\n```\n")
            f.write(rendered)
            f.write("\n```\n\n## JSON\n\n```json\n")
            f.write(json.dumps(payload, indent=2, default=str))
            f.write("\n```\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
