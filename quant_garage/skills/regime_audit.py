"""
regime-audit as an importable library function.

Workflow composite: runs change-point-detector + hurst-exponent on
SPY plus the 11 SPDR sector ETFs. Produces a per-name regime map with
the last detected change point and the current persistence
classification (mean_reverting / random_walk / trending).

Answers "where has the market regime shifted, and which sectors are
in what regime right now?"

    from quant_garage.skills.regime_audit import run, render
    payload = run()
"""
from __future__ import annotations

from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import change_point_detector, hurst_exponent


SPDR_SECTOR_ETFS = (
    "XLK",  # Technology
    "XLF",  # Financials
    "XLE",  # Energy
    "XLV",  # Health Care
    "XLI",  # Industrials
    "XLY",  # Consumer Discretionary
    "XLP",  # Consumer Staples
    "XLU",  # Utilities
    "XLB",  # Materials
    "XLRE",  # Real Estate
    "XLC",  # Communication Services
)


def _safe_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def run(
    tickers: Iterable[str] | None = None,
    lookback_days: int = 504,
    lambda_run: float = 250.0,
    client: MassiveClient | None = None,
) -> dict:
    """Run change-point + hurst on a basket of index/sector ETFs.

    Args:
        tickers: iterable of tickers to audit. Default: SPY plus the
            11 SPDR sector ETFs.
        lookback_days: window for both sub-skills.
        lambda_run: prior mean run length for change-point detector.
        client: reuse an existing MassiveClient.
    """
    if tickers is None:
        tickers = ("SPY",) + SPDR_SECTOR_ETFS
    else:
        tickers = tuple(t.strip().upper() for t in tickers if t and t.strip())
    if not tickers:
        raise ValueError("provide at least one ticker")

    client = client or MassiveClient()

    per_ticker: list[dict] = []
    for t in tickers:
        cp_out, cp_err = _safe_run(
            change_point_detector.run,
            t,
            lookback_days=lookback_days,
            lambda_run=lambda_run,
            client=client,
        )
        h_out, h_err = _safe_run(
            hurst_exponent.run,
            t,
            lookback_days=lookback_days,
            client=client,
        )

        entry = {"ticker": t, "errors": {}}
        if cp_err:
            entry["errors"]["change_point_detector"] = cp_err
        if h_err:
            entry["errors"]["hurst_exponent"] = h_err

        if cp_out:
            cps = cp_out.get("change_points") or []
            entry["n_change_points"] = cp_out.get("n_change_points", 0)
            entry["last_change_point_date"] = cps[-1]["date"] if cps else None
            entry["last_change_point_confidence"] = cps[-1]["confidence"] if cps else None
            segments = cp_out.get("segments") or []
            entry["current_segment"] = segments[-1] if segments else None
            entry["n_segments"] = len(segments)
        else:
            entry["n_change_points"] = None

        if h_out:
            entry["hurst"] = h_out.get("hurst_exponent")
            entry["hurst_classification"] = h_out.get("classification")
            boot = h_out.get("bootstrap") or {}
            entry["hurst_p5"] = boot.get("p5")
            entry["hurst_p95"] = boot.get("p95")
        else:
            entry["hurst"] = None
            entry["hurst_classification"] = None

        per_ticker.append(entry)

    # Aggregate
    n_valid = sum(1 for e in per_ticker if e.get("hurst") is not None)
    n_shifted_recently = sum(
        1 for e in per_ticker
        if e.get("last_change_point_date")
        and (today().isoformat() > e["last_change_point_date"])
        and _within_days(e["last_change_point_date"], 60)
    )
    by_regime = {"mean_reverting": 0, "random_walk": 0, "trending": 0}
    for e in per_ticker:
        c = e.get("hurst_classification")
        if c in by_regime:
            by_regime[c] += 1

    # Verdict
    if n_shifted_recently >= 3:
        summary_verdict = "broad_regime_shift"
    elif n_shifted_recently >= 1:
        summary_verdict = "localized_regime_shift"
    elif by_regime["trending"] > by_regime["mean_reverting"] * 2:
        summary_verdict = "trend_dominated_regime"
    elif by_regime["mean_reverting"] > by_regime["trending"] * 2:
        summary_verdict = "mean_reversion_dominated_regime"
    else:
        summary_verdict = "mixed_stable_regime"

    tier_caveats: list[str] = [
        "Change-point detector lags real regime changes by 5-20 observations; "
        "recent-shift counts are directional, not exact.",
        "Hurst exponent bootstrap bands can be wide (0.10-0.20). Read the "
        "classification as directional; check hurst_p5 and hurst_p95 in the "
        "JSON for confidence.",
    ]

    return {
        "skill": "regime-audit",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "tickers": list(tickers),
        "lookback_days": int(lookback_days),
        "n_tickers": len(tickers),
        "n_valid": n_valid,
        "per_ticker": per_ticker,
        "n_shifted_recently": n_shifted_recently,
        "by_regime": by_regime,
        "summary_verdict": summary_verdict,
        "tier_caveats": tier_caveats,
    }


def _within_days(date_str: str, days: int) -> bool:
    from datetime import date as _date_cls
    try:
        d = _date_cls.fromisoformat(date_str)
    except (TypeError, ValueError):
        return False
    return (today() - d).days <= days


# ----- Renderer -----

_VERDICT_TAG = {
    "broad_regime_shift": "BROAD REGIME SHIFT",
    "localized_regime_shift": "LOCALIZED REGIME SHIFT",
    "trend_dominated_regime": "TREND-DOMINATED REGIME",
    "mean_reversion_dominated_regime": "MEAN-REVERSION REGIME",
    "mixed_stable_regime": "mixed / stable regime",
}


def render(payload: dict) -> str:
    lines: list[str] = []
    n = payload["n_tickers"]
    lb = payload["lookback_days"]

    lines.append(f"Regime audit: {n} tickers · {lb}d lookback")
    tag = _VERDICT_TAG.get(payload["summary_verdict"], payload["summary_verdict"])
    lines.append(f"Verdict: {tag}")
    lines.append(
        f"Recent shifts (last 60d): {payload['n_shifted_recently']} · "
        f"Trending: {payload['by_regime']['trending']} · "
        f"Random walk: {payload['by_regime']['random_walk']} · "
        f"Mean-reverting: {payload['by_regime']['mean_reverting']}"
    )
    lines.append("")

    lines.append(
        f"{'Ticker':<8} {'H':>7} {'Regime':<16} {'CPs':>4} {'Last shift':>13} {'Conf':>6} {'Segment ret':>13} {'Segment vol':>13}"
    )
    lines.append("-" * 85)
    for e in payload["per_ticker"]:
        t = e["ticker"]
        h = f"{e['hurst']:.3f}" if e.get("hurst") is not None else "n/a"
        regime = (e.get("hurst_classification") or "n/a").replace("_", "-")
        n_cp = e.get("n_change_points")
        n_cp_s = f"{n_cp:>4}" if n_cp is not None else "  n/a"
        last_cp = e.get("last_change_point_date") or "n/a"
        conf = f"{e['last_change_point_confidence']:.2f}" if e.get("last_change_point_confidence") is not None else "n/a"
        seg = e.get("current_segment") or {}
        seg_ret = f"{seg.get('annualized_return', 0)*100:+.1f}%" if seg.get("annualized_return") is not None else "n/a"
        seg_vol = f"{seg.get('annualized_vol', 0)*100:.1f}%" if seg.get("annualized_vol") is not None else "n/a"
        lines.append(
            f"{t:<8} {h:>7} {regime:<16} {n_cp_s} {last_cp:>13} {conf:>6} {seg_ret:>13} {seg_vol:>13}"
        )
    lines.append("")

    take_parts: list[str] = []
    if payload["summary_verdict"] == "broad_regime_shift":
        take_parts.append(
            f"Broad regime shift: {payload['n_shifted_recently']} tickers in the basket "
            "detected a change point in the last 60 days."
        )
    elif payload["summary_verdict"] == "localized_regime_shift":
        take_parts.append(
            f"Localized regime shift: {payload['n_shifted_recently']} ticker(s) with "
            "a recent change point."
        )
    elif payload["summary_verdict"] == "trend_dominated_regime":
        take_parts.append(
            f"Trend-dominated regime: {payload['by_regime']['trending']} tickers are "
            f"trending vs only {payload['by_regime']['mean_reverting']} mean-reverting. "
            "Momentum strategies favored."
        )
    elif payload["summary_verdict"] == "mean_reversion_dominated_regime":
        take_parts.append(
            f"Mean-reversion regime: {payload['by_regime']['mean_reverting']} tickers "
            f"are mean-reverting vs only {payload['by_regime']['trending']} trending. "
            "Pair strategies and z-score entries favored."
        )
    else:
        take_parts.append(
            "Mixed / stable: no dominant regime signal across the basket."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
