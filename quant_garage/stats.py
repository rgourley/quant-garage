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
