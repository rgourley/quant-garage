"""
market-regime as an importable library function.

Daily-use macro context tool. Pulls SPY + VIX + 11 sector ETFs, computes
SPY trend (5 buckets via SMA stack), VIX state with percentile rank vs
trailing year, breadth proxy from sector ETF % above 50-day / 200-day,
and 20-day RS leadership ranking. Composite regime label (risk_on,
risk_off, mixed_risk_on, mixed_risk_off, neutral) with explicit reasons[].

    from quant_garage.skills.market_regime import run, render
    payload = run(lookback_days=252)
    print(render(payload))
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    sma,
    percentile_rank,
    format_rank_label,
)


SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

GROWTH_SECTORS = {"XLK", "XLY", "XLC"}
DEFENSIVE_SECTORS = {"XLP", "XLU", "XLV"}

# Module-level per-ticker aggs cache. Safe: same ticker may be requested
# twice within a run (e.g. SPY as trend AND as RS benchmark) and by
# downstream skills reusing the module in the same process.
_AGGS_CACHE: dict[str, list[dict]] = {}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- Data pull -----

def _fetch_daily_aggs(
    client: MassiveClient, ticker: str, lookback_days: int, sources: _Sources,
) -> list[dict]:
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]
    to_date = today()
    from_date = to_date - timedelta(days=int(lookback_days * 1.6))
    path = (
        f"/v2/aggs/ticker/{ticker}"
        f"/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}"
    )
    try:
        body, fetched_at = client.get(
            path, {"adjusted": "true", "sort": "asc", "limit": 50000}
        )
    except FetchError:
        _AGGS_CACHE[ticker] = []
        return []
    results = body.get("results") or []
    _AGGS_CACHE[ticker] = results
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}",
        fetched_at,
        f"Daily closes for {ticker} ({lookback_days}d lookback)",
    )
    return results


def _fetch_vix_aggs(
    client: MassiveClient, vix_ticker: str, lookback_days: int, sources: _Sources,
) -> tuple[list[dict], Optional[str]]:
    primary = _fetch_daily_aggs(client, vix_ticker, lookback_days, sources)
    if primary:
        return primary, vix_ticker
    fallback_t = f"I:{vix_ticker}" if not vix_ticker.startswith("I:") else vix_ticker
    if fallback_t != vix_ticker:
        fallback = _fetch_daily_aggs(client, fallback_t, lookback_days, sources)
        if fallback:
            return fallback, fallback_t
    return [], None


# ----- Helpers -----

def _closes(aggs: list[dict]) -> np.ndarray:
    return np.asarray([a["c"] for a in aggs if a.get("c") is not None], dtype=float)


def _pct_change_window(arr: np.ndarray, window: int) -> Optional[float]:
    if arr.size <= window:
        return None
    prev = float(arr[-1 - window])
    last = float(arr[-1])
    if prev <= 0:
        return None
    return last / prev - 1.0


# ----- Compute blocks -----

def _compute_spy_trend(aggs: list[dict]) -> Optional[dict]:
    arr = _closes(aggs)
    if arr.size < 200:
        return None
    s20 = sma(arr, 20)
    s50 = sma(arr, 50)
    s200 = sma(arr, 200)
    price = float(arr[-1])
    v20 = float(s20[-1]); v50 = float(s50[-1]); v200 = float(s200[-1])
    above_20 = price > v20; above_50 = price > v50; above_200 = price > v200

    if price > v20 > v50 > v200:
        trend = "uptrend_strong"
    elif above_50 and above_200:
        trend = "uptrend_weak"
    elif price < v20 < v50 < v200:
        trend = "downtrend_strong"
    elif not above_50 and not above_200:
        trend = "downtrend_weak"
    else:
        trend = "range"

    return {
        "current_price": round(price, 2),
        "sma_20": round(v20, 2),
        "sma_50": round(v50, 2),
        "sma_200": round(v200, 2),
        "trend": trend,
        "above_sma_20": above_20,
        "above_sma_50": above_50,
        "above_sma_200": above_200,
        "return_1d_pct": _pct_change_window(arr, 1),
        "return_5d_pct": _pct_change_window(arr, 5),
        "return_20d_pct": _pct_change_window(arr, 20),
    }


def _compute_vix_state(aggs: list[dict], lookback_days: int, source_ticker: str) -> Optional[dict]:
    arr = _closes(aggs)
    if arr.size < 20:
        return None
    current = float(arr[-1])
    distribution = arr[-lookback_days:].tolist() if arr.size > lookback_days else arr.tolist()
    rank = percentile_rank(current, distribution)
    rank_label = format_rank_label(rank)
    avg_20d = float(np.mean(arr[-20:]))

    if current < 15:
        state = "quiet"
    elif current < 22:
        state = "normal"
    elif current < 30:
        state = "elevated"
    else:
        state = "stressed"

    return {
        "current": round(current, 2),
        "percentile_rank": round(rank, 1) if rank is not None else None,
        "rank_label": rank_label,
        "state": state,
        "avg_20d": round(avg_20d, 2),
        "source": source_ticker,
    }


def _compute_sector_breadth(sector_data: dict[str, list[dict]]) -> dict:
    n_above_50 = 0; n_above_200 = 0; n_total = 0
    for ticker, aggs in sector_data.items():
        arr = _closes(aggs)
        if arr.size < 200:
            continue
        n_total += 1
        s50 = sma(arr, 50)
        s200 = sma(arr, 200)
        price = float(arr[-1])
        if price > float(s50[-1]):
            n_above_50 += 1
        if price > float(s200[-1]):
            n_above_200 += 1

    if n_total == 0:
        return {
            "method": "sector_etf_proxy",
            "n_sector_etfs": 0,
            "n_above_sma_50": 0,
            "n_above_sma_200": 0,
            "pct_above_sma_50": None,
            "pct_above_sma_200": None,
            "read": "insufficient data",
        }
    pct_50 = n_above_50 / n_total
    pct_200 = n_above_200 / n_total
    if pct_50 >= 0.70:
        read = "broad participation"
    elif pct_50 >= 0.50:
        read = "mixed participation"
    elif pct_50 >= 0.30:
        read = "narrow participation"
    else:
        read = "thin participation"
    return {
        "method": "sector_etf_proxy",
        "n_sector_etfs": n_total,
        "n_above_sma_50": n_above_50,
        "n_above_sma_200": n_above_200,
        "pct_above_sma_50": round(pct_50, 3),
        "pct_above_sma_200": round(pct_200, 3),
        "read": read,
    }


def _compute_sector_leadership(
    sector_data: dict[str, list[dict]], spy_aggs: list[dict],
) -> Optional[dict]:
    spy_closes = _closes(spy_aggs)
    spy_20d = _pct_change_window(spy_closes, 20)
    if spy_20d is None:
        return None
    all_sectors: list[dict] = []
    for ticker, name in SECTOR_ETFS.items():
        aggs = sector_data.get(ticker, [])
        arr = _closes(aggs)
        if arr.size < 21:
            continue
        r1 = _pct_change_window(arr, 1)
        r5 = _pct_change_window(arr, 5)
        r20 = _pct_change_window(arr, 20)
        if r20 is None:
            continue
        rs_bps = round((r20 - spy_20d) * 10000, 1)
        all_sectors.append({
            "ticker": ticker, "name": name,
            "return_1d_pct": r1, "return_5d_pct": r5, "return_20d_pct": r20,
            "rs_20d_bps": rs_bps,
        })
    if not all_sectors:
        return None
    sorted_by_rs = sorted(all_sectors, key=lambda s: s["rs_20d_bps"], reverse=True)
    leaders = sorted_by_rs[:3]
    laggards = list(reversed(sorted_by_rs[-3:]))
    return {
        "top_3_leaders": [{"ticker": s["ticker"], "name": s["name"], "rs_20d_bps": s["rs_20d_bps"]} for s in leaders],
        "bottom_3_laggards": [{"ticker": s["ticker"], "name": s["name"], "rs_20d_bps": s["rs_20d_bps"]} for s in laggards],
        "all_sectors": all_sectors,
    }


def _compute_composite_regime(
    spy_trend: Optional[dict], vix_state: Optional[dict],
    breadth: dict, leadership: Optional[dict],
) -> dict:
    reasons: list[str] = []
    if spy_trend is None:
        return {"label": "neutral", "reasons": ["insufficient SPY history for trend computation"]}
    trend = spy_trend["trend"]
    is_up = trend in ("uptrend_strong", "uptrend_weak")
    is_down = trend in ("downtrend_strong", "downtrend_weak")
    vix_quiet_or_normal = vix_state is not None and vix_state["state"] in ("quiet", "normal")
    vix_elevated_or_stressed = vix_state is not None and vix_state["state"] in ("elevated", "stressed")
    vix_unavailable = vix_state is None
    pct_50 = breadth.get("pct_above_sma_50")
    breadth_broad = pct_50 is not None and pct_50 > 0.50
    breadth_narrow = pct_50 is not None and pct_50 < 0.50
    top_tickers: set[str] = set()
    if leadership and leadership.get("top_3_leaders"):
        top_tickers = {s["ticker"] for s in leadership["top_3_leaders"]}
    n_growth_in_top = len(top_tickers & GROWTH_SECTORS)
    n_defensive_in_top = len(top_tickers & DEFENSIVE_SECTORS)
    growth_leads = n_growth_in_top >= 2
    defensive_leads = n_defensive_in_top >= 2

    if is_up and vix_quiet_or_normal and breadth_broad and growth_leads:
        reasons.append(f"SPY {trend}")
        if vix_state:
            reasons.append(
                f"VIX {vix_state['state']} at {vix_state['percentile_rank']}th %ile"
                if vix_state.get("percentile_rank") is not None else f"VIX {vix_state['state']}"
            )
        reasons.append(f"{int(pct_50 * 100)}% of sectors above 50-day SMA")
        reasons.append(f"Growth leadership ({', '.join(sorted(top_tickers & GROWTH_SECTORS))})")
        return {"label": "risk_on", "reasons": reasons}

    if is_down and vix_elevated_or_stressed and breadth_narrow and defensive_leads:
        reasons.append(f"SPY {trend}")
        reasons.append(
            f"VIX {vix_state['state']} at {vix_state['percentile_rank']}th %ile"
            if vix_state.get("percentile_rank") is not None else f"VIX {vix_state['state']}"
        )
        reasons.append(f"{int(pct_50 * 100)}% of sectors above 50-day SMA")
        reasons.append(f"Defensive leadership ({', '.join(sorted(top_tickers & DEFENSIVE_SECTORS))})")
        return {"label": "risk_off", "reasons": reasons}

    if is_up:
        reasons.append(f"SPY {trend}")
        negatives: list[str] = []
        if breadth_narrow:
            negatives.append(f"narrow breadth ({int(pct_50 * 100)}% above 50-day)")
        if vix_state and vix_state["state"] in ("elevated", "stressed"):
            negatives.append(f"VIX {vix_state['state']}")
        if defensive_leads:
            negatives.append("defensive leadership")
        if vix_unavailable:
            negatives.append("VIX data unavailable")
        if negatives:
            reasons.append(f"but {'; '.join(negatives)}")
        elif not growth_leads:
            reasons.append("but no clear growth-sector leadership")
        else:
            reasons.append("with mixed confirmation across breadth and leadership")
        return {"label": "mixed_risk_on", "reasons": reasons}

    if is_down:
        reasons.append(f"SPY {trend}")
        positives: list[str] = []
        if breadth_broad:
            positives.append(f"broad breadth ({int(pct_50 * 100)}% above 50-day)")
        if vix_state and vix_state["state"] in ("quiet", "normal"):
            positives.append(f"VIX {vix_state['state']}")
        if growth_leads:
            positives.append("growth leadership recovering")
        if positives:
            reasons.append(f"but {'; '.join(positives)}")
        else:
            reasons.append("with no clear positive offset")
        return {"label": "mixed_risk_off", "reasons": reasons}

    reasons.append(f"SPY in {trend}")
    if vix_state:
        reasons.append(f"VIX {vix_state['state']}")
    if pct_50 is not None:
        reasons.append(f"{int(pct_50 * 100)}% of sectors above 50-day SMA")
    return {"label": "neutral", "reasons": reasons}


# ----- Public API -----

def run(
    lookback_days: int = 252,
    benchmark: str = "SPY",
    vix_ticker: str = "VIX",
    client: MassiveClient | None = None,
) -> dict:
    """Compute a full market-regime payload.

    Args:
        lookback_days: trading-day lookback for percentile / RS windows. Default 252.
        benchmark: trend + RS denominator. Default 'SPY'.
        vix_ticker: primary VIX ticker; falls back to 'I:<TICKER>'. Default 'VIX'.
        client: reuse an existing MassiveClient (avoids re-init cost when composed).

    Returns the canonical market-regime payload dict.
    """
    client = client or MassiveClient()
    sources = _Sources()
    as_of = today().isoformat()

    spy_aggs = _fetch_daily_aggs(client, benchmark, lookback_days, sources)
    vix_aggs, vix_source = _fetch_vix_aggs(client, vix_ticker, lookback_days, sources)
    sector_data = {t: _fetch_daily_aggs(client, t, lookback_days, sources) for t in SECTOR_ETFS}

    tier_caveats: list[str] = [
        "Breadth computed from 11 sector ETFs as a proxy; not the full advance/decline line. Sufficient for regime read, not for fine-grain breadth analysis."
    ]
    if vix_source is None:
        tier_caveats.append("VIX data unavailable; regime read computed without volatility component.")

    spy_trend = _compute_spy_trend(spy_aggs)
    vix_state = _compute_vix_state(vix_aggs, lookback_days, vix_source) if vix_aggs and vix_source else None
    breadth = _compute_sector_breadth(sector_data)
    leadership = _compute_sector_leadership(sector_data, spy_aggs)
    composite = _compute_composite_regime(spy_trend, vix_state, breadth, leadership)

    return {
        "skill": "market-regime",
        "as_of": as_of,
        "fetched_at": utcnow_iso(),
        "lookback_days": lookback_days,
        "benchmark": benchmark,
        "spy_trend": spy_trend,
        "vix_state": vix_state,
        "breadth": breadth,
        "sector_leadership": leadership,
        "composite_regime": composite,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_pct(x: Optional[float], decimals: int = 2) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.{decimals}f}%"


def _fmt_bps(bps: float) -> str:
    sign = "+" if bps >= 0 else ""
    return f"{sign}{bps:.0f}bp"


def _render_take(
    label: str, spy_trend: Optional[dict], vix_state: Optional[dict],
    breadth: dict, leadership: Optional[dict],
) -> str:
    if label == "risk_on":
        line = "Risk-on regime. SPY uptrend with broad participation and growth sector leadership"
        if vix_state and vix_state.get("percentile_rank") is not None:
            line += f"; VIX at the {format_rank_label(vix_state['percentile_rank'])} signals no immediate fear"
        line += "."
        return line + " Watch for VIX > 22 or breadth dropping below 50% as the first sign of regime change."
    if label == "risk_off":
        line = "Risk-off regime. SPY downtrend with thin participation and defensive leadership"
        if vix_state:
            line += f"; VIX {vix_state['state']} confirms stress"
        line += "."
        return line + " Watch for VIX retreat below 22 or growth sectors returning to the top of the RS table for a regime turn."
    if label == "mixed_risk_on":
        line = "Mixed risk-on. SPY trend is up but confirmation is incomplete"
        gaps: list[str] = []
        pct_50 = breadth.get("pct_above_sma_50")
        if pct_50 is not None and pct_50 < 0.50:
            gaps.append("breadth has thinned")
        if vix_state and vix_state["state"] in ("elevated", "stressed"):
            gaps.append(f"VIX has lifted to {vix_state['state']}")
        if gaps:
            line += " — " + ", ".join(gaps) + "."
        else:
            line += "."
        return line + " Treat as constructive but reduce trust until breadth and VIX confirm."
    if label == "mixed_risk_off":
        line = "Mixed risk-off. SPY trend is down but at least one block is recovering"
        offsets: list[str] = []
        pct_50 = breadth.get("pct_above_sma_50")
        if pct_50 is not None and pct_50 > 0.50:
            offsets.append("breadth still above 50%")
        if vix_state and vix_state["state"] in ("quiet", "normal"):
            offsets.append(f"VIX {vix_state['state']}")
        if offsets:
            line += " — " + ", ".join(offsets) + "."
        else:
            line += "."
        return line + " Treat as a defensive lean but watch for a confirmed bottom (breadth turning + VIX rolling over)."
    return ("Neutral regime. No clear directional read across SPY trend, VIX, breadth, and sector leadership."
            " Wait for confirmation across two or more blocks before sizing up directional exposure.")


def render(payload: dict) -> str:
    lines: list[str] = []
    as_of = payload["as_of"]
    benchmark = payload["benchmark"]
    composite = payload["composite_regime"]
    label = composite["label"]
    spy_trend = payload["spy_trend"]
    vix_state = payload["vix_state"]
    breadth = payload["breadth"]
    leadership = payload["sector_leadership"]

    lines.append(f"Market Regime — {as_of}")
    lines.append(label.upper())
    lines.append("")

    if spy_trend:
        r1 = _fmt_pct(spy_trend.get("return_1d_pct"))
        r5 = _fmt_pct(spy_trend.get("return_5d_pct"), decimals=1)
        r20 = _fmt_pct(spy_trend.get("return_20d_pct"), decimals=1)
        lines.append(f"{benchmark}: ${spy_trend['current_price']:.2f} ({r1} today, {r5} 5d, {r20} 20d) — {spy_trend['trend']}")
        above_bits = []
        if spy_trend["above_sma_20"]: above_bits.append("20")
        if spy_trend["above_sma_50"]: above_bits.append("50")
        if spy_trend["above_sma_200"]: above_bits.append("200")
        if len(above_bits) == 3:
            lines.append("  Above 20/50/200-day MAs")
        elif above_bits:
            lines.append(f"  Above {'/'.join(above_bits)}-day MA(s); below the rest")
        else:
            lines.append("  Below 20/50/200-day MAs")
    else:
        lines.append(f"{benchmark}: insufficient history for trend computation")
    lines.append("")

    if vix_state:
        pct_rank = vix_state.get("percentile_rank")
        rank_str = f"{pct_rank:.0f}th %ile" if pct_rank is not None else "rank n/a"
        lines.append(f"VIX: {vix_state['current']:.1f} ({rank_str} of trailing year) — {vix_state['state']}")
        avg = vix_state.get("avg_20d")
        if avg is not None:
            stress_note = " No stress signal." if vix_state["state"] in ("quiet", "normal") else " Stress signal active."
            lines.append(f"  20-day avg {avg:.1f}.{stress_note}")
    else:
        lines.append("VIX: data unavailable; regime read computed without volatility component")
    lines.append("")

    if breadth.get("pct_above_sma_50") is not None:
        pct50 = breadth["pct_above_sma_50"]
        pct200 = breadth["pct_above_sma_200"]
        n50 = breadth["n_above_sma_50"]
        n200 = breadth["n_above_sma_200"]
        ntot = breadth["n_sector_etfs"]
        lines.append(f"Breadth (sector ETF proxy): {n50} of {ntot} above 50-day MA ({int(round(pct50 * 100))}%)")
        lines.append(f"  {n200} of {ntot} above 200-day ({int(round(pct200 * 100))}%). {breadth['read'].capitalize()}.")
    else:
        lines.append("Breadth: insufficient data (need >=200-day history per sector ETF)")
    lines.append("")

    if leadership and leadership.get("top_3_leaders"):
        leaders = leadership["top_3_leaders"]
        laggards = leadership["bottom_3_laggards"]
        leaders_str = "  ·  ".join(f"{s['ticker']} {_fmt_bps(s['rs_20d_bps'])}" for s in leaders)
        laggards_str = "  ·  ".join(f"{s['ticker']} {_fmt_bps(s['rs_20d_bps'])}" for s in laggards)
        lines.append("Sector leadership (20-day RS vs SPY):")
        lines.append(f"  Leaders:  {leaders_str}")
        lines.append(f"  Laggards: {laggards_str}")
    else:
        lines.append("Sector leadership: insufficient data")
    lines.append("")

    take = _render_take(label, spy_trend, vix_state, breadth, leadership)
    lines.append("Take: " + take)

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")

    return "\n".join(lines)
