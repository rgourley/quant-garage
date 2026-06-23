#!/usr/bin/env python3
"""
Reference run of the corp-actions-reconciler skill against a CSV.

Reads positions, queries Massive splits + dividends, applies the
methodology from skills/corp-actions-reconciler/references/, and emits
the dual-layer output:
  Layer 1: canonical JSON matching output-schema.json
  Layer 2: rendered exception report per references/rendering.md

Usage:
    python3 examples/run-corp-actions.py examples/sample-positions.csv

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/reconciliation-output.md (gitignored).
"""
import csv
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

if len(sys.argv) < 2:
    print("Usage: run-corp-actions.py POSITIONS.csv", file=sys.stderr)
    sys.exit(1)

CSV_PATH = sys.argv[1]
KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
TODAY = date(2026, 6, 23)

# Spinoffs overrides: there is no /v3/reference/spinoffs endpoint, so
# operators supply known spinoff events here. Format matches
# references/spinoffs-methodology.md.
SPINOFFS_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "spinoffs.json")


def fetch(path):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{e.code} on {path}: {body}")


def fetch_all(path):
    out = []
    url = f"{BASE}{path}"
    while url:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            doc = json.load(r)
        out.extend(doc.get("results", []) or [])
        next_url = doc.get("next_url")
        if next_url:
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={KEY}"
        else:
            url = None
    return out


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_positions(path):
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "ticker": r["ticker"].strip().upper(),
                "shares": float(r["shares"]),
                "cost_basis": float(r["cost_basis"]) if r.get("cost_basis") else None,
                "as_of_date": r["as_of_date"].strip(),
            })
    return rows


def load_spinoffs_overrides(path):
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def fetch_splits(ticker, as_of_date):
    """Return splits with execution_date > as_of_date, ascending."""
    results = fetch_all(
        f"/v3/reference/splits?ticker={ticker}"
        f"&execution_date.gt={as_of_date}&limit=50&order=asc&sort=execution_date"
    )
    return results


def fetch_dividends(ticker, as_of_date):
    """Return dividends with ex_dividend_date > as_of_date, ascending."""
    results = fetch_all(
        f"/v3/reference/dividends?ticker={ticker}"
        f"&ex_dividend_date.gt={as_of_date}&limit=100&order=asc&sort=ex_dividend_date"
    )
    return results


def apply_split(state, split, fetched_at):
    """
    Apply a single split record to the running state. Returns a break
    record describing the action and the post-split share count and
    cost basis.
    """
    split_to = float(split["split_to"])
    split_from = float(split["split_from"])
    ratio = split_to / split_from

    pre_shares = state["shares"]
    raw_post = pre_shares * ratio
    # Floor to whole shares; brokers pay CIL on the fractional. See
    # references/edge-cases.md.
    post_shares = math.floor(raw_post)
    cil_expected = post_shares != raw_post

    pre_basis = state["cost_basis"]
    post_basis = pre_basis / ratio if pre_basis is not None else None

    is_reverse = split_to < split_from
    kind = "reverse_split" if is_reverse else "split"
    # Ratio string: always largest number first so direction is clear.
    if is_reverse:
        ratio_str = f"{int(split_from)}-for-{int(split_to)}"
    else:
        ratio_str = f"{int(split_to)}-for-{int(split_from)}"

    state["shares"] = post_shares
    state["cost_basis"] = post_basis
    state["actions_applied"] += 1

    return {
        "kind": kind,
        "ex_date": split["execution_date"],
        "ratio": ratio_str,
        "split_to": split_to,
        "split_from": split_from,
        "pre_shares": pre_shares,
        "post_shares": post_shares,
        "pre_cost_basis": pre_basis,
        "post_cost_basis": post_basis,
        "cash_in_lieu_expected": cil_expected,
        "source": {
            "endpoint": f"https://api.massive.com/v3/reference/splits?ticker={state['ticker']}",
            "fetched_at": fetched_at,
        },
    }


def apply_dividend(state, div, fetched_at):
    """
    Apply a single dividend record. Most cash dividends are
    informational; special-cash and return-of-capital adjust basis.
    Returns a break dict only when an adjustment was made, else None.
    """
    dtype = div.get("dividend_type")
    amount = float(div.get("cash_amount") or 0.0)
    ex_date = div.get("ex_dividend_date")
    currency = div.get("currency", "USD")

    if dtype == "RC":
        # Return of capital: reduce basis by amount per share.
        pre_basis = state["cost_basis"]
        if pre_basis is None:
            return None
        post_basis = pre_basis - amount
        state["cost_basis"] = post_basis
        state["actions_applied"] += 1
        return {
            "kind": "cash_dividend_basis",
            "ex_date": ex_date,
            "amount": amount,
            "currency": currency,
            "dividend_type": dtype,
            "pre_cost_basis": pre_basis,
            "post_cost_basis": post_basis,
            "cash_in_lieu_expected": False,
            "source": {
                "endpoint": f"https://api.massive.com/v3/reference/dividends?ticker={state['ticker']}",
                "fetched_at": fetched_at,
            },
        }

    # Regular cash dividends are informational. Tracked in sources, not
    # surfaced as breaks. See references/dividends-methodology.md.
    return None


def apply_spinoff(state, spin, fetched_at):
    """
    Apply a spinoff: adjust parent basis, return a tuple
    (parent_break, new_position_break) describing the parent
    adjustment and the new subsidiary position.
    """
    ratio_y_per_x = float(spin["ratio_y_per_x"])
    ex_date = spin["ex_date"]
    sub_ticker = spin["spinoff_ticker"]

    pre_basis = state["cost_basis"]
    parent_alloc_pct = spin.get("parent_alloc_pct")
    if parent_alloc_pct is None:
        # No explicit allocation: skip basis change, flag as
        # spinoff-with-no-allocation. The new position is still created.
        post_basis = pre_basis
        sub_basis_per_share = None
        alloc_method = "manual"
    else:
        alloc_method = spin.get("alloc_method", "first_session_market_cap")
        post_basis = pre_basis * parent_alloc_pct if pre_basis is not None else None
        sub_alloc_pct = 1 - parent_alloc_pct
        if pre_basis is not None:
            total_sub_basis = pre_basis * state["shares"] * sub_alloc_pct
            raw_sub_shares = state["shares"] * ratio_y_per_x
            sub_shares_whole = math.floor(raw_sub_shares)
            sub_basis_per_share = (
                total_sub_basis / raw_sub_shares if raw_sub_shares > 0 else None
            )
        else:
            sub_basis_per_share = None

    raw_sub_shares = state["shares"] * ratio_y_per_x
    sub_shares_whole = math.floor(raw_sub_shares)
    sub_cil = sub_shares_whole != raw_sub_shares

    state["cost_basis"] = post_basis
    state["actions_applied"] += 1

    parent_break = {
        "kind": "spinoff",
        "ex_date": ex_date,
        "spinoff_ticker": sub_ticker,
        "spin_ratio_y_per_x": ratio_y_per_x,
        "alloc_method": alloc_method,
        "parent_alloc_pct": parent_alloc_pct,
        "pre_cost_basis": pre_basis,
        "post_cost_basis": post_basis,
        "post_shares": state["shares"],
        "cash_in_lieu_expected": False,
        "source": {
            "endpoint": f"spinoffs.json (operator override) + /v2/aggs/ticker/{state['ticker']}/range/1/day/{ex_date}/{ex_date}",
            "fetched_at": fetched_at,
        },
    }

    new_position_break = {
        "kind": "spinoff_new_position",
        "ex_date": ex_date,
        "spinoff_ticker": sub_ticker,
        "parent_ticker": state["ticker"],
        "spin_ratio_y_per_x": ratio_y_per_x,
        "expected_shares": sub_shares_whole,
        "expected_cost_basis": sub_basis_per_share,
        "cash_in_lieu_expected": sub_cil,
        "source": {
            "endpoint": f"spinoffs.json (operator override) + /v2/aggs/ticker/{sub_ticker}/range/1/day/{ex_date}/{ex_date}",
            "fetched_at": fetched_at,
        },
    }

    return parent_break, new_position_break


def reconcile_position(position, spinoff_records):
    """
    Walk all corporate actions for one position, return:
      - list of action_records describing each applied event
      - final state with expected_shares and expected_cost_basis
      - list of source entries for the audit trail
    """
    ticker = position["ticker"]
    as_of = position["as_of_date"]
    state = {
        "ticker": ticker,
        "shares": position["shares"],
        "cost_basis": position["cost_basis"],
        "actions_applied": 0,
    }

    sources = []
    fetched_at = now_iso()
    splits = fetch_splits(ticker, as_of)
    sources.append({
        "endpoint": f"/v3/reference/splits?ticker={ticker}&execution_date.gt={as_of}",
        "fetched_at": fetched_at,
        "ticker": ticker,
    })
    fetched_at = now_iso()
    dividends = fetch_dividends(ticker, as_of)
    sources.append({
        "endpoint": f"/v3/reference/dividends?ticker={ticker}&ex_dividend_date.gt={as_of}",
        "fetched_at": fetched_at,
        "ticker": ticker,
    })

    spins_for_ticker = [
        s for s in spinoff_records
        if s.get("parent_ticker") == ticker and s.get("ex_date", "") > as_of
    ]

    # Merge all events, sort by ex-date ascending, splits before
    # dividends on the same date (per edge-cases.md).
    events = []
    for s in splits:
        events.append(("split", s["execution_date"], 0, s))
    for d in dividends:
        events.append(("dividend", d["ex_dividend_date"], 1, d))
    for sp in spins_for_ticker:
        events.append(("spinoff", sp["ex_date"], 0, sp))
    events.sort(key=lambda e: (e[1], e[2]))

    actions = []
    new_positions = []
    for event_kind, ex_date, _, payload in events:
        action_at = now_iso()
        if event_kind == "split":
            actions.append(apply_split(state, payload, action_at))
        elif event_kind == "dividend":
            rec = apply_dividend(state, payload, action_at)
            if rec is not None:
                actions.append(rec)
        elif event_kind == "spinoff":
            parent_break, new_position = apply_spinoff(state, payload, action_at)
            actions.append(parent_break)
            new_positions.append(new_position)

    return state, actions, new_positions, sources


def fmt_shares(x):
    if x is None:
        return "n/a"
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def render(payload):
    lines = []
    s = payload["summary"]
    if s["breaks_found"] == 0:
        lines.append(
            f"{s['positions_checked']} positions checked. No breaks found."
        )
    else:
        lines.append(
            f"{s['breaks_found']} BREAKS found across {s['positions_checked']} positions checked."
        )
    if s.get("passes_count"):
        clean = [p["ticker"] for p in payload.get("passes", [])]
        if clean:
            lines.append(f"Clean: {', '.join(clean)}")
    lines.append("")

    for idx, b in enumerate(payload["breaks"], start=1):
        kind = b["kind"]
        ticker = b["ticker"]
        action = b["action"]
        if kind == "spinoff_new_position":
            parent = action.get("parent_ticker", "?")
            lines.append(f"BREAK {idx}: {ticker} (new position from {parent} spin)")
            lines.append("  Recorded:    not in input file")
            lines.append(
                f"  Action:      Spinoff distribution at {action['spin_ratio_y_per_x']} per share, ex-date {action['ex_date']}"
            )
            basis_str = (
                f", basis ${b['expected_cost_basis']:.4f}/sh"
                if b.get("expected_cost_basis") is not None else ""
            )
            lines.append(f"  Expected:    {fmt_shares(b['expected_shares'])} shares{basis_str}")
            if b.get("cash_in_lieu_expected"):
                lines.append("  Note:        Fractional from ratio, broker CIL expected")
            lines.append(f"  Source:      {b['source']['endpoint']}")
            lines.append(f"  Verified:    {b['source']['fetched_at']}")
            lines.append("")
            continue

        if kind == "spinoff":
            lines.append(f"BREAK {idx}: {ticker} (parent basis adjustment)")
            lines.append(
                f"  Recorded:    {fmt_shares(b['current_shares'])} shares as of {b['recorded_as_of']}, basis ${b['current_cost_basis']:.2f}/sh"
            )
            lines.append(
                f"  Action:      Spinoff of {action['spinoff_ticker']} at {action['spin_ratio_y_per_x']} per share, ex-date {action['ex_date']}"
            )
            lines.append(
                f"  Expected:    {fmt_shares(b['expected_shares'])} shares (no change), basis ${b['expected_cost_basis']:.4f}/sh"
            )
            lines.append(f"  Source:      {b['source']['endpoint']}")
            lines.append(f"  Verified:    {b['source']['fetched_at']}")
            lines.append("")
            continue

        # Standard split or dividend break.
        if kind == "reverse_split":
            action_str = f"{action['ratio']} reverse split, ex-date {action['ex_date']}"
        elif kind == "split":
            action_str = f"{action['ratio']} split, ex-date {action['ex_date']}"
        elif kind == "cash_dividend_basis":
            amount = action.get("amount", 0)
            action_str = f"Cash dividend ${amount:.4f}/share (RoC), ex-date {action['ex_date']}"
        else:
            action_str = f"{kind}, ex-date {action.get('ex_date')}"

        lines.append(f"BREAK {idx}: {ticker}")
        lines.append(
            f"  Recorded:    {fmt_shares(b['current_shares'])} shares as of {b['recorded_as_of']}"
        )
        lines.append(f"  Action:      {action_str}")
        lines.append(f"  Expected:    {fmt_shares(b['expected_shares'])} shares")
        delta = b["delta_shares"]
        if delta > 0:
            direction = "under-allocated"
            delta_str = f"+{fmt_shares(delta)}"
        elif delta < 0:
            direction = "over-allocated"
            delta_str = fmt_shares(delta)
        else:
            direction = "matched"
            delta_str = "0"
        lines.append(f"  Delta:       {delta_str} ({direction})")
        if (
            b.get("current_cost_basis") is not None
            and b.get("expected_cost_basis") is not None
            and abs(b["current_cost_basis"] - b["expected_cost_basis"]) > 0.005
        ):
            lines.append(
                f"  Basis:       ${b['expected_cost_basis']:.4f}/sh (was ${b['current_cost_basis']:.2f}/sh)"
            )
        if b.get("cash_in_lieu_expected"):
            lines.append("  Note:        Fractional share expected, broker CIL")
        lines.append(f"  Source:      {b['source']['endpoint']}")
        lines.append(f"  Verified:    {b['source']['fetched_at']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    positions = load_positions(CSV_PATH)
    spinoffs = load_spinoffs_overrides(SPINOFFS_OVERRIDES_PATH)

    breaks = []
    passes = []
    all_sources = []

    for pos in positions:
        ticker = pos["ticker"]
        print(f"Reconciling {ticker}...", file=sys.stderr)
        state, actions, new_positions, sources = reconcile_position(pos, spinoffs)
        all_sources.extend(sources)

        # Did the recorded position match what we expect?
        recorded_shares = pos["shares"]
        recorded_basis = pos["cost_basis"]
        expected_shares = state["shares"]
        expected_basis = state["cost_basis"]

        share_break = expected_shares != recorded_shares
        basis_break = (
            recorded_basis is not None
            and expected_basis is not None
            and abs(expected_basis - recorded_basis) > 0.005
        )

        if not actions:
            # No corporate actions touched this position: clean pass.
            passes.append({
                "ticker": ticker,
                "current_shares": recorded_shares,
                "actions_applied": 0,
            })
            continue

        if not share_break and not basis_break:
            # Actions applied but recorded position is post-action: clean.
            passes.append({
                "ticker": ticker,
                "current_shares": recorded_shares,
                "actions_applied": len(actions),
            })
            continue

        # Surface every action as a break for the audit trail.
        for action in actions:
            if action["kind"] == "spinoff_new_position":
                # Will be handled in new_positions loop.
                continue
            kind = action["kind"]
            ex_date = action["ex_date"]
            if kind in ("split", "reverse_split"):
                ratio_str = action["ratio"]
                action_obj = {
                    "ex_date": ex_date,
                    "ratio": ratio_str,
                    "split_to": action["split_to"],
                    "split_from": action["split_from"],
                }
            elif kind == "cash_dividend_basis":
                action_obj = {
                    "ex_date": ex_date,
                    "amount": action.get("amount"),
                    "currency": action.get("currency", "USD"),
                }
            elif kind == "spinoff":
                action_obj = {
                    "ex_date": ex_date,
                    "spinoff_ticker": action["spinoff_ticker"],
                    "spin_ratio_y_per_x": action["spin_ratio_y_per_x"],
                    "alloc_method": action["alloc_method"],
                    "parent_alloc_pct": action["parent_alloc_pct"],
                }
            else:
                action_obj = {"ex_date": ex_date}
            breaks.append({
                "ticker": ticker,
                "kind": kind,
                "recorded_as_of": pos["as_of_date"],
                "current_shares": recorded_shares,
                "expected_shares": action.get("post_shares", expected_shares),
                "delta_shares": (
                    action.get("post_shares", expected_shares) - recorded_shares
                ),
                "current_cost_basis": recorded_basis,
                "expected_cost_basis": action.get("post_cost_basis"),
                "cash_in_lieu_expected": action.get("cash_in_lieu_expected", False),
                "action": action_obj,
                "source": action["source"],
            })

        for new_pos in new_positions:
            breaks.append({
                "ticker": new_pos["spinoff_ticker"],
                "kind": "spinoff_new_position",
                "recorded_as_of": pos["as_of_date"],
                "current_shares": 0,
                "expected_shares": new_pos["expected_shares"],
                "delta_shares": new_pos["expected_shares"],
                "current_cost_basis": None,
                "expected_cost_basis": new_pos["expected_cost_basis"],
                "cash_in_lieu_expected": new_pos["cash_in_lieu_expected"],
                "action": {
                    "ex_date": new_pos["ex_date"],
                    "spinoff_ticker": new_pos["spinoff_ticker"],
                    "parent_ticker": new_pos["parent_ticker"],
                    "spin_ratio_y_per_x": new_pos["spin_ratio_y_per_x"],
                },
                "source": new_pos["source"],
            })

    payload = {
        "summary": {
            "positions_checked": len(positions),
            "breaks_found": len(breaks),
            "passes_count": len(passes),
            "as_of": now_iso(),
        },
        "breaks": breaks,
        "passes": passes,
        "sources": all_sources,
    }

    rendered = render(payload)

    # Write paired output.
    out_path = os.path.join(os.path.dirname(__file__), "reconciliation-output.md")
    with open(out_path, "w") as f:
        f.write("# Reconciliation run: corp-actions-reconciler\n\n")
        f.write(f"Generated: {now_iso()}\n")
        f.write(f"Input: `{CSV_PATH}`\n\n")
        f.write("## Layer 1: canonical JSON (live data)\n\n")
        f.write("```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n\n")
        f.write("## Layer 2: rendered exception report (live data)\n\n")
        f.write("```\n")
        f.write(rendered)
        f.write("```\n")

    print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
    print(rendered)


if __name__ == "__main__":
    main()
