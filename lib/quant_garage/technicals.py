"""
Standard technical-indicator helpers shared across skills.

Built for the technical-briefing and market-regime skills. All helpers
take a sequence of float closes (or OHLC for ATR) and return numpy
arrays of indicator values aligned to the input length. Earlier
positions are NaN where insufficient lookback is available — callers
should slice/drop NaNs before reading the latest value.

Math is standard textbook (Wilder's RSI, MACD = EMA(12)-EMA(26) with
9-day EMA signal, Bollinger = SMA(20) ± 2σ, ATR = Wilder smoothing of
true range over 14 periods). No exotic variants, no smoothing tweaks.

Used together with `lib.quant_garage.stats` (df-aware significance) and
`lib.quant_garage.percentile` (rank vs distribution) so technical
readings carry honest sample-size + base-rate context.
"""
from __future__ import annotations

import numpy as np
from typing import Sequence


def sma(values: Sequence[float], window: int) -> np.ndarray:
    """Simple moving average. Returns NaN for positions before window-1."""
    if window < 1:
        raise ValueError(f"sma window must be >= 1, got {window}")
    arr = np.asarray(values, dtype=float)
    if arr.size < window:
        return np.full(arr.size, np.nan)
    out = np.full(arr.size, np.nan)
    cumsum = np.cumsum(arr, dtype=float)
    out[window - 1] = cumsum[window - 1] / window
    out[window:] = (cumsum[window:] - cumsum[:-window]) / window
    return out


def ema(values: Sequence[float], window: int) -> np.ndarray:
    """Exponential moving average with smoothing 2/(window+1).

    Seeds with the simple average over the first `window` values, then
    iterates the standard EMA recurrence. NaN before window-1.
    """
    if window < 1:
        raise ValueError(f"ema window must be >= 1, got {window}")
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n < window:
        return np.full(n, np.nan)
    out = np.full(n, np.nan)
    alpha = 2.0 / (window + 1.0)
    out[window - 1] = float(arr[:window].mean())
    for i in range(window, n):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def rsi(closes: Sequence[float], window: int = 14) -> np.ndarray:
    """Wilder's RSI. NaN for positions before `window` returns are seen.

    Returns values in [0, 100]. Constant or all-up sequences return 100;
    all-down returns 0. Standard interpretation: <30 oversold, >70
    overbought, 50 = neutral momentum.
    """
    if window < 2:
        raise ValueError(f"rsi window must be >= 2, got {window}")
    arr = np.asarray(closes, dtype=float)
    n = arr.size
    if n < window + 1:
        return np.full(n, np.nan)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    out = np.full(n, np.nan)
    avg_gain = float(gains[:window].mean())
    avg_loss = float(losses[:window].mean())
    if avg_loss == 0:
        out[window] = 100.0 if avg_gain > 0 else 50.0
    else:
        rs = avg_gain / avg_loss
        out[window] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(window + 1, n):
        avg_gain = (avg_gain * (window - 1) + gains[i - 1]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i - 1]) / window
        if avg_loss == 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD = EMA(fast) - EMA(slow), signal = EMA(MACD, signal).

    Returns (macd_line, signal_line, histogram). Histogram = MACD - signal.
    Standard parameterization (12, 26, 9); pass override for variants.
    """
    if not (fast >= 1 and slow > fast and signal >= 1):
        raise ValueError(f"need fast >= 1, slow > fast, signal >= 1; got {fast}/{slow}/{signal}")
    arr = np.asarray(closes, dtype=float)
    ema_fast = ema(arr, fast)
    ema_slow = ema(arr, slow)
    macd_line = ema_fast - ema_slow
    # signal EMA computed over the MACD series, ignoring its leading NaN slice
    macd_valid = macd_line[~np.isnan(macd_line)]
    if macd_valid.size < signal:
        signal_line = np.full(arr.size, np.nan)
    else:
        sig_full = ema(macd_valid, signal)
        signal_line = np.full(arr.size, np.nan)
        first_valid = int(np.argmax(~np.isnan(macd_line)))
        signal_line[first_valid:first_valid + sig_full.size] = sig_full
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger(
    closes: Sequence[float],
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands. Returns (upper, mid, lower).

    Mid = SMA(window). Upper/lower = mid ± num_std * rolling_std. NaN
    for positions before window-1. Standard parameterization (20, 2.0).
    """
    if window < 2:
        raise ValueError(f"bollinger window must be >= 2, got {window}")
    arr = np.asarray(closes, dtype=float)
    n = arr.size
    if n < window:
        nan = np.full(n, np.nan)
        return nan, nan, nan
    mid = sma(arr, window)
    std = np.full(n, np.nan)
    for i in range(window - 1, n):
        std[i] = float(arr[i - window + 1: i + 1].std(ddof=0))
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> np.ndarray:
    """Wilder's Average True Range over `window` periods.

    True range = max(H-L, |H-prev_close|, |L-prev_close|). ATR is the
    Wilder-smoothed (RMA) mean of TR. NaN for positions before `window`
    bars are seen.
    """
    if window < 2:
        raise ValueError(f"atr window must be >= 2, got {window}")
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    n = h.size
    if not (l.size == n and c.size == n):
        raise ValueError(f"highs/lows/closes must be same length; got {h.size}/{l.size}/{c.size}")
    if n < window + 1:
        return np.full(n, np.nan)
    tr = np.full(n, np.nan)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(
            h[i] - l[i],
            abs(h[i] - c[i - 1]),
            abs(l[i] - c[i - 1]),
        )
    out = np.full(n, np.nan)
    out[window] = float(tr[1:window + 1].mean())
    for i in range(window + 1, n):
        out[i] = (out[i - 1] * (window - 1) + tr[i]) / window
    return out
