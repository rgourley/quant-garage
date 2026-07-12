"""
Performance tearsheet helpers.

One-call API for a full performance report on a return series. Pattern
borrowed from QuantStats (2024): call tearsheet(returns) and get every
standard performance metric in a single dict.

    from quant_garage.performance import tearsheet
    stats = tearsheet(daily_returns, benchmark=spy_returns)
    print(stats["sharpe"], stats["deflated_sharpe_pvalue"], stats["max_drawdown"])
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from .stats import sharpe_ratio, deflated_sharpe_ratio


TRADING_DAYS = 252
MONTHS_PER_YEAR = 12


def _clean(returns: Sequence[float]) -> np.ndarray:
    arr = np.asarray([float(v) for v in returns if v is not None and math.isfinite(float(v))], dtype=float)
    return arr


def _max_drawdown(returns: np.ndarray) -> tuple[float, int, int]:
    """
    Max drawdown as a negative decimal, plus the peak and trough indices
    within the return series.
    """
    nav = np.cumprod(1.0 + returns)
    running_peak = np.maximum.accumulate(nav)
    dd = nav / running_peak - 1.0
    trough = int(np.argmin(dd))
    max_dd = float(dd[trough])
    peak = int(np.argmax(nav[: trough + 1]))
    return max_dd, peak, trough


def _sortino(returns: np.ndarray, annualize_factor: float = TRADING_DAYS) -> float:
    """Sortino ratio: mean / downside-std, annualized."""
    if returns.size < 2:
        return 0.0
    mean = float(np.mean(returns))
    downside = returns[returns < 0]
    if downside.size == 0:
        return float("inf")
    downside_std = float(np.std(downside, ddof=1)) if downside.size > 1 else float(np.std(downside))
    if downside_std <= 0:
        return 0.0
    return mean / downside_std * math.sqrt(annualize_factor)


def _calmar(cagr: float, max_dd: float) -> float:
    """Calmar: annualized return / |max drawdown|. Infinite when dd is 0."""
    if max_dd == 0:
        return float("inf") if cagr > 0 else 0.0
    return cagr / abs(max_dd)


def _tail_ratio(returns: np.ndarray) -> float:
    """|p95| / |p5|. Values > 1 mean the right tail is fatter."""
    if returns.size < 20:
        return 1.0
    p95 = float(np.percentile(returns, 95))
    p5 = float(np.percentile(returns, 5))
    if p5 == 0:
        return float("inf") if p95 > 0 else 0.0
    return abs(p95) / abs(p5)


def _ulcer_index(returns: np.ndarray) -> float:
    """
    Ulcer Index (Martin 1987): sqrt(mean of squared drawdowns).
    Penalizes deep and prolonged drawdowns more than the max_dd stat.
    """
    if returns.size < 2:
        return 0.0
    nav = np.cumprod(1.0 + returns)
    running_peak = np.maximum.accumulate(nav)
    dd_pct = 100 * (nav / running_peak - 1.0)
    return float(np.sqrt(np.mean(dd_pct ** 2)))


def _profit_factor(returns: np.ndarray) -> float:
    """sum(gains) / |sum(losses)|. > 1 = profitable."""
    gains = returns[returns > 0].sum()
    losses = returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / abs(losses))


def _cagr(returns: np.ndarray, periods_per_year: float = TRADING_DAYS) -> float:
    if returns.size == 0:
        return 0.0
    total_return = float(np.prod(1.0 + returns) - 1.0)
    years = returns.size / periods_per_year
    if years <= 0:
        return 0.0
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _monthly_stats(returns: np.ndarray, periods_per_year: float = TRADING_DAYS) -> dict:
    """Group daily returns into 21-day months and report best/worst/hit rate."""
    period_per_month = int(periods_per_year / MONTHS_PER_YEAR)
    if returns.size < period_per_month * 2:
        return {"best_month": None, "worst_month": None, "monthly_hit_rate": None, "n_months": 0}
    n_months = returns.size // period_per_month
    trimmed = returns[: n_months * period_per_month]
    monthly = np.array([
        float(np.prod(1.0 + trimmed[i * period_per_month:(i + 1) * period_per_month]) - 1.0)
        for i in range(n_months)
    ])
    return {
        "best_month": round(float(monthly.max()), 6),
        "worst_month": round(float(monthly.min()), 6),
        "monthly_hit_rate": round(float(np.mean(monthly > 0)), 4),
        "n_months": int(n_months),
    }


def tearsheet(
    returns: Sequence[float],
    benchmark: Sequence[float] | None = None,
    annualize_factor: float = TRADING_DAYS,
    dsr_n_trials: int = 1,
) -> dict:
    """
    Full performance summary for a return series in one call.

    Args:
        returns: sequence of periodic returns (daily by default).
        benchmark: optional benchmark returns for beta/alpha/tracking error.
        annualize_factor: sqrt scaling factor for annualization. Default
            252 (daily). Use 12 for monthly, 52 for weekly.
        dsr_n_trials: candidate-strategy count for the deflated Sharpe
            multiple-testing correction. 1 = no correction, > 1 accounts
            for search bias when the returns come from the best of N
            backtests.

    Returns a dict with:
        n_obs, cagr, annualized_return, annualized_vol, sharpe,
        deflated_sharpe_pvalue, sortino, calmar, max_drawdown,
        max_drawdown_start_ix, max_drawdown_end_ix, hit_rate,
        profit_factor, tail_ratio, ulcer_index, skew, excess_kurtosis,
        best_month, worst_month, monthly_hit_rate, n_months,
        beta, alpha_annualized (when benchmark supplied).
    """
    arr = _clean(returns)
    n = arr.size
    if n < 30:
        raise ValueError(f"tearsheet: need at least 30 valid returns, got {n}")

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    cagr_val = _cagr(arr, annualize_factor)
    ann_return = mean * annualize_factor
    ann_vol = std * math.sqrt(annualize_factor)

    try:
        sr = sharpe_ratio(arr, annualize_factor=annualize_factor)
    except ValueError:
        sr = 0.0
    try:
        dsr = deflated_sharpe_ratio(arr, n_trials=dsr_n_trials, annualize_factor=annualize_factor)
        dsr_p = dsr["deflated_sharpe_pvalue"]
        dsr_sig = dsr["deflated_sharpe_significant"]
        skew = dsr["skew"]
        kurt = dsr["excess_kurtosis"]
    except ValueError:
        dsr_p = None
        dsr_sig = False
        skew = 0.0
        kurt = 0.0

    max_dd, dd_peak, dd_trough = _max_drawdown(arr)
    sortino = _sortino(arr, annualize_factor)
    calmar = _calmar(cagr_val, max_dd)
    hit = float(np.mean(arr > 0))
    profit_factor = _profit_factor(arr)
    tail = _tail_ratio(arr)
    ulcer = _ulcer_index(arr)
    monthly = _monthly_stats(arr, annualize_factor)

    out: dict = {
        "n_obs": n,
        "cagr": round(cagr_val, 6),
        "annualized_return": round(ann_return, 6),
        "annualized_vol": round(ann_vol, 6),
        "sharpe": round(sr, 4),
        "deflated_sharpe_pvalue": dsr_p,
        "deflated_sharpe_significant": dsr_sig,
        "sortino": round(sortino, 4) if math.isfinite(sortino) else None,
        "calmar": round(calmar, 4) if math.isfinite(calmar) else None,
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_peak_ix": dd_peak,
        "max_drawdown_trough_ix": dd_trough,
        "hit_rate": round(hit, 4),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else None,
        "tail_ratio": round(tail, 4) if math.isfinite(tail) else None,
        "ulcer_index": round(ulcer, 4),
        "skew": round(skew, 4),
        "excess_kurtosis": round(kurt, 4),
        "n_trials_correction": int(dsr_n_trials),
    }
    out.update(monthly)

    if benchmark is not None:
        bench = _clean(benchmark)
        m = min(n, bench.size)
        if m >= 30:
            a = arr[-m:]
            b = bench[-m:]
            cov = float(np.cov(a, b, ddof=1)[0, 1])
            var_b = float(np.var(b, ddof=1))
            beta = cov / var_b if var_b > 0 else 0.0
            alpha_daily = float(np.mean(a) - beta * np.mean(b))
            resid = a - beta * b
            te = float(np.std(resid, ddof=1))
            out["beta"] = round(beta, 4)
            out["alpha_annualized"] = round(alpha_daily * annualize_factor, 6)
            out["tracking_error_annualized"] = round(te * math.sqrt(annualize_factor), 6)
            out["n_benchmark_aligned"] = int(m)
    return out
