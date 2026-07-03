"""
sector-rotation-signal as an importable library function.

Change-detection layer on top of market-regime. market-regime reports
current sector leadership (e.g. XLV/XLF/XLI leading, XLE/XLK lagging)
as a snapshot. This skill tracks how the leadership ORDER has changed
over the rotation window and flags sectors moving up or down the rank.

Rank change is the leading signal. The market already prices absolute
strength; rotation is what tells you the composition of leadership is
shifting.

    from quant_garage.skills.sector_rotation_signal import run, render
    payload = run(rotation_window=30)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
)


# Reuse the sector taxonomy from market-regime so both skills stay in sync.
from .market_regime import (
    SECTOR_ETFS,
    GROWTH_SECTORS,
    DEFENSIVE_SECTORS,
)


# Additional groupings for the thematic read
VALUE_CYCLICAL_SECTORS = {"XLE", "XLI", "XLB", "XLF"}
RATE_SENSITIVE_SECTORS = {"XLRE", "XLU", "XLF"}


def _fetch_bars(
    client: MassiveClient, ticker: str, from_date: date, to_date: date,
) -> list[dict]:
    try:
        body, _ = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{from_date.isoformat()}/{to_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 5000},
        )
    except FetchError:
        return []
    return body.get("results") or []


def _bars_to_close_by_date(bars: list[dict]) -> dict[date, float]:
    out: dict[date, float] = {}
    for b in bars:
        d = datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        out[d] = float(b["c"])
    return out


def _pct_return(close_series: dict[date, float], end_d: date,
                lookback: int) -> float | None:
    """N-day close-to-close percentage return ending at end_d."""
    if end_d not in close_series:
        return None
    sorted_dates = sorted(close_series.keys())
    try:
        idx = sorted_dates.index(end_d)
    except ValueError:
        return None
    start_idx = idx - lookback
    if start_idx < 0:
        return None
    start_close = close_series[sorted_dates[start_idx]]
    end_close = close_series[end_d]
    if start_close <= 0:
        return None
    return (end_close - start_close) / start_close


def _rs_ranks_on_day(
    sector_closes: dict[str, dict[date, float]],
    spy_closes: dict[date, float],
    ref_day: date,
    rs_window: int,
) -> tuple[dict[str, float], dict[str, int]] | None:
    """Compute RS (bps vs SPY) and rank per sector for a specific reference day.

    Returns (rs_by_sector, rank_by_sector) where rank 1 = best RS.
    None if data insufficient for that day.
    """
    spy_ret = _pct_return(spy_closes, ref_day, rs_window)
    if spy_ret is None:
        return None
    rs_bps: dict[str, float] = {}
    for sector, closes in sector_closes.items():
        r = _pct_return(closes, ref_day, rs_window)
        if r is None:
            continue
        rs_bps[sector] = (r - spy_ret) * 10000  # convert to bps
    if not rs_bps:
        return None
    # Rank: 1 = highest RS
    sorted_by_rs = sorted(rs_bps.items(), key=lambda x: -x[1])
    rank_by_sector = {s: i + 1 for i, (s, _) in enumerate(sorted_by_rs)}
    return rs_bps, rank_by_sector


def _last_trading_day_at_or_before(
    closes_by_date: dict[date, float], ref_day: date,
) -> date | None:
    """Return the most recent day in closes_by_date on or before ref_day."""
    for d in sorted(closes_by_date.keys(), reverse=True):
        if d <= ref_day:
            return d
    return None


def _classify_rotation(rank_delta: int) -> str:
    """Positive rank_delta means the sector moved UP the leadership order
    (rank number went DOWN, i.e. from rank 8 to rank 3). Negative means
    the sector fell in the ranks."""
    if rank_delta >= 3:
        return "rotating_in_strong"
    if rank_delta >= 2:
        return "rotating_in"
    if rank_delta <= -3:
        return "rotating_out_strong"
    if rank_delta <= -2:
        return "rotating_out"
    return "stable"


def _theme_read(rotations: list[dict]) -> str:
    """Generate a plain-English macro theme read based on which categories
    are rotating in and out."""
    inflow = {r["sector"] for r in rotations if "rotating_in" in r["rotation"]}
    outflow = {r["sector"] for r in rotations if "rotating_out" in r["rotation"]}

    defensive_in = len(inflow & DEFENSIVE_SECTORS)
    defensive_out = len(outflow & DEFENSIVE_SECTORS)
    growth_in = len(inflow & GROWTH_SECTORS)
    growth_out = len(outflow & GROWTH_SECTORS)
    value_in = len(inflow & VALUE_CYCLICAL_SECTORS)
    value_out = len(outflow & VALUE_CYCLICAL_SECTORS)
    rate_in = len(inflow & RATE_SENSITIVE_SECTORS)
    rate_out = len(outflow & RATE_SENSITIVE_SECTORS)

    # Order matters: check strongest signals first
    if defensive_in >= 2 and growth_out >= 2:
        return ("Risk-off rotation: defensives (Utilities, Staples, "
                "Healthcare) taking share from growth (Tech, "
                "Discretionary, Communications). Late-cycle or "
                "risk-averse positioning signal.")
    if growth_in >= 2 and defensive_out >= 2:
        return ("Risk-on rotation: growth sectors (Tech, Discretionary, "
                "Communications) taking share from defensives. Consistent "
                "with a constructive tape and expansion regime.")
    if value_in >= 2 and growth_out >= 2:
        return ("Value rotation: cyclicals (Energy, Industrials, "
                "Materials, Financials) taking share from growth. "
                "Consistent with late-cycle expansion or rate-normalizing "
                "regimes.")
    if growth_in >= 2 and value_out >= 2:
        return ("Growth rotation: Tech/Discretionary/Communications "
                "taking share from cyclicals. Consistent with slowing "
                "growth or falling-rate expectations.")
    if rate_in >= 2:
        return ("Rate-sensitive rotation: Real Estate, Utilities, and/or "
                "Financials rotating in. Suggests a shift in rate "
                "expectations (falling nominals into REIT/Utilities, "
                "or rising into Financials).")
    if defensive_in >= 2:
        return ("Partial defensive rotation: defensives moving up without "
                "broad growth pullback. Constructive-cautious tape.")
    if growth_in >= 2:
        return ("Partial growth rotation: growth moving up without broad "
                "defensive pullback. Constructive tape, no clean "
                "risk-on read yet.")
    if not inflow and not outflow:
        return ("No meaningful rotation this window. Leadership is stable; "
                "trade at the current regime signal.")
    return ("Mixed rotation: no clean thematic read. Individual sector "
            "moves are not aligning into a growth/value or "
            "risk-on/risk-off pattern.")


def run(
    rotation_window: int = 30,
    rs_window: int = 20,
    lookback_days: int = 252,
    rs_secondary_window: int = 60,
    client: MassiveClient | None = None,
) -> dict:
    """Compute sector rotation across the SPDR sector ETFs.

    Args:
        rotation_window: days over which to compute rank change. Default 30.
        rs_window: primary RS window in trading days. Default 20.
        lookback_days: history for RS baseline. Default 252.
        rs_secondary_window: secondary RS window for the table. Default 60.
        client: reuse an existing MassiveClient.
    """
    if rotation_window < 5:
        raise ValueError("rotation_window must be >= 5")
    if rs_window < 5:
        raise ValueError("rs_window must be >= 5")

    client = client or MassiveClient()
    today_d = today()
    # Pull enough history for the secondary window
    fetch_days = max(lookback_days, rotation_window + rs_secondary_window + 30)
    fetch_start = today_d - timedelta(days=int(fetch_days * 1.6))

    # Fetch SPY + 11 sector ETFs
    print(f"Fetching SPY + {len(SECTOR_ETFS)} sector ETFs...", file=sys.stderr)
    spy_bars = _fetch_bars(client, "SPY", fetch_start, today_d)
    if not spy_bars:
        raise RuntimeError("no SPY bars; check MASSIVE_API_KEY")
    spy_closes = _bars_to_close_by_date(spy_bars)

    sector_closes: dict[str, dict[date, float]] = {}
    missing: list[str] = []
    for ticker in SECTOR_ETFS:
        bars = _fetch_bars(client, ticker, fetch_start, today_d)
        if not bars:
            missing.append(ticker)
            continue
        sector_closes[ticker] = _bars_to_close_by_date(bars)

    if not sector_closes:
        raise RuntimeError("no sector ETF data available")

    # Reference days
    now_ref = _last_trading_day_at_or_before(spy_closes, today_d)
    then_ref = _last_trading_day_at_or_before(
        spy_closes, today_d - timedelta(days=rotation_window)
    )
    if now_ref is None or then_ref is None:
        raise RuntimeError("insufficient trading days for the rotation window")

    now_result = _rs_ranks_on_day(
        sector_closes, spy_closes, now_ref, rs_window
    )
    then_result = _rs_ranks_on_day(
        sector_closes, spy_closes, then_ref, rs_window
    )
    if now_result is None or then_result is None:
        raise RuntimeError("insufficient history for RS at reference days")
    now_rs, now_rank = now_result
    then_rs, then_rank = then_result

    # Secondary RS at now (for display context)
    secondary_result = _rs_ranks_on_day(
        sector_closes, spy_closes, now_ref, rs_secondary_window
    )
    secondary_rs = secondary_result[0] if secondary_result else {}

    # Build per-sector table
    rotations: list[dict] = []
    all_sectors = list(SECTOR_ETFS.keys())
    for s in all_sectors:
        if s not in now_rank or s not in then_rank:
            continue
        # rank_delta > 0 means moved UP the leadership order
        # (lower rank number is better)
        rank_delta = then_rank[s] - now_rank[s]
        rotation = _classify_rotation(rank_delta)
        rotations.append({
            "sector": s,
            "sector_name": SECTOR_ETFS[s],
            "rank_now": now_rank[s],
            "rank_then": then_rank[s],
            "rank_delta": rank_delta,
            "rs_now_bps": round(now_rs[s], 1),
            "rs_then_bps": round(then_rs[s], 1),
            "rs_bps_delta": round(now_rs[s] - then_rs[s], 1),
            "rs_60d_bps": (round(secondary_rs[s], 1)
                             if s in secondary_rs else None),
            "rotation": rotation,
            "category_tags": _sector_tags(s),
        })
    rotations.sort(key=lambda r: r["rank_now"])

    # Rotating in/out lists
    rotating_in = [r for r in rotations if "rotating_in" in r["rotation"]]
    rotating_out = [r for r in rotations if "rotating_out" in r["rotation"]]
    theme = _theme_read(rotations)

    return {
        "scan_params": {
            "rotation_window_days": rotation_window,
            "rs_window_days": rs_window,
            "rs_secondary_window_days": rs_secondary_window,
            "lookback_days": lookback_days,
            "as_of": today_d.isoformat(),
            "now_ref_date": now_ref.isoformat(),
            "then_ref_date": then_ref.isoformat(),
            "missing_sector_etfs": missing,
        },
        "rotations": rotations,
        "rotating_in": [
            {"sector": r["sector"], "name": r["sector_name"],
             "rank_delta": r["rank_delta"],
             "category_tags": r["category_tags"]}
            for r in rotating_in
        ],
        "rotating_out": [
            {"sector": r["sector"], "name": r["sector_name"],
             "rank_delta": r["rank_delta"],
             "category_tags": r["category_tags"]}
            for r in rotating_out
        ],
        "theme_read": theme,
        "generated_at": utcnow_iso(),
        "caveats": [
            "Rank change is a lagging measure of leadership shift. A "
            "1-position move is inside the noise floor at short "
            "rotation windows; the tool classifies only 2+ position "
            "moves as rotation.",
            "SPDR sector ETFs are a proxy for the full sector universe. "
            "Real-world sector performance can diverge from the ETF "
            "(especially in Energy, where XLE is oil-major heavy).",
            "RS is past-return relative to benchmark; the tool does not "
            "predict continuation. Pair with market-regime for context.",
            "Rotation reads are heuristic. Real regime classification "
            "belongs in a dedicated macro tool; this is a change-detection "
            "surface, not a regime call.",
        ],
    }


def _sector_tags(sector: str) -> list[str]:
    """Return category tags for a sector ETF."""
    tags: list[str] = []
    if sector in GROWTH_SECTORS:
        tags.append("growth")
    if sector in DEFENSIVE_SECTORS:
        tags.append("defensive")
    if sector in VALUE_CYCLICAL_SECTORS:
        tags.append("value_cyclical")
    if sector in RATE_SENSITIVE_SECTORS:
        tags.append("rate_sensitive")
    return tags


# ---------- Renderer ----------

def _fmt_bps(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{'+' if x >= 0 else ''}{x:.0f}bp"


def _rotation_label(rotation: str) -> str:
    return {
        "rotating_in_strong": "↑↑ rotating in",
        "rotating_in":         "↑  rotating in",
        "stable":              "   stable",
        "rotating_out":        "↓  rotating out",
        "rotating_out_strong": "↓↓ rotating out",
    }.get(rotation, "?")


def render(payload: dict) -> str:
    params = payload["scan_params"]
    rotations = payload["rotations"]
    theme = payload["theme_read"]
    lines: list[str] = []

    lines.append(
        f"Sector Rotation Signal — {params['as_of']}\n"
        f"Rotation window: {params['rotation_window_days']}d "
        f"({params['then_ref_date']} -> {params['now_ref_date']}) · "
        f"Primary RS: {params['rs_window_days']}d · "
        f"Secondary RS: {params['rs_secondary_window_days']}d"
    )
    lines.append("")

    lines.append(f"Theme: {theme}")
    lines.append("")

    lines.append(
        f"{'Sector':<8}{'Name':<24}{'Rank':>6}"
        f"{'Δ Rank':>8}{'20d RS':>10}{'Δ 20d RS':>11}{'60d RS':>10}  Rotation"
    )
    lines.append("-" * 96)
    for r in rotations:
        delta = r["rank_delta"]
        delta_str = f"{'+' if delta > 0 else ''}{delta}"
        lines.append(
            f"{r['sector']:<8}{r['sector_name'][:24]:<24}"
            f"{r['rank_now']:>6}{delta_str:>8}"
            f"{_fmt_bps(r['rs_now_bps']):>10}"
            f"{_fmt_bps(r['rs_bps_delta']):>11}"
            f"{_fmt_bps(r['rs_60d_bps']):>10}  "
            f"{_rotation_label(r['rotation'])}"
        )
    lines.append("")

    rotating_in = payload.get("rotating_in") or []
    rotating_out = payload.get("rotating_out") or []
    if rotating_in:
        in_str = ", ".join(
            f"{r['sector']} ({'+' if r['rank_delta'] > 0 else ''}{r['rank_delta']})"
            for r in rotating_in
        )
        lines.append(f"Rotating in:  {in_str}")
    if rotating_out:
        out_str = ", ".join(
            f"{r['sector']} ({r['rank_delta']})"
            for r in rotating_out
        )
        lines.append(f"Rotating out: {out_str}")
    if not rotating_in and not rotating_out:
        lines.append("No sectors moved more than 1 rank position this window.")

    lines.append("")
    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines)
