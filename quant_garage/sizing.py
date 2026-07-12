"""
Position-sizing helpers shared across skills.

Built initially for the position-sizer skill (vol-target, fractional
Kelly, risk parity, equal weight). The covariance + return utilities
are generic and will be reused by risk-report's portfolio-vol
calculations.

Each sizing function takes pre-computed statistics (not raw returns)
so the same vol/correlation pull serves multiple methods in one
script run. Cleaner than passing raw returns into four separate
functions that each recompute σ.

Fits the 2026-06-26 audit's H4/H5 pattern: one source of truth per
computation, no per-script reimplementation.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


TRADING_DAYS_PER_YEAR = 252


def annualized_vol(daily_returns: Sequence[float], trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualize daily-return std. Drops non-finite, raises on n<5."""
    arr = np.asarray(list(daily_returns), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        raise ValueError(
            f"annualized_vol: need at least 5 valid daily returns, got {arr.size}"
        )
    sigma_daily = float(np.std(arr, ddof=1))
    return sigma_daily * math.sqrt(trading_days_per_year)


def ewma_vol(
    daily_returns: Sequence[float],
    lambda_: float = 0.94,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Annualized exponentially-weighted-moving-average vol (RiskMetrics).

    σ²_t = λ · σ²_{t-1} + (1 - λ) · r²_{t-1}. Seeded with the full-sample
    realized variance so the estimate does not depend on the first
    observation. Returns σ_final · sqrt(trading_days_per_year).

    λ = 0.94 is the RiskMetrics daily convention (effective half-life
    about 11 days). Recent moves dominate. λ ∈ (0, 1); at λ = 1 this
    reduces to the seed variance, at λ → 0 to the last squared return.

    Compared to `annualized_vol`, EWMA responds faster to regime shifts
    at the cost of noisier estimates on quiet series. Prefer for sizing
    decisions where you want to cut exposure into rising vol; prefer
    realized when you want a stable multi-month estimate.
    """
    if not (0.0 < lambda_ < 1.0):
        raise ValueError(f"ewma_vol: lambda_ must be in (0, 1), got {lambda_}")
    arr = np.asarray(list(daily_returns), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        raise ValueError(
            f"ewma_vol: need at least 5 valid daily returns, got {arr.size}"
        )
    var_seed = float(np.var(arr, ddof=1))
    var_t = var_seed
    for r in arr:
        var_t = lambda_ * var_t + (1.0 - lambda_) * (r * r)
    sigma_daily = math.sqrt(var_t)
    return sigma_daily * math.sqrt(trading_days_per_year)


def correlation_matrix(returns_panel: dict[str, Sequence[float]]) -> tuple[list[str], np.ndarray]:
    """Compute pairwise Pearson correlation across aligned-date returns.

    Aligns to the intersection of date indices (callers should pass
    already-aligned series). Returns (ordered_tickers, NxN correlation
    matrix). Raises ValueError if any pair has fewer than 5 overlapping
    observations.
    """
    tickers = sorted(returns_panel.keys())
    if len(tickers) < 1:
        raise ValueError("correlation_matrix: empty returns_panel")

    arrays: dict[str, np.ndarray] = {}
    lengths: set[int] = set()
    for t in tickers:
        arr = np.asarray(list(returns_panel[t]), dtype=float)
        arrays[t] = arr
        lengths.add(arr.size)
    if len(lengths) != 1:
        raise ValueError(
            f"correlation_matrix: series have differing lengths {lengths}; "
            f"caller must align to the intersection of date indices first"
        )
    n_obs = lengths.pop()
    if n_obs < 5:
        raise ValueError(
            f"correlation_matrix: need at least 5 observations per series, got {n_obs}"
        )

    n = len(tickers)
    corr = np.eye(n, dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            xi = arrays[tickers[i]]
            xj = arrays[tickers[j]]
            mask = np.isfinite(xi) & np.isfinite(xj)
            if mask.sum() < 5:
                raise ValueError(
                    f"correlation_matrix: {tickers[i]}/{tickers[j]} have "
                    f"only {int(mask.sum())} overlapping finite observations"
                )
            xi_m = xi[mask]
            xj_m = xj[mask]
            # numpy corrcoef returns 2x2; pull off-diagonal
            si = np.std(xi_m, ddof=1)
            sj = np.std(xj_m, ddof=1)
            if si == 0 or sj == 0:
                rho = 0.0
            else:
                rho = float(np.corrcoef(xi_m, xj_m)[0, 1])
            if not math.isfinite(rho):
                rho = 0.0
            corr[i, j] = rho
            corr[j, i] = rho
    return tickers, corr


def covariance_matrix(vols: dict[str, float], correlations: np.ndarray, tickers: list[str]) -> np.ndarray:
    """Build a covariance matrix from per-name vols and a correlation matrix.

    Σ[i,j] = ρ[i,j] * σ_i * σ_j

    Tickers must be in the same order as the correlation matrix's rows.
    """
    n = len(tickers)
    if correlations.shape != (n, n):
        raise ValueError(
            f"covariance_matrix: correlations shape {correlations.shape} != ({n}, {n})"
        )
    sigma_vec = np.array([float(vols[t]) for t in tickers], dtype=float)
    outer = np.outer(sigma_vec, sigma_vec)
    return correlations * outer


def shrink_correlation(corr: np.ndarray, shrinkage: float = 0.05) -> np.ndarray:
    """Shrink a correlation matrix toward identity for numerical safety.

    shrunk = (1 - shrinkage) * corr + shrinkage * I

    Default 5% is enough to make most empirical matrices positive
    definite without distorting the structure. Raises ValueError if
    shrinkage is outside [0, 1].
    """
    if not (0.0 <= shrinkage <= 1.0):
        raise ValueError(f"shrink_correlation: shrinkage must be in [0, 1], got {shrinkage}")
    n = corr.shape[0]
    return (1.0 - shrinkage) * corr + shrinkage * np.eye(n)


# ---------- internal helpers ----------

def _portfolio_vol(weights: np.ndarray, cov: np.ndarray) -> float:
    var = float(weights @ cov @ weights)
    return math.sqrt(max(var, 0.0))


def _apply_max_weight_cap(
    weights: np.ndarray, max_weight: float | None
) -> tuple[np.ndarray, bool]:
    """Iteratively cap any name above max_weight and redistribute the excess
    proportionally to the uncapped names. Preserves Σw = 1 (long-only).

    Returns (new_weights, binding_was_hit).
    """
    if max_weight is None:
        return weights, False
    w = weights.astype(float).copy()
    if max_weight <= 0 or max_weight >= 1.0:
        return w, False
    binding = False
    for _ in range(100):  # safety bound; converges very fast
        over = w > max_weight + 1e-12
        if not over.any():
            break
        binding = True
        excess = float((w[over] - max_weight).sum())
        w[over] = max_weight
        free_mask = ~over
        if not free_mask.any() or w[free_mask].sum() <= 0:
            # Every name capped or no headroom; redistribute by free count
            if free_mask.any():
                w[free_mask] += excess / free_mask.sum()
            break
        # Redistribute proportional to current free weight
        free_sum = float(w[free_mask].sum())
        w[free_mask] += excess * (w[free_mask] / free_sum)
    return w, binding


def _apply_leverage_cap(
    weights: np.ndarray, leverage_cap: float
) -> tuple[np.ndarray, bool]:
    """Scale weights so Σ|w| <= leverage_cap. Returns (new_weights, was_binding)."""
    gross = float(np.abs(weights).sum())
    if gross <= leverage_cap + 1e-12 or gross == 0:
        return weights, False
    return weights * (leverage_cap / gross), True


def _binding_label(
    *,
    target_vol_binding: bool = False,
    leverage_binding: bool = False,
    max_weight_binding: bool = False,
) -> str | None:
    """Pick the binding constraint label. Precedence: leverage_cap, max_weight,
    target_vol. Leverage and per-name caps trump the target-vol scaler because
    they cap the result; target-vol is just a scale factor."""
    if leverage_binding:
        return "leverage_cap"
    if max_weight_binding:
        return "max_weight"
    if target_vol_binding:
        return "target_vol"
    return None


# ---------- sizing methods ----------

def vol_target_weights(
    vols: dict[str, float],
    cov: np.ndarray,
    tickers: list[str],
    target_vol: float,
    leverage_cap: float = 1.0,
    max_weight: float | None = None,
) -> dict:
    """Vol-targeted (inverse-vol) weights.

    Raw weights w_i = 1 / σ_i. Normalize so Σ w_i = 1. Scale so
    portfolio vol = target_vol. Cap gross exposure at leverage_cap.
    Cap per-name at max_weight (iteratively redistribute the excess
    if any cap binds).
    """
    if target_vol <= 0:
        raise ValueError(f"vol_target_weights: target_vol must be > 0, got {target_vol}")
    n = len(tickers)
    sigma = np.array([float(vols[t]) for t in tickers], dtype=float)
    if (sigma <= 0).any():
        raise ValueError("vol_target_weights: all vols must be > 0")

    raw = 1.0 / sigma
    w = raw / raw.sum()  # normalized inverse-vol weights, Σw = 1

    # Apply per-name cap on the normalized weights first
    w, _ = _apply_max_weight_cap(w, max_weight)

    # Scale to hit target vol
    port_vol_unscaled = _portfolio_vol(w, cov)
    target_binding = False
    if port_vol_unscaled > 0:
        scale = target_vol / port_vol_unscaled
        # If scale > leverage_cap (target wants more leverage than allowed),
        # leverage_cap will bind below
        w = w * scale
        target_binding = True

    # Apply leverage cap (binds when the scaler took us above gross cap)
    w, leverage_binding = _apply_leverage_cap(w, leverage_cap)

    # Re-check whether the max-weight cap binds on the final scaled weights;
    # if the target_vol scaling brought every name below the cap, max_weight
    # should not be reported as the binding constraint.
    if max_weight is not None and float(w.max()) >= max_weight - 1e-9:
        final_max_binding = True
    else:
        final_max_binding = False

    port_vol = _portfolio_vol(w, cov)
    gross = float(np.abs(w).sum())

    return {
        "weights": {t: float(w[i]) for i, t in enumerate(tickers)},
        "portfolio_vol_annualized": float(port_vol),
        "gross_exposure": float(gross),
        "binding_constraint": _binding_label(
            target_vol_binding=target_binding and not leverage_binding and not final_max_binding,
            leverage_binding=leverage_binding,
            max_weight_binding=final_max_binding,
        ),
    }


def fractional_kelly_weights(
    edges: dict[str, float],
    cov: np.ndarray,
    tickers: list[str],
    scale: float = 0.25,
    leverage_cap: float = 1.0,
    max_weight: float | None = None,
) -> dict | None:
    """Fractional Kelly weights via matrix form.

    Full Kelly: f = Σ⁻¹ μ, where μ is the vector of expected
    annualized returns and Σ is the annualized covariance matrix.
    Scaled by `scale` (default 0.25 — common "quarter Kelly" choice).

    Edges dict must include EVERY ticker in `tickers`; missing edge =
    return None for that ticker (caller emits insufficient_edges
    caveat).
    """
    missing = [t for t in tickers if t not in edges]
    if missing:
        return None

    n = len(tickers)
    mu = np.array([float(edges[t]) for t in tickers], dtype=float)
    try:
        sigma_inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"fractional_kelly_weights: cov matrix is singular: {exc}") from exc

    f_full = sigma_inv @ mu          # full Kelly weights
    f = scale * f_full               # fractional Kelly

    # Normalize ONLY for the long-only v1 case where every f_i > 0.
    # If any are negative (Kelly suggests a short), v1 floors at 0 and
    # surfaces in binding constraint.
    has_short_signal = bool((f < 0).any())
    if has_short_signal:
        f = np.clip(f, 0.0, None)

    if f.sum() <= 0:
        # No positive signal at all; fall back to a 0-weight book and
        # surface via binding_constraint = "no_positive_edge"
        w = np.zeros_like(f)
        return {
            "weights": {t: 0.0 for t in tickers},
            "portfolio_vol_annualized": 0.0,
            "gross_exposure": 0.0,
            "binding_constraint": "no_positive_edge",
            "scale": float(scale),
            "edges_used": {t: float(edges[t]) for t in tickers},
            "negative_signals_floored": has_short_signal,
        }

    # Normalize so the fractional book sums to 1 (preserves the relative
    # Kelly tilt), then apply caps.
    w = f / f.sum()

    w, _ = _apply_max_weight_cap(w, max_weight)
    w, leverage_binding = _apply_leverage_cap(w, leverage_cap)

    # If after caps Σ|w| < leverage_cap and the original raw fractional
    # Kelly book was smaller than leverage_cap, scale gross_exposure to
    # reflect the actual fractional gross (scale * Σf_full).
    raw_gross = float(scale * np.abs(f_full).sum())
    final_max_binding = (
        max_weight is not None and float(w.max()) >= max_weight - 1e-9
    )
    if not leverage_binding and not final_max_binding:
        # Apply scale-driven gross. If raw_gross < leverage_cap, the
        # fractional Kelly book is "small" and we don't need the
        # leverage_cap to bind.
        target_gross = min(raw_gross, leverage_cap)
        if w.sum() > 0:
            w = w * (target_gross / float(w.sum()))
        # Recheck cap on the rescaled weights
        final_max_binding = (
            max_weight is not None and float(w.max()) >= max_weight - 1e-9
        )

    port_vol = _portfolio_vol(w, cov)
    gross = float(np.abs(w).sum())

    return {
        "weights": {t: float(w[i]) for i, t in enumerate(tickers)},
        "portfolio_vol_annualized": float(port_vol),
        "gross_exposure": float(gross),
        "binding_constraint": _binding_label(
            leverage_binding=leverage_binding,
            max_weight_binding=final_max_binding,
        ),
        "scale": float(scale),
        "edges_used": {t: float(edges[t]) for t in tickers},
        "negative_signals_floored": has_short_signal,
    }


def risk_parity_weights(
    cov: np.ndarray,
    tickers: list[str],
    max_iters: int = 200,
    tol: float = 1e-6,
    leverage_cap: float = 1.0,
    max_weight: float | None = None,
) -> dict:
    """Equal Risk Contribution (ERC) weights via iterative algorithm.

    Marginal risk contribution: MRC_i = w_i * (Σ w)_i / sqrt(w' Σ w)
    Target: equalize MRC across names. Standard iterative update
    converges for long-only covariance matrices.

    Falls back to inverse-vol if iteration doesn't converge within
    max_iters; surfaces "converged: false" in the output.
    """
    n = len(tickers)
    if cov.shape != (n, n):
        raise ValueError(f"risk_parity_weights: cov shape {cov.shape} != ({n}, {n})")

    target_contrib = 1.0 / n  # equal share of total portfolio variance

    # Start from inverse-vol normalized weights (good warm start for ERC)
    sigma = np.sqrt(np.diag(cov))
    if (sigma <= 0).any():
        raise ValueError("risk_parity_weights: zero diagonal in cov; one or more vols are 0")
    w = (1.0 / sigma) / np.sum(1.0 / sigma)

    converged = False
    iters_used = 0
    for it in range(1, max_iters + 1):
        iters_used = it
        port_var = float(w @ cov @ w)
        if port_var <= 0:
            break
        # Marginal contributions to portfolio variance: MC_i = (Σw)_i
        mc = cov @ w
        # Share of total variance contributed by each name: w_i * MC_i / port_var
        rc = w * mc / port_var

        # Convergence check: are all rc[i] equal to 1/n?
        if float(np.max(np.abs(rc - target_contrib))) < tol:
            converged = True
            break

        # ERC iterative update: scale weights by sqrt(target_contrib / rc).
        # Standard fixed-point step for ERC; converges for any positive-
        # definite cov (Maillard, Roncalli, Teiletche 2010).
        update = np.sqrt(target_contrib / np.maximum(rc, 1e-12))
        w = w * update
        w = w / w.sum()  # renormalize to Σw = 1 long-only

    if not converged:
        # Fall back to inverse-vol
        w = (1.0 / sigma) / np.sum(1.0 / sigma)

    # Apply caps
    w, _ = _apply_max_weight_cap(w, max_weight)
    w, leverage_binding = _apply_leverage_cap(w, leverage_cap)

    final_max_binding = (
        max_weight is not None and float(w.max()) >= max_weight - 1e-9
    )

    port_vol = _portfolio_vol(w, cov)
    port_var_final = port_vol * port_vol
    if port_var_final > 0:
        mrc_share = {
            tickers[i]: float(w[i] * (cov @ w)[i] / port_var_final)
            for i in range(n)
        }
    else:
        mrc_share = {tickers[i]: 0.0 for i in range(n)}
    gross = float(np.abs(w).sum())

    return {
        "weights": {t: float(w[i]) for i, t in enumerate(tickers)},
        "portfolio_vol_annualized": float(port_vol),
        "gross_exposure": float(gross),
        "binding_constraint": _binding_label(
            leverage_binding=leverage_binding,
            max_weight_binding=final_max_binding,
        ),
        "iterations": int(iters_used),
        "converged": bool(converged),
        "marginal_risk_contributions": mrc_share,
    }


def equal_weights(
    tickers: list[str],
    cov: np.ndarray,
    leverage_cap: float = 1.0,
) -> dict:
    """Equal 1/N weights. Trivial baseline; emit for comparison."""
    n = len(tickers)
    if n == 0:
        raise ValueError("equal_weights: empty tickers")
    w = np.full(n, 1.0 / n, dtype=float)

    w, leverage_binding = _apply_leverage_cap(w, leverage_cap)

    port_vol = _portfolio_vol(w, cov)
    gross = float(np.abs(w).sum())

    return {
        "weights": {t: float(w[i]) for i, t in enumerate(tickers)},
        "portfolio_vol_annualized": float(port_vol),
        "gross_exposure": float(gross),
        "binding_constraint": _binding_label(leverage_binding=leverage_binding),
    }
