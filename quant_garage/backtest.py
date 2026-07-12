"""
Rolling backtest helpers.

Pattern borrowed from scikit-learn / Skfolio (2024): expose a
`rolling_backtest(signal_fn, prices, ...)` primitive that any signal
skill can call. The helper handles window bookkeeping and returns a
per-window score plus an aggregate summary.

    from quant_garage.backtest import rolling_backtest, forward_returns

    def my_signal(train_prices):
        # any function of past prices returning a scalar signal
        return train_prices[-1] / train_prices[-20] - 1.0  # 20-day momentum

    result = rolling_backtest(
        my_signal, prices, train_window=252, test_window=20, step=20
    )
    print(result["per_window_ic"])
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np


def forward_returns(prices: Sequence[float], horizon: int) -> np.ndarray:
    """
    Log returns over the next `horizon` bars for each price row.

    Returns are aligned so `forward_returns[t]` is the log return from
    price[t] to price[t+horizon]. Last `horizon` entries are NaN because
    the future is unknown.
    """
    p = np.asarray(prices, dtype=float)
    n = p.size
    if horizon <= 0:
        raise ValueError(f"horizon must be positive, got {horizon}")
    if n < horizon + 1:
        raise ValueError(f"need at least {horizon + 1} prices, got {n}")
    fut = np.full(n, np.nan)
    fut[: n - horizon] = np.log(p[horizon:] / p[: n - horizon])
    return fut


def rolling_backtest(
    signal_fn: Callable[[np.ndarray], float],
    prices: Sequence[float],
    train_window: int = 252,
    test_window: int = 20,
    step: int = 20,
    forward_horizon: int | None = None,
) -> dict:
    """
    Rolling / walk-forward backtest of a scalar signal against forward
    returns.

    Args:
        signal_fn: takes a numpy array of past prices, returns a scalar
            signal.
        prices: full price series (any length >= train_window +
            test_window).
        train_window: number of past bars visible to signal_fn.
        test_window: forward-return horizon for scoring (bars).
        step: step size between windows in bars.
        forward_horizon: if set, use as the return horizon and let
            test_window drive only the window overlap. Default: same
            as test_window.

    Returns dict:
        n_windows, per_window: list of {end_ix, signal, forward_return},
        ic_pearson: correlation of signals vs forward returns,
        ic_spearman: rank correlation of signals vs forward returns,
        n_positive, n_negative,
        mean_signal, mean_forward_return.

    Runs one signal_fn call per window. Signal function is deterministic
    from the training prices; no fitting or hyperparameter tuning done
    here (add via cross-validation in the caller).
    """
    p = np.asarray(prices, dtype=float)
    n = p.size
    horizon = forward_horizon if forward_horizon is not None else test_window
    if train_window < 5 or test_window < 1 or horizon < 1:
        raise ValueError("train_window >= 5, test_window >= 1, horizon >= 1 required")
    if n < train_window + horizon:
        raise ValueError(
            f"need at least {train_window + horizon} prices, got {n}"
        )
    if step < 1:
        raise ValueError("step >= 1 required")

    per_window: list[dict] = []
    for end_ix in range(train_window, n - horizon, step):
        train_slice = p[end_ix - train_window: end_ix]
        try:
            signal = float(signal_fn(train_slice))
        except Exception:
            continue
        if not math.isfinite(signal):
            continue
        p0 = float(p[end_ix])
        p1 = float(p[end_ix + horizon])
        if p0 <= 0 or p1 <= 0:
            continue
        forward = math.log(p1 / p0)
        per_window.append({
            "end_ix": int(end_ix),
            "signal": round(signal, 6),
            "forward_return": round(forward, 6),
        })

    n_windows = len(per_window)
    if n_windows < 5:
        return {
            "n_windows": n_windows,
            "per_window": per_window,
            "ic_pearson": None,
            "ic_spearman": None,
            "mean_signal": None,
            "mean_forward_return": None,
        }

    signals = np.array([w["signal"] for w in per_window])
    forwards = np.array([w["forward_return"] for w in per_window])
    ic_pearson = _pearson(signals, forwards)
    ic_spearman = _spearman(signals, forwards)

    return {
        "n_windows": n_windows,
        "per_window": per_window,
        "ic_pearson": round(ic_pearson, 4) if ic_pearson is not None else None,
        "ic_spearman": round(ic_spearman, 4) if ic_spearman is not None else None,
        "mean_signal": round(float(np.mean(signals)), 6),
        "mean_forward_return": round(float(np.mean(forwards)), 6),
        "std_signal": round(float(np.std(signals, ddof=1)), 6),
        "std_forward_return": round(float(np.std(forwards, ddof=1)), 6),
    }


def rolling_ic_series(
    signal_series: Sequence[float],
    forward_returns_series: Sequence[float],
    window: int = 63,
) -> list[dict]:
    """
    Rolling window IC of a signal series vs a forward-return series.

    Both series are indexed by time. IC at time t is the correlation of
    signal[t-window:t] vs forward_returns[t-window:t]. Returned as a
    list of {index, ic_pearson, ic_spearman, n_obs} for the caller to
    interpolate / plot / fit decay against.
    """
    s = np.asarray(signal_series, dtype=float)
    r = np.asarray(forward_returns_series, dtype=float)
    n = min(s.size, r.size)
    if window < 20 or n < window + 1:
        raise ValueError(f"window >= 20 and series length >= {window + 1} required")
    out: list[dict] = []
    for t in range(window, n):
        s_win = s[t - window: t]
        r_win = r[t - window: t]
        mask = np.isfinite(s_win) & np.isfinite(r_win)
        if mask.sum() < 20:
            continue
        ic_p = _pearson(s_win[mask], r_win[mask])
        ic_s = _spearman(s_win[mask], r_win[mask])
        out.append({
            "index": int(t),
            "ic_pearson": round(ic_p, 4) if ic_p is not None else None,
            "ic_spearman": round(ic_s, 4) if ic_s is not None else None,
            "n_obs": int(mask.sum()),
        })
    return out


def _pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < 2 or y.size < 2:
        return None
    mx = float(np.mean(x))
    my = float(np.mean(y))
    num = float(np.sum((x - mx) * (y - my)))
    denx = float(np.sqrt(np.sum((x - mx) ** 2)))
    deny = float(np.sqrt(np.sum((y - my) ** 2)))
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    rx = _rankdata(x)
    ry = _rankdata(y)
    return _pearson(rx, ry)


def _rankdata(x: np.ndarray) -> np.ndarray:
    """Average-rank tiebreaking, like scipy.stats.rankdata."""
    order = np.argsort(x)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, x.size + 1)
    # Average tied ranks
    sorted_x = x[order]
    i = 0
    while i < x.size:
        j = i
        while j + 1 < x.size and sorted_x[j + 1] == sorted_x[i]:
            j += 1
        if j > i:
            avg_rank = (i + 1 + j + 1) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
        i = j + 1
    return ranks
