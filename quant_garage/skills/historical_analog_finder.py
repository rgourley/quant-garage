"""
historical-analog-finder as an importable library function.

market-regime tells you the current state (SPY trend, breadth, sector
leadership). This skill takes that state and finds K historical periods
with the most similar setup, then reports the forward return
distribution across those analogs. Regime-conditional forecasting.

    from quant_garage.skills.historical_analog_finder import run, render
    payload = run(k=20, horizon_days=90)

Approach:
- Build a daily regime-feature panel over the last N years.
- For each historical day, compute a feature vector.
- Compute today's feature vector.
- Find K nearest neighbors by z-scored Euclidean distance.
- Deduplicate overlapping matches so one historical window doesn't
  dominate the K.
- For each surviving analog, look up the forward SPY return at each
  requested horizon.
- Report the distribution.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
)


# Feature keys: each is a scalar computed on a daily bar panel.
# The default set uses SPY-only features so the skill runs on any Massive
# tier. Add optional sector/breadth features if we later want richer
# regime dimensions.
DEFAULT_FEATURES = [
    "spy_ret_5d",
    "spy_ret_20d",
    "spy_ret_60d",
    "spy_ret_120d",
    "spy_above_sma_50",
    "spy_above_sma_200",
    "spy_rsi_14",
    "spy_realized_vol_20d",
    "spy_drawdown_from_252d_high",
]


HISTORY_YEARS_DEFAULT = 20


def _fetch_bars(
    client: MassiveClient, ticker: str,
    from_date: date, to_date: date,
) -> list[dict]:
    try:
        body, _ = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{from_date.isoformat()}/{to_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 50000},
        )
    except FetchError:
        return []
    return body.get("results") or []


def _bars_to_array(bars: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Return (dates_array, closes_array) both length N."""
    dates = np.array([
        datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        for b in bars
    ])
    closes = np.array([float(b["c"]) for b in bars], dtype=float)
    return dates, closes


def _compute_feature_panel(
    dates: np.ndarray, closes: np.ndarray,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Compute daily features. Returns (valid_dates, features_by_day).

    features_by_day[i] is the feature vector for valid_dates[i].
    Days without enough history for all features are dropped.
    """
    n = len(closes)
    if n < 260:  # need at least ~1yr for the 252d high
        return np.array([]), []

    # Log returns for realized vol
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(closes[1:] / closes[:-1])

    # Precompute rolling metrics
    def _pct_return(idx: int, lookback: int) -> float | None:
        if idx - lookback < 0:
            return None
        return float(closes[idx] / closes[idx - lookback] - 1)

    def _sma(idx: int, window: int) -> float | None:
        if idx - window + 1 < 0:
            return None
        return float(np.mean(closes[idx - window + 1: idx + 1]))

    def _rsi(idx: int, window: int = 14) -> float | None:
        if idx - window < 0:
            return None
        diffs = np.diff(closes[idx - window: idx + 1])
        gains = diffs.clip(min=0)
        losses = (-diffs).clip(min=0)
        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _realized_vol(idx: int, window: int = 20) -> float | None:
        if idx - window + 1 < 0:
            return None
        window_returns = log_ret[idx - window + 1: idx + 1]
        return float(np.std(window_returns, ddof=1) * np.sqrt(252))

    def _drawdown_from_high(idx: int, window: int = 252) -> float | None:
        if idx - window + 1 < 0:
            return None
        high = float(np.max(closes[idx - window + 1: idx + 1]))
        if high <= 0:
            return None
        return float(closes[idx] / high - 1)

    valid_idxs: list[int] = []
    features: list[np.ndarray] = []
    for i in range(n):
        r5 = _pct_return(i, 5)
        r20 = _pct_return(i, 20)
        r60 = _pct_return(i, 60)
        r120 = _pct_return(i, 120)
        sma50 = _sma(i, 50)
        sma200 = _sma(i, 200)
        rsi = _rsi(i, 14)
        vol = _realized_vol(i, 20)
        dd = _drawdown_from_high(i, 252)
        if any(x is None for x in [r5, r20, r60, r120, sma50, sma200,
                                    rsi, vol, dd]):
            continue
        vec = np.array([
            r5, r20, r60, r120,
            1.0 if closes[i] > sma50 else 0.0,
            1.0 if closes[i] > sma200 else 0.0,
            rsi,
            vol,
            dd,
        ], dtype=float)
        valid_idxs.append(i)
        features.append(vec)

    return dates[valid_idxs], features


def _zscore_matrix(mat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (zscored_matrix, means, stds). Constant columns get std=1."""
    means = mat.mean(axis=0)
    stds = mat.std(axis=0, ddof=1)
    stds = np.where(stds <= 1e-12, 1.0, stds)
    return (mat - means) / stds, means, stds


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    """Euclidean distance in z-scored feature space."""
    return float(np.sqrt(np.sum((a - b) ** 2)))


def _dedupe_analogs(
    ranked: list[tuple[int, float, date]],
    min_gap_days: int = 30,
) -> list[tuple[int, float, date]]:
    """From a distance-ranked list of (idx, distance, date), drop matches
    that are within min_gap_days of a previously-accepted match. This
    prevents one historical window (e.g. late-2018 correction) from
    dominating the K nearest neighbors."""
    kept: list[tuple[int, float, date]] = []
    for entry in ranked:
        idx, dist, d = entry
        too_close = any(abs((d - kd).days) < min_gap_days for _, _, kd in kept)
        if not too_close:
            kept.append(entry)
    return kept


def _forward_return(
    dates: np.ndarray, closes: np.ndarray, idx: int, horizon: int,
) -> float | None:
    """Close-to-close percentage return from index idx to idx+horizon."""
    if idx + horizon >= len(closes):
        return None
    base = closes[idx]
    future = closes[idx + horizon]
    if base <= 0:
        return None
    return float(future / base - 1)


def _summary_stats(vals: list[float]) -> dict:
    if not vals:
        return {
            "n": 0, "mean": None, "median": None, "p25": None, "p75": None,
            "p10": None, "p90": None, "hit_rate_above_zero": None,
        }
    a = np.array(vals, dtype=float)
    return {
        "n": len(a),
        "mean": round(float(a.mean()), 4),
        "median": round(float(np.percentile(a, 50)), 4),
        "p25": round(float(np.percentile(a, 25)), 4),
        "p75": round(float(np.percentile(a, 75)), 4),
        "p10": round(float(np.percentile(a, 10)), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "hit_rate_above_zero": round(float((a > 0).sum() / len(a)), 3),
    }


def run(
    k: int = 20,
    horizon_days: int | list[int] = 90,
    benchmark: str = "SPY",
    history_years: int = HISTORY_YEARS_DEFAULT,
    min_gap_days: int = 30,
    features: list[str] | None = None,
    client: MassiveClient | None = None,
) -> dict:
    """Find K historical periods most similar to the current market
    regime and report forward SPY return distributions.

    Args:
        k: nearest-analog count. Default 20.
        horizon_days: forward horizon in trading days. Can be a single
            int or list of ints (e.g. [30, 60, 90, 252]). Default 90.
        benchmark: benchmark ticker. Default SPY.
        history_years: years of history to search. Default 20.
        min_gap_days: minimum calendar gap between accepted matches to
            prevent one window from dominating. Default 30.
        features: optional feature list override.
        client: reuse an existing MassiveClient.
    """
    if k < 5:
        raise ValueError("k must be >= 5")
    if history_years < 3:
        raise ValueError("history_years must be >= 3")

    horizons = [horizon_days] if isinstance(horizon_days, int) else list(horizon_days)
    max_horizon = max(horizons)

    client = client or MassiveClient()
    today_d = today()
    from_date = today_d - timedelta(days=int(365.25 * history_years))

    print(f"Fetching {benchmark} bars for {history_years}y regime history...",
          file=sys.stderr)
    bars = _fetch_bars(client, benchmark, from_date, today_d)
    if not bars:
        raise RuntimeError(f"no {benchmark} bars; check MASSIVE_API_KEY")

    dates, closes = _bars_to_array(bars)
    valid_dates, feature_vecs = _compute_feature_panel(dates, closes)
    if len(feature_vecs) < 500:
        raise RuntimeError(
            f"only {len(feature_vecs)} valid daily feature vectors; "
            f"need >= 500. Extend --history-years."
        )

    # The last valid day is "today's" feature vector
    mat = np.stack(feature_vecs, axis=0)
    z_mat, means, stds = _zscore_matrix(mat)
    today_vec = z_mat[-1]
    today_valid_date = valid_dates[-1]

    # Trim the search space so today is not compared against itself and
    # so every analog has room for the longest horizon.
    n_valid = len(valid_dates)
    # Convert valid indices back to positions in the original bars/closes
    # arrays so we can look up forward returns.
    valid_to_bar_idx = {}
    j = 0
    for i in range(len(dates)):
        # Find which valid index corresponds to bar i
        if j < len(valid_dates) and valid_dates[j] == dates[i]:
            valid_to_bar_idx[j] = i
            j += 1

    # Distance to today for every prior valid day.
    # Exclude the last max_horizon+1 valid days so we always have forward
    # returns; also exclude days within 90 days of today to avoid look-
    # ahead in the neighbor set.
    exclude_recent_valid_days = max(max_horizon + 5, 90)
    search_end = n_valid - exclude_recent_valid_days
    if search_end < 200:
        raise RuntimeError("not enough history after excluding recent window")

    dists: list[tuple[int, float, date]] = []
    for i in range(search_end):
        d = _distance(z_mat[i], today_vec)
        dists.append((i, d, valid_dates[i]))
    dists.sort(key=lambda x: x[1])

    # Dedupe overlapping matches; keep taking until we have K
    accepted: list[tuple[int, float, date]] = []
    candidate_iter = iter(dists)
    while len(accepted) < k:
        try:
            entry = next(candidate_iter)
        except StopIteration:
            break
        idx, dist, d = entry
        too_close = any(abs((d - kd).days) < min_gap_days for _, _, kd in accepted)
        if not too_close:
            accepted.append(entry)
    if len(accepted) < k:
        # Not enough non-overlapping matches; keep what we have but flag.
        pass

    # Forward returns for each analog at each horizon
    analogs: list[dict] = []
    for valid_idx, dist, d in accepted:
        bar_idx = valid_to_bar_idx[valid_idx]
        forward_by_horizon: dict[str, float | None] = {}
        for h in horizons:
            r = _forward_return(dates, closes, bar_idx, h)
            forward_by_horizon[f"{h}d"] = round(r, 4) if r is not None else None
        analogs.append({
            "date": d.isoformat(),
            "distance_z": round(dist, 3),
            "forward_returns": forward_by_horizon,
        })

    # Aggregate forward distributions per horizon
    forward_dist: dict[str, dict] = {}
    for h in horizons:
        vals = [
            a["forward_returns"][f"{h}d"] for a in analogs
            if a["forward_returns"][f"{h}d"] is not None
        ]
        forward_dist[f"{h}d"] = _summary_stats(vals)

    # Current-day feature snapshot for the payload
    feature_names = features or DEFAULT_FEATURES
    current_snapshot = {
        feature_names[i]: round(float(mat[-1][i]), 4)
        for i in range(len(feature_names))
    }
    current_snapshot_z = {
        feature_names[i]: round(float(today_vec[i]), 4)
        for i in range(len(feature_names))
    }

    return {
        "scan_params": {
            "k": k,
            "horizons_days": horizons,
            "benchmark": benchmark,
            "history_years": history_years,
            "min_gap_days": min_gap_days,
            "as_of": today_d.isoformat(),
            "reference_valid_date": today_valid_date.isoformat(),
            "feature_names": feature_names,
        },
        "current_regime": {
            "raw": current_snapshot,
            "z_scores": current_snapshot_z,
        },
        "analogs": analogs,
        "n_analogs": len(analogs),
        "forward_return_distributions": forward_dist,
        "effective_sample_size_note": (
            f"K={k} requested, {len(accepted)} accepted after dedupe. "
            f"Deduplication requires min {min_gap_days}d gap between "
            f"analogs to prevent one historical window from dominating."
        ),
        "generated_at": utcnow_iso(),
        "caveats": [
            "Regime-conditional forecasting works UNTIL the world "
            "changes structurally. Analog periods pre-2008, pre-QE, or "
            "pre-2020 sample from different macro machinery.",
            "Feature set is SPY-only. Sector rotation and rates are not "
            "captured; add them via sector-rotation-signal and "
            "fixed-income-context for a richer analog.",
            "The distribution is descriptive. Do not interpret the mean "
            "as a point forecast. The IQR is the honest range.",
            "K=20 with 30-day dedupe requires >2 years of non-overlapping "
            "history. If effective sample < K, treat the distribution "
            "as noisier than it looks.",
        ],
    }


def _fmt_pct(x, decimals=1, signed=True):
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 and signed else ""
    return f"{sign}{x * 100:.{decimals}f}%"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    analogs = payload["analogs"]
    dist = payload["forward_return_distributions"]
    lines: list[str] = []

    lines.append(
        f"Historical Analog Finder — {params['as_of']}\n"
        f"K={params['k']} nearest analogs over {params['history_years']}y history · "
        f"Benchmark {params['benchmark']} · "
        f"Feature set: {len(params['feature_names'])} regime features"
    )
    lines.append("")

    # Current regime feature snapshot (raw values)
    raw = payload["current_regime"]["raw"]
    zs = payload["current_regime"]["z_scores"]
    lines.append("Current regime snapshot (raw · z-score):")
    for name in params["feature_names"]:
        raw_v = raw.get(name)
        z_v = zs.get(name)
        if isinstance(raw_v, float):
            # Format context-appropriately per feature type
            if "ret" in name or "drawdown" in name:
                raw_str = _fmt_pct(raw_v)
            elif "vol" in name:
                raw_str = _fmt_pct(raw_v, decimals=0)
            elif "above_sma" in name:
                raw_str = "yes" if raw_v > 0.5 else "no"
            elif "rsi" in name:
                raw_str = f"{raw_v:.1f}"
            else:
                raw_str = f"{raw_v:.3f}"
        else:
            raw_str = str(raw_v)
        lines.append(f"  {name:<32} {raw_str:>10}  ·  z {z_v:+.2f}")
    lines.append("")

    # Forward return distributions
    lines.append(
        f"Forward SPY return distribution across {payload['n_analogs']} analogs:"
    )
    lines.append("")
    lines.append(
        f"{'Horizon':>8}  {'n':>4}  {'p10':>8}  {'p25':>8}  "
        f"{'median':>8}  {'p75':>8}  {'p90':>8}  {'mean':>8}  {'>0':>5}"
    )
    lines.append("-" * 74)
    for h in params["horizons_days"]:
        key = f"{h}d"
        s = dist.get(key, {})
        n = s.get("n") or 0
        hit = s.get("hit_rate_above_zero")
        hit_str = f"{hit * 100:.0f}%" if hit is not None else "n/a"
        lines.append(
            f"{key:>8}  {n:>4}  "
            f"{_fmt_pct(s.get('p10')):>8}  "
            f"{_fmt_pct(s.get('p25')):>8}  "
            f"{_fmt_pct(s.get('median')):>8}  "
            f"{_fmt_pct(s.get('p75')):>8}  "
            f"{_fmt_pct(s.get('p90')):>8}  "
            f"{_fmt_pct(s.get('mean')):>8}  "
            f"{hit_str:>5}"
        )
    lines.append("")

    # Top analog dates (first 8)
    lines.append("Top analog dates (nearest first):")
    for a in analogs[:8]:
        forwards = " · ".join(
            f"{k} {_fmt_pct(v)}" for k, v in a["forward_returns"].items()
            if v is not None
        )
        lines.append(
            f"  {a['date']}  (z-dist {a['distance_z']:.2f})  ->  {forwards}"
        )
    if len(analogs) > 8:
        lines.append(f"  ... {len(analogs) - 8} more analogs")
    lines.append("")

    lines.append(payload.get("effective_sample_size_note", ""))
    lines.append("")

    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines)
