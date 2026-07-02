"""
Percentile-rank and base-rate context helpers.

Composite scores (options-flow, crypto-vol-scanner, news-scanner) and
single-name reaction metrics (event-study, earnings-drilldown) emit
raw numbers that are hard to anchor without comparison context. These
helpers compute percentile rank against a prior-window distribution
and format the result for both JSON and rendered output.

Fixes M9 and M10 from the 2026-06-26 audit.
"""
from __future__ import annotations

import math
from typing import Sequence


# Minimum sample size before percentile / base-rate stats are emitted.
# Anything below this is too noisy to anchor a trader's read; we surface
# `None` and a reason instead of a misleading number.
MIN_DISTRIBUTION_N = 5


def _finite_values(distribution: Sequence[float]) -> list[float]:
    """Drop None and non-finite (NaN, +/-inf) entries."""
    out: list[float] = []
    for x in distribution:
        if x is None:
            continue
        try:
            xf = float(x)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(xf):
            continue
        out.append(xf)
    return out


def percentile_rank(value: float, distribution: Sequence[float]) -> float | None:
    """Return percentile rank in [0, 100] of `value` within `distribution`.

    Uses the 'mean' rule: counts strictly-less + half of equal. Standard
    in finance for ranking non-unique scores. Returns None if the
    distribution has fewer than MIN_DISTRIBUTION_N valid (non-None,
    finite) entries — any percentile with n<5 is too noisy to be useful.

    A value outside the distribution range still returns a clamped rank
    (0 for "less than the min", 100 for "above the max") rather than
    None, since that information is still useful to the caller.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    clean = _finite_values(distribution)
    n = len(clean)
    if n < MIN_DISTRIBUTION_N:
        return None
    less = sum(1 for x in clean if x < v)
    equal = sum(1 for x in clean if x == v)
    rank = (less + 0.5 * equal) / n * 100.0
    return rank


def format_rank_label(rank: float | None) -> str:
    """Map a percentile rank to a human-readable tier label.

    >>> format_rank_label(95)   -> "top 5%"
    >>> format_rank_label(80)   -> "top quartile"
    >>> format_rank_label(50)   -> "median"
    >>> format_rank_label(15)   -> "bottom quartile"
    >>> format_rank_label(None) -> "insufficient data"
    """
    if rank is None:
        return "insufficient data"
    try:
        r = float(rank)
    except (TypeError, ValueError):
        return "insufficient data"
    if not math.isfinite(r):
        return "insufficient data"
    if r >= 95:
        return "top 5%"
    if r >= 90:
        return "top 10%"
    if r >= 75:
        return "top quartile"
    if r >= 55:
        return "above median"
    if r >= 45:
        return "median"
    if r >= 25:
        return "below median"
    if r >= 10:
        return "bottom quartile"
    return "bottom 10%"


def base_rate(values: Sequence[float]) -> dict:
    """Return {n, median, mean, p25, p75} for a metric distribution.

    Drops None / non-finite. Returns {n: 0, median: None, ...} on empty
    input rather than raising. Designed for single-name skills to emit
    a 'how does this compare' block next to the individual number.
    """
    clean = sorted(_finite_values(values))
    n = len(clean)
    if n == 0:
        return {"n": 0, "median": None, "mean": None, "p25": None, "p75": None}

    def _q(p: float) -> float:
        # Linear-interpolation percentile (numpy-style "linear" / matches
        # event-study.percentile). p in [0, 1].
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return clean[int(k)]
        return clean[f] + (clean[c] - clean[f]) * (k - f)

    if n % 2:
        med = clean[n // 2]
    else:
        med = (clean[n // 2 - 1] + clean[n // 2]) / 2.0

    return {
        "n": n,
        "median": med,
        "mean": sum(clean) / n,
        "p25": _q(0.25),
        "p75": _q(0.75),
    }
