"""
cash-secured-puts as an importable library function.

hedge-suggester prices protection against a long you already own.
options-structure-analyzer picks a structure for a directional view.
Neither answers the income seller's question: "which put do I sell,
at what strike and expiry, and what am I actually being paid to take
the stock?" This skill does. It pulls the put chain, prices each
candidate at the chain mid, and reports the yield you collect against
the assignment risk you carry, with the assignment risk stated three
ways (chain delta, Black-Scholes N(-d2), and the stock's own history).

    from quant_garage.skills.cash_secured_puts import run, render
    payload = run(ticker="AAPL", cash=50_000, horizon_days=45)
    print(render(payload))

Method:
  1. Resolve underlying spot (v2 snapshot with the shared best-price
     fallback chain; zero is never a price).
  2. Pull the put side of the chain for expiries in [min_dte, max_dte]
     and strikes in a band below spot. Keep the `expiries` most liquid
     expiries by summed open interest (this naturally picks monthlies).
  3. Per expiry, pick one put per target delta (0.10 .. 0.40). When the
     chain carries no greeks, fall back to Black-Scholes delta from the
     chain IV, then to fixed moneyness targets.
  4. Per candidate: premium, static yield on cash secured, annualized
     yield, breakeven, cushion, |delta|, N(-d2), and the empirical base
     rate: how often the stock finished below that strike's moneyness
     over the same number of trading days in the trailing lookback.
  5. Expected P&L at expiry under the empirical return distribution
     (premium minus the historical average intrinsic loss). History is
     a base rate, not a forecast; every render says so.
  6. IV context (ATM IV vs 20d realized), earnings inside the window
     (Benzinga calendar via earnings-blackout, EDGAR fallback), ex-div
     dates inside the window.
  7. Three picks (conservative ~0.15 delta, balanced ~0.25, aggressive
     ~0.35) and a take fitted to the stated stance.

This proposes, it does not advise. Mids are optimistic for a seller
(you receive the bid, not the mid), a short put is long the stock below
the strike all the way to zero, and the tape is delayed on non-realtime
entitlements.
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
from . import earnings_blackout


# ----- Tunables -----

CONTRACT_MULTIPLIER = 100
OI_FLOOR = 100
SPREAD_PCT_CEILING = 0.15
STRIKE_BAND_LO = 0.70          # lowest strike considered, as a fraction of spot
STRIKE_BAND_HI = 1.02          # highest strike considered (slightly ITM allowed)
DELTA_TARGETS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
MONEYNESS_TARGETS = (0.97, 0.95, 0.92, 0.90, 0.87, 0.85, 0.80)
DELTA_MIN = 0.05
DELTA_MAX = 0.50
STANCE_DELTA = {"conservative": 0.15, "balanced": 0.25, "aggressive": 0.35}
VALID_STANCES = tuple(STANCE_DELTA)
REALIZED_VOL_WINDOW = 20
REALIZED_VOL_WINDOW_LONG = 60
HISTORY_CALENDAR_DAYS = 760    # ~2 trading years for the empirical base rate
IV_EXPENSIVE_RATIO = 1.30
IV_CHEAP_RATIO = 1.00
MAX_CHAIN_ROWS = 2500
DAYS_PER_YEAR = 365.0

_RATE_LIMIT_COOLDOWN_SECONDS = 13


class _State:
    def __init__(self, sleep_between: float) -> None:
        self.client = MassiveClient()
        self.today = today()
        self.sleep_between = sleep_between
        self.rate_limited: set[str] = set()
        self.sources: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self.sources.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def pause(self) -> None:
        if self.sleep_between > 0:
            time.sleep(self.sleep_between)


# ----- HTTP -----

def _get_spot(state: _State, ticker: str) -> tuple[float | None, str]:
    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    try:
        doc, fetched_at = state.client.get(path)
    except FetchError as exc:
        print(f"  WARN: snapshot for {ticker}: {exc}", file=sys.stderr)
        state.record(path, utcnow_iso(), "underlying spot (request failed)")
        return None, "no_price"
    finally:
        state.pause()
    state.record(path, fetched_at, "underlying spot price (best-price fallback chain)")
    res = resolve_price(doc)
    return res.price, res.source


def _get_put_chain(state: _State, ticker: str, spot: float,
                   min_dte: int, max_dte: int) -> tuple[list[dict], bool]:
    """Returns (rows, entitled). entitled=False on a NOT_AUTHORIZED style error."""
    exp_from = (state.today + timedelta(days=min_dte)).isoformat()
    exp_to = (state.today + timedelta(days=max_dte)).isoformat()
    strike_lo = max(0.01, math.floor(spot * STRIKE_BAND_LO))
    strike_hi = math.ceil(spot * STRIKE_BAND_HI)
    path = f"/v3/snapshot/options/{ticker}"
    params = {
        "contract_type": "put",
        "expiration_date.gte": exp_from,
        "expiration_date.lte": exp_to,
        "strike_price.gte": strike_lo,
        "strike_price.lte": strike_hi,
        "limit": 250,
    }
    cite = (
        f"{path}?contract_type=put&expiration_date.gte={exp_from}"
        f"&expiration_date.lte={exp_to}&strike_price.gte={strike_lo}"
        f"&strike_price.lte={strike_hi}"
    )
    rows: list[dict] = []
    try:
        for page, fetched_at in state.client.paginate(path, params):
            rows.extend(page)
            state.record(cite, fetched_at,
                         "put chain snapshot (bid/ask, OI, IV, greeks) in the DTE window")
            if len(rows) >= MAX_CHAIN_ROWS:
                break
    except FetchError as exc:
        msg = str(exc).lower()
        state.record(cite, utcnow_iso(), f"put chain snapshot (request failed: {exc})")
        if "not_authorized" in msg or "not entitled" in msg or (
            getattr(exc, "status_code", None) in (401, 402, 403)
        ):
            return rows, False
        print(f"  WARN: chain for {ticker}: {exc}", file=sys.stderr)
    finally:
        state.pause()
    return rows, True


def _fetch_daily_closes(state: _State, ticker: str, calendar_days: int) -> list[dict]:
    end = state.today
    start = end - timedelta(days=calendar_days)
    path = f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    doc: dict = {}
    fetched_at = utcnow_iso()
    try:
        doc, fetched_at = state.client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker} aggs; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, fetched_at = state.client.get(path, params)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} aggs after cooldown: {exc}",
                  file=sys.stderr)
            state.rate_limited.add(ticker)
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
    finally:
        state.pause()
    state.record(path, fetched_at,
                 "underlying daily closes (realized vol + empirical base rates)")

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None or float(close) <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    return rows


def _fetch_earnings(state: _State, ticker: str, max_dte: int) -> dict:
    """Next earnings date via earnings-blackout (Benzinga, EDGAR fallback)."""
    try:
        payload = earnings_blackout.run(
            [ticker], window_days=max_dte + 7, include_past_days=3, client=state.client,
        )
    except Exception as exc:  # noqa: BLE001 (any failure is a caveat, not a crash)
        state.record("/benzinga/v1/earnings?ticker={ticker}", utcnow_iso(),
                     f"earnings calendar via earnings-blackout (failed: {exc})")
        return {"next_earnings_date": None, "source": None, "fetched": False}
    finally:
        state.pause()
    for s in payload.get("sources") or []:
        state.record(s.get("endpoint", ""), s.get("fetched_at", utcnow_iso()),
                     f"earnings calendar via earnings-blackout: {s.get('context', '')}")
    row = None
    for r in payload.get("results") or payload.get("tickers") or []:
        if str(r.get("ticker", "")).upper() == ticker:
            row = r
            break
    if row is None:
        return {"next_earnings_date": None, "source": None, "fetched": True}
    return {
        "next_earnings_date": row.get("next_earnings_date"),
        "expected_release_time": row.get("expected_release_time"),
        "source": row.get("source"),
        "fetched": True,
    }


def _fetch_dividends(state: _State, ticker: str, max_dte: int) -> list[dict]:
    exp_to = (state.today + timedelta(days=max_dte)).isoformat()
    path = "/v3/reference/dividends"
    params = {
        "ticker": ticker,
        "ex_dividend_date.gt": state.today.isoformat(),
        "ex_dividend_date.lte": exp_to,
        "limit": 50,
        "order": "asc",
        "sort": "ex_dividend_date",
    }
    cite = f"{path}?ticker={ticker}&ex_dividend_date.gt={state.today.isoformat()}&ex_dividend_date.lte={exp_to}"
    out: list[dict] = []
    try:
        for page, fetched_at in state.client.paginate(path, params):
            state.record(cite, fetched_at, "ex-dividend dates inside the DTE window")
            for r in page:
                if r.get("ex_dividend_date"):
                    out.append({
                        "ex_dividend_date": r["ex_dividend_date"],
                        "cash_amount": r.get("cash_amount"),
                    })
    except FetchError as exc:
        state.record(cite, utcnow_iso(), f"ex-dividend dates (request failed: {exc})")
    finally:
        state.pause()
    return out


# ----- Chain parsing -----

def _parse_put(o: dict) -> dict | None:
    det = o.get("details") or {}
    occ = det.get("ticker")
    ctype = det.get("contract_type")
    strike = det.get("strike_price")
    expiry = det.get("expiration_date")
    if not occ or ctype != "put" or strike is None or not expiry:
        return None
    q = o.get("last_quote") or {}
    bid = q.get("bid")
    ask = q.get("ask")
    mid = q.get("midpoint")
    if mid is None and bid is not None and ask is not None and ask >= bid:
        mid = (bid + ask) / 2.0
    price_source = "quote_mid"
    if mid is None or mid <= 0:
        day = o.get("day") or {}
        mid = day.get("close") or day.get("vwap")
        price_source = "day_close"
    if mid is None or mid <= 0:
        fmv = o.get("fmv")
        mid = fmv if fmv and fmv > 0 else None
        price_source = "fmv"
    g = o.get("greeks") or {}
    return {
        "occ": occ,
        "strike": float(strike),
        "expiry": expiry,
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "mid": float(mid) if mid is not None else None,
        "price_source": price_source if mid is not None else None,
        "oi": o.get("open_interest"),
        "volume": (o.get("day") or {}).get("volume"),
        "iv": o.get("implied_volatility"),
        "delta": g.get("delta"),
        "theta": g.get("theta"),
    }


def _select_expiries(puts: list[dict], n: int) -> list[str]:
    """The n expiries with the most summed open interest, returned in date order."""
    oi_by_exp: dict[str, float] = {}
    for p in puts:
        oi_by_exp[p["expiry"]] = oi_by_exp.get(p["expiry"], 0.0) + float(p.get("oi") or 0)
    ranked = sorted(oi_by_exp.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen = [e for e, _ in ranked[:max(1, n)]]
    return sorted(chosen)


# ----- Black-Scholes helpers (fallback only; chain greeks win when present) -----

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_put_delta_and_pitm(spot: float, strike: float, iv: float | None, t_years: float,
                           r: float) -> tuple[float | None, float | None]:
    """Returns (put delta, N(-d2)) or (None, None) when unpriceable."""
    if iv is None or iv <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None, None
    sig_sqrt_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / sig_sqrt_t
    d2 = d1 - sig_sqrt_t
    return _norm_cdf(d1) - 1.0, _norm_cdf(-d2)


# ----- Empirical base rates -----

def _log_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _realized_vol(closes: list[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    rets = _log_returns(closes[-(window + 1):])
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def _trading_days(dte_calendar: int) -> int:
    return max(1, int(round(dte_calendar * 252.0 / 365.0)))


def _horizon_returns(closes: list[float], horizon_td: int) -> list[float]:
    """Overlapping simple returns over `horizon_td` trading days."""
    if len(closes) <= horizon_td:
        return []
    return [closes[i + horizon_td] / closes[i] - 1.0 for i in range(len(closes) - horizon_td)]


def _empirical_block(rets: list[float], spot: float, strike: float, premium_per_share: float,
                     lookback_label: str) -> dict:
    n = len(rets)
    if n < 20:
        return {
            "n_windows": n,
            "lookback": lookback_label,
            "p_below_strike": None,
            "p_below_breakeven": None,
            "expected_pnl_usd": None,
            "avg_loss_when_assigned_usd": None,
            "worst_window_pnl_usd": None,
        }
    k_ret = strike / spot - 1.0
    be_ret = (strike - premium_per_share) / spot - 1.0
    below_k = sum(1 for r in rets if r < k_ret)
    below_be = sum(1 for r in rets if r < be_ret)
    losses = []
    pnls = []
    for r in rets:
        s_t = spot * (1.0 + r)
        intrinsic = max(strike - s_t, 0.0)
        pnl = (premium_per_share - intrinsic) * CONTRACT_MULTIPLIER
        pnls.append(pnl)
        if intrinsic > 0:
            losses.append(intrinsic * CONTRACT_MULTIPLIER)
    return {
        "n_windows": n,
        "lookback": lookback_label,
        "p_below_strike": round(below_k / n, 4),
        "p_below_breakeven": round(below_be / n, 4),
        "expected_pnl_usd": round(sum(pnls) / n, 2),
        "avg_loss_when_assigned_usd": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "worst_window_pnl_usd": round(min(pnls), 2),
    }


# ----- IV context -----

def _iv_context(atm_iv: float | None, rv20: float | None, rv60: float | None) -> dict:
    if atm_iv is None or rv20 is None or rv20 <= 0:
        return {
            "atm_iv": atm_iv,
            "realized_vol_20d": rv20,
            "realized_vol_60d": rv60,
            "iv_to_realized_ratio": None,
            "label": "unknown",
            "note": (
                "IV-vs-realized context unavailable (missing ATM IV or underlying "
                "history). True IV percentile needs a historical IV series, which "
                "this run does not fetch."
            ),
        }
    ratio = atm_iv / rv20
    if ratio >= IV_EXPENSIVE_RATIO:
        label = "expensive"
    elif ratio <= IV_CHEAP_RATIO:
        label = "cheap"
    else:
        label = "fair"
    return {
        "atm_iv": round(atm_iv, 4),
        "realized_vol_20d": round(rv20, 4),
        "realized_vol_60d": round(rv60, 4) if rv60 is not None else None,
        "iv_to_realized_ratio": round(ratio, 2),
        "label": label,
        "note": (
            "Proxy only: ATM implied vol versus 20-day realized vol (the variance "
            "risk premium), not a true IV percentile. A ratio well above 1 means "
            "the put seller is being paid for more vol than the stock has recently "
            "delivered, which is the seller's edge if realized stays low."
        ),
    }


# ----- Candidate construction -----

def _liquidity_flags(p: dict) -> list[str]:
    flags: list[str] = []
    oi = p.get("oi")
    if oi is None:
        flags.append("open interest unavailable")
    elif oi < OI_FLOOR:
        flags.append(f"thin OI {oi} (below {OI_FLOOR} floor)")
    bid, ask, mid = p.get("bid"), p.get("ask"), p.get("mid")
    if bid is not None and ask is not None and mid and mid > 0:
        spread_pct = (ask - bid) / mid
        if spread_pct > SPREAD_PCT_CEILING:
            flags.append(
                f"wide bid-ask {spread_pct * 100:.0f}% (above "
                f"{SPREAD_PCT_CEILING * 100:.0f}% ceiling); mid fill unlikely"
            )
    else:
        flags.append("no two-sided quote; premium is a fallback estimate")
    if p.get("price_source") in ("day_close", "fmv"):
        flags.append(f"premium from {p['price_source']}, not a live quote")
    return flags


def _build_candidate(p: dict, spot: float, dte: int, cash: float | None,
                     horizon_rets: list[float], lookback_label: str,
                     risk_free_rate: float) -> dict | None:
    mid = p.get("mid")
    if mid is None or mid <= 0:
        return None
    strike = p["strike"]
    t_years = dte / DAYS_PER_YEAR
    premium_per_share = mid
    premium_usd = mid * CONTRACT_MULTIPLIER
    bid = p.get("bid")
    premium_bid_usd = bid * CONTRACT_MULTIPLIER if bid is not None and bid > 0 else None
    cash_secured = strike * CONTRACT_MULTIPLIER
    static_yield = premium_usd / cash_secured
    ann_yield = static_yield * (DAYS_PER_YEAR / dte) if dte > 0 else None
    static_yield_bid = premium_bid_usd / cash_secured if premium_bid_usd else None
    ann_yield_bid = static_yield_bid * (DAYS_PER_YEAR / dte) if static_yield_bid and dte > 0 else None
    breakeven = strike - premium_per_share
    otm_pct = (spot - strike) / spot
    cushion_pct = (spot - breakeven) / spot

    chain_delta = p.get("delta")
    bs_delta, p_itm = _bs_put_delta_and_pitm(spot, strike, p.get("iv"), t_years, risk_free_rate)
    if chain_delta is not None:
        delta_abs = abs(float(chain_delta))
        delta_source = "chain"
    elif bs_delta is not None:
        delta_abs = abs(bs_delta)
        delta_source = "black_scholes"
    else:
        delta_abs = None
        delta_source = None

    emp = _empirical_block(horizon_rets, spot, strike, premium_per_share, lookback_label)

    contracts = None
    total_premium = None
    cash_used = None
    if cash is not None:
        contracts = int(cash // cash_secured)
        total_premium = round(contracts * premium_usd, 2)
        cash_used = round(contracts * cash_secured, 2)

    theta = p.get("theta")
    return {
        "expiry": p["expiry"],
        "days_to_expiry": dte,
        "strike": strike,
        "occ_ticker": p["occ"],
        "bid": bid,
        "ask": p.get("ask"),
        "mid": round(mid, 4),
        "premium_source": p.get("price_source"),
        "open_interest": p.get("oi"),
        "volume": p.get("volume"),
        "iv": round(p["iv"], 4) if p.get("iv") is not None else None,
        "delta_abs": round(delta_abs, 4) if delta_abs is not None else None,
        "delta_source": delta_source,
        "theta_per_day_usd": round(theta * CONTRACT_MULTIPLIER, 2) if theta is not None else None,
        "prob_itm_bs": round(p_itm, 4) if p_itm is not None else None,
        "otm_pct": round(otm_pct, 4),
        "premium_usd": round(premium_usd, 2),
        "premium_bid_usd": round(premium_bid_usd, 2) if premium_bid_usd is not None else None,
        "cash_secured_usd": round(cash_secured, 2),
        "static_yield": round(static_yield, 5),
        "annualized_yield": round(ann_yield, 5) if ann_yield is not None else None,
        "static_yield_at_bid": round(static_yield_bid, 5) if static_yield_bid is not None else None,
        "annualized_yield_at_bid": round(ann_yield_bid, 5) if ann_yield_bid is not None else None,
        "breakeven": round(breakeven, 4),
        "cushion_pct": round(cushion_pct, 4),
        "max_loss_usd": round((strike - premium_per_share) * CONTRACT_MULTIPLIER, 2),
        "empirical": emp,
        "sizing": {
            "contracts_affordable": contracts,
            "cash_used_usd": cash_used,
            "total_premium_usd": total_premium,
        },
        "caveats": _liquidity_flags(p),
    }


def _pick_per_target(exp_puts: list[dict], spot: float) -> list[dict]:
    """One put per delta target (or moneyness target when no delta is available)."""
    with_delta = [p for p in exp_puts if p.get("_delta_abs") is not None]
    chosen: dict[str, dict] = {}
    if len(with_delta) >= 3:
        pool = [p for p in with_delta if DELTA_MIN <= p["_delta_abs"] <= DELTA_MAX] or with_delta
        for tgt in DELTA_TARGETS:
            best = min(pool, key=lambda p: abs(p["_delta_abs"] - tgt))
            if abs(best["_delta_abs"] - tgt) <= 0.05:
                chosen.setdefault(best["occ"], best)
    else:
        for tgt in MONEYNESS_TARGETS:
            best = min(exp_puts, key=lambda p: abs(p["strike"] / spot - tgt))
            if abs(best["strike"] / spot - tgt) <= 0.03:
                chosen.setdefault(best["occ"], best)
    return sorted(chosen.values(), key=lambda p: -p["strike"])


# ----- Public API -----

def run(
    ticker: str,
    cash: float | None = None,
    stance: str = "balanced",
    horizon_days: int = 45,
    min_dte: int = 7,
    max_dte: int | None = None,
    expiries: int = 3,
    risk_free_rate: float = 0.0,
    sleep: float = 0.0,
) -> dict:
    """Screen cash-secured put candidates on one underlying and rank them by stance.

    `cash` (optional) sizes contracts per candidate. `horizon_days` is the
    preferred DTE; the chain window is [min_dte, max_dte] with max_dte
    defaulting to horizon_days + 30. `expiries` caps how many expiries
    (by summed open interest) are analyzed. `risk_free_rate` feeds the
    Black-Scholes N(-d2) fallback only (default 0: stated, not assumed).
    """
    if stance not in VALID_STANCES:
        raise ValueError(f"stance must be one of {VALID_STANCES}")
    ticker = ticker.strip().upper()
    horizon_days = int(horizon_days)
    min_dte = int(min_dte)
    max_dte = int(max_dte) if max_dte is not None else horizon_days + 30
    if max_dte <= min_dte:
        raise ValueError("max_dte must exceed min_dte")
    if cash is not None and cash <= 0:
        raise ValueError("cash must be positive when provided")

    state = _State(sleep)
    tier_caveats: list[str] = []

    def _base(extra: dict) -> dict:
        out = {
            "skill": "cash-secured-puts",
            "as_of": state.today.isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "stance": stance,
            "params": {
                "cash": cash,
                "horizon_days": horizon_days,
                "min_dte": min_dte,
                "max_dte": max_dte,
                "expiries_requested": expiries,
                "risk_free_rate": risk_free_rate,
            },
            "spot": None,
            "spot_source": "no_price",
            "expiries": [],
            "candidates": [],
            "picks": {},
            "iv_context": _iv_context(None, None, None),
            "events": {"earnings": None, "dividends": []},
            "take": "",
            "tier_caveats": tier_caveats,
            "sources": state.sources,
        }
        out.update(extra)
        return out

    spot, spot_source = _get_spot(state, ticker)
    if spot is None or spot <= 0:
        tier_caveats.append(
            "Underlying snapshot returned no price. Check the ticker and that the "
            "key carries Stocks Starter or higher."
        )
        return _base({
            "spot_source": spot_source,
            "error": "no spot price for underlying; cannot price puts",
            "take": f"No spot price available for {ticker}; nothing to screen.",
        })

    raw, entitled = _get_put_chain(state, ticker, spot, min_dte, max_dte)
    if not entitled:
        tier_caveats.append(
            "Options chain endpoint returned NOT_AUTHORIZED. This skill needs the "
            "Options Developer add-on (or higher)."
        )
        return _base({
            "spot": round(spot, 4),
            "spot_source": spot_source,
            "error": "options chain not entitled",
            "take": f"Key is not entitled to the options chain; cannot screen puts on {ticker}.",
        })
    puts = [p for p in (_parse_put(o) for o in raw) if p is not None]
    if len(raw) >= MAX_CHAIN_ROWS:
        tier_caveats.append(
            f"Chain pull capped at {MAX_CHAIN_ROWS} contracts; the densest weeklies "
            f"may be truncated. Narrow min_dte/max_dte to see them all."
        )

    closes_rows = _fetch_daily_closes(state, ticker, HISTORY_CALENDAR_DAYS)
    closes = [r["close"] for r in closes_rows]
    rv20 = _realized_vol(closes, REALIZED_VOL_WINDOW)
    rv60 = _realized_vol(closes, REALIZED_VOL_WINDOW_LONG)
    lookback_label = (
        f"{closes_rows[0]['date']}..{closes_rows[-1]['date']} ({len(closes)} closes)"
        if closes_rows else "no history"
    )
    if state.rate_limited:
        tier_caveats.append(
            "RATE LIMIT: underlying aggregates returned no data because the API "
            "rate limit was hit, not because history is missing. Realized vol and "
            "the empirical base rates are UNKNOWN, not zero. Rerun with sleep=13 "
            "or upgrade the stocks tier."
        )

    earnings = _fetch_earnings(state, ticker, max_dte)
    dividends = _fetch_dividends(state, ticker, max_dte)

    if not puts:
        tier_caveats.append(
            "No put contracts came back in the DTE and strike window; the name may "
            "not be optionable or the window may be empty. Widen min_dte/max_dte."
        )
        return _base({
            "spot": round(spot, 4),
            "spot_source": spot_source,
            "iv_context": _iv_context(None, rv20, rv60),
            "events": {"earnings": earnings, "dividends": dividends},
            "take": f"No puts found on {ticker} between {min_dte} and {max_dte} days out.",
        })

    chosen_expiries = _select_expiries(puts, expiries)
    exp_blocks: list[dict] = []
    candidates: list[dict] = []
    atm_iv: float | None = None

    for exp in chosen_expiries:
        dte = (date.fromisoformat(exp) - state.today).days
        if dte <= 0:
            continue
        exp_puts = [p for p in puts if p["expiry"] == exp]
        t_years = dte / DAYS_PER_YEAR
        for p in exp_puts:
            if p.get("delta") is not None:
                p["_delta_abs"] = abs(float(p["delta"]))
            else:
                bs_delta, _ = _bs_put_delta_and_pitm(spot, p["strike"], p.get("iv"), t_years, risk_free_rate)
                p["_delta_abs"] = abs(bs_delta) if bs_delta is not None else None
        if atm_iv is None:
            near = sorted(exp_puts, key=lambda p: abs(p["strike"] - spot))
            for p in near:
                if p.get("iv") is not None and p["iv"] > 0:
                    atm_iv = float(p["iv"])
                    break
        horizon_td = _trading_days(dte)
        horizon_rets = _horizon_returns(closes, horizon_td)
        picked = _pick_per_target(exp_puts, spot)
        built = []
        for p in picked:
            c = _build_candidate(p, spot, dte, cash, horizon_rets, lookback_label, risk_free_rate)
            if c is not None:
                built.append(c)
        exp_blocks.append({
            "expiry": exp,
            "days_to_expiry": dte,
            "trading_days": horizon_td,
            "n_puts_in_chain": len(exp_puts),
            "n_candidates": len(built),
            "total_open_interest": int(sum(float(p.get("oi") or 0) for p in exp_puts)),
            "earnings_inside": bool(
                earnings.get("next_earnings_date")
                and earnings["next_earnings_date"] <= exp
            ),
            "ex_dividends_inside": [
                d for d in dividends if d["ex_dividend_date"] <= exp
            ],
        })
        candidates.extend(built)

    ivc = _iv_context(atm_iv, rv20, rv60)
    picks = _make_picks(candidates, horizon_days)

    # Run-level caveats.
    tier_caveats.append(
        "Premiums are chain MIDS on a 15-min delayed tape (Options Developer). A "
        "put seller receives the BID, not the mid; the *_at_bid fields carry the "
        "conservative yield."
    )
    tier_caveats.append(
        "A cash-secured put is long the stock below the strike all the way to "
        "zero. max_loss_usd is strike minus premium, per contract. The yield is "
        "compensation for that exposure, not free income."
    )
    if not earnings.get("fetched"):
        tier_caveats.append(
            "EARNINGS: the calendar could not be fetched. Check manually whether "
            "an earnings date falls before each expiry; a print inside the window "
            "is where short puts get gapped through the strike."
        )
    elif earnings.get("next_earnings_date") is None:
        tier_caveats.append(
            f"EARNINGS: no earnings date found within {max_dte + 7} days "
            f"(source: {earnings.get('source') or 'none'}). Verify against the "
            f"company IR calendar before selling through a print."
        )
    elif any(b["earnings_inside"] for b in exp_blocks):
        tier_caveats.append(
            f"EARNINGS: {earnings['next_earnings_date']} falls inside at least one "
            f"analyzed expiry. IV is elevated into the print and the stock can gap "
            f"through the strike; the higher yield on those expiries is the "
            f"event premium, not an edge."
        )
    if dividends:
        tier_caveats.append(
            "Ex-dividend inside the window: spot steps down by the dividend on the "
            "ex-date, which nudges assignment odds up; the put holder does not "
            "collect the dividend."
        )
    if ivc["label"] == "expensive":
        tier_caveats.append(
            "IV context: puts are EXPENSIVE (ATM IV well above 20d realized). The "
            "premium is rich relative to recent movement, which favors the seller "
            "if realized stays where it is."
        )
    elif ivc["label"] == "cheap":
        tier_caveats.append(
            "IV context: puts are CHEAP (ATM IV at or below 20d realized). You are "
            "being paid less than the stock has recently moved; the yield is thin "
            "for the risk."
        )
    tier_caveats.append(
        "Empirical base rates use overlapping windows from the trailing lookback, "
        "so effective sample size is smaller than n_windows, and history is a base "
        "rate, not a forecast. Black-Scholes N(-d2) uses the chain IV and "
        f"risk_free_rate={risk_free_rate:g}."
    )
    tier_caveats.append(
        "Short puts can be assigned early (American style), most likely when deep "
        "ITM near expiry. Assignment has tax consequences. Not advice."
    )

    take = _compose_take(ticker, stance, picks, ivc, exp_blocks, earnings, spot)

    return _base({
        "spot": round(spot, 4),
        "spot_source": spot_source,
        "expiries": exp_blocks,
        "n_candidates": len(candidates),
        "candidates": candidates,
        "picks": picks,
        "iv_context": ivc,
        "events": {"earnings": earnings, "dividends": dividends},
        "take": take,
    })


# ----- Picks and take -----

def _make_picks(candidates: list[dict], horizon_days: int) -> dict:
    """Per stance: the candidate nearest the stance delta on the primary expiry
    (the analyzed expiry nearest the horizon). Falls back to every expiry when
    the primary has nothing within 0.08 of the target."""
    picks: dict = {"primary_expiry": None}
    with_delta = [c for c in candidates if c.get("delta_abs") is not None]
    if not with_delta:
        for name in STANCE_DELTA:
            picks[name] = None
        return picks
    by_dte = {c["expiry"]: c["days_to_expiry"] for c in with_delta}
    primary = min(by_dte, key=lambda e: (abs(by_dte[e] - horizon_days), e))
    picks["primary_expiry"] = primary
    primary_pool = [c for c in with_delta if c["expiry"] == primary]

    def _nearest(pool: list[dict], tgt: float) -> dict | None:
        best = min(
            pool,
            key=lambda c: (
                abs(c["delta_abs"] - tgt),
                abs(c["days_to_expiry"] - horizon_days),
                -(c.get("annualized_yield") or 0.0),
            ),
        )
        return best if abs(best["delta_abs"] - tgt) <= 0.08 else None

    for name, tgt in STANCE_DELTA.items():
        best = _nearest(primary_pool, tgt) if primary_pool else None
        if best is None:
            best = _nearest(with_delta, tgt)
        if best is None:
            picks[name] = None
            continue
        picks[name] = {
            "expiry": best["expiry"],
            "days_to_expiry": best["days_to_expiry"],
            "strike": best["strike"],
            "occ_ticker": best["occ_ticker"],
            "mid": best["mid"],
            "premium_usd": best["premium_usd"],
            "static_yield": best["static_yield"],
            "annualized_yield": best["annualized_yield"],
            "breakeven": best["breakeven"],
            "cushion_pct": best["cushion_pct"],
            "delta_abs": best["delta_abs"],
            "prob_itm_bs": best["prob_itm_bs"],
            "p_below_strike_hist": best["empirical"]["p_below_strike"],
            "n_windows": best["empirical"]["n_windows"],
            "expected_pnl_usd_hist": best["empirical"]["expected_pnl_usd"],
            "contracts_affordable": best["sizing"]["contracts_affordable"],
            "caveats": best["caveats"],
        }
    return picks


def _signed_usd(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.0f}"


def _pct(x: float | None, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%" if x is not None else "n/a"


def _compose_take(ticker: str, stance: str, picks: dict, ivc: dict, exp_blocks: list[dict],
                  earnings: dict, spot: float) -> str:
    pick = picks.get(stance)
    if pick is None:
        return (
            f"No {stance} candidate (target |delta| {STANCE_DELTA[stance]:.2f}) could be "
            f"priced on {ticker}; widen the DTE window or check the chain. Not advice."
        )
    parts = [
        f"For a {stance} stance, the {pick['expiry']} {pick['strike']:g} put "
        f"({pick['days_to_expiry']}d) collects ${pick['premium_usd']:,.0f} per contract: "
        f"{_pct(pick['static_yield'], 2)} on ${pick['strike'] * CONTRACT_MULTIPLIER:,.0f} secured, "
        f"{_pct(pick['annualized_yield'])} annualized, breakeven ${pick['breakeven']:.2f} "
        f"({_pct(pick['cushion_pct'])} below spot)."
    ]
    odds = []
    if pick.get("delta_abs") is not None:
        odds.append(f"|delta| {pick['delta_abs']:.2f}")
    if pick.get("prob_itm_bs") is not None:
        odds.append(f"N(-d2) {_pct(pick['prob_itm_bs'], 0)}")
    if pick.get("p_below_strike_hist") is not None:
        odds.append(
            f"history {_pct(pick['p_below_strike_hist'], 0)} of {pick['days_to_expiry']}d "
            f"windows finished below the strike (n = {pick['n_windows']})"
        )
    if odds:
        parts.append("Assignment odds: " + ", ".join(odds) + ".")
    if pick.get("expected_pnl_usd_hist") is not None:
        parts.append(
            f"Expected P&L per contract under the historical distribution: "
            f"{_signed_usd(pick['expected_pnl_usd_hist'])}."
        )
    if pick.get("contracts_affordable") is not None:
        parts.append(f"Cash covers {pick['contracts_affordable']} contract(s).")
    if ivc.get("label") in ("expensive", "cheap"):
        parts.append(f"Puts are {ivc['label']} vs realized vol.")
    ed = earnings.get("next_earnings_date")
    if ed and ed <= pick["expiry"]:
        parts.append(f"Earnings {ed} is inside this expiry.")
    elif ed:
        parts.append(f"Earnings {ed} is after this expiry.")
    for flag in pick.get("caveats") or []:
        if "thin OI" in flag or "wide bid-ask" in flag:
            parts.append(f"Liquidity: {flag}.")
            break
    return " ".join(parts) + " Not advice."


# ----- Renderer -----

def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 10_000:
        return f"{sign}${a / 1_000:.1f}K"
    return f"{sign}${a:,.0f}"


def render(payload: dict) -> str:
    lines: list[str] = []
    tkr = payload["ticker"]
    if payload.get("error"):
        lines.append(f"cash-secured-puts {tkr} ({payload['as_of']})")
        lines.append("")
        lines.append(f"ERROR: {payload['error']}")
        for c in payload.get("tier_caveats", []):
            lines.append(f"  - {c}")
        return "\n".join(lines)

    prm = payload["params"]
    lines.append(f"Cash-Secured Puts: {tkr} ({payload['as_of']})")
    cash = prm.get("cash")
    cash_s = f"cash ${cash:,.0f}" if cash is not None else "cash not specified"
    lines.append(
        f"Spot ${payload['spot']:.2f} ({payload['spot_source']}) · {cash_s} · "
        f"stance: {payload['stance']} · DTE window {prm['min_dte']}-{prm['max_dte']}d"
    )
    ivc = payload["iv_context"]
    if ivc.get("iv_to_realized_ratio") is not None:
        rv60 = ivc.get("realized_vol_60d")
        rv60_s = f" · realized 60d {rv60 * 100:.0f}%" if rv60 is not None else ""
        lines.append(
            f"IV context: ATM IV {ivc['atm_iv'] * 100:.0f}% vs realized 20d "
            f"{ivc['realized_vol_20d'] * 100:.0f}% = {ivc['iv_to_realized_ratio']}x "
            f"({ivc['label']}){rv60_s}"
        )
    else:
        lines.append(f"IV context: {ivc['label']}")
    ev = payload.get("events") or {}
    earn = ev.get("earnings") or {}
    if not earn.get("fetched"):
        lines.append("Earnings: calendar not fetched (check manually)")
    elif earn.get("next_earnings_date"):
        lines.append(
            f"Earnings: {earn['next_earnings_date']}"
            f"{' ' + earn['expected_release_time'] if earn.get('expected_release_time') else ''}"
            f" (source: {earn.get('source') or 'n/a'})"
        )
    else:
        lines.append("Earnings: none found in window")
    divs = ev.get("dividends") or []
    if divs:
        lines.append("Ex-dividend: " + ", ".join(
            f"{d['ex_dividend_date']}"
            + (f" ${d['cash_amount']:.2f}" if d.get("cash_amount") is not None else "")
            for d in divs
        ))

    if not payload.get("candidates"):
        lines.append("")
        lines.append("No candidates could be priced.")
        if payload.get("tier_caveats"):
            lines.append("")
            lines.append("Caveats:")
            for c in payload["tier_caveats"]:
                lines.append(f"  - {c}")
        return "\n".join(lines)

    header = (
        f"{'Strike':>8}{'OTM':>7}{'Mid':>7}{'Prem':>7}{'Yield':>7}{'Ann':>7}"
        f"{'B/E':>9}{'|d|':>6}{'P(ITM)':>8}{'Hist<K':>8}{'E[PnL]':>9}{'OI':>8}{'Sprd':>6}"
    )
    for blk in payload["expiries"]:
        lines.append("")
        tags = []
        if blk.get("earnings_inside"):
            tags.append("EARNINGS INSIDE")
        if blk.get("ex_dividends_inside"):
            tags.append("ex-div inside")
        tag_s = f" · {' · '.join(tags)}" if tags else ""
        lines.append(
            f"Expiry {blk['expiry']} ({blk['days_to_expiry']}d, {blk['trading_days']} trading days) · "
            f"{blk['n_candidates']} candidates of {blk['n_puts_in_chain']} puts · "
            f"OI {blk['total_open_interest']:,}{tag_s}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        rows = [c for c in payload["candidates"] if c["expiry"] == blk["expiry"]]
        for c in rows:
            d = c.get("delta_abs")
            d_s = f"{d:.2f}" if d is not None else "n/a"
            if c.get("delta_source") == "black_scholes" and d is not None:
                d_s += "*"
            pit = c.get("prob_itm_bs")
            emp = c.get("empirical") or {}
            hist = emp.get("p_below_strike")
            epnl = emp.get("expected_pnl_usd")
            oi = c.get("open_interest")
            bid, ask, mid = c.get("bid"), c.get("ask"), c.get("mid")
            sprd = f"{(ask - bid) / mid * 100:.0f}%" if bid is not None and ask is not None and mid else "n/a"
            lines.append(
                f"{c['strike']:>8g}{-c['otm_pct'] * 100:>+6.1f}%{c['mid']:>7.2f}"
                f"{c['premium_usd']:>7,.0f}{c['static_yield'] * 100:>6.2f}%"
                f"{(c['annualized_yield'] or 0) * 100:>6.1f}%{c['breakeven']:>9.2f}"
                f"{d_s:>6}{_pct(pit, 0) if pit is not None else 'n/a':>8}"
                f"{_pct(hist, 0) if hist is not None else 'n/a':>8}"
                f"{(format(epnl, '+,.0f') if epnl is not None else 'n/a'):>9}"
                f"{(format(oi, ',') if oi is not None else 'n/a'):>8}{sprd:>6}"
            )
        flagged = [c for c in rows if c.get("caveats")]
        for c in flagged:
            lines.append(f"  ! {c['strike']:g}P: " + "; ".join(c["caveats"]))

    lines.append("")
    primary = payload["picks"].get("primary_expiry")
    lines.append(
        f"Picks (from the {primary} expiry, nearest the {prm['horizon_days']}d horizon; "
        f"other expiries only when it has no fit):" if primary else "Picks:"
    )
    for name in VALID_STANCES:
        p = payload["picks"].get(name)
        if p is None:
            lines.append(f"  {name:<13}(~{STANCE_DELTA[name]:.2f}d): none within range")
            continue
        size = ""
        if p.get("contracts_affordable") is not None:
            size = f" · {p['contracts_affordable']} contract(s)"
        hist = p.get("p_below_strike_hist")
        hist_s = f", hist {_pct(hist, 0)} (n = {p['n_windows']})" if hist is not None else ""
        lines.append(
            f"  {name:<13}(~{STANCE_DELTA[name]:.2f}d): {p['expiry']} {p['strike']:g}P @ ${p['mid']:.2f} mid: "
            f"{_pct(p['static_yield'], 2)} static, {_pct(p['annualized_yield'])} annualized, "
            f"breakeven ${p['breakeven']:.2f} ({_pct(p['cushion_pct'])} below spot), "
            f"|delta| {p['delta_abs']:.2f}{hist_s}{size}"
        )

    lines.append("")
    lines.append("Take: " + payload["take"])

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    if any(c.get("delta_source") == "black_scholes" for c in payload["candidates"]):
        lines.append("  * delta from Black-Scholes on chain IV (chain greeks absent)")

    return "\n".join(lines)
