#!/usr/bin/env python3
"""
Reference run of the corp-actions-reconciler skill against a CSV.

Reads positions, queries Massive splits + dividends, applies the
methodology from skills/corp-actions-reconciler/references/, and emits
the dual-layer output:
  Layer 1: canonical JSON matching output-schema.json
  Layer 2: rendered exception report per references/rendering.md

Dividend types handled (Massive `/v3/reference/dividends.dividend_type`):
  RC  Regular cash      -> cost-basis reduction by amount/share
  SC  Special cash      -> same math as RC, flagged "special" in render
  SD  Stock dividend    -> fractional split, ratio = (1 + amount)
  LT  Large stock div   -> treated as split per IRS Rev. Rul. (>25%)
  ST  Stock split (rare on dividends endpoint) -> routes to apply_split

Unknown dividend_type values are recorded in `tier_caveats` rather than
silently dropped (audit C9, 2026-06-26).

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

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import MassiveClient, today, utcnow_iso

if len(sys.argv) < 2:
    print("Usage: run-corp-actions.py POSITIONS.csv", file=sys.stderr)
    sys.exit(1)

CSV_PATH = sys.argv[1]
TODAY = today()

# Spinoffs overrides: there is no /v3/reference/spinoffs endpoint, so
# operators supply known spinoff events here. Format matches
# references/spinoffs-methodology.md.
SPINOFFS_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "spinoffs.json")

client = MassiveClient()


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
    """Return (splits_list, fetched_at) with execution_date > as_of_date, ascending."""
    results = []
    last_fetched = utcnow_iso()
    pages = client.paginate(
        "/v3/reference/splits",
        {
            "ticker": ticker,
            "execution_date.gt": as_of_date,
            "limit": 50,
            "order": "asc",
            "sort": "execution_date",
        },
    )
    for page, fetched_at in pages:
        results.extend(page)
        last_fetched = fetched_at
    return results, last_fetched


def fetch_dividends(ticker, as_of_date):
    """Return (dividends_list, fetched_at) with ex_dividend_date > as_of_date, ascending."""
    results = []
    last_fetched = utcnow_iso()
    pages = client.paginate(
        "/v3/reference/dividends",
        {
            "ticker": ticker,
            "ex_dividend_date.gt": as_of_date,
            "limit": 100,
            "order": "asc",
            "sort": "ex_dividend_date",
        },
    )
    for page, fetched_at in pages:
        results.extend(page)
        last_fetched = fetched_at
    return results, last_fetched


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
            "endpoint": f"https://api.polygon.io/v3/reference/splits?ticker={state['ticker']}",
            "fetched_at": fetched_at,
        },
    }


def apply_dividend(state, div, fetched_at):
    """
    Apply a single dividend record. Routes by Massive's dividend_type:

      RC (regular cash):      basis adjustment by amount/share (taxable
                              accounts treat as basis reduction for
                              return-of-capital portion). Surfaced.
      SC (special cash):      same math as RC, larger size. Surfaced
                              with a "special" flag in the rendered
                              output.
      SD (stock dividend):    fractional share-count adjustment. Treated
                              as a split with ratio = (1 + amount). Per
                              IRS Rev. Rul. 90-11, small stock dividends
                              prorate basis across new + old shares.
      ST (stock split):       Massive emits splits through the splits
                              endpoint, so this is defensive. Route to
                              apply_split() with the dividend payload
                              reshaped.
      LT (large stock div):   >25% stock dividend; per IRS guidance
                              treated as a split for tax purposes.
                              Routes through the SD/split path.

    Unknown types emit a caveat (state["caveats"] list) and are skipped
    rather than silently dropped. The rendered output names the type.

    Returns a break dict when an adjustment was made, else None.
    """
    dtype = div.get("dividend_type")
    amount = float(div.get("cash_amount") or 0.0)
    ex_date = div.get("ex_dividend_date")
    currency = div.get("currency", "USD")

    if dtype in ("RC", "SC"):
        # Cash dividend that adjusts basis. SC (special cash) is
        # typically much larger than the regular RC, but the math is
        # identical: reduce basis per share by the cash amount.
        pre_basis = state["cost_basis"]
        if pre_basis is None:
            return None
        post_basis = pre_basis - amount
        state["cost_basis"] = post_basis
        state["actions_applied"] += 1
        is_special = dtype == "SC"
        return {
            "kind": "cash_dividend_basis",
            "ex_date": ex_date,
            "amount": amount,
            "currency": currency,
            "dividend_type": dtype,
            "is_special": is_special,
            "pre_cost_basis": pre_basis,
            "post_cost_basis": post_basis,
            "cash_in_lieu_expected": False,
            "source": {
                "endpoint": f"https://api.polygon.io/v3/reference/dividends?ticker={state['ticker']}",
                "fetched_at": fetched_at,
            },
        }

    if dtype in ("SD", "LT"):
        # Stock dividend (SD) and large stock dividend (LT, >25%) both
        # adjust share count, not cash basis. IRS treats LT as a split.
        # The "ratio" is 1 + amount, where amount is the fractional rate
        # (e.g. 0.05 for a 5% stock dividend, or 1.0 for a 100% / 2-for-1
        # large stock dividend).
        if amount <= 0:
            # Defensive: no positive rate means nothing to apply.
            return None
        ratio = 1.0 + amount
        pre_shares = state["shares"]
        raw_post = pre_shares * ratio
        post_shares = math.floor(raw_post)
        cil_expected = post_shares != raw_post

        pre_basis = state["cost_basis"]
        post_basis = pre_basis / ratio if pre_basis is not None else None

        state["shares"] = post_shares
        state["cost_basis"] = post_basis
        state["actions_applied"] += 1

        kind_label = "stock_dividend" if dtype == "SD" else "large_stock_dividend"
        return {
            "kind": kind_label,
            "ex_date": ex_date,
            "dividend_type": dtype,
            "rate": amount,
            "ratio_str": f"{amount * 100:.2f}%",
            "pre_shares": pre_shares,
            "post_shares": post_shares,
            "pre_cost_basis": pre_basis,
            "post_cost_basis": post_basis,
            "cash_in_lieu_expected": cil_expected,
            "source": {
                "endpoint": f"https://api.polygon.io/v3/reference/dividends?ticker={state['ticker']}",
                "fetched_at": fetched_at,
            },
        }

    if dtype == "ST":
        # Stock split via the dividends endpoint. Massive emits splits
        # through /v3/reference/splits, so this is a defensive branch.
        # Reshape into the split-payload shape and route to apply_split.
        if amount <= 0:
            return None
        synthetic_split = {
            "execution_date": ex_date,
            "split_to": 1.0 + amount,
            "split_from": 1.0,
        }
        return apply_split(state, synthetic_split, fetched_at)

    # Unknown dividend_type: record a caveat rather than silently skip.
    # The audit (C9, 2026-06-26) called out silent skips on SC/SD/LT.
    caveats = state.setdefault("caveats", [])
    caveats.append({
        "kind": "unhandled_dividend_type",
        "ex_date": ex_date,
        "dividend_type": dtype,
        "amount": amount,
        "currency": currency,
        "note": (
            f"dividend_type={dtype!r} not handled by apply_dividend; "
            "event was not applied to shares or basis"
        ),
    })
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
        "caveats": [],
    }

    sources = []
    splits, splits_fetched_at = fetch_splits(ticker, as_of)
    sources.append({
        "endpoint": f"/v3/reference/splits?ticker={ticker}&execution_date.gt={as_of}",
        "fetched_at": splits_fetched_at,
        "ticker": ticker,
    })
    dividends, divs_fetched_at = fetch_dividends(ticker, as_of)
    sources.append({
        "endpoint": f"/v3/reference/dividends?ticker={ticker}&ex_dividend_date.gt={as_of}",
        "fetched_at": divs_fetched_at,
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
        events.append(("split", s["execution_date"], 0, s, splits_fetched_at))
    for d in dividends:
        events.append(("dividend", d["ex_dividend_date"], 1, d, divs_fetched_at))
    for sp in spins_for_ticker:
        events.append(("spinoff", sp["ex_date"], 0, sp, utcnow_iso()))
    events.sort(key=lambda e: (e[1], e[2]))

    actions = []
    new_positions = []
    for event_kind, ex_date, _, payload, action_at in events:
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

    # Stamp ticker on caveats so the main loop can attribute them.
    for c in state["caveats"]:
        c.setdefault("ticker", ticker)

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
            dtype = action.get("dividend_type", "RC")
            label = "Special cash" if action.get("is_special") or dtype == "SC" else "RC"
            action_str = (
                f"{label} dividend ${amount:.4f}/share applied to cost basis, "
                f"ex-date {action['ex_date']}"
            )
        elif kind == "stock_dividend":
            rate = action.get("rate", 0)
            action_str = (
                f"SD {rate * 100:.2f}% stock dividend applied as share-count "
                f"adjustment, ex-date {action['ex_date']}"
            )
        elif kind == "large_stock_dividend":
            rate = action.get("rate", 0)
            action_str = (
                f"LT large stock dividend {rate * 100:.2f}% (treated as split per "
                f"IRS), ex-date {action['ex_date']}"
            )
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

    caveats = payload.get("tier_caveats") or []
    if caveats:
        lines.append("Caveats")
        for c in caveats:
            if c.get("kind") == "unhandled_dividend_type":
                lines.append(
                    f"- {c.get('ticker', '?')}: unhandled dividend_type "
                    f"{c.get('dividend_type')!r} on {c.get('ex_date')} "
                    f"(amount {c.get('amount')}); event was NOT applied"
                )
            else:
                lines.append(f"- {c}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    positions = load_positions(CSV_PATH)
    spinoffs = load_spinoffs_overrides(SPINOFFS_OVERRIDES_PATH)

    breaks = []
    passes = []
    all_sources = []
    tier_caveats = []

    for pos in positions:
        ticker = pos["ticker"]
        print(f"Reconciling {ticker}...", file=sys.stderr)
        state, actions, new_positions, sources = reconcile_position(pos, spinoffs)
        all_sources.extend(sources)
        tier_caveats.extend(state.get("caveats", []))

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
                    "dividend_type": action.get("dividend_type"),
                    "is_special": action.get("is_special", False),
                }
            elif kind in ("stock_dividend", "large_stock_dividend"):
                action_obj = {
                    "ex_date": ex_date,
                    "dividend_type": action.get("dividend_type"),
                    "rate": action.get("rate"),
                    "ratio_str": action.get("ratio_str"),
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
            "as_of": utcnow_iso(),
        },
        "breaks": breaks,
        "passes": passes,
        "sources": all_sources,
        "tier_caveats": tier_caveats,
    }

    rendered = render(payload)

    # Write paired output.
    out_path = os.path.join(os.path.dirname(__file__), "reconciliation-output.md")
    with open(out_path, "w") as f:
        f.write("# Reconciliation run: corp-actions-reconciler\n\n")
        f.write(f"Generated: {utcnow_iso()}\n")
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
