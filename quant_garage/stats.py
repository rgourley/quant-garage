"""
Statistical helpers shared across skills.

Built to fix items from the 2026-06-26 audit:

- C3: factor-research IC t-stat is inflated ~3x at long horizons because
  monthly-overlapping forward returns are treated as iid. Provide
  Newey-West SE with lag = horizon-months so the standard error reflects
  the overlap.
- C6: event-study significance threshold is hardcoded `|t| > 2.0` with no
  df correction. At n=7-8 the 5% critical t is 2.36 (two-sided). Provide
  df-aware critical_t() so callers compare against the right cutoff.
- M9 prerequisite: percentile/base-rate context on composite scores
  benefits from a winsorize helper to keep tails out of summary stats.

Dependencies: `scipy` and `numpy` are already in `requirements.txt`. The
helpers fail loud (raise) on degenerate inputs (zero variance, n<2) rather
than returning `nan` or `None` silently. Callers should check sample
sizes before invoking and surface "sample too small" in the rendered
output (the existing skills already do this for hand-coded t-stats).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import stats


def critical_t(n: int, alpha: float = 0.05, two_sided: bool = True) -> float:
    """Critical t-value for the given sample size and confidence level.

    Use this anywhere you previously hardcoded `2.0` as the
    "significant" threshold. At n=8 the two-sided 5% critical t is
    2.365; at n=20 it's 2.093; at n=60 it's 2.000.

    Parameters
    ----------
    n : int
        Sample size (degrees of freedom = n - 1).
    alpha : float
        Significance level. Default 0.05.
    two_sided : bool
        If True (default), use alpha/2 in each tail; if False, alpha in
        the upper tail only.

    Returns
    -------
    float
        The critical t-value. Raises ValueError if n < 2.
    """
    if n < 2:
        raise ValueError(f"critical_t requires n >= 2, got {n}")
    df = n - 1
    if two_sided:
        return float(stats.t.ppf(1 - alpha / 2, df))
    return float(stats.t.ppf(1 - alpha, df))


def is_significant(t_stat: float, n: int, alpha: float = 0.05) -> bool:
    """True if |t_stat| exceeds the df-aware two-sided critical t."""
    if not math.isfinite(t_stat) or n < 2:
        return False
    return abs(t_stat) > critical_t(n, alpha=alpha, two_sided=True)


def newey_west_se(series: Sequence[float], lag: int) -> float:
    """Newey-West heteroskedasticity + autocorrelation-consistent SE of the mean.

    Use this for the standard error of any time-series average where
    observations may be serially correlated (the classic case in
    factor-research: monthly rebalances with 3M/6M/12M forward returns
    create k-1 months of overlap).

    Set `lag` = the maximum expected autocorrelation horizon. For
    monthly returns over a 12-month forward window, `lag=12` is a safe
    default; `lag=horizon-1` is the academically common choice.

    Parameters
    ----------
    series : sequence of float
        Time-ordered observations.
    lag : int
        Bartlett kernel bandwidth. Must be >= 0 and < len(series).

    Returns
    -------
    float
        The Newey-West SE of the mean. Raises ValueError on bad input.
    """
    x = np.asarray(list(series), dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    if n < 2:
        raise ValueError(f"newey_west_se requires n >= 2, got {n}")
    if lag < 0 or lag >= n:
        raise ValueError(f"lag must be in [0, n-1] = [0, {n - 1}], got {lag}")

    # Demeaned series
    e = x - x.mean()

    # Bartlett-kernel weighted sum of autocovariances
    gamma0 = float(np.dot(e, e)) / n
    s2 = gamma0
    for k in range(1, lag + 1):
        cov_k = float(np.dot(e[k:], e[:-k])) / n
        weight = 1.0 - k / (lag + 1)
        s2 += 2 * weight * cov_k

    if s2 <= 0:
        # Negative long-run variance is a known pathology of Newey-West on
        # short series; report it loudly rather than returning NaN.
        raise ValueError(
            f"newey_west_se: long-run variance estimate is non-positive "
            f"({s2:.4g}). Sample may be too short for lag={lag}."
        )
    return math.sqrt(s2 / n)


def spearman_ic(scores: Sequence[float], forward_returns: Sequence[float]) -> tuple[float, float, int]:
    """Cross-sectional Spearman rank correlation (information coefficient) + SE.

    Standard quant-research IC at a single rebalance: rank the universe
    by factor score, rank by realized forward return, compute the
    Spearman correlation. Pair with `newey_west_se` over a time-series
    of monthly ICs for inference.

    Drops NaN/None pairs. Returns (ic, se, n) where:
    - ic: Spearman rho on the surviving sample.
    - se: large-sample SE = 1/sqrt(n-1).
    - n : number of valid pairs.

    Raises ValueError when fewer than 5 valid pairs remain (any IC with
    n<5 is noise).
    """
    pairs = [
        (float(s), float(r))
        for s, r in zip(scores, forward_returns)
        if s is not None and r is not None and math.isfinite(float(s)) and math.isfinite(float(r))
    ]
    n = len(pairs)
    if n < 5:
        raise ValueError(f"spearman_ic requires n >= 5 valid pairs, got {n}")
    s_arr = np.array([p[0] for p in pairs])
    r_arr = np.array([p[1] for p in pairs])
    rho, _p = stats.spearmanr(s_arr, r_arr)
    if not math.isfinite(rho):
        # Degenerate (e.g., all scores tied). Return 0 IC with reported n.
        return 0.0, 1.0 / math.sqrt(n - 1), n
    se = 1.0 / math.sqrt(n - 1)
    return float(rho), float(se), n


def gaussian_kde(
    sample: Sequence[float],
    n_grid: int = 100,
    bandwidth: float | None = None,
    x_min: float | None = None,
    x_max: float | None = None,
) -> tuple[list[float], list[float]]:
    """
    Gaussian kernel density estimate on a fixed grid.

    Bandwidth defaults to Silverman's rule of thumb:
        h = 1.06 * σ * n^(-1/5)
    which is fine for unimodal-ish distributions and only mildly
    over-smooths bimodals. Callers who want more resolution can pass
    an explicit `bandwidth` (in the same units as the sample).

    Grid defaults to `[min - 0.5·h, max + 0.5·h]` so kernels near the
    edges don't get clipped. Returns two same-length lists (grid, density)
    so the caller can render or serialize both.

    Failures raise (n<5, zero std): callers should check sample size and
    surface a "distribution too small" caveat instead of calling this.
    """
    arr = np.asarray([v for v in sample if v is not None and math.isfinite(float(v))], dtype=float)
    if arr.size < 5:
        raise ValueError(f"gaussian_kde: need at least 5 finite values, got {arr.size}")
    sigma = float(np.std(arr, ddof=1))
    if sigma <= 0:
        raise ValueError("gaussian_kde: sample has zero variance")

    if bandwidth is None:
        bandwidth = 1.06 * sigma * arr.size ** (-1.0 / 5.0)
    if bandwidth <= 0:
        raise ValueError(f"gaussian_kde: bandwidth must be > 0, got {bandwidth}")

    lo = float(arr.min()) - 0.5 * bandwidth if x_min is None else x_min
    hi = float(arr.max()) + 0.5 * bandwidth if x_max is None else x_max
    if hi <= lo:
        raise ValueError(f"gaussian_kde: x_max must exceed x_min, got {lo} and {hi}")

    grid = np.linspace(lo, hi, n_grid)
    # Broadcast the Gaussian kernel over every sample point.
    diffs = (grid[:, None] - arr[None, :]) / bandwidth
    dens = np.exp(-0.5 * diffs * diffs).sum(axis=1) / (arr.size * bandwidth * math.sqrt(2 * math.pi))
    return grid.tolist(), dens.tolist()


def find_peaks(density: Sequence[float], min_prominence: float = 0.05) -> list[int]:
    """
    Simple local-maximum finder on a KDE density array.

    A peak is an index where density[i] > density[i-1] AND density[i] >
    density[i+1], and its prominence (peak height minus the deeper of
    its two adjacent valleys) exceeds `min_prominence * max_density`.
    Prominence filter kills spurious wobbles from Silverman
    over-smoothing.

    Returns peak indices sorted by density descending (tallest first).
    """
    d = list(density)
    n = len(d)
    if n < 3:
        return []
    max_d = max(d) or 1.0
    peaks: list[int] = []
    for i in range(1, n - 1):
        if d[i] <= d[i - 1] or d[i] <= d[i + 1]:
            continue
        # Walk left to the deeper valley
        left_min = d[i]
        for j in range(i - 1, -1, -1):
            if d[j] > d[i]:
                break
            left_min = min(left_min, d[j])
        # Walk right
        right_min = d[i]
        for j in range(i + 1, n):
            if d[j] > d[i]:
                break
            right_min = min(right_min, d[j])
        valley = max(left_min, right_min)
        prominence = d[i] - valley
        if prominence >= min_prominence * max_d:
            peaks.append(i)
    peaks.sort(key=lambda idx: d[idx], reverse=True)
    return peaks


def analyze_distribution_shape(sample: Sequence[float]) -> dict:
    """
    Distribution shape summary for a 1-D sample.

    Computes the KDE, finds prominent modes, classifies as unimodal /
    bimodal / multimodal, reports skew and excess kurtosis, and labels
    the tail as fat / normal / thin using an excess-kurtosis threshold
    of ±1.5 (heuristic, honest given typical event-study n).

    Returns a JSON-serializable dict:

        {
          "n": int,
          "mean": float,
          "median": float,
          "std": float,
          "skew": float,
          "excess_kurtosis": float,
          "tail_label": "fat" | "normal" | "thin",
          "n_modes": int,
          "modes": [{"x": float, "density": float}, ...],
          "modality_label": "unimodal" | "bimodal" | "multimodal",
          "warn_mean_misleading": bool,
        }

    Raises on n<10: the shape read is unreliable below that and the
    caller should not present it as a signal.
    """
    arr = np.asarray([v for v in sample if v is not None and math.isfinite(float(v))], dtype=float)
    if arr.size < 10:
        raise ValueError(f"analyze_distribution_shape: need at least 10 finite values, got {arr.size}")

    grid, dens = gaussian_kde(arr.tolist())
    peaks = find_peaks(dens, min_prominence=0.10)
    modes = [{"x": round(float(grid[i]), 4), "density": round(float(dens[i]), 4)} for i in peaks]
    n_modes = len(modes)
    if n_modes <= 1:
        modality = "unimodal"
    elif n_modes == 2:
        modality = "bimodal"
    else:
        modality = "multimodal"

    mean = float(np.mean(arr))
    med = float(np.median(arr))
    sigma = float(np.std(arr, ddof=1))
    if sigma > 0:
        z = (arr - mean) / sigma
        skew = float(np.mean(z ** 3))
        excess_kurt = float(np.mean(z ** 4) - 3.0)
    else:
        skew = 0.0
        excess_kurt = 0.0

    if excess_kurt >= 1.5:
        tail = "fat"
    elif excess_kurt <= -1.5:
        tail = "thin"
    else:
        tail = "normal"

    return {
        "n": int(arr.size),
        "mean": round(mean, 6),
        "median": round(med, 6),
        "std": round(sigma, 6),
        "skew": round(skew, 3),
        "excess_kurtosis": round(excess_kurt, 3),
        "tail_label": tail,
        "n_modes": n_modes,
        "modes": modes,
        "modality_label": modality,
        "warn_mean_misleading": modality in ("bimodal", "multimodal") or tail == "fat" or abs(skew) >= 1.0,
    }


def sparkline(density: Sequence[float], width: int = 40, min_height: int = 0) -> str:
    """
    Compact ASCII sparkline of a density array. Uses 8 block characters
    for granularity. Downsamples to `width` columns via averaging.
    """
    d = list(density)
    if not d:
        return ""
    if len(d) > width:
        # Bucket into `width` groups and take the mean of each
        step = len(d) / width
        cols = []
        for i in range(width):
            lo = int(i * step)
            hi = int((i + 1) * step)
            hi = max(hi, lo + 1)
            cols.append(sum(d[lo:hi]) / (hi - lo))
        d = cols
    top = max(d) or 1.0
    chars = "▁▂▃▄▅▆▇█"
    out = []
    for v in d:
        norm = max(0.0, v / top)
        idx = min(len(chars) - 1, int(round(norm * (len(chars) - 1))))
        out.append(chars[idx])
    return "".join(out)


def winsorize(
    values: Sequence[float],
    lower_pct: float = 0.01,
    upper_pct: float = 0.99,
) -> list[float]:
    """Clip the tails of a series at given percentiles.

    The repo's `factor-definitions.md` calls for "winsorize at 1/99
    percentile" as the standard outlier-handling rule. This helper
    implements that. Default bounds match the documented convention.

    Drops NaN/None values before clipping. Returns a plain list (not
    numpy) so the result composes with the existing pure-Python code
    paths in the example scripts.
    """
    if not (0.0 <= lower_pct < upper_pct <= 1.0):
        raise ValueError(f"need 0 <= lower < upper <= 1, got {lower_pct} and {upper_pct}")
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return []
    arr = np.array(clean)
    lo = float(np.quantile(arr, lower_pct))
    hi = float(np.quantile(arr, upper_pct))
    return [max(lo, min(hi, v)) for v in clean]
