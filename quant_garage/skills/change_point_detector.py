"""
change-point-detector as an importable library function.

Runs Bayesian Online Change-Point Detection (Adams and MacKay, 2007)
on a ticker's daily log returns and reports the dates where a regime
break was detected, plus the current run length posterior.

Answers "when did this name's return regime shift?" Useful for:
- Sharpening market-regime reads (rule buckets miss regime edges)
- Flagging when a pair's cointegration likely broke (before the OS
  stability check fires)
- Catching when a mean-reverting name became trend-following (or
  vice versa) partway through the lookback

    from quant_garage.skills.change_point_detector import run, render
    payload = run("SPY", lookback_days=504)

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.

Reference: Adams and MacKay (2007), *Bayesian Online Changepoint
Detection*, arXiv:0710.3742. Implementation uses a Student-t
predictive with a Normal-Gamma prior on (mu, tau) so hyperparameters
update in closed form as observations arrive.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import numpy as np

from .. import MassiveClient, FetchError, today, utcnow_iso


# Hazard: geometric with rate 1/lambda. The mean run length between
# change points is lambda observations. 250 = about 1 year of trading
# days, a reasonable prior for daily equity returns.
DEFAULT_LAMBDA_RUN = 250.0

# Threshold for declaring a change point: the run-length posterior
# collapses onto r=0 (a fresh regime) with probability above this
# level. 0.5 catches the "clear regime shift" cases without triggering
# on every wobble.
CHANGE_POINT_PROB_THRESHOLD = 0.5


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _fetch_daily_aggs(client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources) -> list[dict]:
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&limit=50000"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        return []
    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None or close <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily aggs for {ticker}",
    )
    return rows


def _student_t_logpdf(x: float, df: float, loc: float, scale: float) -> float:
    """Log pdf of a Student-t. Vectorized version is used in the loop below."""
    z = (x - loc) / scale
    logc = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi) - math.log(scale)
    return logc - 0.5 * (df + 1) * math.log(1 + z * z / df)


def _bocpd(
    returns: np.ndarray,
    lambda_run: float = DEFAULT_LAMBDA_RUN,
    alpha0: float = 1.0,
    beta0: float = 1.0,
    kappa0: float = 1.0,
    mu0: float = 0.0,
) -> tuple[np.ndarray, list[float]]:
    """
    Bayesian Online Change-Point Detection with Normal-Gamma prior and
    Student-t predictive.

    Returns are pre-standardized (subtract global mean, divide by global
    std) so unit-scale hyperparameters are appropriate. Without
    standardization, the r=0 predictive under a diffuse prior loses
    every comparison against a well-fit long run, and no change points
    are ever detected. This is a standard BOCPD implementation trick.

    Returns:
        run_length_posterior: (T+1, T+1) matrix P(r_t | x_{1:t}) where
            row t is the posterior at time t over run lengths 0..t.
        map_runs: length-T list of the MAP (argmax) run length at each
            step. Change points are detected when this trajectory
            resets from a long-running peak; see
            _extract_change_points below.
    """
    mu_global = float(np.mean(returns))
    sigma_global = float(np.std(returns, ddof=1))
    if sigma_global > 0:
        returns = (returns - mu_global) / sigma_global

    T = len(returns)
    # Hazard is constant under geometric prior
    hazard = 1.0 / lambda_run

    # Sufficient statistics that grow per run length
    mu = np.zeros(T + 1)
    kappa = np.zeros(T + 1)
    alpha = np.zeros(T + 1)
    beta = np.zeros(T + 1)
    mu[0] = mu0
    kappa[0] = kappa0
    alpha[0] = alpha0
    beta[0] = beta0

    # Posterior over run lengths
    R = np.zeros((T + 1, T + 1))
    R[0, 0] = 1.0

    map_runs: list[float] = []
    for t in range(1, T + 1):
        x = returns[t - 1]

        # Predictive log-likelihood P(x_t | x_{(t-r):t-1}) for each r
        max_r = t  # possible run lengths from 0 to t-1
        # Student-t predictive: df = 2*alpha, loc = mu, scale = sqrt(beta * (kappa + 1) / (alpha * kappa))
        pred = np.zeros(max_r)
        for r in range(max_r):
            df = 2.0 * alpha[r]
            if df <= 0:
                pred[r] = -1e10
                continue
            scale = math.sqrt(beta[r] * (kappa[r] + 1) / (alpha[r] * kappa[r]))
            if scale <= 0:
                pred[r] = -1e10
                continue
            pred[r] = _student_t_logpdf(x, df=df, loc=mu[r], scale=scale)

        # Growth probabilities: r increments by 1 with prob (1 - hazard)
        growth = R[t - 1, :max_r] * np.exp(pred) * (1 - hazard)

        # Change-point probability: sum over previous r * hazard
        cp = float(np.sum(R[t - 1, :max_r] * np.exp(pred)) * hazard)

        # Update posterior
        R[t, 1:max_r + 1] = growth
        R[t, 0] = cp
        s = R[t, :].sum()
        if s > 0:
            R[t, :] /= s

        # Detection signal: MAP run length. When the mode of R[t, :] drops
        # sharply relative to t, evidence has shifted from "long run" to
        # "recent fresh start." Using MAP run length as the signal is the
        # standard BOCPD detection approach because P(r=0) collapses to
        # the hazard rate under any concentrated posterior.
        map_run = int(np.argmax(R[t, :t + 1]))
        map_runs.append(float(map_run))

        # Update sufficient statistics for each run length r+1 (from x_t)
        # New parameters for r+1 (having seen x_t):
        # kappa_new = kappa + 1
        # mu_new = (kappa * mu + x) / kappa_new
        # alpha_new = alpha + 0.5
        # beta_new = beta + 0.5 * (kappa * (x - mu)^2) / kappa_new
        # Shift stats forward: new r+1 slot gets updated (r,x) stats;
        # r=0 slot resets to prior for the next iteration.
        new_kappa = kappa[:max_r] + 1
        new_mu = (kappa[:max_r] * mu[:max_r] + x) / new_kappa
        new_alpha = alpha[:max_r] + 0.5
        diff = x - mu[:max_r]
        new_beta = beta[:max_r] + 0.5 * kappa[:max_r] * diff * diff / new_kappa

        # Shift right: index r+1 gets stats for a run of length r+1
        kappa[1:max_r + 1] = new_kappa
        mu[1:max_r + 1] = new_mu
        alpha[1:max_r + 1] = new_alpha
        beta[1:max_r + 1] = new_beta

        # Reset r=0 slot to prior
        kappa[0] = kappa0
        mu[0] = mu0
        alpha[0] = alpha0
        beta[0] = beta0

    return R, map_runs


def _extract_change_points(map_runs: list[float], min_separation: int = 20) -> list[int]:
    """
    Detect change points from the MAP run length trajectory.

    A change point at time t is when the MAP run length RESETS (drops
    substantially from its running peak), meaning the posterior mode
    just moved from "long-running regime" to "fresh regime."

    Merges detections within min_separation observations so a single
    shift doesn't produce a cluster of adjacent flags.
    """
    if not map_runs:
        return []
    detections: list[int] = []
    running_peak = 0
    last_detection = -1_000_000
    for i, r in enumerate(map_runs):
        r_int = int(r)
        if r_int > running_peak:
            running_peak = r_int
        # A reset is when MAP run length drops by at least half of its
        # running peak (or drops to below 15). Half is a heuristic:
        # loose enough to catch real shifts, tight enough to ignore
        # sampling wobbles inside a stable regime.
        threshold_drop = max(15, running_peak // 2)
        if running_peak - r_int >= threshold_drop and running_peak >= 30:
            if (i - last_detection) >= min_separation:
                detections.append(i)
                last_detection = i
                running_peak = r_int  # reset the peak after a detection
    return detections


def _segment_stats(returns: np.ndarray, boundaries: list[int]) -> list[dict]:
    """Compute per-segment mean and std."""
    n = len(returns)
    if not boundaries:
        segs = [(0, n)]
    else:
        segs = []
        prev = 0
        for b in boundaries:
            if b > prev:
                segs.append((prev, b))
            prev = b
        if prev < n:
            segs.append((prev, n))
    out: list[dict] = []
    for start, end in segs:
        segment = returns[start:end]
        if segment.size == 0:
            continue
        out.append({
            "start_index": int(start),
            "end_index": int(end - 1),
            "n_obs": int(segment.size),
            "mean_daily_return": round(float(np.mean(segment)), 6),
            "std_daily_return": round(float(np.std(segment, ddof=1)), 6) if segment.size > 1 else 0.0,
            "annualized_return": round(float(np.mean(segment) * 252), 4),
            "annualized_vol": round(float(np.std(segment, ddof=1) * math.sqrt(252)), 4) if segment.size > 1 else 0.0,
        })
    return out


# ----- Public API -----

def run(
    ticker: str,
    lookback_days: int = 504,
    lambda_run: float = DEFAULT_LAMBDA_RUN,
    client: MassiveClient | None = None,
) -> dict:
    """Detect return-regime change points in a ticker's daily log returns.

    Args:
        ticker: single stock ticker.
        lookback_days: trading-day window. Default 504 (~2 years).
        lambda_run: prior mean run length between change points, in
            observations. Default 250 (~1 year).
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if lookback_days < 100:
        raise ValueError("lookback_days must be at least 100")

    client = client or MassiveClient()
    sources = _Sources()

    calendar_days = int(lookback_days * 1.6) + 21
    rows = _fetch_daily_aggs(client, ticker, calendar_days, sources)
    if len(rows) < lookback_days // 2:
        raise ValueError(
            f"insufficient history: got {len(rows)} bars, need >= {lookback_days // 2}"
        )
    if len(rows) > lookback_days + 1:
        rows = rows[-(lookback_days + 1):]

    dates = [r["date"] for r in rows]
    closes = np.array([r["close"] for r in rows], dtype=float)
    log_returns = np.diff(np.log(closes))
    return_dates = dates[1:]  # returns are indexed one behind closes

    _, map_runs = _bocpd(log_returns, lambda_run=lambda_run)
    boundaries = _extract_change_points(map_runs)

    segments = _segment_stats(log_returns, boundaries)

    change_points: list[dict] = []
    for b in boundaries:
        # Report the boundary as the date on which the new regime started
        cp_date = return_dates[b] if 0 <= b < len(return_dates) else None
        # Confidence at the exact boundary
        # "Confidence" here is the run-length drop magnitude: how much
        # the MAP run reset. Bigger drop = stronger evidence.
        cp_confidence = None
        if 0 <= b < len(map_runs) and b > 0:
            prior_peak = max(map_runs[:b]) if b > 0 else 0
            drop = prior_peak - map_runs[b]
            # Convert to a soft [0, 1] confidence: fraction of peak that dropped
            cp_confidence = round(min(1.0, drop / max(prior_peak, 1)), 4)
        change_points.append({
            "index": int(b),
            "date": cp_date,
            "confidence": cp_confidence,
        })

    current_run_length = None
    if map_runs:
        # Number of observations since last detected change point
        current_run_length = int(len(map_runs) - (boundaries[-1] if boundaries else 0))

    tier_caveats: list[str] = [
        "BOCPD is Bayesian and prior-sensitive; lambda_run prior of "
        f"{int(lambda_run)} observations means '~1 regime break per {int(lambda_run/250)} year(s)' on average.",
        "Detection lags real regime changes by 5-20 observations. Use for post-hoc labeling, not for real-time entries.",
    ]
    if len(change_points) == 0:
        tier_caveats.append(
            "No change points detected. Either the regime was stable through the window or "
            "the prior lambda_run is too long relative to the true regime cadence."
        )
    if len(change_points) > lookback_days // 60:
        tier_caveats.append(
            f"Detected {len(change_points)} change points in {lookback_days} days: "
            "the model is finding many breaks, which may indicate noise fitting; "
            "consider raising lambda_run."
        )

    return {
        "skill": "change-point-detector",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "lookback_days": int(lookback_days),
        "n_returns": int(log_returns.size),
        "lambda_run_prior": float(lambda_run),
        "detection_method": "map_run_length_reset",
        "n_change_points": len(change_points),
        "change_points": change_points,
        "segments": segments,
        "current_run_length_obs": current_run_length,
        "current_segment_start_date": (segments[-1] and return_dates[segments[-1]["start_index"]]) if segments else None,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x*100:.1f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    lookback = payload["lookback_days"]
    n_ret = payload["n_returns"]
    n_cp = payload["n_change_points"]

    lines.append(
        f"Change-point detector: {ticker} · {lookback}d lookback · "
        f"{n_ret} log returns · {n_cp} change point(s)"
    )
    lines.append(
        f"Prior mean run length: {int(payload['lambda_run_prior'])} obs · "
        f"Detection: {payload['detection_method']}"
    )
    lines.append("")

    if payload["change_points"]:
        lines.append("Detected change points")
        for cp in payload["change_points"]:
            lines.append(
                f"  · {cp['date']} · confidence {cp['confidence']} "
                f"(index {cp['index']})"
            )
        lines.append("")

    if payload["segments"]:
        lines.append("Segments (return regime per interval)")
        for i, seg in enumerate(payload["segments"]):
            marker = " (current)" if i == len(payload["segments"]) - 1 else ""
            lines.append(
                f"  #{i+1}: n={seg['n_obs']} obs · "
                f"ann-return {_fmt_pct(seg['annualized_return'])} · "
                f"ann-vol {_fmt_pct(seg['annualized_vol'])}{marker}"
            )
        lines.append("")

    take_parts: list[str] = []
    if n_cp == 0:
        take_parts.append(
            "No regime shifts detected. The return distribution has been stable across the window."
        )
    else:
        latest = payload["change_points"][-1]
        take_parts.append(
            f"{n_cp} regime shift(s) detected; the most recent was around {latest['date']} "
            f"(confidence {latest['confidence']})."
        )
        if payload["segments"] and len(payload["segments"]) >= 2:
            cur = payload["segments"][-1]
            prev = payload["segments"][-2]
            vol_delta = cur["annualized_vol"] - prev["annualized_vol"]
            ret_delta = cur["annualized_return"] - prev["annualized_return"]
            take_parts.append(
                f"Current vs prior regime: return {_fmt_pct(ret_delta)}, vol {_fmt_pct(vol_delta)}."
            )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
