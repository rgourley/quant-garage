"""
signal-decay as an importable library function.

Takes a ticker and a signal specification (momentum, mean-reversion,
or vol expansion), computes rolling information coefficient (IC) vs
forward returns over a 2-year window, fits an exponential decay to
the IC series, and reports the half-life in trading days. Motivated
by the growing 2024-2025 literature on factor decay (Israel-Moskowitz
updates, Falck-Rej-Thesmar 2024, Chen-Zimmermann factor zoo) showing
most published signals have decayed sharply post-publication.

Answers "is this signal still working, and if so, for how long
looking forward?"

    from quant_garage.skills.signal_decay import run, render
    payload = run("SPY", signal_kind="momentum")

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Literal

import numpy as np

from .. import MassiveClient, FetchError, today, utcnow_iso
from ..backtest import rolling_backtest, forward_returns, rolling_ic_series
from ..performance import tearsheet


SignalKind = Literal["momentum", "mean_reversion", "vol_expansion", "trend_break"]


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
        vol = r.get("v")
        if ts_ms is None or close is None or close <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close), "volume": float(vol or 0)})
    rows.sort(key=lambda x: x["date"])
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily bars for {ticker}",
    )
    return rows


# ----- Signal builders -----

def _signal_momentum(prices: np.ndarray, volumes: np.ndarray, window: int = 20) -> np.ndarray:
    """N-day log return."""
    n = prices.size
    out = np.full(n, np.nan)
    for i in range(window, n):
        p0 = prices[i - window]
        p1 = prices[i]
        if p0 > 0 and p1 > 0:
            out[i] = math.log(p1 / p0)
    return out


def _signal_mean_reversion(prices: np.ndarray, volumes: np.ndarray, window: int = 20) -> np.ndarray:
    """Negative of z-score to a moving average: high when oversold vs SMA."""
    n = prices.size
    out = np.full(n, np.nan)
    for i in range(window, n):
        window_prices = prices[i - window: i]
        m = float(np.mean(window_prices))
        s = float(np.std(window_prices, ddof=1))
        if s > 0:
            out[i] = -(prices[i] - m) / s
    return out


def _signal_vol_expansion(prices: np.ndarray, volumes: np.ndarray, window: int = 20) -> np.ndarray:
    """Ratio of short vol to long vol. > 1 = vol expanding."""
    n = prices.size
    out = np.full(n, np.nan)
    if n < window * 2 + 2:
        return out
    log_rets = np.diff(np.log(prices))
    for i in range(window * 2, n):
        short = log_rets[i - window: i]
        long = log_rets[i - 2 * window: i - window]
        s_short = float(np.std(short, ddof=1))
        s_long = float(np.std(long, ddof=1))
        if s_long > 0:
            out[i] = s_short / s_long
    return out


def _signal_trend_break(prices: np.ndarray, volumes: np.ndarray, window: int = 20) -> np.ndarray:
    """(close - N-day SMA) / (N-day ATR). Trend-following breakout indicator."""
    n = prices.size
    out = np.full(n, np.nan)
    if n < window + 2:
        return out
    log_rets = np.diff(np.log(prices))
    for i in range(window, n):
        window_prices = prices[i - window: i]
        window_rets = log_rets[i - window: i]
        sma = float(np.mean(window_prices))
        atr = float(np.std(window_rets, ddof=1)) * prices[i]
        if atr > 0:
            out[i] = (prices[i] - sma) / atr
    return out


_SIGNAL_BUILDERS = {
    "momentum": _signal_momentum,
    "mean_reversion": _signal_mean_reversion,
    "vol_expansion": _signal_vol_expansion,
    "trend_break": _signal_trend_break,
}


# ----- Decay fit -----

def _fit_exp_decay(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    """
    Fit |y| = a * exp(-lambda * x) by log-linear regression on log|y|.

    Returns (half_life_x_units, decay_rate). half_life = ln(2) / lambda.
    Returns (None, None) when no consistent decay (positive slope or
    insufficient signal) is found.
    """
    mag = np.abs(y)
    mask = (mag > 1e-8) & np.isfinite(mag) & np.isfinite(x)
    if mask.sum() < 5:
        return (None, None)
    logmag = np.log(mag[mask])
    x_fit = x[mask]
    # OLS: log(mag) = c - lambda * x
    n = x_fit.size
    mean_x = float(np.mean(x_fit))
    mean_y = float(np.mean(logmag))
    num = float(np.sum((x_fit - mean_x) * (logmag - mean_y)))
    den = float(np.sum((x_fit - mean_x) ** 2))
    if den == 0:
        return (None, None)
    slope = num / den
    if slope >= 0:
        return (None, round(float(slope), 6))
    lam = -slope
    if lam <= 0 or not math.isfinite(lam):
        return (None, None)
    half_life = math.log(2) / lam
    if not math.isfinite(half_life) or half_life < 1 or half_life > 5000:
        return (None, round(float(-slope), 6))
    return (round(float(half_life), 1), round(float(-slope), 6))


# ----- Public API -----

def run(
    ticker: str,
    signal_kind: str = "momentum",
    signal_window: int = 20,
    forward_horizon: int = 5,
    ic_window: int = 63,
    lookback_days: int = 1260,
    client: MassiveClient | None = None,
) -> dict:
    """
    Compute the rolling IC of a signal and fit its decay.

    Args:
        ticker: single stock ticker.
        signal_kind: one of momentum / mean_reversion / vol_expansion /
            trend_break.
        signal_window: lookback for the signal itself (default 20 bars).
        forward_horizon: forward return horizon for scoring (default 5
            bars).
        ic_window: rolling window for IC computation (default 63 = one
            quarter).
        lookback_days: total window in trading days (default 1260 ~= 5
            years to capture decay).
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if signal_kind not in _SIGNAL_BUILDERS:
        raise ValueError(f"signal_kind must be one of {list(_SIGNAL_BUILDERS.keys())}")
    if lookback_days < 400:
        raise ValueError("lookback_days must be at least 400 to fit decay")

    client = client or MassiveClient()
    sources = _Sources()

    calendar_days = int(lookback_days * 1.6) + 30
    rows = _fetch_daily(client, ticker, calendar_days, sources)
    if len(rows) < 300:
        raise ValueError(f"insufficient history: got {len(rows)} bars")
    if len(rows) > lookback_days + 1:
        rows = rows[-(lookback_days + 1):]

    dates = [r["date"] for r in rows]
    prices = np.array([r["close"] for r in rows], dtype=float)
    volumes = np.array([r["volume"] for r in rows], dtype=float)

    builder = _SIGNAL_BUILDERS[signal_kind]
    signal_series = builder(prices, volumes, window=signal_window)
    fwd = forward_returns(prices, horizon=forward_horizon)

    # Rolling IC series
    try:
        ic_series = rolling_ic_series(signal_series, fwd, window=ic_window)
    except ValueError as e:
        raise ValueError(f"IC series computation failed: {e}") from e

    if len(ic_series) < 30:
        raise ValueError(f"IC series too short: {len(ic_series)} points")

    # Fit decay to |IC|
    ic_values = np.array([e["ic_pearson"] for e in ic_series if e["ic_pearson"] is not None])
    ic_indices = np.array([e["index"] for e in ic_series if e["ic_pearson"] is not None], dtype=float)
    ic_indices = ic_indices - ic_indices[0]  # normalize so t=0 is first observation

    half_life, decay_rate = _fit_exp_decay(ic_indices, ic_values)

    # Overall IC stats
    ic_mean_full = float(np.mean(ic_values))
    ic_std_full = float(np.std(ic_values, ddof=1)) if ic_values.size > 1 else 0.0

    # Recent vs full window comparison
    recent_n = min(63, ic_values.size // 4)
    ic_mean_recent = float(np.mean(ic_values[-recent_n:])) if recent_n > 0 else 0.0
    ic_mean_early = float(np.mean(ic_values[:recent_n])) if recent_n > 0 else 0.0
    ic_delta = ic_mean_recent - ic_mean_early

    # Signal PnL (long the sign of the signal at each forward horizon)
    signed = np.sign(signal_series[:-forward_horizon])
    signed[~np.isfinite(signed)] = 0
    fwd_valid = fwd[:-forward_horizon]
    pnl_daily = signed * (fwd_valid / forward_horizon)  # scaled to daily equivalent
    pnl_daily = pnl_daily[np.isfinite(pnl_daily)]
    tear = None
    try:
        if pnl_daily.size >= 60:
            tear = tearsheet(pnl_daily)
    except ValueError:
        tear = None

    # Classify
    if half_life is None:
        classification = "not_significantly_decaying"
    elif half_life < 60:
        classification = "fast_decay"
    elif half_life < 250:
        classification = "moderate_decay"
    elif half_life < 1000:
        classification = "slow_decay"
    else:
        classification = "essentially_stable"

    tier_caveats: list[str] = []
    tier_caveats.append(
        "Decay fit is log-linear on |IC|. Signals with regime shifts (rather than "
        "smooth decay) may not fit well; check the recent-vs-early IC delta."
    )
    if half_life is None:
        tier_caveats.append(
            "No significant decay detected: either the signal is stable across the window "
            "or the IC series is too noisy to fit a decay rate."
        )
    if abs(ic_mean_full) < 0.03:
        tier_caveats.append(
            f"|Mean IC| = {abs(ic_mean_full):.3f} is low; signal may not be economically "
            "meaningful regardless of decay rate."
        )

    return {
        "skill": "signal-decay",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "signal_kind": signal_kind,
        "signal_window": int(signal_window),
        "forward_horizon": int(forward_horizon),
        "ic_window": int(ic_window),
        "lookback_days": int(lookback_days),
        "n_prices": int(prices.size),
        "n_ic_points": int(ic_values.size),
        "ic_mean": round(ic_mean_full, 4),
        "ic_std": round(ic_std_full, 4),
        "ic_mean_early": round(ic_mean_early, 4),
        "ic_mean_recent": round(ic_mean_recent, 4),
        "ic_delta_recent_minus_early": round(ic_delta, 4),
        "decay_rate_per_day": decay_rate,
        "half_life_trading_days": half_life,
        "classification": classification,
        "signal_tearsheet": tear,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_CLASS_TAG = {
    "fast_decay": "FAST DECAY",
    "moderate_decay": "MODERATE DECAY",
    "slow_decay": "slow decay",
    "essentially_stable": "STABLE",
    "not_significantly_decaying": "no significant decay",
}


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x*100:.2f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    sk = payload["signal_kind"]
    sw = payload["signal_window"]
    fh = payload["forward_horizon"]
    ic_w = payload["ic_window"]

    lines.append(
        f"Signal decay: {ticker} · signal={sk}({sw}d) · forward={fh}d · IC window={ic_w}d · "
        f"{payload['n_ic_points']} IC obs"
    )
    tag = _CLASS_TAG.get(payload["classification"], payload["classification"])
    if payload["half_life_trading_days"] is not None:
        lines.append(
            f"Half-life: {payload['half_life_trading_days']:.1f} trading days · {tag}"
        )
    else:
        lines.append(f"Half-life: n/a · {tag}")
    lines.append("")

    lines.append("IC statistics")
    lines.append(f"  Mean IC (full window):  {payload['ic_mean']:+.4f} (σ={payload['ic_std']:.4f})")
    lines.append(f"  Mean IC (early quarter): {payload['ic_mean_early']:+.4f}")
    lines.append(f"  Mean IC (recent quarter): {payload['ic_mean_recent']:+.4f}")
    lines.append(f"  Δ recent - early:         {payload['ic_delta_recent_minus_early']:+.4f}")
    lines.append("")

    tear = payload.get("signal_tearsheet")
    if tear:
        lines.append("Signed-signal PnL tearsheet")
        lines.append(f"  CAGR:              {_fmt_pct(tear['cagr'])}")
        lines.append(f"  Annualized vol:    {_fmt_pct(tear['annualized_vol'])}")
        lines.append(f"  Sharpe:            {tear['sharpe']:>6.2f}")
        dsr_p = tear.get("deflated_sharpe_pvalue")
        dsr_str = f"{dsr_p:.4f}" if dsr_p is not None else "n/a"
        lines.append(f"  Deflated Sharpe p: {dsr_str}")
        lines.append(f"  Sortino:           {tear['sortino']:>6.2f}" if tear['sortino'] else f"  Sortino:           n/a")
        lines.append(f"  Max drawdown:      {_fmt_pct(tear['max_drawdown'])}")
        lines.append(f"  Calmar:            {tear['calmar']:>6.2f}" if tear['calmar'] else f"  Calmar:            n/a")
        lines.append(f"  Ulcer index:       {tear['ulcer_index']:>6.2f}")
        lines.append(f"  Profit factor:     {tear['profit_factor']:>6.2f}" if tear['profit_factor'] else f"  Profit factor:     n/a")
        lines.append(f"  Tail ratio:        {tear['tail_ratio']:>6.2f}" if tear['tail_ratio'] else f"  Tail ratio:        n/a")
        lines.append(f"  Hit rate (daily):  {tear['hit_rate']*100:>5.1f}%")
        if tear.get("monthly_hit_rate") is not None:
            lines.append(f"  Hit rate (monthly): {tear['monthly_hit_rate']*100:>5.1f}%")
        lines.append("")

    # Take
    take_parts: list[str] = []
    if payload["classification"] == "fast_decay":
        take_parts.append(
            f"Fast decay: {sk} on {ticker} loses half its predictive power every "
            f"{payload['half_life_trading_days']:.0f} trading days. Refit signals monthly "
            "or drop them."
        )
    elif payload["classification"] == "moderate_decay":
        take_parts.append(
            f"Moderate decay: {sk} half-life {payload['half_life_trading_days']:.0f} days. "
            "Signal is still useful but retune quarterly."
        )
    elif payload["classification"] == "slow_decay":
        take_parts.append(
            f"Slow decay: {sk} half-life {payload['half_life_trading_days']:.0f} days. "
            "Structural signal, but monitor quarterly."
        )
    elif payload["classification"] == "essentially_stable":
        take_parts.append(
            f"{sk} signal on {ticker} appears essentially stable over the window (half-life "
            f"> 1000 days). Unusual; may indicate a fundamental factor."
        )
    else:
        take_parts.append(
            "No significant decay detected in the IC series. The signal is either steady "
            "across the window or has regime shifts that don't fit an exponential decay."
        )
    if payload["ic_delta_recent_minus_early"] < -0.02:
        take_parts.append(
            f"Recent IC ({payload['ic_mean_recent']:+.3f}) is meaningfully weaker than early "
            f"({payload['ic_mean_early']:+.3f}); signal may be regime-broken beyond the decay fit."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
