"""
hedge-suggester as an importable library function.

risk-report flags positions carrying most of the book's variance.
options-flow shows what others are doing. Neither proposes what to do.
hedge-suggester takes one long position and returns concrete, live-priced
option overlays ranked by cost per dollar of downside protected.

    from quant_garage.skills.hedge_suggester import run, render
    payload = run(ticker="ALLO", shares=1000, horizon_days=90)
    print(render(payload))

Method:
  1. Resolve underlying spot (v2 snapshot with best-price fallback).
  2. Pull the options chain around the horizon expiry.
  3. Construct five standard overlays against the LONG position, priced
     from chain mids: covered_call, protective_put, collar, put_spread,
     ratio_put_spread.
  4. Rank by cost per dollar of downside protected.
  5. IV context: ATM IV vs 20-day realized vol as a variance-risk-premium
     proxy (not a true IV percentile).
  6. Caveat every structure (liquidity floors, tail risk) and the run.

This proposes, it does not advise. Mid-price fills are optimistic, greeks
are point-in-time, and assignment risk is real.
"""
from __future__ import annotations

import math
import sys
import time
from datetime import date, datetime, timedelta, timezone

from .. import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    resolve_price,
)


# ----- Tunables -----

CONTRACT_MULTIPLIER = 100
OI_FLOOR = 100
SPREAD_PCT_CEILING = 0.15
OTM_CALL_TARGET = 0.05
PROTECTIVE_PUT_TARGET = 0.00
SPREAD_LOW_TARGET = 0.10
RATIO_LOW_TARGET = 0.15
STRIKE_BAND_PCT = 0.30
REALIZED_VOL_WINDOW = 20
IV_EXPENSIVE_RATIO = 1.30
IV_CHEAP_RATIO = 1.00

_RATE_LIMIT_COOLDOWN_SECONDS = 13


class _State:
    def __init__(self, sleep_between: float) -> None:
        self.client = MassiveClient()
        self.today = today()
        self.rate_limited: set[str] = set()
        self.sleep_between = sleep_between


# ----- HTTP -----

def _fetch_daily_aggs(state: _State, ticker: str, calendar_days: int) -> list[dict]:
    end = state.today
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        doc, _ = state.client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker} aggs; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = state.client.get(path, params)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} aggs after cooldown: {exc}",
                  file=sys.stderr)
            state.rate_limited.add(ticker)
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        return []
    finally:
        if state.sleep_between > 0:
            time.sleep(state.sleep_between)

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    return rows


def _get_spot(state: _State, ticker: str) -> tuple[float | None, str]:
    try:
        doc, _ = state.client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    except FetchError as exc:
        print(f"  WARN: snapshot for {ticker}: {exc}", file=sys.stderr)
        return None, "no_price"
    res = resolve_price(doc)
    return res.price, res.source


def _get_chain(state: _State, ticker: str, spot: float, horizon_days: int) -> list[dict]:
    strike_lo = int(spot * (1 - STRIKE_BAND_PCT))
    strike_hi = int(spot * (1 + STRIKE_BAND_PCT) + 1)
    exp_from = (state.today + timedelta(days=max(0, horizon_days - 14))).isoformat()
    exp_to = (state.today + timedelta(days=horizon_days + 45)).isoformat()
    path = (
        f"/v3/snapshot/options/{ticker}"
        f"?expiration_date.gte={exp_from}&expiration_date.lte={exp_to}"
        f"&strike_price.gte={strike_lo}&strike_price.lte={strike_hi}"
        f"&limit=250"
    )
    try:
        doc, _ = state.client.get(path)
    except FetchError as exc:
        print(f"  WARN: chain for {ticker}: {exc}", file=sys.stderr)
        return []
    return doc.get("results") or []


# ----- Chain parsing -----

def _parse_contract(o: dict) -> dict | None:
    det = o.get("details") or {}
    occ = det.get("ticker")
    ctype = det.get("contract_type")
    strike = det.get("strike_price")
    expiry = det.get("expiration_date")
    if not occ or ctype not in ("put", "call") or strike is None or not expiry:
        return None

    q = o.get("last_quote") or {}
    bid = q.get("bid")
    ask = q.get("ask")
    mid = q.get("midpoint")
    if mid is None and bid is not None and ask is not None and ask >= bid:
        mid = (bid + ask) / 2.0
    if mid is None:
        day = o.get("day") or {}
        mid = day.get("close") or day.get("vwap")

    g = o.get("greeks") or {}
    return {
        "occ": occ,
        "type": ctype,
        "strike": float(strike),
        "expiry": expiry,
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "mid": float(mid) if mid is not None else None,
        "oi": o.get("open_interest"),
        "iv": o.get("implied_volatility"),
        "delta": g.get("delta"),
        "gamma": g.get("gamma"),
        "theta": g.get("theta"),
    }


def _choose_expiry(state: _State, contracts: list[dict], horizon_days: int) -> str | None:
    horizon_date = state.today + timedelta(days=horizon_days)
    expiries = sorted({c["expiry"] for c in contracts})
    if not expiries:
        return None
    at_or_beyond = [e for e in expiries if date.fromisoformat(e) >= horizon_date]
    if at_or_beyond:
        return at_or_beyond[0]
    return expiries[-1]


def _nearest_strike(strikes: list[float], target: float,
                    prefer: str = "any") -> float | None:
    if not strikes:
        return None
    pool = strikes
    if prefer == "below":
        below = [s for s in strikes if s <= target + 1e-9]
        pool = below or strikes
    elif prefer == "above":
        above = [s for s in strikes if s >= target - 1e-9]
        pool = above or strikes
    return min(pool, key=lambda s: abs(s - target))


# ----- Payoff math -----

def _leg_expiry_value(leg: dict, s: float) -> float:
    k = leg["strike"]
    if leg["type"] == "call":
        intrinsic = max(s - k, 0.0)
    else:
        intrinsic = max(k - s, 0.0)
    sign = 1.0 if leg["action"] == "buy" else -1.0
    return sign * intrinsic * CONTRACT_MULTIPLIER * leg["qty"]


def _structure_pnl(legs: list[dict], shares: int, s0: float,
                   net_cost_usd: float, s: float) -> float:
    stock_pnl = (s - s0) * shares
    opt = sum(_leg_expiry_value(leg, s) for leg in legs)
    return stock_pnl + opt - net_cost_usd


def _leg_premium_usd(leg: dict) -> float | None:
    if leg["mid"] is None:
        return None
    sign = 1.0 if leg["action"] == "buy" else -1.0
    return sign * leg["mid"] * CONTRACT_MULTIPLIER * leg["qty"]


def _leg_greek_contrib(leg: dict, greek: str) -> float | None:
    g = leg.get(greek)
    if g is None:
        return None
    sign = 1.0 if leg["action"] == "buy" else -1.0
    return sign * g * CONTRACT_MULTIPLIER * leg["qty"]


def _liquidity_flags(legs: list[dict]) -> list[str]:
    flags: list[str] = []
    for leg in legs:
        tag = f"{leg['action']} {leg['strike']:g}{leg['type'][0].upper()}"
        oi = leg.get("oi")
        if oi is None:
            flags.append(f"{tag}: open interest unavailable")
        elif oi < OI_FLOOR:
            flags.append(f"{tag}: thin OI {oi} (below {OI_FLOOR} floor)")
        bid, ask, mid = leg.get("bid"), leg.get("ask"), leg.get("mid")
        if bid is not None and ask is not None and mid and mid > 0:
            spread_pct = (ask - bid) / mid
            if spread_pct > SPREAD_PCT_CEILING:
                flags.append(
                    f"{tag}: wide bid-ask {spread_pct * 100:.0f}% "
                    f"(above {SPREAD_PCT_CEILING * 100:.0f}% ceiling); "
                    f"mid fill unlikely"
                )
        else:
            flags.append(f"{tag}: no two-sided quote; mid is a fallback estimate")
    return flags


def _build_structure(name: str, legs: list[dict], shares: int, s0: float,
                     protection_ceiling: float | None,
                     protection_floor: float | None,
                     tradeoff: str, upside_cap: float | None = None,
                     extra_caveats: list[str] | None = None) -> dict | None:
    premiums = [_leg_premium_usd(leg) for leg in legs]
    if any(p is None for p in premiums):
        return None
    net_cost_usd = float(sum(premiums))
    net_cost_per_share = net_cost_usd / shares if shares else 0.0
    notional = s0 * shares
    net_cost_pct = (net_cost_usd / notional) if notional else None

    grid = [s0 * i / 200.0 for i in range(0, 601)]
    pnls = [_structure_pnl(legs, shares, s0, net_cost_usd, s) for s in grid]
    max_loss_usd = min(pnls)
    max_gain_usd = max(pnls)
    step = s0 / 200.0
    slope_top = pnls[-1] - pnls[-2]
    uncapped_upside = slope_top > shares * step * 0.25

    if net_cost_usd < 0:
        breakeven = s0 + net_cost_per_share
    else:
        breakeven = s0 + net_cost_per_share

    if protection_ceiling is not None and protection_floor is not None:
        downside_protected_usd = max(0.0, (protection_ceiling - protection_floor) * shares)
    else:
        downside_protected_usd = None

    if downside_protected_usd and downside_protected_usd > 0:
        cost_per_dollar_protected = net_cost_usd / downside_protected_usd
    else:
        cost_per_dollar_protected = None

    deltas = [_leg_greek_contrib(leg, "delta") for leg in legs]
    gammas = [_leg_greek_contrib(leg, "gamma") for leg in legs]
    thetas = [_leg_greek_contrib(leg, "theta") for leg in legs]
    net_delta = float(shares) + sum(d for d in deltas if d is not None) \
        if all(d is not None for d in deltas) else None
    net_gamma = sum(g for g in gammas if g is not None) \
        if all(g is not None for g in gammas) else None
    net_theta = sum(t for t in thetas if t is not None) \
        if all(t is not None for t in thetas) else None

    caveats = _liquidity_flags(legs)
    if extra_caveats:
        caveats = list(extra_caveats) + caveats

    return {
        "name": name,
        "legs": [
            {
                "action": leg["action"],
                "type": leg["type"],
                "strike": leg["strike"],
                "qty_contracts": leg["qty"],
                "occ_ticker": leg.get("occ"),
                "mid": leg.get("mid"),
                "bid": leg.get("bid"),
                "ask": leg.get("ask"),
                "open_interest": leg.get("oi"),
                "iv": leg.get("iv"),
                "delta": leg.get("delta"),
                "gamma": leg.get("gamma"),
                "theta": leg.get("theta"),
            }
            for leg in legs
        ],
        "net_cost_usd": round(net_cost_usd, 2),
        "net_cost_per_share": round(net_cost_per_share, 4),
        "net_cost_pct_of_notional": round(net_cost_pct, 5) if net_cost_pct is not None else None,
        "is_credit": net_cost_usd < 0,
        "breakeven": round(breakeven, 2),
        "protection_ceiling": round(protection_ceiling, 2) if protection_ceiling is not None else None,
        "protection_floor": round(protection_floor, 2) if protection_floor is not None else None,
        "upside_cap": round(upside_cap, 2) if upside_cap is not None else None,
        "downside_protected_usd": round(downside_protected_usd, 2) if downside_protected_usd is not None else None,
        "cost_per_dollar_protected": round(cost_per_dollar_protected, 4) if cost_per_dollar_protected is not None else None,
        "max_loss_usd": round(max_loss_usd, 2),
        "max_gain_usd": None if uncapped_upside else round(max_gain_usd, 2),
        "max_gain_uncapped": bool(uncapped_upside),
        "net_delta": round(net_delta, 1) if net_delta is not None else None,
        "net_gamma": round(net_gamma, 4) if net_gamma is not None else None,
        "net_theta": round(net_theta, 2) if net_theta is not None else None,
        "tradeoff": tradeoff,
        "caveats": caveats,
    }


def _find(contracts: list[dict], ctype: str, strike: float, expiry: str) -> dict | None:
    for c in contracts:
        if c["type"] == ctype and c["expiry"] == expiry and abs(c["strike"] - strike) < 1e-6:
            return c
    return None


def _leg(contract: dict, action: str, qty: int) -> dict:
    d = dict(contract)
    d["action"] = action
    d["qty"] = qty
    return d


# ----- IV context -----

def _realized_vol(rows: list[dict], window: int) -> float | None:
    if len(rows) < window + 1:
        return None
    closes = [r["close"] for r in rows[-(window + 1):]]
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def _iv_context(atm_iv: float | None, rv: float | None) -> dict:
    if atm_iv is None or rv is None or rv <= 0:
        return {
            "atm_iv": atm_iv,
            "realized_vol_20d": rv,
            "iv_to_realized_ratio": None,
            "label": "unknown",
            "note": (
                "IV-vs-realized context unavailable (missing ATM IV or "
                "underlying history). True IV percentile needs a historical "
                "IV series, which this run does not fetch."
            ),
        }
    ratio = atm_iv / rv
    if ratio >= IV_EXPENSIVE_RATIO:
        label = "expensive"
    elif ratio <= IV_CHEAP_RATIO:
        label = "cheap"
    else:
        label = "fair"
    return {
        "atm_iv": round(atm_iv, 4),
        "realized_vol_20d": round(rv, 4),
        "iv_to_realized_ratio": round(ratio, 2),
        "label": label,
        "note": (
            "Proxy only: ATM implied vol versus 20-day realized vol (the "
            "variance risk premium), not a true IV percentile. A ratio well "
            "above 1 means options are pricing more vol than the stock has "
            "recently delivered, so protection is relatively expensive."
        ),
    }


# ----- Public API -----

def run(
    ticker: str,
    shares: int | None = None,
    notional: float | None = None,
    risk_tolerance: str = "medium",
    horizon_days: int = 45,
    sleep: float = 0.0,
) -> dict:
    """Propose five option overlays against a long position and rank by cost/protection."""
    if shares is None and notional is None:
        raise ValueError("provide shares or notional")
    if risk_tolerance not in ("low", "medium", "high"):
        raise ValueError("risk_tolerance must be low, medium, or high")

    state = _State(sleep)
    ticker = ticker.strip().upper()
    horizon_days = int(horizon_days)

    tier_caveats: list[str] = []
    sources: list[dict] = []

    spot, spot_source = _get_spot(state, ticker)
    sources.append({
        "endpoint": f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
        "fetched_at": utcnow_iso(),
        "context": "underlying spot price (best-price fallback chain)",
    })
    if spot is None or spot <= 0:
        return {
            "skill": "hedge-suggester",
            "as_of": state.today.isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "error": "no spot price for underlying; cannot price hedges",
            "position": None,
            "expiry": None,
            "spot": None,
            "spot_source": spot_source,
            "structures": [],
            "ranking": [],
            "iv_context": _iv_context(None, None),
            "take": f"No spot price available for {ticker}; cannot propose hedges.",
            "tier_caveats": [
                "Underlying snapshot returned no price. Check the ticker and "
                "that the key carries Stocks Starter or higher."
            ],
            "sources": sources,
        }

    if shares is not None:
        shares = int(shares)
    else:
        shares = int(notional // spot)
    if shares <= 0:
        raise ValueError("resolved share count is zero; check shares/notional")

    n_contracts = shares // CONTRACT_MULTIPLIER
    if n_contracts <= 0:
        n_contracts = 1
        tier_caveats.append(
            f"Position of {shares} shares is below one round lot (100). "
            f"Structures are priced with 1 contract as an illustration; the "
            f"option leg over-hedges the stock."
        )
    notional_usd = spot * shares

    raw = _get_chain(state, ticker, spot, horizon_days)
    sources.append({
        "endpoint": f"/v3/snapshot/options/{ticker}?expiration_date.gte=&expiration_date.lte=&strike_price.gte=&strike_price.lte=",
        "fetched_at": utcnow_iso(),
        "context": "options chain snapshot (bid/ask, OI, IV, greeks) around horizon expiry",
    })
    contracts = [c for c in (_parse_contract(o) for o in raw) if c is not None]

    agg_rows = _fetch_daily_aggs(state, ticker, calendar_days=int(REALIZED_VOL_WINDOW * 2.5) + 30)
    sources.append({
        "endpoint": f"/v2/aggs/ticker/{ticker}/range/1/day/{{from}}/{{to}}",
        "fetched_at": utcnow_iso(),
        "context": "underlying daily closes for realized-vol / IV context",
    })
    rv = _realized_vol(agg_rows, REALIZED_VOL_WINDOW)
    if state.rate_limited:
        tier_caveats.append(
            "RATE LIMIT: underlying aggregates returned no data because the "
            "API rate limit was hit, not because history is missing. The "
            "IV-vs-realized context is UNKNOWN, not neutral. Rerun with "
            "sleep=13 or upgrade the stocks tier."
        )

    expiry = _choose_expiry(state, contracts, horizon_days)
    if expiry is None:
        tier_caveats.append(
            "No listed options expiry found in the horizon window; the chain "
            "may be empty (entitlement gap) or the name may not be optionable."
        )
        return {
            "skill": "hedge-suggester",
            "as_of": state.today.isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "spot": round(spot, 4),
            "spot_source": spot_source,
            "position": {
                "shares": shares,
                "contracts_per_leg": n_contracts,
                "notional_usd": round(notional_usd, 2),
                "sized_from": "shares" if shares is not None else "notional",
            },
            "risk_tolerance": risk_tolerance,
            "horizon_days": horizon_days,
            "expiry": None,
            "days_to_expiry": None,
            "structures": [],
            "ranking": [],
            "iv_context": _iv_context(None, rv),
            "take": f"No optionable chain found for {ticker} near the horizon; "
                    f"cannot propose hedges.",
            "tier_caveats": tier_caveats,
            "sources": sources,
        }

    exp_contracts = [c for c in contracts if c["expiry"] == expiry]
    days_to_expiry = (date.fromisoformat(expiry) - state.today).days

    calls = sorted([c for c in exp_contracts if c["type"] == "call"], key=lambda c: c["strike"])
    puts = sorted([c for c in exp_contracts if c["type"] == "put"], key=lambda c: c["strike"])
    call_strikes = [c["strike"] for c in calls]
    put_strikes = [c["strike"] for c in puts]

    atm_iv = None
    atm_candidates = sorted(exp_contracts, key=lambda c: abs(c["strike"] - spot))
    for c in atm_candidates:
        if c.get("iv") is not None:
            atm_iv = c["iv"]
            break
    ivc = _iv_context(atm_iv, rv)

    kc = _nearest_strike(call_strikes, spot * (1 + OTM_CALL_TARGET), prefer="above")
    kp = _nearest_strike(put_strikes, spot * (1 - PROTECTIVE_PUT_TARGET), prefer="below")
    kp_low_spread = _nearest_strike(put_strikes, spot * (1 - SPREAD_LOW_TARGET), prefer="below")
    kp_low_ratio = _nearest_strike(put_strikes, spot * (1 - RATIO_LOW_TARGET), prefer="below")

    structures: list[dict] = []
    skipped: list[dict] = []

    if kc is not None:
        c_call = _find(exp_contracts, "call", kc, expiry)
        if c_call and c_call.get("mid") is not None:
            legs = [_leg(c_call, "sell", n_contracts)]
            prem_per_share = (c_call["mid"] * CONTRACT_MULTIPLIER * n_contracts) / shares
            st = _build_structure(
                "covered_call", legs, shares, spot,
                protection_ceiling=spot,
                protection_floor=spot - prem_per_share,
                upside_cap=kc,
                tradeoff=(
                    f"Collects premium (a small downside cushion) but caps upside "
                    f"at {kc:g}. No real protection in a large drawdown."
                ),
            )
            if st:
                structures.append(st)
        else:
            skipped.append({"structure": "covered_call", "reason": f"no priced call at {kc:g}"})
    else:
        skipped.append({"structure": "covered_call", "reason": "no OTM call strike in band"})

    if kp is not None:
        c_put = _find(exp_contracts, "put", kp, expiry)
        if c_put and c_put.get("mid") is not None:
            legs = [_leg(c_put, "buy", n_contracts)]
            st = _build_structure(
                "protective_put", legs, shares, spot,
                protection_ceiling=kp,
                protection_floor=0.0,
                tradeoff=(
                    f"Full downside floor at {kp:g} with uncapped upside, but you "
                    f"pay the premium outright."
                ),
            )
            if st:
                structures.append(st)
        else:
            skipped.append({"structure": "protective_put", "reason": f"no priced put at {kp:g}"})
    else:
        skipped.append({"structure": "protective_put", "reason": "no ATM put strike in band"})

    if kp is not None and kc is not None:
        c_put = _find(exp_contracts, "put", kp, expiry)
        c_call = _find(exp_contracts, "call", kc, expiry)
        if c_put and c_call and c_put.get("mid") is not None and c_call.get("mid") is not None:
            legs = [_leg(c_put, "buy", n_contracts), _leg(c_call, "sell", n_contracts)]
            st = _build_structure(
                "collar", legs, shares, spot,
                protection_ceiling=kp,
                protection_floor=0.0,
                upside_cap=kc,
                tradeoff=(
                    f"Downside floor at {kp:g} financed by capping upside at {kc:g}. "
                    f"Often near zero cost; the price is your upside above {kc:g}."
                ),
            )
            if st:
                structures.append(st)

    if kp is not None and kp_low_spread is not None and kp_low_spread < kp:
        c_hi = _find(exp_contracts, "put", kp, expiry)
        c_lo = _find(exp_contracts, "put", kp_low_spread, expiry)
        if c_hi and c_lo and c_hi.get("mid") is not None and c_lo.get("mid") is not None:
            legs = [_leg(c_hi, "buy", n_contracts), _leg(c_lo, "sell", n_contracts)]
            st = _build_structure(
                "put_spread", legs, shares, spot,
                protection_ceiling=kp,
                protection_floor=kp_low_spread,
                tradeoff=(
                    f"Cheap protection over the {kp:g} to {kp_low_spread:g} band. "
                    f"No protection below {kp_low_spread:g}; a crash blows through it."
                ),
            )
            if st:
                structures.append(st)
        else:
            skipped.append({"structure": "put_spread", "reason": "put spread legs not both priced"})
    else:
        skipped.append({"structure": "put_spread", "reason": "no distinct lower put strike for spread"})

    if kp is not None and kp_low_ratio is not None and kp_low_ratio < kp:
        c_hi = _find(exp_contracts, "put", kp, expiry)
        c_lo = _find(exp_contracts, "put", kp_low_ratio, expiry)
        if c_hi and c_lo and c_hi.get("mid") is not None and c_lo.get("mid") is not None:
            legs = [_leg(c_hi, "buy", n_contracts), _leg(c_lo, "sell", 2 * n_contracts)]
            st = _build_structure(
                "ratio_put_spread", legs, shares, spot,
                protection_ceiling=kp,
                protection_floor=kp_low_ratio,
                tradeoff=(
                    f"Cheapest (often a credit) over the {kp:g} to {kp_low_ratio:g} "
                    f"band, but the extra short put re-opens downside below "
                    f"{kp_low_ratio:g}: losses accelerate in a crash."
                ),
                extra_caveats=[
                    f"TAIL RISK: net short 1 put below {kp_low_ratio:g}. Below that "
                    f"strike the position loses on both the stock and the naked "
                    f"short put; max loss is large and grows toward zero price."
                ],
            )
            if st:
                structures.append(st)
        else:
            skipped.append({"structure": "ratio_put_spread", "reason": "ratio legs not both priced"})
    else:
        skipped.append({"structure": "ratio_put_spread", "reason": "no distinct lower put strike for ratio"})

    rankable = [s for s in structures if s.get("cost_per_dollar_protected") is not None]
    rankable.sort(key=lambda s: s["cost_per_dollar_protected"])
    ranking = [
        {
            "rank": i + 1,
            "name": s["name"],
            "net_cost_usd": s["net_cost_usd"],
            "net_cost_pct_of_notional": s["net_cost_pct_of_notional"],
            "cost_per_dollar_protected": s["cost_per_dollar_protected"],
            "downside_protected_usd": s["downside_protected_usd"],
            "tradeoff": s["tradeoff"],
        }
        for i, s in enumerate(rankable)
    ]

    tier_caveats.append(
        "Options Developer tape is 15-min delayed; quotes and greeks are "
        "point-in-time snapshots, not live."
    )
    tier_caveats.append(
        "All structures are priced at chain MIDS. Real fills cross the "
        "spread, so live cost is worse than shown, especially on the "
        "wide-quote legs flagged per structure."
    )
    tier_caveats.append(
        f"EARNINGS: this run does not fetch the earnings calendar. Check "
        f"manually whether an earnings date falls before {expiry}; an "
        f"earnings event inside the horizon inflates IV and can gap the "
        f"stock through a hedge."
    )
    tier_caveats.append(
        "Greeks are point-in-time and drift as spot, vol, and time move. "
        "Short legs carry assignment risk; American options can be exercised "
        "early, especially puts near/through the strike and around dividends."
    )
    if ivc["label"] == "expensive":
        tier_caveats.append(
            "IV context: protection looks EXPENSIVE (ATM IV well above realized "
            "vol). Credit and spread structures that SELL vol are relatively "
            "more attractive than outright long puts right now."
        )
    elif ivc["label"] == "cheap":
        tier_caveats.append(
            "IV context: protection looks CHEAP (ATM IV near or below realized "
            "vol). Outright protective puts are relatively attractive."
        )

    take = _compose_take(risk_tolerance, horizon_days, structures, ranking, spot, ivc)

    return {
        "skill": "hedge-suggester",
        "as_of": state.today.isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "spot": round(spot, 4),
        "spot_source": spot_source,
        "position": {
            "shares": shares,
            "contracts_per_leg": n_contracts,
            "notional_usd": round(notional_usd, 2),
            "sized_from": "shares" if notional is None else "notional",
        },
        "risk_tolerance": risk_tolerance,
        "horizon_days": horizon_days,
        "expiry": expiry,
        "days_to_expiry": days_to_expiry,
        "n_structures": len(structures),
        "structures": structures,
        "ranking": ranking,
        "skipped_structures": skipped,
        "iv_context": ivc,
        "take": take,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }


# ----- Take line -----

def _by_name(structures: list[dict], name: str) -> dict | None:
    for s in structures:
        if s["name"] == name:
            return s
    return None


def _pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _compose_take(risk_tolerance: str, horizon_days: int, structures: list[dict],
                  ranking: list[dict], spot: float, ivc: dict) -> str:
    if not structures:
        return "No structures could be priced from the chain; no hedge to recommend."

    prefs = {
        "low": ["collar", "protective_put", "put_spread", "covered_call", "ratio_put_spread"],
        "medium": ["put_spread", "collar", "protective_put", "covered_call", "ratio_put_spread"],
        "high": ["put_spread", "covered_call", "ratio_put_spread", "collar", "protective_put"],
    }
    order = prefs.get(risk_tolerance, prefs["medium"])
    chosen = None
    for name in order:
        s = _by_name(structures, name)
        if s is not None:
            chosen = s
            break
    if chosen is None:
        chosen = structures[0]

    label = chosen["name"].replace("_", " ")
    cost_pct = _pct(chosen.get("net_cost_pct_of_notional"))
    floor = chosen.get("protection_floor")
    ceil = chosen.get("protection_ceiling")
    cheapest = ranking[0]["name"].replace("_", " ") if ranking else None

    parts = [f"For {risk_tolerance} risk over {horizon_days}d, the {label}"]
    if chosen.get("is_credit"):
        parts.append(f"is a net credit ({cost_pct} of notional)")
    else:
        parts.append(f"costs {cost_pct} of notional")

    cap = chosen.get("upside_cap")
    if chosen["name"] == "protective_put":
        parts.append(f"and floors downside at {ceil:g}")
    elif chosen["name"] == "collar":
        parts.append(f"and floors downside at {ceil:g} while capping upside at {cap:g}")
    elif chosen["name"] in ("put_spread", "ratio_put_spread"):
        parts.append(f"and covers the {ceil:g} to {floor:g} band")
    elif chosen["name"] == "covered_call":
        parts.append(f"and caps upside at {cap:g} for a small cushion")

    sentence = " ".join(parts) + "."

    extras = []
    if cheapest and (not ranking or ranking[0]["name"] != chosen["name"]):
        extras.append(f"Cheapest insurance per dollar protected is the {cheapest}.")
    collar = _by_name(structures, "collar")
    if collar is not None and chosen["name"] != "collar":
        cc = collar.get("net_cost_pct_of_notional")
        zc = "zero-cost" if cc is not None and abs(cc) < 0.002 else _pct(cc) + " cost"
        cap = collar.get("upside_cap") or collar.get("protection_ceiling")
        extras.append(f"The collar is {zc} but caps upside at {cap:g}.")
    if ivc.get("label") in ("expensive", "cheap"):
        extras.append(f"Protection is {ivc['label']} right now (ATM IV vs realized vol).")

    return " ".join([sentence] + extras) + " Not advice."


# ----- Renderer -----

def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.1f}K"
    return f"{sign}${a:.0f}"


def render(payload: dict) -> str:
    lines: list[str] = []
    tkr = payload["ticker"]
    if payload.get("error"):
        lines.append(f"hedge-suggester {tkr} ({payload['as_of']})")
        lines.append("")
        lines.append(f"ERROR: {payload['error']}")
        for c in payload.get("tier_caveats", []):
            lines.append(f"  - {c}")
        return "\n".join(lines)

    pos = payload["position"]
    lines.append(f"Hedge Suggester: {tkr} ({payload['as_of']})")
    lines.append(
        f"Spot ${payload['spot']:.2f} ({payload['spot_source']}) · "
        f"{pos['shares']:,} shares (${pos['notional_usd']:,.0f} notional) · "
        f"{pos['contracts_per_leg']} contracts/leg"
    )
    lines.append(
        f"Horizon {payload['horizon_days']}d · expiry {payload.get('expiry')} "
        f"({payload.get('days_to_expiry')}d out) · risk tolerance: "
        f"{payload['risk_tolerance']}"
    )
    ivc = payload["iv_context"]
    if ivc.get("iv_to_realized_ratio") is not None:
        lines.append(
            f"IV context: ATM IV {ivc['atm_iv'] * 100:.0f}% vs realized "
            f"{ivc['realized_vol_20d'] * 100:.0f}% = {ivc['iv_to_realized_ratio']}x "
            f"({ivc['label']})"
        )
    else:
        lines.append(f"IV context: {ivc['label']} ({ivc.get('note', '')[:60]}...)")

    if not payload["structures"]:
        lines.append("")
        lines.append("No structures could be priced.")
        if payload.get("tier_caveats"):
            lines.append("")
            lines.append("Caveats:")
            for c in payload["tier_caveats"]:
                lines.append(f"  - {c}")
        return "\n".join(lines)

    lines.append("")
    lines.append("Structures (ranked cheapest insurance per $ protected first):")
    lines.append("")
    header = (
        f"{'Structure':<18}{'Net cost':>11}{'% notl':>8}"
        f"{'Cost/$prot':>12}{'Breakeven':>11}{'Max loss':>11}{'Max gain':>11}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    order = {r["name"]: r["rank"] for r in payload["ranking"]}
    ordered = sorted(payload["structures"],
                     key=lambda s: order.get(s["name"], 999))
    for s in ordered:
        cpd = s.get("cost_per_dollar_protected")
        cpd_s = f"{cpd:+.3f}" if cpd is not None else "n/a"
        pct = s.get("net_cost_pct_of_notional")
        pct_s = f"{pct * 100:+.1f}%" if pct is not None else "n/a"
        mg = "uncapped" if s.get("max_gain_uncapped") else _fmt_usd(s.get("max_gain_usd"))
        name = s["name"].replace("_", " ")[:17]
        lines.append(
            f"{name:<18}{_fmt_usd(s['net_cost_usd']):>11}{pct_s:>8}"
            f"{cpd_s:>12}{('$' + format(s['breakeven'], '.2f')):>11}"
            f"{_fmt_usd(s['max_loss_usd']):>11}{mg:>11}"
        )

    lines.append("")
    for s in ordered:
        lines.append(f"{s['name'].replace('_', ' ').upper()}")
        for lg in s["legs"]:
            mid = lg.get("mid")
            mid_s = f"${mid:.2f}" if mid is not None else "n/a"
            oi = lg.get("open_interest")
            oi_s = f"OI {oi:,}" if oi is not None else "OI n/a"
            lines.append(
                f"  {lg['action']} {lg['qty_contracts']}x "
                f"{lg['strike']:g}{lg['type'][0].upper()} @ {mid_s} · {oi_s}"
            )
        nd = s.get("net_delta")
        ng = s.get("net_gamma")
        nt = s.get("net_theta")
        lines.append(
            f"  net delta {nd if nd is not None else 'n/a'} · "
            f"gamma {ng if ng is not None else 'n/a'} · "
            f"theta/day {_fmt_usd(nt) if nt is not None else 'n/a'}"
        )
        lines.append(f"  tradeoff: {s['tradeoff']}")
        for c in s.get("caveats", []):
            lines.append(f"  ! {c}")
        lines.append("")

    lines.append("Take: " + payload["take"])

    if payload.get("skipped_structures"):
        skipped = ", ".join(
            f"{x['structure']} ({x['reason']})" for x in payload["skipped_structures"]
        )
        lines.append("")
        lines.append(f"Not priced: {skipped}")

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")

    return "\n".join(lines)
