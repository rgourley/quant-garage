"""
mc-portfolio-simulator as an importable library function.

Standalone Monte Carlo forward P&L simulator for a book. Given a
weights dict and a horizon, returns the full cumulative-return
distribution, tail scenarios, path max-drawdown distribution, and
probability of loss / gain at configurable thresholds.

Companion to position-sizer: answers "given my proposed weights, what
is the 5th percentile 60-day portfolio outcome?" Uses the same
covariance-matrix pipeline (shrunk correlation × per-name vols) that
risk-report and position-sizer already use, so a single MC run is
directly comparable to those tools.

    from quant_garage.skills.mc_portfolio_simulator import run, render
    payload = run(
        "NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25",
        simulation_days=60,
        n_paths=10_000,
    )

Reads MASSIVE_API_KEY from env. Stocks Basic minimum (daily aggs only).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Iterable, Mapping

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    annualized_vol,
    ewma_vol,
    correlation_matrix,
    covariance_matrix,
    shrink_correlation,
    simulate_correlated_paths,
    percentile_summary,
)


N_TRADING_MIN = 60
_AGGS_CACHE: dict[str, list[dict]] = {}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _parse_positions(positions: Mapping[str, float] | str) -> dict[str, float]:
    if isinstance(positions, str):
        out: dict[str, float] = {}
        for chunk in positions.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"Bad positions entry {chunk!r}, expected 'TKR=weight'.")
            t, w = chunk.split("=", 1)
            out[t.strip().upper()] = float(w.strip())
        return out
    return {k.strip().upper(): float(v) for k, v in positions.items()}


def _fetch_daily_aggs(client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources) -> list[dict]:
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        _AGGS_CACHE[ticker] = []
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
    _AGGS_CACHE[ticker] = rows
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily aggs for {ticker}",
    )
    return rows


def _log_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(float(np.log(closes[i] / closes[i - 1])))
    return out


def run(
    positions: Mapping[str, float] | str,
    simulation_days: int = 60,
    n_paths: int = 10_000,
    tail: str = "normal",
    tail_df: float = 4.0,
    lookback_days: int = 252,
    vol_method: str = "realized",
    ewma_lambda: float = 0.94,
    shrinkage: float = 0.05,
    seed: int | None = 42,
    client: MassiveClient | None = None,
) -> dict:
    """Simulate `n_paths` correlated return trajectories for a book.

    Args:
        positions: dict {ticker: weight} or 'TKR=w,TKR2=w' string. Weights
            should sum to 1.0 for a fully-invested long book; short weights
            are allowed.
        simulation_days: forward horizon in trading days. Default 60.
        n_paths: MC path count. Default 10,000.
        tail: 'normal' or 'student_t'. Student-t gives fatter tails.
        tail_df: student-t degrees of freedom (default 4).
        lookback_days: trading-day window to fit covariance. Default 252.
        vol_method: 'realized' or 'ewma'. Default realized.
        ewma_lambda: EWMA decay when vol_method='ewma'. Default 0.94.
        shrinkage: correlation shrinkage toward identity. Default 0.05.
        seed: rng seed for reproducibility. Default 42.
        client: reuse an existing MassiveClient.
    """
    weights = _parse_positions(positions)
    if not weights:
        raise ValueError("provide at least one position")
    if simulation_days < 1 or n_paths < 100:
        raise ValueError("simulation_days >= 1 and n_paths >= 100 required")
    if tail not in ("normal", "student_t"):
        raise ValueError(f"tail must be 'normal' or 'student_t', got {tail!r}")
    if vol_method not in ("realized", "ewma"):
        raise ValueError(f"vol_method must be 'realized' or 'ewma', got {vol_method!r}")

    client = client or MassiveClient()
    sources = _Sources()
    tickers = sorted(weights.keys())
    calendar_days = int(lookback_days * 1.6) + 14

    # Pull aggs, compute log returns
    per_ticker_returns: dict[str, list[float]] = {}
    per_ticker_dates: dict[str, list[str]] = {}
    for t in tickers:
        rows = _fetch_daily_aggs(client, t, calendar_days, sources)
        if len(rows) < N_TRADING_MIN:
            continue
        recent = rows[-lookback_days - 1:] if len(rows) > lookback_days + 1 else rows
        closes = [r["close"] for r in recent]
        rets = _log_returns(closes)
        if len(rets) < N_TRADING_MIN - 1:
            continue
        per_ticker_returns[t] = rets
        per_ticker_dates[t] = [r["date"] for r in recent[1:]]
        time.sleep(0.02)

    tier_caveats: list[str] = []

    missing = [t for t in tickers if t not in per_ticker_returns]
    if missing:
        tier_caveats.append(
            f"Insufficient history for {', '.join(missing)}; dropped from simulation."
        )
    tickers_used = [t for t in tickers if t in per_ticker_returns]
    if len(tickers_used) < 1:
        raise ValueError("no tickers had sufficient history")

    # Align to common dates
    date_sets = [set(per_ticker_dates[t]) for t in tickers_used]
    common = sorted(set.intersection(*date_sets))
    if len(common) < N_TRADING_MIN:
        raise ValueError(
            f"only {len(common)} common trading days across the book; need >= {N_TRADING_MIN}"
        )
    aligned: dict[str, list[float]] = {}
    for t in tickers_used:
        d2r = dict(zip(per_ticker_dates[t], per_ticker_returns[t]))
        aligned[t] = [d2r[d] for d in common if d in d2r]

    # Per-name annualized vol
    vols: dict[str, float] = {}
    for t in tickers_used:
        r = np.asarray(aligned[t], dtype=float)
        if vol_method == "ewma":
            vols[t] = float(ewma_vol(r, lambda_=ewma_lambda))
        else:
            vols[t] = float(annualized_vol(r))

    # Correlation matrix + covariance
    ordered_tickers, raw_corr = correlation_matrix(aligned)
    shrunk = shrink_correlation(raw_corr, shrinkage)
    cov = covariance_matrix(vols, shrunk, ordered_tickers)

    # Weight vector and drop tickers that were excluded from history
    weight_vec = np.array([weights.get(t, 0.0) for t in ordered_tickers], dtype=float)
    excluded_weight = sum(w for t, w in weights.items() if t not in ordered_tickers)

    mean_daily = np.array(
        [float(np.mean(aligned[t])) for t in ordered_tickers],
        dtype=float,
    )

    # Simulate
    paths = simulate_correlated_paths(
        mean_daily=mean_daily,
        cov_annualized=cov,
        n_paths=int(n_paths),
        n_days=int(simulation_days),
        tail=tail,
        df=float(tail_df),
        seed=seed,
    )
    port_daily = np.einsum("pdk,k->pd", paths, weight_vec)
    cum_ret = port_daily.sum(axis=1)  # (n_paths,)

    nav_paths = np.cumprod(np.exp(port_daily), axis=1)
    running_peak = np.maximum.accumulate(nav_paths, axis=1)
    dd = (nav_paths / running_peak) - 1.0
    max_dd_per_path = dd.min(axis=1)

    ret_summary = percentile_summary(cum_ret)
    dd_summary = percentile_summary(max_dd_per_path)

    loss_thresholds = [0.05, 0.10, 0.20, 0.30]
    gain_thresholds = [0.05, 0.10, 0.20]
    loss_probs = {
        f"P_loss_gt_{int(t*100)}pct": round(float(np.mean(cum_ret < -t)), 4)
        for t in loss_thresholds
    }
    gain_probs = {
        f"P_gain_gt_{int(t*100)}pct": round(float(np.mean(cum_ret > t)), 4)
        for t in gain_thresholds
    }

    tier_caveats.append(
        f"Monte Carlo: {n_paths:,} paths × {simulation_days}-day horizon "
        f"under {tail} innovations"
        + (f" (df={tail_df})" if tail == "student_t" else "")
        + ". Simulates from the fitted covariance; does not model regime "
        "shifts, jumps, or correlation breaks beyond what the sample "
        "already captured."
    )
    if excluded_weight != 0:
        tier_caveats.append(
            f"{excluded_weight*100:.1f}% of the book was excluded from the simulation "
            "due to missing history."
        )

    return {
        "skill": "mc-portfolio-simulator",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "tickers_used": ordered_tickers,
        "weights_used": {t: round(float(weights.get(t, 0.0)), 6) for t in ordered_tickers},
        "excluded_weight": round(float(excluded_weight), 6),
        "lookback_days": int(lookback_days),
        "n_obs_aligned": int(len(common)),
        "vol_method": vol_method,
        "ewma_lambda": ewma_lambda if vol_method == "ewma" else None,
        "shrinkage": float(shrinkage),
        "simulation_days": int(simulation_days),
        "n_paths": int(n_paths),
        "tail": tail,
        "tail_df": float(tail_df) if tail == "student_t" else None,
        "seed": seed,
        "annualized_vols": {t: round(float(vols[t]), 4) for t in ordered_tickers},
        "cumulative_return_distribution": {
            k: round(float(v), 6) for k, v in ret_summary.items() if k != "n"
        } | {"n": int(ret_summary["n"])},
        "max_drawdown_distribution": {
            k: round(float(v), 6) for k, v in dd_summary.items() if k != "n"
        } | {"n": int(dd_summary["n"])},
        "loss_probabilities": loss_probs,
        "gain_probabilities": gain_probs,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_pct(x: float, signed: bool = False, decimals: int = 1) -> str:
    if x is None:
        return "n/a"
    if signed:
        sign = "+" if x >= 0 else ""
        return f"{sign}{x*100:.{decimals}f}%"
    return f"{x*100:.{decimals}f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    ordered = payload["tickers_used"]
    horizon = payload["simulation_days"]
    n_paths = payload["n_paths"]
    tail = payload["tail"]
    tail_df = payload.get("tail_df")

    lines.append(
        f"MC Portfolio Simulator: {len(ordered)} names · "
        f"{horizon}d horizon · {n_paths:,} paths"
    )
    tail_str = tail + (f" df={tail_df}" if tail == "student_t" else "")
    lines.append(
        f"Lookback {payload['lookback_days']}d ({payload['n_obs_aligned']} obs) · "
        f"{payload['vol_method']} vol · {tail_str}"
    )
    lines.append("")

    lines.append("Portfolio composition")
    weights = payload["weights_used"]
    vols = payload["annualized_vols"]
    for t in ordered:
        lines.append(
            f"  {t:<8} weight {weights[t]*100:>5.1f}%  σ(annual) {vols[t]*100:>5.1f}%"
        )
    lines.append("")

    cum = payload["cumulative_return_distribution"]
    lines.append(f"Cumulative return over {horizon}d")
    lines.append(
        f"  Mean {_fmt_pct(cum['mean'], signed=True)} · σ {_fmt_pct(cum['std'])}"
    )
    lines.append(
        f"  p5 {_fmt_pct(cum['p5'], signed=True):<8} "
        f"p10 {_fmt_pct(cum['p10'], signed=True):<8} "
        f"p25 {_fmt_pct(cum['p25'], signed=True):<8} "
        f"p50 {_fmt_pct(cum['p50'], signed=True):<8}"
    )
    lines.append(
        f"  p75 {_fmt_pct(cum['p75'], signed=True):<8} "
        f"p90 {_fmt_pct(cum['p90'], signed=True):<8} "
        f"p95 {_fmt_pct(cum['p95'], signed=True):<8}"
    )
    lines.append("")

    dd = payload["max_drawdown_distribution"]
    lines.append("Path max drawdown")
    lines.append(
        f"  Median {_fmt_pct(dd['p50'])} · "
        f"p25 (typical bad) {_fmt_pct(dd['p25'])} · "
        f"p10 (bad case) {_fmt_pct(dd['p10'])} · "
        f"p5 (tail case) {_fmt_pct(dd['p5'])}"
    )
    lines.append("")

    loss = payload["loss_probabilities"]
    gain = payload["gain_probabilities"]
    lines.append("Probability")
    lines.append(
        f"  Loss > 5%:  {loss['P_loss_gt_5pct']*100:>5.1f}%   "
        f"Loss > 10%: {loss['P_loss_gt_10pct']*100:>5.1f}%   "
        f"Loss > 20%: {loss['P_loss_gt_20pct']*100:>5.1f}%   "
        f"Loss > 30%: {loss['P_loss_gt_30pct']*100:>5.1f}%"
    )
    lines.append(
        f"  Gain > 5%:  {gain['P_gain_gt_5pct']*100:>5.1f}%   "
        f"Gain > 10%: {gain['P_gain_gt_10pct']*100:>5.1f}%   "
        f"Gain > 20%: {gain['P_gain_gt_20pct']*100:>5.1f}%"
    )
    lines.append("")

    # Take
    p50 = cum["p50"]
    p5 = cum["p5"]
    p_loss_10 = loss["P_loss_gt_10pct"]
    dd_p10 = dd["p10"]
    take_parts: list[str] = []
    take_parts.append(
        f"Median {horizon}d outcome is {_fmt_pct(p50, signed=True)}, "
        f"tail (p5) is {_fmt_pct(p5, signed=True)}."
    )
    if p_loss_10 >= 0.10:
        take_parts.append(f"{p_loss_10*100:.0f}% chance of a 10%+ drawdown; size accordingly.")
    take_parts.append(f"Worst-case path drawdown (p10) is {_fmt_pct(dd_p10)}.")
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines).rstrip()
