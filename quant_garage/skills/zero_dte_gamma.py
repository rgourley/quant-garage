"""
zero-dte-gamma as an importable library function.

Estimates net dealer gamma exposure for same-day-expiry (or nearest-
expiry) SPY options and identifies gamma pins. Motivated by 2024-25
research (Baltussen-Terhorst-Van Vliet 2024, Bhattacharya 2024) on how
0-day-to-expiration options now drive systematic intraday moves
through market-maker delta hedging pressure.

Skill approach:
1. Query the options chain snapshot for the target expiry date
   (default: nearest expiry to today).
2. Compute per-contract dealer gamma exposure (GEX) using
   Black-Scholes gamma, open interest, and contract multiplier.
3. Aggregate by strike into a gamma profile.
4. Identify the gamma-flip level (strike where cumulative gamma
   crosses zero) and the largest gamma pins.
5. Report expected end-of-day hedging pressure direction and
   magnitude.

    from quant_garage.skills.zero_dte_gamma import run, render
    payload = run("SPY")

Reads MASSIVE_API_KEY from env. Requires Options Developer or higher
for the chain snapshot endpoint.
"""
from __future__ import annotations

import math
from datetime import date as _date_cls, datetime, timedelta, timezone
from typing import Iterable

import numpy as np

from .. import MassiveClient, FetchError, today, utcnow_iso


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _bs_gamma(S: float, K: float, T_years: float, r: float, sigma: float) -> float:
    """
    Black-Scholes gamma. S=spot, K=strike, T=years to expiry, r=rate,
    sigma=IV. Returns 0 when inputs are degenerate.
    """
    if S <= 0 or K <= 0 or sigma <= 0 or T_years <= 0:
        return 0.0
    from math import log, sqrt, exp, pi
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T_years) / (sigma * sqrt(T_years))
    phi = exp(-0.5 * d1 * d1) / sqrt(2 * pi)
    return phi / (S * sigma * sqrt(T_years))


def _fetch_chain(
    client: MassiveClient, underlying: str, expiration_date: str, sources: _Sources,
) -> tuple[list[dict], bool]:
    """
    Returns (contracts, entitled). entitled=False when the endpoint
    returns NOT_AUTHORIZED.
    """
    path = (
        f"/v3/snapshot/options/{underlying}"
        f"?expiration_date={expiration_date}&limit=250"
    )
    rows: list[dict] = []
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            sources.record(
                f"/v3/snapshot/options/{underlying}?expiration_date={expiration_date}",
                fetched_at,
                f"options chain snapshot for {underlying} exp {expiration_date}",
            )
            if len(rows) >= 5000:
                break
        return rows, True
    except FetchError as e:
        msg = str(e).lower()
        if "not_authorized" in msg or "not entitled" in msg or (
            getattr(e, "status_code", None) in (401, 402, 403)
        ):
            return [], False
        raise


def _fetch_spot(client: MassiveClient, underlying: str, sources: _Sources) -> float | None:
    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{underlying}"
    try:
        doc, fetched_at = client.get(path)
    except FetchError:
        return None
    ticker = doc.get("ticker") or {}
    for source in (
        ticker.get("lastTrade") or {},
        ticker.get("day") or {},
        ticker.get("prevDay") or {},
    ):
        p = source.get("p") or source.get("c")
        if p and p > 0:
            sources.record(f"/v2/snapshot/.../{underlying}", fetched_at, "spot price fallback chain")
            return float(p)
    return None


def _pick_nearest_expiry(client: MassiveClient, underlying: str) -> str | None:
    path = (
        f"/v3/reference/options/contracts"
        f"?underlying_ticker={underlying}&limit=200"
        f"&sort=expiration_date&order=asc"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        return None
    for c in doc.get("results") or []:
        d = c.get("expiration_date")
        if d and d >= today().isoformat():
            return d
    return None


def run(
    underlying: str = "SPY",
    expiration_date: str | None = None,
    risk_free_rate: float = 0.045,
    default_iv: float = 0.15,
    client: MassiveClient | None = None,
) -> dict:
    """
    Aggregate dealer gamma exposure for the target expiry chain.

    Args:
        underlying: SPY / SPX / QQQ / IWM. Default SPY.
        expiration_date: YYYY-MM-DD target expiry. Default: nearest
            listed expiration to today.
        risk_free_rate: for BS gamma. Default 4.5% (fed funds proxy
            2026 mid-year).
        default_iv: fallback IV when Massive's greeks or IV field is
            missing. Default 15%.
        client: reuse an existing MassiveClient.
    """
    underlying = underlying.strip().upper()
    if not underlying:
        raise ValueError("underlying required")

    client = client or MassiveClient()
    sources = _Sources()

    if expiration_date is None:
        expiration_date = _pick_nearest_expiry(client, underlying)
        if expiration_date is None:
            raise ValueError(f"no listed expiration found for {underlying}")

    contracts, entitled = _fetch_chain(client, underlying, expiration_date, sources)
    if not entitled:
        return {
            "skill": "zero-dte-gamma",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "underlying": underlying,
            "expiration_date": expiration_date,
            "entitled": False,
            "spot": None,
            "gamma_by_strike": [],
            "gamma_flip_strike": None,
            "top_gamma_pins": [],
            "tier_caveats": [
                "This key is NOT entitled to the options chain snapshot endpoint. "
                "Add Options Developer or higher at massive.com/pricing to unlock this skill."
            ],
            "sources": sources.to_list(),
        }

    if not contracts:
        return {
            "skill": "zero-dte-gamma",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "underlying": underlying,
            "expiration_date": expiration_date,
            "entitled": True,
            "spot": None,
            "gamma_by_strike": [],
            "gamma_flip_strike": None,
            "top_gamma_pins": [],
            "tier_caveats": [
                f"No option contracts returned for {underlying} exp {expiration_date}."
            ],
            "sources": sources.to_list(),
        }

    spot = _fetch_spot(client, underlying, sources)
    if spot is None or spot <= 0:
        raise ValueError(f"unable to resolve spot for {underlying}")

    # Time to expiration in years
    try:
        exp_date = _date_cls.fromisoformat(expiration_date)
    except ValueError as e:
        raise ValueError(f"bad expiration_date {expiration_date}: {e}") from e
    days_to_exp = max(1, (exp_date - today()).days)
    T_years = days_to_exp / 365.0

    # Per-contract gamma exposure
    per_strike: dict[float, dict] = {}
    total_call_gamma = 0.0
    total_put_gamma = 0.0
    for c in contracts:
        details = c.get("details") or {}
        strike = details.get("strike_price")
        ctype = details.get("contract_type")
        if strike is None or ctype not in ("call", "put"):
            continue
        try:
            strike = float(strike)
        except (TypeError, ValueError):
            continue
        oi = c.get("open_interest") or 0
        try:
            oi = int(oi)
        except (TypeError, ValueError):
            oi = 0
        if oi <= 0:
            continue

        iv = None
        greeks = c.get("greeks") or {}
        gamma_from_endpoint = greeks.get("gamma")
        iv = c.get("implied_volatility") or greeks.get("iv")
        try:
            iv = float(iv) if iv is not None else default_iv
        except (TypeError, ValueError):
            iv = default_iv
        if iv <= 0 or iv > 5:
            iv = default_iv

        # Prefer endpoint gamma; fall back to BS
        if gamma_from_endpoint is not None:
            try:
                gamma = float(gamma_from_endpoint)
            except (TypeError, ValueError):
                gamma = _bs_gamma(spot, strike, T_years, risk_free_rate, iv)
        else:
            gamma = _bs_gamma(spot, strike, T_years, risk_free_rate, iv)
        if not math.isfinite(gamma):
            gamma = 0.0

        # Dealer position: dealers are typically SHORT customer calls
        # and LONG customer puts (they take the other side of retail flow).
        # Under this assumption, dealer gamma exposure per contract:
        #   calls: -gamma * OI * 100 * spot * spot / 100  (short gamma)
        #   puts:  +gamma * OI * 100 * spot * spot / 100  (long gamma)
        # Cash gamma = notional gamma * spot^2 / 100 (per 1% move).
        cash_gamma = gamma * oi * 100 * spot * spot / 100.0
        if ctype == "call":
            dealer_gex = -cash_gamma
            total_call_gamma += cash_gamma
        else:
            dealer_gex = +cash_gamma
            total_put_gamma += cash_gamma

        entry = per_strike.setdefault(strike, {
            "strike": strike,
            "call_gamma_notional": 0.0,
            "put_gamma_notional": 0.0,
            "dealer_gex": 0.0,
            "call_oi": 0,
            "put_oi": 0,
        })
        if ctype == "call":
            entry["call_gamma_notional"] += cash_gamma
            entry["call_oi"] += oi
        else:
            entry["put_gamma_notional"] += cash_gamma
            entry["put_oi"] += oi
        entry["dealer_gex"] += dealer_gex

    if not per_strike:
        return {
            "skill": "zero-dte-gamma",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "underlying": underlying,
            "expiration_date": expiration_date,
            "entitled": True,
            "spot": round(spot, 2),
            "days_to_expiration": int(days_to_exp),
            "gamma_by_strike": [],
            "gamma_flip_strike": None,
            "top_gamma_pins": [],
            "tier_caveats": [
                f"No contracts with open interest for {underlying} exp {expiration_date}."
            ],
            "sources": sources.to_list(),
        }

    strikes_sorted = sorted(per_strike.keys())
    gamma_by_strike = [per_strike[k] for k in strikes_sorted]

    # Cumulative dealer GEX walking from lowest to highest strike;
    # gamma-flip is the strike where cumulative dealer GEX crosses zero.
    cum = 0.0
    gamma_flip = None
    for s in strikes_sorted:
        cum += per_strike[s]["dealer_gex"]
        per_strike[s]["cum_dealer_gex"] = round(cum, 2)
    # Search for sign change
    prev_cum = 0.0
    for s in strikes_sorted:
        cur = per_strike[s]["cum_dealer_gex"]
        if prev_cum <= 0 < cur or prev_cum >= 0 > cur:
            gamma_flip = s
            break
        prev_cum = cur

    # Total dealer net gamma (short if negative → destabilizing, long if
    # positive → stabilizing)
    net_dealer_gex = sum(per_strike[s]["dealer_gex"] for s in strikes_sorted)

    # Largest gamma pins: strikes with the biggest |total gamma notional|
    pins = sorted(
        gamma_by_strike,
        key=lambda e: -(abs(e["call_gamma_notional"]) + abs(e["put_gamma_notional"]))
    )[:5]
    top_pins_out = [{
        "strike": p["strike"],
        "call_gamma_notional": round(p["call_gamma_notional"], 2),
        "put_gamma_notional": round(p["put_gamma_notional"], 2),
        "call_oi": int(p["call_oi"]),
        "put_oi": int(p["put_oi"]),
        "distance_from_spot_pct": round((p["strike"] - spot) / spot * 100, 2),
    } for p in pins]

    tier_caveats: list[str] = []
    tier_caveats.append(
        "Dealer positioning assumes short customer calls and long customer puts (typical retail flow); "
        "actual dealer books can be very different, especially near month-end."
    )
    tier_caveats.append(
        "Gamma exposure computed from Black-Scholes greeks and reported open interest. "
        "Intraday flow (0DTE especially) can flip this picture entirely within hours."
    )
    if days_to_exp > 1:
        tier_caveats.append(
            f"Target expiry is {days_to_exp} days out — not strictly 0DTE. "
            "Gamma effect dilutes with more time to expiry."
        )

    return {
        "skill": "zero-dte-gamma",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "underlying": underlying,
        "expiration_date": expiration_date,
        "days_to_expiration": int(days_to_exp),
        "entitled": True,
        "spot": round(spot, 2),
        "n_strikes": len(strikes_sorted),
        "n_contracts": len(contracts),
        "total_call_gamma_notional": round(total_call_gamma, 2),
        "total_put_gamma_notional": round(total_put_gamma, 2),
        "net_dealer_gex": round(net_dealer_gex, 2),
        "gamma_regime": "long_gamma" if net_dealer_gex > 0 else "short_gamma",
        "gamma_flip_strike": round(gamma_flip, 2) if gamma_flip else None,
        "top_gamma_pins": top_pins_out,
        "gamma_by_strike": [{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()} for row in gamma_by_strike],
        "risk_free_rate": float(risk_free_rate),
        "default_iv": float(default_iv),
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_notional(x: float) -> str:
    if x is None:
        return "n/a"
    absx = abs(x)
    sign = "-" if x < 0 else ""
    if absx >= 1_000_000_000:
        return f"{sign}${absx / 1_000_000_000:.2f}B"
    if absx >= 1_000_000:
        return f"{sign}${absx / 1_000_000:.1f}M"
    if absx >= 1_000:
        return f"{sign}${absx / 1_000:.1f}k"
    return f"{sign}${absx:,.0f}"


def render(payload: dict) -> str:
    lines: list[str] = []
    under = payload["underlying"]
    exp = payload["expiration_date"]

    if not payload.get("entitled", True):
        lines.append(f"0DTE gamma flow: {under} exp {exp} — ENTITLEMENT REQUIRED")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    spot = payload.get("spot")
    if spot is None:
        lines.append(f"0DTE gamma flow: {under} exp {exp}")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    dte = payload.get("days_to_expiration", 0)
    dte_tag = "0DTE" if dte <= 1 else f"{dte}DTE"
    lines.append(
        f"0DTE gamma flow: {under} · exp {exp} ({dte_tag}) · "
        f"spot ${spot:.2f} · {payload['n_strikes']} strikes"
    )
    regime = payload["gamma_regime"]
    regime_tag = "LONG GAMMA (stabilizing)" if regime == "long_gamma" else "SHORT GAMMA (destabilizing)"
    lines.append(
        f"Net dealer gamma: {_fmt_notional(payload['net_dealer_gex'])} → {regime_tag}"
    )
    if payload.get("gamma_flip_strike"):
        lines.append(f"Gamma flip strike: ${payload['gamma_flip_strike']:.2f}")
    lines.append("")

    lines.append("Top gamma pins (strikes with largest total notional gamma)")
    lines.append(
        f"  {'Strike':>8} {'Distance':>10} {'Call γ ($)':>14} {'Put γ ($)':>14} {'Call OI':>10} {'Put OI':>10}"
    )
    for pin in payload["top_gamma_pins"]:
        lines.append(
            f"  {pin['strike']:>8.2f} {pin['distance_from_spot_pct']:>+9.2f}% "
            f"{_fmt_notional(pin['call_gamma_notional']):>14} "
            f"{_fmt_notional(pin['put_gamma_notional']):>14} "
            f"{pin['call_oi']:>10,} {pin['put_oi']:>10,}"
        )
    lines.append("")

    take_parts: list[str] = []
    if regime == "short_gamma":
        take_parts.append(
            "Dealers are net-short gamma at these strikes; expect intraday moves to "
            "accelerate (dealers hedge with the market). Big up-days get bigger; "
            "big down-days get worse."
        )
    else:
        take_parts.append(
            "Dealers are net-long gamma; expect intraday range compression as dealers "
            "hedge against price moves. Late-day chop typical."
        )
    if payload.get("gamma_flip_strike"):
        flip = payload["gamma_flip_strike"]
        gap = (flip - spot) / spot * 100
        take_parts.append(
            f"Gamma flip at ${flip:.2f} ({gap:+.2f}% from spot); a break past this level shifts "
            f"the dealer hedging regime."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
