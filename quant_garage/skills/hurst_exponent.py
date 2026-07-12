"""
hurst-exponent as an importable library function.

Estimates the Hurst exponent (H) of a name's daily log returns using
rescaled-range (R/S) analysis. Classifies the series as
mean_reverting (H < 0.45), random_walk (0.45 <= H <= 0.55), or
trending (H > 0.55). Reports confidence bands via block-size sensitivity.

Answers "is this name mean-reversion setup or momentum setup?"
Companion to pairs-scanner: pairs handles two-name cointegration,
hurst handles single-name persistence.

    from quant_garage.skills.hurst_exponent import run, render
    payload = run("AAPL", lookback_days=504)

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Iterable

import numpy as np

from .. import MassiveClient, FetchError, today, utcnow_iso


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


# ----- Hurst math -----

def _rs_block_stat(returns: np.ndarray, n: int) -> float:
    """
    Rescaled range statistic for one block size n.

    Splits `returns` into non-overlapping blocks of length n, computes
    R/S per block, and returns the mean R/S across blocks. Skips
    degenerate blocks (zero std).
    """
    n_blocks = len(returns) // n
    if n_blocks < 1:
        return float("nan")
    rs_values: list[float] = []
    for b in range(n_blocks):
        block = returns[b * n:(b + 1) * n]
        mean_b = float(np.mean(block))
        centered = block - mean_b
        cumsum = np.cumsum(centered)
        R = float(np.max(cumsum) - np.min(cumsum))
        S = float(np.std(block, ddof=1))
        if S <= 0 or not math.isfinite(S):
            continue
        rs_values.append(R / S)
    if not rs_values:
        return float("nan")
    return float(np.mean(rs_values))


def _hurst_estimate(
    returns: np.ndarray, min_block: int = 10, max_block: int | None = None
) -> tuple[float, list[dict]]:
    """
    Hurst exponent via R/S regression across block sizes.

    Fits log(R/S(n)) = c + H * log(n) + eps and returns (H, per_block_stats).
    """
    n_total = len(returns)
    if max_block is None:
        max_block = n_total // 4  # standard rule: max block <= n/4 for stability
    if max_block < min_block:
        raise ValueError(
            f"insufficient returns for R/S regression: need max_block ({max_block}) "
            f">= min_block ({min_block})"
        )
    # Log-spaced block sizes for stable regression
    block_sizes = np.unique(
        np.round(np.logspace(np.log10(min_block), np.log10(max_block), num=12)).astype(int)
    )
    block_sizes = block_sizes[(block_sizes >= min_block) & (block_sizes <= max_block)]

    per_block: list[dict] = []
    log_n: list[float] = []
    log_rs: list[float] = []
    for n in block_sizes:
        rs = _rs_block_stat(returns, int(n))
        if math.isfinite(rs) and rs > 0:
            log_n.append(math.log(n))
            log_rs.append(math.log(rs))
            per_block.append({"block_size": int(n), "rs_mean": round(float(rs), 4)})

    if len(log_n) < 4:
        raise ValueError("not enough valid block sizes to fit R/S regression")

    # OLS log(rs) = c + H * log(n)
    x = np.array(log_n)
    y = np.array(log_rs)
    n_pts = x.shape[0]
    mx = float(np.mean(x))
    my = float(np.mean(y))
    num = float(np.sum((x - mx) * (y - my)))
    den = float(np.sum((x - mx) ** 2))
    H = num / den if den > 0 else float("nan")
    return (H, per_block)


def _bootstrap_hurst(returns: np.ndarray, n_bootstrap: int = 100, seed: int = 42) -> dict:
    """Block-bootstrap Hurst estimate to get a confidence band."""
    rng = np.random.default_rng(seed)
    block_len = 20  # bootstrap block length
    n = len(returns)
    n_blocks = n // block_len
    hs: list[float] = []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n_blocks, size=n_blocks)
        sample = np.concatenate([returns[i * block_len:(i + 1) * block_len] for i in idx])
        try:
            h, _ = _hurst_estimate(sample)
            if math.isfinite(h):
                hs.append(h)
        except (ValueError, IndexError):
            continue
    if not hs:
        return {"p5": None, "p50": None, "p95": None, "n_bootstrap_valid": 0}
    arr = np.array(hs)
    return {
        "p5": round(float(np.percentile(arr, 5)), 4),
        "p50": round(float(np.percentile(arr, 50)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
        "n_bootstrap_valid": int(arr.size),
    }


def _classify(H: float) -> tuple[str, str]:
    if H < 0.45:
        return ("mean_reverting", f"H={H:.2f} < 0.45 -> mean-reverting")
    if H > 0.55:
        return ("trending", f"H={H:.2f} > 0.55 -> trending / momentum")
    return ("random_walk", f"H={H:.2f} in [0.45, 0.55] -> random walk / no persistence")


# ----- Public API -----

def run(
    ticker: str,
    lookback_days: int = 504,
    n_bootstrap: int = 100,
    seed: int = 42,
    client: MassiveClient | None = None,
) -> dict:
    """Estimate the Hurst exponent for `ticker`'s daily log returns.

    Args:
        ticker: single stock ticker.
        lookback_days: trading-day window. Default 504 (~2 years). More
            data = tighter H estimate.
        n_bootstrap: block-bootstrap iterations for the confidence band.
            Default 100. Set to 0 to skip.
        seed: rng seed.
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if lookback_days < 80:
        raise ValueError("lookback_days must be at least 80 for a stable R/S estimate")

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

    closes = np.array([r["close"] for r in rows], dtype=float)
    returns = np.diff(np.log(closes))
    returns = returns[np.isfinite(returns)]

    if returns.size < 80:
        raise ValueError(f"only {returns.size} valid returns; need >= 80")

    H, per_block = _hurst_estimate(returns)
    if not math.isfinite(H):
        raise ValueError("Hurst estimate is not finite; series may be degenerate")

    label, reason = _classify(H)
    boot = _bootstrap_hurst(returns, n_bootstrap=n_bootstrap, seed=seed) if n_bootstrap > 0 else None

    tier_caveats: list[str] = [
        "R/S analysis on daily log returns. Small-sample H estimates are noisy; treat as directional not literal.",
        "Regime shifts break persistence estimates. A recent structural change may not show in a 2-year H.",
    ]
    if H < 0.35 or H > 0.65:
        tier_caveats.append(
            "Extreme H value; verify no data quality issue (dividends/splits) or one-way move dominating the sample."
        )
    if boot and boot["p5"] is not None and boot["p95"] is not None:
        band_width = boot["p95"] - boot["p5"]
        if band_width > 0.20:
            tier_caveats.append(
                f"Bootstrap band wide ({boot['p5']:.2f}-{boot['p95']:.2f}). "
                "Estimate is noisy; use as directional evidence only."
            )

    return {
        "skill": "hurst-exponent",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "lookback_days": int(lookback_days),
        "n_returns": int(returns.size),
        "hurst_exponent": round(float(H), 4),
        "classification": label,
        "reasoning": reason,
        "bootstrap": boot,
        "per_block_rs": per_block,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_LABEL_TAG = {
    "mean_reverting": "MEAN-REVERTING",
    "random_walk": "random walk",
    "trending": "TRENDING",
}


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    lookback = payload["lookback_days"]
    n = payload["n_returns"]

    lines.append(f"Hurst exponent: {ticker} · {lookback}d lookback · {n} log returns")
    H = payload["hurst_exponent"]
    label = payload["classification"]
    tag = _LABEL_TAG.get(label, label)
    lines.append(f"H = {H:.3f} → {tag}")
    lines.append("")

    boot = payload.get("bootstrap")
    if boot and boot.get("p50") is not None:
        lines.append(
            f"Bootstrap band (n={boot['n_bootstrap_valid']}): "
            f"p5 {boot['p5']:.3f} · p50 {boot['p50']:.3f} · p95 {boot['p95']:.3f}"
        )
        lines.append("")

    per_block = payload.get("per_block_rs") or []
    if per_block:
        lines.append("R/S per block size")
        for pb in per_block:
            lines.append(f"  n={pb['block_size']:>4}   R/S = {pb['rs_mean']}")
        lines.append("")

    take_parts: list[str] = []
    if label == "mean_reverting":
        take_parts.append(
            f"Mean-reversion setup. Pair strategies, range trading, and "
            f"z-score entries have historically had structural edge on this name."
        )
    elif label == "trending":
        take_parts.append(
            f"Trend / momentum setup. Breakout strategies and trend-following "
            f"have historically had structural edge; mean-reversion has not."
        )
    else:
        take_parts.append(
            "No persistence detected. Neither trend nor mean-reversion strategies "
            "have a structural edge over the sample. Trade the fundamentals, not the tape."
        )
    if boot and boot.get("p5") is not None and boot.get("p95") is not None:
        if boot["p5"] < 0.45 and boot["p95"] > 0.55:
            take_parts.append(
                "Bootstrap band crosses both 0.45 and 0.55 thresholds; regime is ambiguous."
            )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
