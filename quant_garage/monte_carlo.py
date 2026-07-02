"""
Monte Carlo sampling helpers shared across skills.

Built initially for valuation-sanity-check's fair-value distribution
(replaces a single-point estimate with a sampled fan of outcomes).
The sampling and sensitivity helpers are generic and will be reused
by risk-report and position-sizer.

Drivers are sampled INDEPENDENTLY by default. This understates the
tails when real-world drivers are correlated (peer growth and peer
margin show rho ~ 0.3-0.5 historically). Callers should surface this
caveat to consumers.

Fixes the deeper symptom under C7 from the 2026-06-26 audit.
"""
from __future__ import annotations

import math
from typing import Literal, Sequence

import numpy as np
from scipy.stats import spearmanr

DistributionKind = Literal["peer", "normal"]

# Small-peer floor. Mirrors MIN_PEERS_FOR_OLS used elsewhere.
_MIN_VALID = 5


def _clean(values: Sequence[float]) -> list[float]:
    """Drop None and non-finite values."""
    cleaned: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(f):
            continue
        cleaned.append(f)
    return cleaned


def sample_empirical(values: Sequence[float], n: int, seed: int | None = None) -> np.ndarray:
    """Sample n values with replacement from the empirical distribution.

    Drops None and non-finite from `values` before sampling. Raises
    ValueError if fewer than 5 valid values remain (small-peer floor
    consistent with MIN_PEERS_FOR_OLS used elsewhere).
    """
    cleaned = _clean(values)
    if len(cleaned) < _MIN_VALID:
        raise ValueError(
            f"sample_empirical: need at least {_MIN_VALID} valid values, "
            f"got {len(cleaned)}"
        )
    if n <= 0:
        raise ValueError(f"sample_empirical: n must be positive, got {n}")
    rng = np.random.default_rng(seed)
    arr = np.asarray(cleaned, dtype=float)
    return rng.choice(arr, size=n, replace=True)


def sample_normal(values: Sequence[float], n: int, seed: int | None = None) -> np.ndarray:
    """Sample n values from N(mean, std) fit to `values`.

    Useful when the peer set is small enough that empirical sampling
    produces a chunky histogram. Same n>=5 floor as sample_empirical.
    Raises ValueError if std == 0 (degenerate fit).
    """
    cleaned = _clean(values)
    if len(cleaned) < _MIN_VALID:
        raise ValueError(
            f"sample_normal: need at least {_MIN_VALID} valid values, "
            f"got {len(cleaned)}"
        )
    if n <= 0:
        raise ValueError(f"sample_normal: n must be positive, got {n}")
    arr = np.asarray(cleaned, dtype=float)
    mu = float(np.mean(arr))
    # Sample std (ddof=1) so a constant input raises rather than producing
    # a degenerate point mass.
    sigma = float(np.std(arr, ddof=1))
    if sigma <= 0 or not math.isfinite(sigma):
        raise ValueError(
            f"sample_normal: degenerate fit (std={sigma}); use sample_empirical "
            f"or supply non-constant values"
        )
    rng = np.random.default_rng(seed)
    return rng.normal(loc=mu, scale=sigma, size=n)


def spearman_sensitivity(driver_samples: dict[str, np.ndarray], output_samples: np.ndarray) -> list[dict]:
    """Return a list of {driver, rho, abs_rho} sorted by abs(rho) desc.

    Spearman handles non-monotone relationships, doesn't assume
    normality, and is robust to the heavy tails fair-value
    distributions often have. Uses scipy.stats.spearmanr.

    Each driver's array must match output_samples length. Raises
    ValueError on length mismatch.
    """
    out_arr = np.asarray(output_samples, dtype=float)
    n_out = out_arr.shape[0]
    results: list[dict] = []
    for name, samples in driver_samples.items():
        arr = np.asarray(samples, dtype=float)
        if arr.shape[0] != n_out:
            raise ValueError(
                f"spearman_sensitivity: driver '{name}' length {arr.shape[0]} "
                f"!= output length {n_out}"
            )
        rho_result = spearmanr(arr, out_arr)
        rho = float(rho_result.correlation) if hasattr(rho_result, "correlation") else float(rho_result[0])
        if not math.isfinite(rho):
            rho = 0.0
        results.append({"driver": name, "rho": rho, "abs_rho": abs(rho)})
    results.sort(key=lambda r: r["abs_rho"], reverse=True)
    return results


def percentile_summary(values: np.ndarray) -> dict:
    """Return {n, mean, std, p5, p10, p25, p50, p75, p90, p95}.

    Standard fan-of-outcomes summary. p5/p95 give tail context that
    PMs reading this output will want; p10/p90 are the headline
    "plausible range"; p25/p50/p75 are the IQR for the body.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        raise ValueError("percentile_summary: values is empty")
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise ValueError("percentile_summary: no finite values")
    pcts = np.percentile(finite, [5, 10, 25, 50, 75, 90, 95])
    return {
        "n": int(finite.size),
        "mean": float(np.mean(finite)),
        "std": float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0,
        "p5": float(pcts[0]),
        "p10": float(pcts[1]),
        "p25": float(pcts[2]),
        "p50": float(pcts[3]),
        "p75": float(pcts[4]),
        "p90": float(pcts[5]),
        "p95": float(pcts[6]),
    }
