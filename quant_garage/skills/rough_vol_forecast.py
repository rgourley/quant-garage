"""
rough-vol-forecast as an importable library function.

Estimates rough-volatility-scaled vol forecasts across multiple horizons
for a ticker. Under Bayer-Friz-Gatheral (2016) rough volatility, realized
vol scales like h^H with H ~ 0.1-0.15 empirically — much slower than
the sqrt(t) growth of Brownian motion. This gives more responsive
short-horizon vol and dampens long-horizon extrapolation.

Also compares against traditional annualized vol (h^0.5), EWMA
(RiskMetrics), and realized vol so the caller sees the differences
directly.

    from quant_garage.skills.rough_vol_forecast import run, render
    payload = run("SPY", horizons_days=[1, 5, 20, 60, 120])

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.

References:
    Bayer, Friz, Gatheral (2016), *Pricing Under Rough Volatility*.
    Livieri, Mouti, Pallavicini, Rosenbaum (2018), *Rough Volatility:
        Evidence from Option Prices*.
    Gatheral, Jaisson, Rosenbaum (2018), *Volatility Is Rough*.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Iterable

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    annualized_vol,
    ewma_vol,
    rough_vol_annualized,
    rough_vol_series,
)


# Empirical H for realized-vol on daily equity data (Livieri et al. 2018).
# Estimation on the raw returns tends to be biased upward; the paper's
# calibration on realized vol series consistently returns H around 0.14.
DEFAULT_EMPIRICAL_HURST = 0.14


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _fetch_daily(client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources) -> list[dict]:
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&limit=50000"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        return []
    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None or close <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily bars for {ticker}",
    )
    return rows


def run(
    ticker: str,
    horizons_days: Iterable[int] | None = None,
    lookback_days: int = 504,
    hurst: float | None = None,
    ewma_lambda: float = 0.94,
    client: MassiveClient | None = None,
) -> dict:
    """
    Rough-vol-scaled vol forecast for `ticker` across multiple horizons.

    Args:
        ticker: single stock ticker.
        horizons_days: forecast horizons in trading days. Default
            [1, 5, 20, 60, 120].
        lookback_days: historical window for fit. Default 504.
        hurst: manual override for the Hurst exponent. Default uses
            0.14 (Livieri et al. 2018 empirical estimate for daily
            equity returns).
        ewma_lambda: EWMA decay. Default 0.94 (RiskMetrics).
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if horizons_days is None:
        horizons_days = [1, 5, 20, 60, 120]
    horizons = sorted(set(int(h) for h in horizons_days if int(h) > 0))
    if not horizons:
        raise ValueError("horizons_days must contain at least one positive integer")
    if lookback_days < 60:
        raise ValueError("lookback_days must be at least 60")

    client = client or MassiveClient()
    sources = _Sources()

    calendar_days = int(lookback_days * 1.6) + 21
    rows = _fetch_daily(client, ticker, calendar_days, sources)
    if len(rows) < 60:
        raise ValueError(f"insufficient history: got {len(rows)} bars")
    if len(rows) > lookback_days + 1:
        rows = rows[-(lookback_days + 1):]

    closes = np.array([r["close"] for r in rows], dtype=float)
    log_rets = np.diff(np.log(closes))

    realized_ann = float(annualized_vol(log_rets))
    ewma_ann = float(ewma_vol(log_rets, lambda_=ewma_lambda))

    used_hurst = hurst if hurst is not None else DEFAULT_EMPIRICAL_HURST
    estimated_hurst, _ = rough_vol_series(log_rets)

    per_horizon: list[dict] = []
    for h in horizons:
        # Traditional Brownian scaling: sigma_annual = sigma_daily * sqrt(252)
        # sigma over horizon h days: sigma_daily * sqrt(h) → annualized × sqrt(h/252)
        traditional_h = realized_ann * math.sqrt(h / 252.0)
        ewma_h = ewma_ann * math.sqrt(h / 252.0)
        # Rough scaling: sigma over horizon h days scales as h^H
        rough_h = realized_ann * (h ** used_hurst) / math.sqrt(252.0)
        ratio_vs_traditional = rough_h / traditional_h if traditional_h > 0 else None
        per_horizon.append({
            "horizon_days": h,
            "traditional_vol": round(traditional_h, 6),
            "ewma_vol": round(ewma_h, 6),
            "rough_vol": round(rough_h, 6),
            "rough_over_traditional": round(ratio_vs_traditional, 4) if ratio_vs_traditional else None,
        })

    tier_caveats: list[str] = [
        f"Rough volatility scaling under Bayer-Friz-Gatheral (2016) with "
        f"H={used_hurst} (Livieri et al. 2018 empirical default for daily returns). "
        "Override with --hurst if you have a name-specific estimate.",
        "Traditional Brownian scaling would use sqrt(t) growth. Rough scaling uses t^H "
        f"with H={used_hurst}, which damps long-horizon growth vs sqrt(t) and lifts short-horizon "
        "estimates.",
    ]
    if hurst is None:
        tier_caveats.append(
            f"Sample R/S estimator gave H_est = {estimated_hurst:.3f} on this series. "
            "This value is not the same as the rough-vol H (which should be estimated on "
            "realized volatility, not raw returns). Not used as default; kept in the payload "
            "for transparency."
        )

    return {
        "skill": "rough-vol-forecast",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "lookback_days": int(lookback_days),
        "n_returns": int(log_rets.size),
        "hurst_used": float(used_hurst),
        "hurst_estimated_on_returns": round(float(estimated_hurst), 4),
        "realized_annualized_vol": round(realized_ann, 6),
        "ewma_annualized_vol": round(ewma_ann, 6),
        "ewma_lambda": float(ewma_lambda),
        "horizons_days": horizons,
        "per_horizon": per_horizon,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x*100:.2f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    lb = payload["lookback_days"]
    H = payload["hurst_used"]

    lines.append(
        f"Rough volatility forecast: {ticker} · {lb}d lookback · "
        f"{payload['n_returns']} returns · H = {H}"
    )
    lines.append(
        f"Realized ann vol: {_fmt_pct(payload['realized_annualized_vol'])} · "
        f"EWMA (λ={payload['ewma_lambda']}) ann vol: {_fmt_pct(payload['ewma_annualized_vol'])}"
    )
    lines.append("")

    lines.append(f"{'Horizon':<10} {'Traditional':>13} {'EWMA':>10} {'Rough (H='+str(H)+')':>18} {'Ratio':>10}")
    lines.append("-" * 62)
    for row in payload["per_horizon"]:
        h = row["horizon_days"]
        trad = _fmt_pct(row["traditional_vol"])
        ewma = _fmt_pct(row["ewma_vol"])
        rough = _fmt_pct(row["rough_vol"])
        ratio = f"{row['rough_over_traditional']:.2f}x" if row["rough_over_traditional"] else "n/a"
        lines.append(f"{h:<10} {trad:>13} {ewma:>10} {rough:>18} {ratio:>10}")
    lines.append("")

    # Take
    take_parts: list[str] = []
    long_horizon = payload["per_horizon"][-1]
    ratio = long_horizon.get("rough_over_traditional")
    if ratio and ratio < 0.7:
        take_parts.append(
            f"Rough vol scaling damps the {long_horizon['horizon_days']}-day vol forecast "
            f"to {ratio:.2f}x traditional sqrt-time (from "
            f"{_fmt_pct(long_horizon['traditional_vol'])} to "
            f"{_fmt_pct(long_horizon['rough_vol'])})."
        )
    elif ratio and ratio > 1.3:
        take_parts.append(
            f"Rough vol scaling lifts the {long_horizon['horizon_days']}-day vol forecast "
            f"to {ratio:.2f}x traditional (rare — check H)."
        )
    else:
        take_parts.append(
            "Rough and traditional vol forecasts are close over the horizons requested; the "
            "rough-vol effect is more pronounced at longer horizons."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
