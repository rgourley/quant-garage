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


def simulate_correlated_paths(
    mean_daily: Sequence[float],
    cov_annualized: np.ndarray,
    n_paths: int,
    n_days: int,
    tail: Literal["normal", "student_t"] = "normal",
    df: float = 4.0,
    seed: int | None = None,
) -> np.ndarray:
    """
    Multivariate correlated daily return paths.

    Args:
        mean_daily: per-asset mean daily return (length K).
        cov_annualized: annualized covariance matrix (K x K). Divided by
            252 internally to get daily cov.
        n_paths: number of paths to simulate.
        n_days: horizon length in trading days.
        tail: "normal" for multivariate Gaussian, "student_t" for a
            multivariate Student-t with `df` degrees of freedom
            (fatter tails than normal).
        df: student-t degrees of freedom. Default 4 gives noticeably
            fatter tails than normal; ignored for tail="normal".
        seed: rng seed.

    Returns:
        (n_paths, n_days, K) array of daily log returns.

    Under student-t, we scale a standard multivariate normal by
    sqrt(df / chi2(df)) so the marginal std matches the target cov.
    See Ruppert & Matteson, *Statistics and Data Analysis for Financial
    Engineering*, 2nd ed., Ch. 7.
    """
    mu = np.asarray(mean_daily, dtype=float).reshape(-1)
    cov_ann = np.asarray(cov_annualized, dtype=float)
    k = mu.shape[0]
    if cov_ann.shape != (k, k):
        raise ValueError(
            f"simulate_correlated_paths: cov shape {cov_ann.shape} vs mu length {k}"
        )
    if n_paths <= 0 or n_days <= 0:
        raise ValueError("n_paths and n_days must be positive")
    if tail not in ("normal", "student_t"):
        raise ValueError(f"tail must be 'normal' or 'student_t', got {tail!r}")

    cov_daily = cov_ann / 252.0
    # Cholesky requires PD. Add tiny ridge to be safe against numerical
    # PD issues on empirically-fit matrices.
    try:
        L = np.linalg.cholesky(cov_daily)
    except np.linalg.LinAlgError:
        L = np.linalg.cholesky(cov_daily + np.eye(k) * 1e-10)

    rng = np.random.default_rng(seed)
    z = rng.standard_normal(size=(n_paths, n_days, k))

    if tail == "student_t":
        if df <= 2:
            raise ValueError(f"student_t df must be > 2, got {df}")
        chi = rng.chisquare(df, size=(n_paths, n_days))
        scaling = np.sqrt(df / chi)
        z = z * scaling[:, :, None]

    # Correlate: z @ L.T (using standard batch matmul)
    correlated = np.einsum("pdi,ji->pdj", z, L)
    return mu[None, None, :] + correlated


def rough_vol_series(
    log_returns: Sequence[float],
    hurst: float | None = None,
    trading_days_per_year: int = 252,
) -> tuple[float, float]:
    """
    Rough volatility scaling per Bayer-Friz-Gatheral (2016).

    Estimates the roughness of the realized volatility process. If
    hurst is provided, uses it directly; otherwise estimates via
    rescaled range on |log returns|.

    Rough volatility research (Bayer-Friz-Gatheral 2016, Livieri et al.
    2018) documented that realized vol has Hurst H ~ 0.1 (very rough),
    not H = 0.5 (Brownian). The rough vol scaling makes short-horizon
    vol forecasts more responsive than sqrt-time scaling while dampening
    the long horizon.

    Returns (H, sigma_annualized) where sigma is scaled to be
    comparable to annualized_vol on the same series.
    """
    arr = np.asarray([float(v) for v in log_returns if v is not None and math.isfinite(float(v))], dtype=float)
    n = arr.size
    if n < 60:
        raise ValueError(f"rough_vol_series: need at least 60 returns, got {n}")

    if hurst is None:
        # R/S on |log(|r_t|)| following the rBergomi-style H estimator.
        abs_rets = np.abs(arr)
        abs_rets = abs_rets[abs_rets > 1e-8]
        if abs_rets.size < 60:
            hurst = 0.5
        else:
            log_abs = np.log(abs_rets)
            block_sizes = np.unique(np.round(np.logspace(1, np.log10(max(30, len(log_abs) // 4)), num=8)).astype(int))
            block_sizes = block_sizes[block_sizes >= 5]
            log_n: list[float] = []
            log_rs: list[float] = []
            for b in block_sizes:
                nb = len(log_abs) // b
                if nb < 2:
                    continue
                blocks = log_abs[: nb * b].reshape(nb, b)
                # R/S per block
                rs_vals = []
                for block in blocks:
                    m = float(np.mean(block))
                    centered = block - m
                    cum = np.cumsum(centered)
                    R = float(np.max(cum) - np.min(cum))
                    S = float(np.std(block, ddof=1))
                    if S > 0:
                        rs_vals.append(R / S)
                if rs_vals:
                    log_n.append(math.log(b))
                    log_rs.append(math.log(float(np.mean(rs_vals))))
            if len(log_n) >= 4:
                x = np.array(log_n)
                y = np.array(log_rs)
                mx = float(np.mean(x))
                my = float(np.mean(y))
                num = float(np.sum((x - mx) * (y - my)))
                den = float(np.sum((x - mx) ** 2))
                hurst = num / den if den > 0 else 0.5
                # Clamp to plausible range
                hurst = float(np.clip(hurst, 0.05, 0.95))
            else:
                hurst = 0.5

    sigma_daily = float(np.std(arr, ddof=1))
    sigma_ann = sigma_daily * math.sqrt(trading_days_per_year)
    return float(hurst), sigma_ann


def rough_vol_annualized(
    log_returns: Sequence[float],
    hurst: float | None = None,
    trading_days_per_year: int = 252,
    forecast_horizon_days: int = 20,
) -> float:
    """
    Rough-volatility annualized forecast.

    Under rough vol, vol scales like h^H rather than h^(1/2). This
    matters most when forecasting horizons far from 1 day: for short
    horizons the estimate is more responsive; for long horizons it
    damps the sqrt(t) blow-up. Returns the annualized-scale forecast
    for the given horizon.

    Args:
        log_returns: recent daily log returns.
        hurst: optional pre-computed Hurst exponent. If None, estimated
            from the returns.
        trading_days_per_year: annualization factor. Default 252.
        forecast_horizon_days: forecast horizon in trading days.
            Default 20 (~1 month).
    """
    H, sigma_ann_traditional = rough_vol_series(log_returns, hurst=hurst, trading_days_per_year=trading_days_per_year)
    # Under Brownian (H=0.5), sigma_horizon = sigma_daily * sqrt(h). Under
    # rough vol (H<0.5), sigma_horizon = sigma_daily * h^H. Rescale so the
    # 20-day forecast is comparable to the traditional annualized number.
    # Ratio (h^H) / (h^0.5) for h = horizon_days.
    if forecast_horizon_days <= 0:
        return sigma_ann_traditional
    rescale = (forecast_horizon_days ** H) / (forecast_horizon_days ** 0.5)
    return float(sigma_ann_traditional * rescale)


def simulate_rough_vol_paths(
    mean_daily: Sequence[float],
    cov_annualized: np.ndarray,
    n_paths: int,
    n_days: int,
    hurst: float = 0.1,
    seed: int | None = None,
) -> np.ndarray:
    """
    Correlated return paths with rough-volatility (fractional Brownian)
    increments.

    Under rBergomi (Bayer-Friz-Gatheral 2016), the vol process is
    driven by fractional Brownian motion with Hurst H ~ 0.1. This
    simulator uses the Cholesky method on the FBM covariance matrix
    K[i, j] = 0.5 * (i^(2H) + j^(2H) - |i-j|^(2H)) to generate
    fractional increments, then scales into the specified covariance
    structure for the multi-asset correlation.

    Args:
        mean_daily: per-asset mean daily log return.
        cov_annualized: annualized covariance matrix (K x K).
        n_paths: number of paths.
        n_days: horizon.
        hurst: Hurst exponent for the vol path. Default 0.1 (Bayer-Friz-
            Gatheral standard). H=0.5 recovers standard Brownian.
        seed: rng seed.

    Returns:
        (n_paths, n_days, K) array of daily returns.

    Note: this is a simplified rBergomi variant. Full rBergomi uses a
    forward variance curve and Volterra kernel; this uses the Cholesky-
    factored fBM covariance to generate rough vol increments and then
    correlates via the observed cov. Adequate for path-VaR use; not a
    calibration-quality options pricing engine.
    """
    mu = np.asarray(mean_daily, dtype=float).reshape(-1)
    cov_ann = np.asarray(cov_annualized, dtype=float)
    k = mu.shape[0]
    if cov_ann.shape != (k, k):
        raise ValueError(f"simulate_rough_vol_paths: cov shape {cov_ann.shape} vs mu length {k}")
    if not (0.01 <= hurst <= 0.99):
        raise ValueError(f"hurst must be in [0.01, 0.99], got {hurst}")

    # FBM covariance matrix over n_days
    idx = np.arange(1, n_days + 1)
    two_h = 2.0 * hurst
    fbm_cov = 0.5 * (idx[:, None] ** two_h + idx[None, :] ** two_h - np.abs(idx[:, None] - idx[None, :]) ** two_h)
    # Cholesky of FBM
    try:
        L_fbm = np.linalg.cholesky(fbm_cov + 1e-10 * np.eye(n_days))
    except np.linalg.LinAlgError:
        # Fall back to eigen decomposition
        w, v = np.linalg.eigh(fbm_cov)
        w = np.maximum(w, 0)
        L_fbm = v @ np.diag(np.sqrt(w))

    cov_daily = cov_ann / 252.0
    try:
        L_asset = np.linalg.cholesky(cov_daily + 1e-10 * np.eye(k))
    except np.linalg.LinAlgError:
        w, v = np.linalg.eigh(cov_daily)
        w = np.maximum(w, 0)
        L_asset = v @ np.diag(np.sqrt(w))

    rng = np.random.default_rng(seed)
    # White noise: (n_paths, n_days, k)
    z = rng.standard_normal(size=(n_paths, n_days, k))
    # Rough time structure via FBM: transform along the day axis.
    # z_rough[p, :, i] = L_fbm @ z[p, :, i]
    # But we want fBM increments, not fBM values. Increments of fBM with
    # H!=0.5 have long-range dependence. To get a per-day increment
    # process with rough covariance, we generate fBM values and take
    # first differences.
    z_time = np.einsum("dj,pjk->pdk", L_fbm, z)
    # First differences to get increments
    if n_days >= 2:
        increments = np.concatenate([z_time[:, :1, :], np.diff(z_time, axis=1)], axis=1)
    else:
        increments = z_time
    # Standardize increments to unit std (per-column)
    inc_std = np.std(increments, axis=(0, 1), keepdims=True)
    inc_std = np.where(inc_std > 0, inc_std, 1.0)
    increments = increments / inc_std
    # Correlate: increments @ L_asset.T
    correlated = np.einsum("pdi,ji->pdj", increments, L_asset)
    return mu[None, None, :] + correlated


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
