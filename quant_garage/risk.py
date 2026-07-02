"""
Risk metrics for a portfolio: VaR, Expected Shortfall, max drawdown,
beta, tracking error, position variance contributions, concentration.

Pairs with the position-sizer skill — same daily-aggs pull pattern,
same covariance machinery via quant_garage/sizing.py. Where
sizing.py answers "how much should I put on?", risk.py answers "what
could happen to what I've already got?"

All metrics are historical and descriptive. The script doesn't
predict future returns; it tells you what the empirical distribution
of the last N days looks like, applied to the current book.

Fits the same philosophy as the rest of quant-garage: descriptive,
not predictive.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import stats

from .sizing import covariance_matrix  # noqa: F401  (re-exported via __init__)


TRADING_DAYS_PER_YEAR = 252
_MIN_TAIL_SAMPLE = 30


def _clean_returns(returns: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    return arr[np.isfinite(arr)]


def historical_var(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Empirical VaR at given confidence (e.g., 0.95 -> 5th percentile).

    Returns a POSITIVE number representing the loss magnitude.
    `historical_var(returns, 0.95)` returns 0.022 to mean "5% of days
    you lose more than 2.2%". Sign convention: positive = loss.

    Raises ValueError on n<30 (not enough sample for tail estimation)
    or invalid confidence.
    """
    if not (0.0 < confidence < 1.0):
        raise ValueError(
            f"historical_var: confidence must be in (0, 1), got {confidence}"
        )
    arr = _clean_returns(returns)
    if arr.size < _MIN_TAIL_SAMPLE:
        raise ValueError(
            f"historical_var: need at least {_MIN_TAIL_SAMPLE} finite returns, "
            f"got {arr.size}"
        )
    # 95% confidence -> 5th percentile of the return distribution.
    pct = (1.0 - confidence) * 100.0
    threshold = float(np.percentile(arr, pct))
    # Sign convention: positive = loss. Flip sign of negative threshold.
    return float(-threshold)


def parametric_var(mean: float, std: float, confidence: float = 0.95) -> float:
    """Gaussian (parametric) VaR at given confidence.

    VaR = -(mean - z * std) where z = Phi^-1(confidence). Sign
    convention: positive = loss. Almost always understates true tail
    risk because returns aren't normal; surface this in caveats.
    """
    if not (0.0 < confidence < 1.0):
        raise ValueError(
            f"parametric_var: confidence must be in (0, 1), got {confidence}"
        )
    if std < 0:
        raise ValueError(f"parametric_var: std must be >= 0, got {std}")
    # z for the upper tail of the standard normal at `confidence`.
    # The (1-confidence) quantile is -z; a loss equal to VaR sits there.
    # Loss magnitude = -(mean - z * std) = z * std - mean.
    z = float(stats.norm.ppf(confidence))
    return float(z * std - mean)


def expected_shortfall(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Mean loss beyond VaR (CVaR / ES). Positive = loss.

    ES = -mean(returns where returns <= var_threshold). More honest
    tail measure than VaR because VaR ignores the shape of the tail.
    Raises on n<30.

    Edge case: if no returns sit at or below the VaR threshold (rare
    rounding case on small samples), falls back to the single worst
    return as the ES estimate.
    """
    if not (0.0 < confidence < 1.0):
        raise ValueError(
            f"expected_shortfall: confidence must be in (0, 1), got {confidence}"
        )
    arr = _clean_returns(returns)
    if arr.size < _MIN_TAIL_SAMPLE:
        raise ValueError(
            f"expected_shortfall: need at least {_MIN_TAIL_SAMPLE} finite returns, "
            f"got {arr.size}"
        )
    pct = (1.0 - confidence) * 100.0
    threshold = float(np.percentile(arr, pct))
    tail = arr[arr <= threshold]
    if tail.size == 0:
        # Rounding edge: ensure something is in the tail by taking the worst day.
        tail = np.array([float(np.min(arr))])
    return float(-np.mean(tail))


def max_drawdown(price_series: Sequence[float]) -> dict:
    """Peak-to-trough decline.

    Returns:
      {
        "peak_index": int, "trough_index": int,
        "peak_value": float, "trough_value": float,
        "drawdown_pct": float,           # negative number, e.g. -0.18
        "duration_periods": int,         # trough_index - peak_index
        "recovered": bool,
        "recovery_index": int | None
      }

    Operates on the supplied series in order. If `price_series` is
    portfolio NAV, durations are in trading days. Raises on n<2.

    Edge case: monotone-up series produces drawdown_pct = 0.0 with
    peak_index == trough_index at the start (no decline occurred).
    """
    arr = np.asarray(list(price_series), dtype=float)
    if arr.size < 2:
        raise ValueError(
            f"max_drawdown: need at least 2 observations, got {arr.size}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError("max_drawdown: series contains non-finite values")

    running_peak = np.maximum.accumulate(arr)
    drawdowns = (arr - running_peak) / running_peak
    trough_idx = int(np.argmin(drawdowns))
    # The peak that produced this trough is the last running-peak update at/before trough_idx.
    peak_value = float(running_peak[trough_idx])
    # Find the index of that peak (first occurrence at the running peak).
    peak_candidates = np.where(arr[: trough_idx + 1] == peak_value)[0]
    peak_idx = int(peak_candidates[0]) if peak_candidates.size > 0 else 0
    trough_value = float(arr[trough_idx])
    drawdown_pct = float(drawdowns[trough_idx])

    recovered = False
    recovery_index: int | None = None
    if trough_idx < arr.size - 1:
        after = arr[trough_idx + 1:]
        rec_local = np.where(after >= peak_value)[0]
        if rec_local.size > 0:
            recovered = True
            recovery_index = int(trough_idx + 1 + rec_local[0])

    duration = trough_idx - peak_idx

    return {
        "peak_index": peak_idx,
        "trough_index": trough_idx,
        "peak_value": peak_value,
        "trough_value": trough_value,
        "drawdown_pct": drawdown_pct,
        "duration_periods": int(duration),
        "recovered": bool(recovered),
        "recovery_index": recovery_index,
    }


def portfolio_returns(
    weights: dict[str, float],
    returns_panel: dict[str, Sequence[float]],
) -> np.ndarray:
    """Compute portfolio daily returns from per-name daily returns.

    All return series MUST be the same length (caller aligned).
    Returns the weighted sum per day. Tickers in weights but not in
    returns_panel are silently dropped — the caller is expected to
    surface the gap; this helper just does the math on what it gets.
    """
    common = [t for t in weights.keys() if t in returns_panel]
    if not common:
        raise ValueError(
            "portfolio_returns: no overlap between weights and returns_panel"
        )
    lengths = {len(returns_panel[t]) for t in common}
    if len(lengths) != 1:
        raise ValueError(
            f"portfolio_returns: series have differing lengths {lengths}; "
            f"caller must align first"
        )
    n_obs = lengths.pop()
    out = np.zeros(n_obs, dtype=float)
    for t in common:
        w = float(weights[t])
        r = np.asarray(list(returns_panel[t]), dtype=float)
        out = out + w * r
    return out


def beta_and_tracking(
    portfolio_returns: Sequence[float],
    benchmark_returns: Sequence[float],
) -> dict:
    """Compute beta + tracking error vs a benchmark.

    Beta = cov(port, bench) / var(bench)
    Alpha (annualized) = port_mean_annualized - beta * bench_mean_annualized
    Tracking error (annualized) = std(port - bench) * sqrt(252)

    Returns:
      {"beta": float, "alpha_annualized": float,
       "tracking_error_annualized": float,
       "correlation": float, "r_squared": float}

    Raises on n<30 or zero benchmark variance.
    """
    p = np.asarray(list(portfolio_returns), dtype=float)
    b = np.asarray(list(benchmark_returns), dtype=float)
    if p.size != b.size:
        raise ValueError(
            f"beta_and_tracking: length mismatch port={p.size} bench={b.size}"
        )
    mask = np.isfinite(p) & np.isfinite(b)
    p = p[mask]
    b = b[mask]
    if p.size < _MIN_TAIL_SAMPLE:
        raise ValueError(
            f"beta_and_tracking: need at least {_MIN_TAIL_SAMPLE} aligned "
            f"finite observations, got {p.size}"
        )
    var_b = float(np.var(b, ddof=1))
    if var_b <= 0:
        raise ValueError("beta_and_tracking: benchmark variance is zero")

    cov_pb = float(np.cov(p, b, ddof=1)[0, 1])
    beta = cov_pb / var_b
    port_mean_ann = float(np.mean(p)) * TRADING_DAYS_PER_YEAR
    bench_mean_ann = float(np.mean(b)) * TRADING_DAYS_PER_YEAR
    alpha_ann = port_mean_ann - beta * bench_mean_ann
    tracking_error_ann = float(np.std(p - b, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    std_p = float(np.std(p, ddof=1))
    std_b = float(np.std(b, ddof=1))
    if std_p == 0 or std_b == 0:
        correlation = 0.0
    else:
        correlation = cov_pb / (std_p * std_b)
    r_squared = correlation * correlation

    return {
        "beta": float(beta),
        "alpha_annualized": float(alpha_ann),
        "tracking_error_annualized": float(tracking_error_ann),
        "correlation": float(correlation),
        "r_squared": float(r_squared),
    }


def position_variance_contributions(
    weights: dict[str, float],
    cov: np.ndarray,
    tickers: list[str],
) -> dict:
    """Per-name share of total portfolio variance.

    MRC_i = w_i * (Sigma w)_i  (marginal contribution to variance)
    Returns each name's MRC / sum_MRC. Equivalent to the marginal
    risk contributions used by risk-parity (in sizing.py) but
    surfaced for an existing book, not solved for.

    Returns: {ticker: contribution_pct, ...} that sums to 1.0.

    Edge case: zero portfolio variance (all weights zero, or zero
    cov) returns equal contributions 1/N to avoid div-by-zero.
    """
    n = len(tickers)
    if cov.shape != (n, n):
        raise ValueError(
            f"position_variance_contributions: cov shape {cov.shape} != ({n}, {n})"
        )
    w = np.array([float(weights.get(t, 0.0)) for t in tickers], dtype=float)
    mc = cov @ w
    contrib = w * mc
    total = float(contrib.sum())
    if total <= 0 or not math.isfinite(total):
        if n == 0:
            return {}
        equal = 1.0 / n
        return {t: equal for t in tickers}
    shares = contrib / total
    return {tickers[i]: float(shares[i]) for i in range(n)}


def concentration_stats(weights: dict[str, float]) -> dict:
    """Top-K concentration + Herfindahl-Hirschman Index.

    Returns:
      {
        "n_positions": int,
        "top_1_weight": float,
        "top_3_weight": float,
        "top_5_weight": float,
        "herfindahl": float,
        "effective_n": float
      }

    Uses absolute weights so long/short books report sensibly.
    """
    if not weights:
        return {
            "n_positions": 0,
            "top_1_weight": 0.0,
            "top_3_weight": 0.0,
            "top_5_weight": 0.0,
            "herfindahl": 0.0,
            "effective_n": 0.0,
        }
    abs_weights = sorted((abs(float(w)) for w in weights.values()), reverse=True)
    n = len(abs_weights)
    top_1 = abs_weights[0]
    top_3 = float(sum(abs_weights[:3]))
    top_5 = float(sum(abs_weights[:5]))
    hhi = float(sum(w * w for w in abs_weights))
    effective_n = (1.0 / hhi) if hhi > 0 else 0.0
    return {
        "n_positions": int(n),
        "top_1_weight": float(top_1),
        "top_3_weight": float(top_3),
        "top_5_weight": float(top_5),
        "herfindahl": float(hhi),
        "effective_n": float(effective_n),
    }


def worst_n_days(
    portfolio_returns: np.ndarray,
    dates: list[str],
    n: int = 5,
    per_name_returns: dict[str, np.ndarray] | None = None,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """Find the N worst historical days for this book and attribute
    the loss to individual names.

    Returns a list (worst first) of:
      {
        "date": "YYYY-MM-DD",
        "book_return_pct": float,                # negative
        "name_contributions": [                  # optional
          {"ticker": "NVDA", "contribution_pct": -0.024},
          ...
        ]
      }

    Stable sort on (return_value, original_index) so ties on the
    worst-day return resolve by chronological order (older first).
    """
    arr = np.asarray(portfolio_returns, dtype=float)
    if arr.size == 0 or n <= 0:
        return []
    if len(dates) != arr.size:
        raise ValueError(
            f"worst_n_days: dates length {len(dates)} != returns length {arr.size}"
        )
    n = min(int(n), arr.size)
    # Index by ascending return value; ties broken by index for stability.
    order = sorted(range(arr.size), key=lambda i: (float(arr[i]), i))
    picks = order[:n]

    has_attribution = (
        per_name_returns is not None and weights is not None and len(per_name_returns) > 0
    )

    out: list[dict] = []
    for idx in picks:
        rec: dict = {
            "date": dates[idx],
            "book_return_pct": float(arr[idx]),
        }
        if has_attribution:
            contribs: list[dict] = []
            for t, r in per_name_returns.items():
                w = float(weights.get(t, 0.0))
                if w == 0.0:
                    continue
                if idx >= len(r):
                    continue
                contribs.append({
                    "ticker": t,
                    "contribution_pct": float(w * float(r[idx])),
                })
            contribs.sort(key=lambda d: d["contribution_pct"])
            rec["name_contributions"] = contribs
        out.append(rec)
    return out
