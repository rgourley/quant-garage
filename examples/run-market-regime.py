#!/usr/bin/env python3
"""
Reference implementation of the market-regime skill.

Daily-use macro context tool. Pulls SPY + VIX + 11 sector ETFs, computes
SPY trend (5 buckets via SMA stack), VIX state with percentile rank vs
trailing year, breadth proxy from sector ETF % above 50-day / 200-day,
and 20-day RS leadership ranking. Composite regime label (risk_on,
risk_off, mixed_risk_on, mixed_risk_off, neutral) is derived from the
four blocks with explicit reasons[] so the user sees the evidence.

Usage:
    python3 examples/run-market-regime.py
    python3 examples/run-market-regime.py --lookback-days 252 --format render
    python3 examples/run-market-regime.py --format json

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/market-regime-output.md (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import numpy as np

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    sma,
    percentile_rank,
    format_rank_label,
    resolve_output_format,
    emit_to_stdout,
)


# ----- Config -----

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

# Module-level per-ticker aggs cache. The run is one-shot but we may
# hit the same ticker twice in different code paths (e.g. SPY for trend
# AND for the RS benchmark) so a tiny cache keeps the call count honest.
_AGGS_CACHE: dict[str, list[dict]] = {}

# Tickers that came back empty because the tier throttled us, not because
# there's no data. Tracked so breadth/leadership caveat the reader loudly
# instead of silently computing on a partial, inconsistent denominator.
_RATE_LIMITED: set[str] = set()

# Seconds between aggregate calls. Free Basic caps at 5/min; --sleep 13 keeps
# the 13-call SPY+VIX+11-sector batch under the ceiling. Set from --sleep.
_SLEEP_BETWEEN: float = 0.0

# One 429 cooldown: 5/min is a 12s spacing; 13s adds a 1s margin.
_RATE_LIMIT_COOLDOWN_SECONDS = 13

_SOURCES: list[dict] = []

_client_singleton: Optional[MassiveClient] = None


def _client() -> MassiveClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = MassiveClient()
    return _client_singleton


# ----- CLI -----

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=252,
        help="Trading-day lookback for percentile/RS windows (default 252)",
    )
    ap.add_argument(
        "--benchmark",
        default="SPY",
        help="Benchmark ticker used for trend + RS denominator (default SPY)",
    )
    ap.add_argument(
        "--vix-ticker",
        default="VIX",
        help="VIX ticker. Tries this first, falls back to I:VIX. (default VIX)",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep between aggregate calls. Free Basic caps at 5 "
             "calls/min and this skill pulls 13+ series (SPY + VIX + 11 "
             "sectors); use --sleep 13 to stay under the ceiling. Default 0.",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Output markdown path (default: examples/market-regime-output.md)",
    )
    ap.add_argument(
        "--format",
        choices=["render", "json", "both"],
        default=None,
        help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.",
    )
    return ap.parse_args()


# ----- Data pull -----

def fetch_daily_aggs(ticker: str, lookback_days: int) -> list[dict]:
    """Fetch daily aggs for `ticker` covering ~lookback_days trading days.

    Pull `lookback_days * 1.6` calendar days back from today (covers
    weekends + holidays). Returns the parsed `results` list in
    ascending-date order, or an empty list on failure.
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    to_date = today()
    from_date = to_date - timedelta(days=int(lookback_days * 1.6))
    path = (
        f"/v2/aggs/ticker/{ticker}"
        f"/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        body, fetched_at = _client().get(path, params)
    except RateLimited:
        # Client already retried with backoff and still got 429. One cooldown,
        # one more try, then flag the ticker so breadth/leadership can caveat
        # the partial denominator rather than silently shrinking it.
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            body, fetched_at = _client().get(path, params)
        except FetchError as e:
            print(f"  WARN: still failing for {ticker} after cooldown: {e}",
                  file=sys.stderr)
            _RATE_LIMITED.add(ticker)
            _AGGS_CACHE[ticker] = []
            return []
    except FetchError:
        _AGGS_CACHE[ticker] = []
        return []
    finally:
        # Space out sequential calls to stay under a metered tier's ceiling.
        if _SLEEP_BETWEEN > 0:
            time.sleep(_SLEEP_BETWEEN)

    results = body.get("results") or []
    _AGGS_CACHE[ticker] = results
    _SOURCES.append({
        "endpoint": f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}",
        "fetched_at": fetched_at,
        "context": f"Daily closes for {ticker} ({lookback_days}d lookback)",
    })
    return results


def fetch_vix_aggs(vix_ticker: str, lookback_days: int) -> tuple[list[dict], Optional[str]]:
    """Walk a chain of VIX symbol variants until one returns data.

    VIX is an index, so on Massive/Polygon it lives under the `I:` prefix
    (`I:VIX`). The bare `VIX` symbol only resolves if the account also has an
    equities symbol by that name, which it usually doesn't. We try, in order:

      1. Whatever the caller passed (`--vix-ticker`, default "VIX")
      2. The `I:`-prefixed index form (`I:VIX`)
      3. A couple of common index aliases (`I:VIXCLS`)

    Returns (results, source_ticker). source_ticker is None if every
    candidate failed, which on a Stocks-only plan almost always means the
    Indices data entitlement is missing (see the caveat in build_payload and
    PLAN-MATRIX.md), not that the symbol is wrong.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for cand in (
        vix_ticker,
        f"I:{vix_ticker}" if not vix_ticker.startswith("I:") else vix_ticker,
        "I:VIX",
        "I:VIXCLS",
    ):
        if cand and cand not in seen:
            seen.add(cand)
            candidates.append(cand)

    for cand in candidates:
        aggs = fetch_daily_aggs(cand, lookback_days)
        if aggs:
            return aggs, cand
    return [], None


# ----- Helpers -----

def closes(aggs: list[dict]) -> np.ndarray:
    return np.asarray([a["c"] for a in aggs if a.get("c") is not None], dtype=float)


def pct_change_window(arr: np.ndarray, window: int) -> Optional[float]:
    """Return (last / arr[-1-window] - 1) or None if too short."""
    if arr.size <= window:
        return None
    prev = float(arr[-1 - window])
    last = float(arr[-1])
    if prev <= 0:
        return None
    return last / prev - 1.0


# ----- Compute blocks -----

def compute_spy_trend(aggs: list[dict]) -> Optional[dict]:
    """Price, SMAs, trend bucket, 1/5/20-day returns."""
    arr = closes(aggs)
    if arr.size < 200:
        return None
    s20 = sma(arr, 20)
    s50 = sma(arr, 50)
    s200 = sma(arr, 200)
    price = float(arr[-1])
    v20 = float(s20[-1])
    v50 = float(s50[-1])
    v200 = float(s200[-1])

    above_20 = price > v20
    above_50 = price > v50
    above_200 = price > v200

    # 5 trend buckets via SMA stack ordering
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
        "return_1d_pct": pct_change_window(arr, 1),
        "return_5d_pct": pct_change_window(arr, 5),
        "return_20d_pct": pct_change_window(arr, 20),
    }


def compute_vix_state(aggs: list[dict], lookback_days: int, source_ticker: str) -> Optional[dict]:
    arr = closes(aggs)
    if arr.size < 20:
        return None
    current = float(arr[-1])
    # Use the trailing `lookback_days` for the percentile distribution.
    distribution = arr[-lookback_days:].tolist() if arr.size > lookback_days else arr.tolist()
    rank = percentile_rank(current, distribution)
    rank_label = format_rank_label(rank)
    avg_20d = float(np.mean(arr[-20:]))

    # State buckets on absolute VIX level (the trader's mental model)
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


def compute_sector_breadth(sector_data: dict[str, list[dict]]) -> dict:
    """Count of sector ETFs above their own 50-day / 200-day SMA."""
    n_above_50 = 0
    n_above_200 = 0
    n_total = 0
    for ticker, aggs in sector_data.items():
        arr = closes(aggs)
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


def compute_sector_leadership(
    sector_data: dict[str, list[dict]],
    spy_aggs: list[dict],
) -> Optional[dict]:
    """Per-sector 1d/5d/20d return + 20d RS vs SPY."""
    spy_closes = closes(spy_aggs)
    spy_20d = pct_change_window(spy_closes, 20)
    if spy_20d is None:
        return None

    all_sectors: list[dict] = []
    for ticker, name in SECTOR_ETFS.items():
        aggs = sector_data.get(ticker, [])
        arr = closes(aggs)
        if arr.size < 21:
            continue
        r1 = pct_change_window(arr, 1)
        r5 = pct_change_window(arr, 5)
        r20 = pct_change_window(arr, 20)
        if r20 is None:
            continue
        rs_bps = round((r20 - spy_20d) * 10000, 1)
        all_sectors.append({
            "ticker": ticker,
            "name": name,
            "return_1d_pct": r1,
            "return_5d_pct": r5,
            "return_20d_pct": r20,
            "rs_20d_bps": rs_bps,
        })

    if not all_sectors:
        return None

    sorted_by_rs = sorted(all_sectors, key=lambda s: s["rs_20d_bps"], reverse=True)
    leaders = sorted_by_rs[:3]
    laggards = list(reversed(sorted_by_rs[-3:]))

    return {
        "top_3_leaders": [
            {"ticker": s["ticker"], "name": s["name"], "rs_20d_bps": s["rs_20d_bps"]}
            for s in leaders
        ],
        "bottom_3_laggards": [
            {"ticker": s["ticker"], "name": s["name"], "rs_20d_bps": s["rs_20d_bps"]}
            for s in laggards
        ],
        "all_sectors": all_sectors,
    }


# ----- Composite regime -----

def compute_composite_regime(
    spy_trend: Optional[dict],
    vix_state: Optional[dict],
    breadth: dict,
    leadership: Optional[dict],
) -> dict:
    """Combine the four blocks into a single regime label with reasons.

    Decision rules (kept explicit so the user sees the evidence):

      risk_on:        SPY uptrend (strong or weak) + VIX quiet/normal
                      + breadth > 50% above 50-day + growth leadership
      risk_off:       SPY downtrend (strong or weak) + VIX elevated/stressed
                      + breadth < 50% + defensive leadership
      mixed_risk_on:  SPY uptrend BUT one negative signal (narrow breadth
                      OR rising VIX)
      mixed_risk_off: SPY downtrend BUT one positive signal (recovering
                      breadth OR VIX falling/normal)
      neutral:        no clear read
    """
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

    # Growth / defensive leadership from the top-3 by RS
    top_tickers: set[str] = set()
    if leadership and leadership.get("top_3_leaders"):
        top_tickers = {s["ticker"] for s in leadership["top_3_leaders"]}

    n_growth_in_top = len(top_tickers & GROWTH_SECTORS)
    n_defensive_in_top = len(top_tickers & DEFENSIVE_SECTORS)
    growth_leads = n_growth_in_top >= 2
    defensive_leads = n_defensive_in_top >= 2

    # ----- decide label -----

    # risk_on: all four blocks line up to the upside
    if is_up and vix_quiet_or_normal and breadth_broad and growth_leads:
        reasons.append(f"SPY {trend}")
        if vix_state:
            reasons.append(
                f"VIX {vix_state['state']} at "
                f"{vix_state['percentile_rank']}th %ile"
                if vix_state.get("percentile_rank") is not None
                else f"VIX {vix_state['state']}"
            )
        reasons.append(
            f"{int(pct_50 * 100)}% of sectors above 50-day SMA"
        )
        leaders_str = ", ".join(sorted(top_tickers & GROWTH_SECTORS))
        reasons.append(f"Growth leadership ({leaders_str})")
        return {"label": "risk_on", "reasons": reasons}

    # risk_off: all four blocks line up to the downside
    if is_down and vix_elevated_or_stressed and breadth_narrow and defensive_leads:
        reasons.append(f"SPY {trend}")
        reasons.append(
            f"VIX {vix_state['state']} at "
            f"{vix_state['percentile_rank']}th %ile"
            if vix_state.get("percentile_rank") is not None
            else f"VIX {vix_state['state']}"
        )
        reasons.append(f"{int(pct_50 * 100)}% of sectors above 50-day SMA")
        defs_str = ", ".join(sorted(top_tickers & DEFENSIVE_SECTORS))
        reasons.append(f"Defensive leadership ({defs_str})")
        return {"label": "risk_off", "reasons": reasons}

    # mixed_risk_on: SPY uptrend with one negative signal
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

    # mixed_risk_off: SPY downtrend with one positive signal
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

    # SPY in `range` — no clear regime
    reasons.append(f"SPY in {trend}")
    if vix_state:
        reasons.append(f"VIX {vix_state['state']}")
    if pct_50 is not None:
        reasons.append(f"{int(pct_50 * 100)}% of sectors above 50-day SMA")
    return {"label": "neutral", "reasons": reasons}


# ----- Render -----

def fmt_pct(x: Optional[float], decimals: int = 2) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.{decimals}f}%"


def fmt_bps(bps: float) -> str:
    sign = "+" if bps >= 0 else ""
    return f"{sign}{bps:.0f}bp"


def render_take(
    label: str,
    spy_trend: Optional[dict],
    vix_state: Optional[dict],
    breadth: dict,
    leadership: Optional[dict],
) -> str:
    """Adaptive single-paragraph take. Rules:

    - For each composite label, summarize what is supporting that read.
    - Mention the *first* thing to watch for a regime change (the
      weakest pillar or the threshold closest to flipping).
    """
    if label == "risk_on":
        first_line = "Risk-on regime. SPY uptrend with broad participation and growth sector leadership"
        if vix_state and vix_state.get("percentile_rank") is not None:
            first_line += f"; VIX at the {format_rank_label(vix_state['percentile_rank'])} signals no immediate fear"
        first_line += "."
        watch = "Watch for VIX > 22 or breadth dropping below 50% as the first sign of regime change."
        return first_line + " " + watch
    if label == "risk_off":
        first_line = "Risk-off regime. SPY downtrend with thin participation and defensive leadership"
        if vix_state:
            first_line += f"; VIX {vix_state['state']} confirms stress"
        first_line += "."
        watch = "Watch for VIX retreat below 22 or growth sectors returning to the top of the RS table for a regime turn."
        return first_line + " " + watch
    if label == "mixed_risk_on":
        first_line = "Mixed risk-on. SPY trend is up but confirmation is incomplete"
        gaps: list[str] = []
        pct_50 = breadth.get("pct_above_sma_50")
        if pct_50 is not None and pct_50 < 0.50:
            gaps.append("breadth has thinned")
        if vix_state and vix_state["state"] in ("elevated", "stressed"):
            gaps.append(f"VIX has lifted to {vix_state['state']}")
        if gaps:
            first_line += " — " + ", ".join(gaps) + "."
        else:
            first_line += "."
        watch = "Treat as constructive but reduce trust until breadth and VIX confirm."
        return first_line + " " + watch
    if label == "mixed_risk_off":
        first_line = "Mixed risk-off. SPY trend is down but at least one block is recovering"
        offsets: list[str] = []
        pct_50 = breadth.get("pct_above_sma_50")
        if pct_50 is not None and pct_50 > 0.50:
            offsets.append("breadth still above 50%")
        if vix_state and vix_state["state"] in ("quiet", "normal"):
            offsets.append(f"VIX {vix_state['state']}")
        if offsets:
            first_line += " — " + ", ".join(offsets) + "."
        else:
            first_line += "."
        watch = "Treat as a defensive lean but watch for a confirmed bottom (breadth turning + VIX rolling over)."
        return first_line + " " + watch
    # neutral
    first_line = "Neutral regime. No clear directional read across SPY trend, VIX, breadth, and sector leadership."
    watch = "Wait for confirmation across two or more blocks before sizing up directional exposure."
    return first_line + " " + watch


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

    # SPY block
    if spy_trend:
        r1 = fmt_pct(spy_trend.get("return_1d_pct"))
        r5 = fmt_pct(spy_trend.get("return_5d_pct"), decimals=1)
        r20 = fmt_pct(spy_trend.get("return_20d_pct"), decimals=1)
        lines.append(
            f"{benchmark}: ${spy_trend['current_price']:.2f} "
            f"({r1} today, {r5} 5d, {r20} 20d) — {spy_trend['trend']}"
        )
        above_bits = []
        if spy_trend["above_sma_20"]:
            above_bits.append("20")
        if spy_trend["above_sma_50"]:
            above_bits.append("50")
        if spy_trend["above_sma_200"]:
            above_bits.append("200")
        if len(above_bits) == 3:
            lines.append("  Above 20/50/200-day MAs")
        elif above_bits:
            lines.append(f"  Above {'/'.join(above_bits)}-day MA(s); below the rest")
        else:
            lines.append("  Below 20/50/200-day MAs")
    else:
        lines.append(f"{benchmark}: insufficient history for trend computation")
    lines.append("")

    # VIX block
    if vix_state:
        pct_rank = vix_state.get("percentile_rank")
        rank_str = f"{pct_rank:.0f}th %ile" if pct_rank is not None else "rank n/a"
        lines.append(
            f"VIX: {vix_state['current']:.1f} ({rank_str} of trailing year) — {vix_state['state']}"
        )
        avg = vix_state.get("avg_20d")
        if avg is not None:
            stress_note = " No stress signal." if vix_state["state"] in ("quiet", "normal") else " Stress signal active."
            lines.append(f"  20-day avg {avg:.1f}.{stress_note}")
    else:
        lines.append("VIX: data unavailable; regime read computed without volatility component")
    lines.append("")

    # Breadth block
    if breadth.get("pct_above_sma_50") is not None:
        pct50 = breadth["pct_above_sma_50"]
        pct200 = breadth["pct_above_sma_200"]
        n50 = breadth["n_above_sma_50"]
        n200 = breadth["n_above_sma_200"]
        ntot = breadth["n_sector_etfs"]
        lines.append(
            f"Breadth (sector ETF proxy): {n50} of {ntot} above 50-day MA "
            f"({int(round(pct50 * 100))}%)"
        )
        lines.append(
            f"  {n200} of {ntot} above 200-day ({int(round(pct200 * 100))}%). "
            f"{breadth['read'].capitalize()}."
        )
    else:
        lines.append("Breadth: insufficient data (need >=200-day history per sector ETF)")
    lines.append("")

    # Leadership block
    if leadership and leadership.get("top_3_leaders"):
        leaders = leadership["top_3_leaders"]
        laggards = leadership["bottom_3_laggards"]
        leaders_str = "  ·  ".join(
            f"{s['ticker']} {fmt_bps(s['rs_20d_bps'])}" for s in leaders
        )
        laggards_str = "  ·  ".join(
            f"{s['ticker']} {fmt_bps(s['rs_20d_bps'])}" for s in laggards
        )
        lines.append("Sector leadership (20-day RS vs SPY):")
        lines.append(f"  Leaders:  {leaders_str}")
        lines.append(f"  Laggards: {laggards_str}")
    else:
        lines.append("Sector leadership: insufficient data")
    lines.append("")

    # Take
    take = render_take(label, spy_trend, vix_state, breadth, leadership)
    # Wrap the take loosely at ~72 chars for readability
    lines.append("Take: " + take)

    # Tier caveats footer (always surface them when present)
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")

    return "\n".join(lines)


# ----- Main -----

def build_payload(args: argparse.Namespace) -> dict:
    global _SLEEP_BETWEEN
    as_of = today().isoformat()
    lookback = args.lookback_days
    benchmark = args.benchmark

    if getattr(args, "sleep", 0.0) and args.sleep > 0:
        _SLEEP_BETWEEN = args.sleep

    # Fetch SPY first; everything else hangs off it
    spy_aggs = fetch_daily_aggs(benchmark, lookback)
    vix_aggs, vix_source = fetch_vix_aggs(args.vix_ticker, lookback)
    sector_data: dict[str, list[dict]] = {
        t: fetch_daily_aggs(t, lookback) for t in SECTOR_ETFS
    }

    # B2: how many of the 11 sectors actually returned usable data. Breadth is
    # computed on whatever came back; if that's fewer than 11 the percentage
    # denominator silently shifts between runs. Surface it explicitly.
    n_expected_sectors = len(SECTOR_ETFS)
    n_fetched_sectors = sum(1 for aggs in sector_data.values() if aggs)

    tier_caveats: list[str] = []

    # Rate-limit caveat goes first and loud: an empty series here shrinks the
    # breadth denominator and can flip the regime read.
    if _RATE_LIMITED:
        tier_caveats.append(
            f"RATE LIMIT: {len(_RATE_LIMITED)} series "
            f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because "
            f"the API rate limit was hit, not because history is missing. The "
            f"regime read below is computed on a PARTIAL set and may be wrong. "
            f"Free Basic tier caps at 5 calls/min: rerun with --sleep 13 or "
            f"upgrade to Stocks Starter."
        )

    if n_fetched_sectors < n_expected_sectors:
        tier_caveats.append(
            f"Breadth computed on {n_fetched_sectors} of {n_expected_sectors} "
            f"sector ETFs; {n_expected_sectors - n_fetched_sectors} did not "
            f"return data. The breadth percentage denominator is partial, so "
            f"compare runs by count (n above MA) not just percent."
        )

    tier_caveats.append(
        "Breadth computed from 11 sector ETFs as a proxy; not the full advance/decline line. Sufficient for regime read, not for fine-grain breadth analysis."
    )
    if vix_source is None:
        tier_caveats.append(
            "VIX data unavailable (tried VIX, I:VIX, I:VIXCLS); regime read "
            "computed without the volatility component. VIX is an index, so it "
            "needs the Indices data entitlement, which Stocks Starter does not "
            "include. See PLAN-MATRIX.md; a Stocks-only plan will always miss "
            "this pillar."
        )

    spy_trend = compute_spy_trend(spy_aggs)
    vix_state = compute_vix_state(vix_aggs, lookback, vix_source) if vix_aggs and vix_source else None
    breadth = compute_sector_breadth(sector_data)
    leadership = compute_sector_leadership(sector_data, spy_aggs)
    composite = compute_composite_regime(spy_trend, vix_state, breadth, leadership)

    return {
        "skill": "market-regime",
        "as_of": as_of,
        "fetched_at": utcnow_iso(),
        "lookback_days": lookback,
        "benchmark": benchmark,
        "spy_trend": spy_trend,
        "vix_state": vix_state,
        "breadth": breadth,
        "sector_leadership": leadership,
        "composite_regime": composite,
        "tier_caveats": tier_caveats,
        "sources": _SOURCES,
    }


def main() -> None:
    args = parse_args()
    fmt = resolve_output_format(args.format)

    payload = build_payload(args)
    rendered = render(payload)

    out_path = args.output or os.path.join(
        os.path.dirname(__file__), "market-regime-output.md"
    )
    with open(out_path, "w") as f:
        f.write("# market-regime run\n\n")
        f.write(f"Generated: {payload['fetched_at']}\n")
        f.write(f"As of: {payload['as_of']}\n")
        f.write(f"Benchmark: {payload['benchmark']} · Lookback: {payload['lookback_days']} trading days\n\n")
        f.write("## Layer 1: canonical JSON\n\n")
        f.write("```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n\n")
        f.write("## Layer 2: rendered hybrid output\n\n")
        f.write("```\n")
        f.write(rendered)
        f.write("\n```\n")

    print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
    emit_to_stdout(rendered, payload, fmt)


if __name__ == "__main__":
    main()
