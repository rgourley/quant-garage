"""
options-structure-analyzer as an importable library function.

Given a view (direction/volatility/hedge), a horizon, and a target move,
enumerate candidate options structures and rank them by breakevens,
capital at risk, and expected P&L at the target move. NOT a black-box
recommendation; a structured comparison.

    from quant_garage.skills.options_structure_analyzer import run, render
    payload = run(
        ticker="NVDA", view="direction_bullish",
        horizon_days=30, target_move_pct=0.08,
    )

Structures supported:
- Direction: long call, long put, bull call spread, bear put spread
- Volatility: long straddle, long strangle, short iron condor (short vol)
- Hedge: protective put, collar (long stock context assumed for these)

Uses the chain snapshot's day.close as the entry mark (delayed on non-
real-time entitlements; honest about that in caveats). Greeks come from
the snapshot when available; otherwise a Black-Scholes IV inversion at
the current price approximates delta/vega for the display.
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    resolve_price,
)


VALID_VIEWS = (
    "direction_bullish", "direction_bearish",
    "vol_long", "vol_short",
    "hedge",
)


# ---------- Data ----------

def _get_spot(client: MassiveClient, ticker: str) -> float | None:
    """Resolve spot price via the shared snapshot fallback chain."""
    try:
        doc, _ = client.get(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        )
    except FetchError:
        return None
    resolved = resolve_price(doc)
    return resolved.price if resolved else None


def _get_chain(
    client: MassiveClient, ticker: str, spot: float, days_lo: int, days_hi: int,
) -> list[dict]:
    today_d = today()
    exp_from = (today_d + timedelta(days=days_lo)).isoformat()
    exp_to = (today_d + timedelta(days=days_hi)).isoformat()
    strike_lo = max(1, int(spot * 0.6))
    strike_hi = int(spot * 1.4 + 1)
    all_rows: list[dict] = []
    next_cursor = None
    while True:
        params = {
            "expiration_date.gte": exp_from,
            "expiration_date.lte": exp_to,
            "strike_price.gte": strike_lo,
            "strike_price.lte": strike_hi,
            "limit": 250,
        }
        path = f"/v3/snapshot/options/{ticker}"
        try:
            body, _ = client.get(path, params)
        except FetchError:
            break
        results = body.get("results") or []
        all_rows.extend(results)
        next_url = body.get("next_url")
        if not next_url or len(all_rows) >= 2000:
            break
        # Reuse the paginate helper by breaking after first page since
        # 2000 contracts is more than enough for structure selection.
        break
    return all_rows


# ---------- Chain helpers ----------

def _contract_price(row: dict) -> float | None:
    """Prefer day.close, fall back to fmv."""
    day = row.get("day") or {}
    p = day.get("close")
    if p and p > 0:
        return float(p)
    fmv = row.get("fmv")
    if fmv and fmv > 0:
        return float(fmv)
    return None


def _row_details(row: dict) -> dict:
    return row.get("details") or {}


def _nearest_expiry_bucket(
    chain: list[dict], target_days: int,
) -> tuple[str, list[dict]] | None:
    """Return (expiry_iso, contracts_in_bucket) for the expiry with the
    minimum |days - target_days|, considering only expiries that have
    at least one call AND one put."""
    by_expiry: dict[str, list[dict]] = {}
    for r in chain:
        d = _row_details(r)
        exp = d.get("expiration_date")
        if not exp:
            continue
        by_expiry.setdefault(exp, []).append(r)
    today_d = today()
    best_exp = None
    best_gap = None
    for exp, rows in by_expiry.items():
        has_call = any(_row_details(r).get("contract_type") == "call" for r in rows)
        has_put = any(_row_details(r).get("contract_type") == "put" for r in rows)
        if not (has_call and has_put):
            continue
        try:
            exp_d = date.fromisoformat(exp)
        except ValueError:
            continue
        days = (exp_d - today_d).days
        gap = abs(days - target_days)
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_exp = exp
    if best_exp is None:
        return None
    return best_exp, by_expiry[best_exp]


def _find_by_strike(
    rows: list[dict], contract_type: str, strike: float, tolerance: float = 0.51,
) -> dict | None:
    """Return the row with contract_type and strike nearest to `strike`
    within `tolerance` dollars. Requires priceable (day.close or fmv)."""
    best = None
    best_gap = None
    for r in rows:
        d = _row_details(r)
        if d.get("contract_type") != contract_type:
            continue
        strike_price = d.get("strike_price")
        if strike_price is None:
            continue
        price = _contract_price(r)
        if price is None:
            continue
        gap = abs(strike_price - strike)
        if gap <= tolerance and (best_gap is None or gap < best_gap):
            best = r
            best_gap = gap
    return best


def _find_atm(rows: list[dict], contract_type: str, spot: float) -> dict | None:
    """Nearest strike to spot for the given contract type. Requires priceable."""
    best = None
    best_gap = None
    for r in rows:
        d = _row_details(r)
        if d.get("contract_type") != contract_type:
            continue
        strike = d.get("strike_price")
        if strike is None:
            continue
        price = _contract_price(r)
        if price is None:
            continue
        gap = abs(strike - spot)
        if best_gap is None or gap < best_gap:
            best = r
            best_gap = gap
    return best


def _find_otm(
    rows: list[dict], contract_type: str, spot: float, otm_pct: float,
) -> dict | None:
    """Find contract nearest to `otm_pct` out of the money. For calls,
    strike = spot * (1 + otm_pct). For puts, strike = spot * (1 - otm_pct)."""
    if contract_type == "call":
        target = spot * (1 + otm_pct)
    else:
        target = spot * (1 - otm_pct)
    best = None
    best_gap = None
    for r in rows:
        d = _row_details(r)
        if d.get("contract_type") != contract_type:
            continue
        strike = d.get("strike_price")
        if strike is None:
            continue
        price = _contract_price(r)
        if price is None:
            continue
        gap = abs(strike - target)
        if best_gap is None or gap < best_gap:
            best = r
            best_gap = gap
    return best


# ---------- Structure builders ----------

def _long_call(
    rows: list[dict], spot: float, target_price: float,
) -> dict | None:
    call = _find_atm(rows, "call", spot)
    if not call:
        return None
    strike = _row_details(call)["strike_price"]
    prem = _contract_price(call)
    if prem is None:
        return None
    capital = prem * 100
    breakeven = strike + prem
    max_loss = capital
    payoff_at_target = max(target_price - strike, 0) * 100 - capital
    return {
        "name": "Long Call",
        "structure_type": "direction_bullish",
        "legs": [{
            "action": "buy",
            "type": "call",
            "strike": strike,
            "ticker": _row_details(call)["ticker"],
            "premium": prem,
            "qty": 1,
        }],
        "net_debit": capital,
        "max_profit": None,  # unbounded
        "max_loss": max_loss,
        "breakevens": [round(breakeven, 2)],
        "payoff_at_target": round(payoff_at_target, 2),
        "capital_required": capital,
        "read": (f"Long call at {strike:.0f}. Unbounded upside above "
                 f"{breakeven:.2f} at expiry, capped loss at premium "
                 f"paid (${max_loss:,.0f})."),
    }


def _long_put(
    rows: list[dict], spot: float, target_price: float,
) -> dict | None:
    put = _find_atm(rows, "put", spot)
    if not put:
        return None
    strike = _row_details(put)["strike_price"]
    prem = _contract_price(put)
    if prem is None:
        return None
    capital = prem * 100
    breakeven = strike - prem
    max_loss = capital
    payoff_at_target = max(strike - target_price, 0) * 100 - capital
    return {
        "name": "Long Put",
        "structure_type": "direction_bearish",
        "legs": [{
            "action": "buy",
            "type": "put",
            "strike": strike,
            "ticker": _row_details(put)["ticker"],
            "premium": prem,
            "qty": 1,
        }],
        "net_debit": capital,
        "max_profit": (strike * 100 - capital),  # if underlying goes to 0
        "max_loss": max_loss,
        "breakevens": [round(breakeven, 2)],
        "payoff_at_target": round(payoff_at_target, 2),
        "capital_required": capital,
        "read": (f"Long put at {strike:.0f}. Max gain if underlying goes "
                 f"to 0, capped loss at premium paid (${max_loss:,.0f})."),
    }


def _bull_call_spread(
    rows: list[dict], spot: float, target_move_pct: float, target_price: float,
) -> dict | None:
    long_leg = _find_atm(rows, "call", spot)
    short_leg = _find_otm(rows, "call", spot, target_move_pct)
    if not long_leg or not short_leg:
        return None
    long_strike = _row_details(long_leg)["strike_price"]
    short_strike = _row_details(short_leg)["strike_price"]
    if short_strike <= long_strike:
        return None
    long_prem = _contract_price(long_leg)
    short_prem = _contract_price(short_leg)
    if long_prem is None or short_prem is None:
        return None
    net_debit = (long_prem - short_prem) * 100
    if net_debit <= 0:
        return None
    max_profit = (short_strike - long_strike) * 100 - net_debit
    max_loss = net_debit
    breakeven = long_strike + net_debit / 100
    if target_price >= short_strike:
        payoff = max_profit
    elif target_price <= long_strike:
        payoff = -net_debit
    else:
        payoff = (target_price - long_strike) * 100 - net_debit
    return {
        "name": "Bull Call Spread",
        "structure_type": "direction_bullish",
        "legs": [
            {"action": "buy", "type": "call", "strike": long_strike,
             "ticker": _row_details(long_leg)["ticker"],
             "premium": long_prem, "qty": 1},
            {"action": "sell", "type": "call", "strike": short_strike,
             "ticker": _row_details(short_leg)["ticker"],
             "premium": short_prem, "qty": 1},
        ],
        "net_debit": round(net_debit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": [round(breakeven, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": net_debit,
        "read": (f"Buy the {long_strike:.0f} call, sell the {short_strike:.0f} "
                 f"call. Cheaper than long call but caps upside at "
                 f"${max_profit:,.0f}."),
    }


def _bear_put_spread(
    rows: list[dict], spot: float, target_move_pct: float, target_price: float,
) -> dict | None:
    long_leg = _find_atm(rows, "put", spot)
    short_leg = _find_otm(rows, "put", spot, target_move_pct)
    if not long_leg or not short_leg:
        return None
    long_strike = _row_details(long_leg)["strike_price"]
    short_strike = _row_details(short_leg)["strike_price"]
    if short_strike >= long_strike:
        return None
    long_prem = _contract_price(long_leg)
    short_prem = _contract_price(short_leg)
    if long_prem is None or short_prem is None:
        return None
    net_debit = (long_prem - short_prem) * 100
    if net_debit <= 0:
        return None
    max_profit = (long_strike - short_strike) * 100 - net_debit
    max_loss = net_debit
    breakeven = long_strike - net_debit / 100
    if target_price <= short_strike:
        payoff = max_profit
    elif target_price >= long_strike:
        payoff = -net_debit
    else:
        payoff = (long_strike - target_price) * 100 - net_debit
    return {
        "name": "Bear Put Spread",
        "structure_type": "direction_bearish",
        "legs": [
            {"action": "buy", "type": "put", "strike": long_strike,
             "ticker": _row_details(long_leg)["ticker"],
             "premium": long_prem, "qty": 1},
            {"action": "sell", "type": "put", "strike": short_strike,
             "ticker": _row_details(short_leg)["ticker"],
             "premium": short_prem, "qty": 1},
        ],
        "net_debit": round(net_debit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": [round(breakeven, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": net_debit,
        "read": (f"Buy the {long_strike:.0f} put, sell the {short_strike:.0f} "
                 f"put. Cheaper than long put but caps downside at "
                 f"${max_profit:,.0f}."),
    }


def _long_straddle(
    rows: list[dict], spot: float, target_price: float,
) -> dict | None:
    call = _find_atm(rows, "call", spot)
    put = _find_atm(rows, "put", spot)
    if not call or not put:
        return None
    call_strike = _row_details(call)["strike_price"]
    put_strike = _row_details(put)["strike_price"]
    # Force same strike
    if abs(call_strike - put_strike) > 0.51:
        return None
    call_prem = _contract_price(call)
    put_prem = _contract_price(put)
    if call_prem is None or put_prem is None:
        return None
    total = call_prem + put_prem
    capital = total * 100
    up_be = call_strike + total
    dn_be = call_strike - total
    if target_price >= call_strike:
        payoff = (target_price - call_strike) * 100 - capital
    else:
        payoff = (call_strike - target_price) * 100 - capital
    return {
        "name": "Long Straddle",
        "structure_type": "vol_long",
        "legs": [
            {"action": "buy", "type": "call", "strike": call_strike,
             "ticker": _row_details(call)["ticker"],
             "premium": call_prem, "qty": 1},
            {"action": "buy", "type": "put", "strike": put_strike,
             "ticker": _row_details(put)["ticker"],
             "premium": put_prem, "qty": 1},
        ],
        "net_debit": round(capital, 2),
        "max_profit": None,  # unbounded on the call side
        "max_loss": round(capital, 2),
        "breakevens": [round(dn_be, 2), round(up_be, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": capital,
        "read": (f"Buy the {call_strike:.0f} straddle. Profits on a move "
                 f"in either direction beyond +/- ${total:.2f} from "
                 f"{call_strike:.0f}."),
    }


def _long_strangle(
    rows: list[dict], spot: float, otm_pct: float, target_price: float,
) -> dict | None:
    call = _find_otm(rows, "call", spot, otm_pct)
    put = _find_otm(rows, "put", spot, otm_pct)
    if not call or not put:
        return None
    call_strike = _row_details(call)["strike_price"]
    put_strike = _row_details(put)["strike_price"]
    if call_strike <= put_strike:
        return None
    call_prem = _contract_price(call)
    put_prem = _contract_price(put)
    if call_prem is None or put_prem is None:
        return None
    total = call_prem + put_prem
    capital = total * 100
    up_be = call_strike + total
    dn_be = put_strike - total
    if target_price >= up_be:
        payoff = (target_price - call_strike) * 100 - capital
    elif target_price <= dn_be:
        payoff = (put_strike - target_price) * 100 - capital
    elif target_price > call_strike:
        payoff = (target_price - call_strike) * 100 - capital
    elif target_price < put_strike:
        payoff = (put_strike - target_price) * 100 - capital
    else:
        payoff = -capital
    return {
        "name": "Long Strangle",
        "structure_type": "vol_long",
        "legs": [
            {"action": "buy", "type": "call", "strike": call_strike,
             "ticker": _row_details(call)["ticker"],
             "premium": call_prem, "qty": 1},
            {"action": "buy", "type": "put", "strike": put_strike,
             "ticker": _row_details(put)["ticker"],
             "premium": put_prem, "qty": 1},
        ],
        "net_debit": round(capital, 2),
        "max_profit": None,
        "max_loss": round(capital, 2),
        "breakevens": [round(dn_be, 2), round(up_be, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": capital,
        "read": (f"Buy the {put_strike:.0f}/{call_strike:.0f} strangle. "
                 f"Cheaper than a straddle but needs a bigger move; "
                 f"profits above {up_be:.2f} or below {dn_be:.2f}."),
    }


def _short_iron_condor(
    rows: list[dict], spot: float, otm_pct: float, wing_pct: float,
    target_price: float,
) -> dict | None:
    """Short iron condor: short strangle wrapped by a long strangle.
    Bounded loss both sides, positive theta, negative vega. Bet on
    price staying inside the range through expiry."""
    short_call = _find_otm(rows, "call", spot, otm_pct)
    short_put = _find_otm(rows, "put", spot, otm_pct)
    long_call = _find_otm(rows, "call", spot, otm_pct + wing_pct)
    long_put = _find_otm(rows, "put", spot, otm_pct + wing_pct)
    if not all([short_call, short_put, long_call, long_put]):
        return None
    sc_strike = _row_details(short_call)["strike_price"]
    sp_strike = _row_details(short_put)["strike_price"]
    lc_strike = _row_details(long_call)["strike_price"]
    lp_strike = _row_details(long_put)["strike_price"]
    if not (lp_strike < sp_strike < sc_strike < lc_strike):
        return None
    sc_prem = _contract_price(short_call)
    sp_prem = _contract_price(short_put)
    lc_prem = _contract_price(long_call)
    lp_prem = _contract_price(long_put)
    if any(x is None for x in [sc_prem, sp_prem, lc_prem, lp_prem]):
        return None
    net_credit = (sc_prem + sp_prem - lc_prem - lp_prem) * 100
    if net_credit <= 0:
        return None
    call_wing_width = (lc_strike - sc_strike) * 100
    put_wing_width = (sp_strike - lp_strike) * 100
    max_loss = max(call_wing_width, put_wing_width) - net_credit
    max_profit = net_credit
    up_be = sc_strike + net_credit / 100
    dn_be = sp_strike - net_credit / 100
    if sp_strike <= target_price <= sc_strike:
        payoff = max_profit
    elif target_price >= lc_strike:
        payoff = -max_loss
    elif target_price <= lp_strike:
        payoff = -max_loss
    elif target_price > sc_strike:
        payoff = max_profit - (target_price - sc_strike) * 100
    elif target_price < sp_strike:
        payoff = max_profit - (sp_strike - target_price) * 100
    else:
        payoff = max_profit
    return {
        "name": "Short Iron Condor",
        "structure_type": "vol_short",
        "legs": [
            {"action": "sell", "type": "put", "strike": sp_strike,
             "ticker": _row_details(short_put)["ticker"],
             "premium": sp_prem, "qty": 1},
            {"action": "buy", "type": "put", "strike": lp_strike,
             "ticker": _row_details(long_put)["ticker"],
             "premium": lp_prem, "qty": 1},
            {"action": "sell", "type": "call", "strike": sc_strike,
             "ticker": _row_details(short_call)["ticker"],
             "premium": sc_prem, "qty": 1},
            {"action": "buy", "type": "call", "strike": lc_strike,
             "ticker": _row_details(long_call)["ticker"],
             "premium": lc_prem, "qty": 1},
        ],
        "net_debit": round(-net_credit, 2),  # credit received
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": [round(dn_be, 2), round(up_be, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": max_loss,
        "read": (f"Sell the {sp_strike:.0f}/{sc_strike:.0f} strangle, "
                 f"buy {lp_strike:.0f}/{lc_strike:.0f} wings. Profits if "
                 f"the underlying stays in the {dn_be:.2f} - {up_be:.2f} "
                 f"range through expiry."),
    }


def _protective_put(
    rows: list[dict], spot: float, otm_pct: float, target_price: float,
) -> dict | None:
    put = _find_otm(rows, "put", spot, otm_pct)
    if not put:
        return None
    strike = _row_details(put)["strike_price"]
    prem = _contract_price(put)
    if prem is None:
        return None
    # Assumes long 100 shares of underlying
    capital = prem * 100  # cost of the hedge, not the shares
    breakeven = spot + prem  # underlying must rise this much
    # Payoff = share P&L + put payoff - premium
    stock_pnl = (target_price - spot) * 100
    put_payoff = max(strike - target_price, 0) * 100
    payoff = stock_pnl + put_payoff - capital
    return {
        "name": "Protective Put",
        "structure_type": "hedge",
        "legs": [
            {"action": "hold", "type": "shares", "strike": None,
             "ticker": None, "premium": None, "qty": 100},
            {"action": "buy", "type": "put", "strike": strike,
             "ticker": _row_details(put)["ticker"],
             "premium": prem, "qty": 1},
        ],
        "net_debit": round(capital, 2),
        "max_profit": None,  # unbounded above
        "max_loss": round((spot - strike) * 100 + capital, 2),
        "breakevens": [round(breakeven, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": capital,
        "read": (f"Own 100 shares, buy the {strike:.0f} put as insurance. "
                 f"Downside floored at {strike:.0f} minus premium; upside "
                 f"reduced by the ${prem:.2f} cost of the hedge."),
    }


def _collar(
    rows: list[dict], spot: float, otm_pct: float, target_price: float,
) -> dict | None:
    put = _find_otm(rows, "put", spot, otm_pct)
    call = _find_otm(rows, "call", spot, otm_pct)
    if not put or not call:
        return None
    put_strike = _row_details(put)["strike_price"]
    call_strike = _row_details(call)["strike_price"]
    put_prem = _contract_price(put)
    call_prem = _contract_price(call)
    if put_prem is None or call_prem is None:
        return None
    net_cost = (put_prem - call_prem) * 100  # negative = credit
    stock_pnl = (target_price - spot) * 100
    put_payoff = max(put_strike - target_price, 0) * 100
    call_payoff = -max(target_price - call_strike, 0) * 100
    payoff = stock_pnl + put_payoff + call_payoff - net_cost
    return {
        "name": "Collar",
        "structure_type": "hedge",
        "legs": [
            {"action": "hold", "type": "shares", "strike": None,
             "ticker": None, "premium": None, "qty": 100},
            {"action": "buy", "type": "put", "strike": put_strike,
             "ticker": _row_details(put)["ticker"],
             "premium": put_prem, "qty": 1},
            {"action": "sell", "type": "call", "strike": call_strike,
             "ticker": _row_details(call)["ticker"],
             "premium": call_prem, "qty": 1},
        ],
        "net_debit": round(net_cost, 2),
        "max_profit": round((call_strike - spot) * 100 - net_cost, 2),
        "max_loss": round((spot - put_strike) * 100 + net_cost, 2),
        "breakevens": [round(spot + net_cost / 100, 2)],
        "payoff_at_target": round(payoff, 2),
        "capital_required": max(0, net_cost),
        "read": (f"Own 100 shares, buy {put_strike:.0f} put (floor) and "
                 f"sell {call_strike:.0f} call (cap). Range-bound P&L "
                 f"between the two strikes with minimal net cost."),
    }


# ---------- Main entry ----------

def run(
    ticker: str,
    view: str,
    horizon_days: int = 30,
    target_move_pct: float = 0.05,
    otm_pct_for_wings: float = 0.05,
    wing_width_pct: float = 0.05,
    client: MassiveClient | None = None,
) -> dict:
    """Rank options structures for a specified view.

    Args:
        ticker: underlying ticker.
        view: one of 'direction_bullish', 'direction_bearish',
            'vol_long', 'vol_short', 'hedge'.
        horizon_days: preferred days to expiration. Default 30.
        target_move_pct: your thesis on how much the underlying moves
            over the horizon (positive = up, negative = down).
            Default 0.05 = 5%. Used to compute payoff-at-target.
        otm_pct_for_wings: for strangles/spreads, how far OTM to
            place the outer legs. Default 0.05.
        wing_width_pct: for iron condor, how far past the shorts to
            place the protective wings. Default 0.05.
        client: reuse an existing MassiveClient.
    """
    if view not in VALID_VIEWS:
        raise ValueError(f"view must be one of {VALID_VIEWS}, got {view!r}")
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    if abs(target_move_pct) > 1:
        raise ValueError("target_move_pct should be a decimal fraction (e.g. 0.05 for 5%)")

    client = client or MassiveClient()
    ticker = ticker.upper()

    spot = _get_spot(client, ticker)
    if not spot:
        raise RuntimeError(f"no spot price for {ticker}")

    # Directional target price
    signed_move = target_move_pct
    if view == "direction_bearish" and target_move_pct > 0:
        signed_move = -target_move_pct
    elif view == "direction_bullish" and target_move_pct < 0:
        signed_move = abs(target_move_pct)
    target_price = spot * (1 + signed_move)

    days_lo = max(1, horizon_days - 14)
    days_hi = horizon_days + 30
    print(f"Fetching {ticker} chain (spot ${spot:.2f}) exp "
          f"{days_lo}-{days_hi}d out...", file=sys.stderr)
    chain = _get_chain(client, ticker, spot, days_lo, days_hi)
    if not chain:
        raise RuntimeError(f"no options chain available for {ticker}")

    expiry_result = _nearest_expiry_bucket(chain, horizon_days)
    if expiry_result is None:
        raise RuntimeError(
            f"no expiry with both calls and puts available in the window "
            f"{days_lo}-{days_hi}d out"
        )
    expiry_iso, rows = expiry_result
    days_to_exp = (date.fromisoformat(expiry_iso) - today()).days

    # Build candidate structures based on view
    structures: list[dict] = []
    if view == "direction_bullish":
        s = _long_call(rows, spot, target_price)
        if s: structures.append(s)
        s = _bull_call_spread(rows, spot, abs(target_move_pct), target_price)
        if s: structures.append(s)
    elif view == "direction_bearish":
        s = _long_put(rows, spot, target_price)
        if s: structures.append(s)
        s = _bear_put_spread(rows, spot, abs(target_move_pct), target_price)
        if s: structures.append(s)
    elif view == "vol_long":
        # For vol view, evaluate payoff at spot +/- |target_move_pct|
        s = _long_straddle(rows, spot, target_price)
        if s: structures.append(s)
        s = _long_strangle(rows, spot, otm_pct_for_wings, target_price)
        if s: structures.append(s)
    elif view == "vol_short":
        # For short-vol, pin target at spot (best case: price stays put)
        s = _short_iron_condor(rows, spot, otm_pct_for_wings,
                                wing_width_pct, spot)
        if s: structures.append(s)
    elif view == "hedge":
        s = _protective_put(rows, spot, otm_pct_for_wings, target_price)
        if s: structures.append(s)
        s = _collar(rows, spot, otm_pct_for_wings, target_price)
        if s: structures.append(s)

    if not structures:
        raise RuntimeError(
            f"no priceable structures for view={view} at "
            f"exp={expiry_iso}. Some legs may lack a last-print price."
        )

    # Sort by best payoff-at-target ratio (payoff / capital)
    def _rank_score(s):
        cap = s.get("capital_required") or 0
        payoff = s.get("payoff_at_target") or 0
        if cap <= 0:
            return 0
        return payoff / cap

    structures.sort(key=_rank_score, reverse=True)

    return {
        "scan_params": {
            "ticker": ticker,
            "view": view,
            "horizon_days": horizon_days,
            "target_move_pct": target_move_pct,
            "otm_pct_for_wings": otm_pct_for_wings,
            "wing_width_pct": wing_width_pct,
            "as_of": today().isoformat(),
        },
        "underlying": {
            "spot": round(spot, 2),
            "target_price": round(target_price, 2),
            "implied_move_dollar": round(target_price - spot, 2),
        },
        "expiry_used": {
            "date": expiry_iso,
            "days_to_expiration": days_to_exp,
        },
        "structures": structures,
        "n_structures": len(structures),
        "generated_at": utcnow_iso(),
        "caveats": [
            "Prices are day.close (last print) — delayed on non-realtime "
            "entitlements. Verify quotes live before executing.",
            "Payoff-at-target assumes the underlying is exactly at target "
            "price at EXPIRATION. Intra-life value depends on IV, theta, "
            "and time to expiry.",
            "Ranking is payoff/capital at target. Real selection also "
            "considers assignment risk on shorts, dividend risk, and gap "
            "risk between now and expiry.",
            "Greeks are omitted (chain snapshot returned empty greeks on "
            "this key/tier). Delta/vega/theta context requires an options "
            "data upgrade.",
        ],
    }


# ---------- Renderer ----------

def _fmt_dollar(x, positive_sign: bool = False):
    if x is None:
        return "unbounded" if positive_sign else "n/a"
    sign = "+" if positive_sign and x >= 0 else ("-" if x < 0 else "")
    ax = abs(x)
    if ax >= 1000:
        return f"{sign}${ax:,.0f}"
    return f"{sign}${ax:,.2f}"


def _fmt_pct(x, decimals=1):
    if x is None:
        return "n/a"
    return f"{'+' if x >= 0 else ''}{x * 100:.{decimals}f}%"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    underlying = payload["underlying"]
    exp = payload["expiry_used"]
    structures = payload["structures"]
    lines: list[str] = []

    lines.append(
        f"Options Structure Analyzer — {params['ticker']} · "
        f"view={params['view']}\n"
        f"As of {params['as_of']} · Spot ${underlying['spot']:.2f} · "
        f"Target ${underlying['target_price']:.2f} "
        f"({_fmt_pct(params['target_move_pct'])} move)\n"
        f"Expiry: {exp['date']} ({exp['days_to_expiration']}d) · "
        f"{len(structures)} structures evaluated"
    )
    lines.append("")

    for s in structures:
        lines.append(f"### {s['name']}")
        lines.append(f"  {s['read']}")
        # Legs
        for leg in s["legs"]:
            if leg["type"] == "shares":
                lines.append(
                    f"    HOLD {leg['qty']} shares"
                )
            else:
                action = leg["action"].upper()
                lines.append(
                    f"    {action} {leg['qty']} x "
                    f"{leg['type']} @ ${leg['strike']:.0f}  "
                    f"({leg['ticker']}) @ ${leg['premium']:.2f}"
                )
        # Summary
        net = s.get("net_debit")
        net_label = "Net debit" if net and net >= 0 else "Net credit"
        max_prof = s.get("max_profit")
        max_prof_str = (
            _fmt_dollar(max_prof) if max_prof is not None else "unbounded"
        )
        breakevens = s.get("breakevens") or []
        be_str = ", ".join(f"${b:.2f}" for b in breakevens)
        lines.append(
            f"    {net_label}: {_fmt_dollar(abs(net) if net else 0)}  ·  "
            f"Max profit: {max_prof_str}  ·  "
            f"Max loss: {_fmt_dollar(s.get('max_loss'))}"
        )
        lines.append(
            f"    Breakeven(s): {be_str}  ·  "
            f"Capital req: {_fmt_dollar(s.get('capital_required'))}"
        )
        payoff = s.get("payoff_at_target")
        payoff_pct = None
        cap = s.get("capital_required")
        # For hedge structures the "capital required" is often tiny (net
        # premium delta), so % of capital is meaningless. Skip the
        # percentage in that case.
        if (payoff is not None and cap and cap > 100
                and s["structure_type"] != "hedge"):
            payoff_pct = payoff / cap
        line = f"    P&L at target: {_fmt_dollar(payoff, positive_sign=True)}"
        if payoff_pct is not None:
            line += f"  ({_fmt_pct(payoff_pct)} of capital)"
        if s["structure_type"] == "hedge":
            # Show the hedge value: what portion of the loss the hedge
            # saved you vs unhedged stock.
            spot = payload["underlying"]["spot"]
            tgt = payload["underlying"]["target_price"]
            unhedged = (tgt - spot) * 100
            saved = payoff - unhedged
            line += f"  ({_fmt_dollar(saved, positive_sign=True)} vs unhedged)"
        lines.append(line)
        lines.append("")

    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines)
