"""
portfolio-rebalancer as an importable library function.

Decision-support layer on top of risk-report. Takes current positions
with weights and outputs a specific rebalance recommendation to hit a
variance-share cap:

    "trim ALLO from 18.3% weight to 10% to move variance share from
     66% to 30%."

Not tax-aware in v1. Not liquidity-aware in v1. Refuses to recommend
more than a preset max churn per rebalance (default 10% of book) so a
single call cannot blow up a portfolio.

    from quant_garage.skills.portfolio_rebalancer import run, render
    payload = run(
        positions="ALLO=0.183,SOFI=0.070,BRK.B=0.163,...",
        max_variance_share=0.25,
        max_weight=0.15,
        book_value=650000,
    )
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    annualized_vol,
    correlation_matrix,
    shrink_correlation,
    covariance_matrix,
    portfolio_returns,
    position_variance_contributions,
)


# Reuse the same helpers as risk-report for a consistent book shape.
from .risk_report import (
    fetch_daily_aggs,
    daily_log_returns,
    align_returns,
    parse_positions_string,
)


# ---------- Rebalance solver ----------

def _variance_shares(
    weights_arr: np.ndarray, cov: np.ndarray,
) -> np.ndarray:
    """Return per-name variance shares (sum to 1). All-zero portfolio
    returns equal shares to avoid div-by-zero."""
    mc = cov @ weights_arr
    contrib = weights_arr * mc
    total = float(contrib.sum())
    if total <= 0 or not math.isfinite(total):
        n = len(weights_arr)
        return np.full(n, 1.0 / max(n, 1))
    return contrib / total


def _rebalance_iterative(
    weights: np.ndarray,
    cov: np.ndarray,
    max_variance_share: float,
    max_weight: float,
    max_iter: int = 30,
    tol: float = 1e-4,
) -> tuple[np.ndarray, int]:
    """Iteratively reduce over-cap variance shares.

    Each iteration:
      1. Find names with variance share > max_variance_share
      2. Scale those weights down by sqrt(target_ratio) since variance
         share for concentrated risk is roughly quadratic in weight.
      3. Redistribute the freed weight to names that are under both
         the variance-share cap AND the weight cap, in proportion to
         their current weight.
      4. Renormalize to keep gross exposure at the original level.

    Converges when no name exceeds max_variance_share (within tol) or
    max_iter reached.
    """
    n = len(weights)
    w = weights.copy()
    gross = float(w.sum())

    for _ in range(max_iter):
        shares = _variance_shares(w, cov)
        over = shares > max_variance_share + tol
        if not over.any():
            break

        # Trim over-cap names
        freed = 0.0
        w_new = w.copy()
        for i in range(n):
            if over[i]:
                target_ratio = max_variance_share / shares[i]
                # sqrt because variance share ~ w^2 * sigma^2 / total_var
                scale = math.sqrt(target_ratio)
                trim = w[i] * (1.0 - scale)
                w_new[i] = w[i] - trim
                freed += trim

        if freed <= 0:
            break

        # Distribute freed weight to under-cap names (both variance cap
        # and weight cap). Proportional to current weight.
        eligible = np.zeros(n, dtype=bool)
        for i in range(n):
            if not over[i] and w[i] < max_weight - tol:
                eligible[i] = True

        total_eligible = float(w_new[eligible].sum())
        if total_eligible <= 0 or not eligible.any():
            # Cannot redistribute: park in equal-weight bump across all
            # eligible slots, respecting max_weight.
            room = np.maximum(max_weight - w_new, 0.0)
            room[over] = 0.0
            room_total = float(room.sum())
            if room_total <= 0:
                # No room anywhere. Leave the trim as reduced gross
                # exposure. This is honest: cannot satisfy the cap
                # without violating other caps or introducing new names.
                break
            w_new = w_new + freed * (room / room_total)
        else:
            add_ratio = np.zeros(n, dtype=float)
            add_ratio[eligible] = w_new[eligible] / total_eligible
            w_new = w_new + freed * add_ratio
            # Enforce max_weight after distribution: any name that
            # exceeded max_weight gets clipped and the excess goes back
            # to the eligible pool (single pass).
            excess_mask = w_new > max_weight
            if excess_mask.any():
                excess = float((w_new[excess_mask] - max_weight).sum())
                w_new = np.minimum(w_new, max_weight)
                # Redistribute excess to remaining eligible names
                further_eligible = eligible & ~excess_mask & (
                    w_new < max_weight - tol
                )
                further_total = float(w_new[further_eligible].sum())
                if further_total > 0:
                    further_ratio = np.zeros(n, dtype=float)
                    further_ratio[further_eligible] = (
                        w_new[further_eligible] / further_total
                    )
                    w_new = w_new + excess * further_ratio

        # Renormalize to preserve gross exposure
        cur_sum = float(w_new.sum())
        if cur_sum > 0:
            w_new = w_new * (gross / cur_sum)

        # Convergence check
        if float(np.abs(w_new - w).max()) < tol:
            w = w_new
            break
        w = w_new
    else:
        return w, max_iter
    return w, _  # last iteration index


def _apply_churn_cap(
    w_current: np.ndarray, w_target: np.ndarray, max_churn: float,
) -> tuple[np.ndarray, float]:
    """Cap total absolute weight change to max_churn.

    Returns (adjusted_weights, actual_churn). Churn is defined as
    sum(|w_target - w_current|) / 2 (one-way turnover)."""
    delta = w_target - w_current
    one_way_churn = float(np.abs(delta).sum()) / 2.0
    if one_way_churn <= max_churn:
        return w_target, one_way_churn
    scale = max_churn / one_way_churn
    return w_current + scale * delta, max_churn


# ---------- Public entry point ----------

def run(
    positions: str | None = None,
    book: str | None = None,
    book_value: float = 100000.0,
    max_variance_share: float = 0.25,
    max_weight: float = 0.15,
    max_churn: float = 0.10,
    benchmark: str = "SPY",
    lookback_days: int = 252,
    shrinkage: float = 0.05,
    min_trade_dollar: float = 100.0,
    client: MassiveClient | None = None,
) -> dict:
    """Recommend a rebalance that brings all variance shares under a cap.

    Args:
        positions: comma-separated 'TICKER=WEIGHT' string
            (mutually exclusive with book).
        book: path to a JSON book file (same shape as risk-report).
        book_value: dollar value of the book. Default 100,000.
            Used to compute dollar trade amounts.
        max_variance_share: per-name cap. Default 0.25.
        max_weight: per-name weight cap. Default 0.15.
        max_churn: max one-way turnover per rebalance. Default 0.10.
        benchmark: benchmark ticker. Default SPY.
        lookback_days: history for covariance. Default 252.
        shrinkage: correlation-matrix shrinkage. Default 0.05.
        min_trade_dollar: skip trades below this size. Default $100.
    """
    if positions and book:
        raise ValueError("positions and book are mutually exclusive")
    if not positions and not book:
        raise ValueError("pass positions=... or book=...")
    if max_variance_share <= 0 or max_variance_share > 1:
        raise ValueError("max_variance_share must be in (0, 1]")
    if max_weight <= 0 or max_weight > 1:
        raise ValueError("max_weight must be in (0, 1]")
    if max_churn < 0 or max_churn > 1:
        raise ValueError("max_churn must be in [0, 1]")

    client = client or MassiveClient()

    # Reuse risk_report's shared module state
    from . import risk_report as _rr
    _rr.client = client

    if positions:
        weights = parse_positions_string(positions)
    else:
        from .risk_report import parse_book_file
        weights, _ = parse_book_file(book)

    if not weights:
        raise ValueError("no positions parsed")

    tickers = sorted(weights.keys())
    n = len(tickers)

    # Fetch daily aggs for each name + benchmark
    print(f"Fetching daily aggs for {n} positions + benchmark {benchmark}...",
          file=sys.stderr)
    per_ticker: dict[str, dict[str, float]] = {}
    for t in tickers + [benchmark]:
        rows = fetch_daily_aggs(t, lookback_days)
        if not rows:
            raise RuntimeError(f"no daily aggs for {t}")
        per_ticker[t] = daily_log_returns(rows)

    all_names, aligned_returns = align_returns(per_ticker)
    position_tickers = [t for t in tickers if t in aligned_returns]
    if len(position_tickers) < n:
        missing = set(tickers) - set(position_tickers)
        print(f"WARN: dropped {len(missing)} positions with no aligned "
              f"returns: {sorted(missing)}", file=sys.stderr)

    # Per-name vol + cov
    name_vols = {
        t: float(annualized_vol(np.asarray(aligned_returns[t], dtype=float)))
        for t in position_tickers
    }
    if len(position_tickers) >= 2:
        ordered_tickers, raw_corr = correlation_matrix(
            {t: aligned_returns[t] for t in position_tickers}
        )
        shrunk = shrink_correlation(raw_corr, shrinkage)
        cov = covariance_matrix(name_vols, shrunk, ordered_tickers)
    else:
        ordered_tickers = list(position_tickers)
        sigma = name_vols[ordered_tickers[0]]
        cov = np.array([[sigma * sigma]], dtype=float)

    # Current weights as ordered array
    w_current = np.array(
        [weights.get(t, 0.0) for t in ordered_tickers], dtype=float
    )
    gross = float(w_current.sum())

    # Current stats
    shares_before = _variance_shares(w_current, cov)
    port_ret_before = portfolio_returns(
        {ordered_tickers[i]: float(w_current[i]) for i in range(len(ordered_tickers))},
        {t: np.asarray(aligned_returns[t], dtype=float) for t in ordered_tickers},
    )
    port_vol_before = float(annualized_vol(port_ret_before))

    # Solve
    w_target, iters = _rebalance_iterative(
        w_current, cov,
        max_variance_share=max_variance_share,
        max_weight=max_weight,
    )
    w_final, actual_churn = _apply_churn_cap(w_current, w_target, max_churn)

    # After stats
    shares_after = _variance_shares(w_final, cov)
    port_ret_after = portfolio_returns(
        {ordered_tickers[i]: float(w_final[i]) for i in range(len(ordered_tickers))},
        {t: np.asarray(aligned_returns[t], dtype=float) for t in ordered_tickers},
    )
    port_vol_after = float(annualized_vol(port_ret_after))

    # Build trade tickets
    trades: list[dict] = []
    for i, t in enumerate(ordered_tickers):
        delta_w = float(w_final[i] - w_current[i])
        delta_dollar = delta_w * book_value
        if abs(delta_dollar) < min_trade_dollar:
            continue
        trades.append({
            "ticker": t,
            "action": "buy" if delta_dollar > 0 else "sell",
            "delta_weight_pp": round(delta_w * 100, 2),
            "delta_dollar": round(delta_dollar, 2),
            "weight_before": round(float(w_current[i]), 4),
            "weight_after": round(float(w_final[i]), 4),
            "variance_share_before": round(float(shares_before[i]), 4),
            "variance_share_after": round(float(shares_after[i]), 4),
        })
    trades.sort(key=lambda x: -abs(x["delta_dollar"]))

    # Portfolio-level before/after
    def _top_k(shares_arr, k):
        idx = np.argsort(-shares_arr)[:k]
        return float(shares_arr[idx].sum())

    def _herfindahl(w_arr):
        return float(np.sum(w_arr ** 2))

    before_top3_vs = _top_k(shares_before, 3)
    after_top3_vs = _top_k(shares_after, 3)
    before_herf = _herfindahl(w_current)
    after_herf = _herfindahl(w_final)

    # Constraint satisfaction check on final
    still_over = [
        {"ticker": ordered_tickers[i],
         "variance_share": round(float(shares_after[i]), 4),
         "weight": round(float(w_final[i]), 4)}
        for i in range(len(ordered_tickers))
        if shares_after[i] > max_variance_share + 1e-3
    ]

    return {
        "scan_params": {
            "tickers": ordered_tickers,
            "book_value": book_value,
            "max_variance_share": max_variance_share,
            "max_weight": max_weight,
            "max_churn": max_churn,
            "benchmark": benchmark,
            "lookback_days": lookback_days,
            "shrinkage": shrinkage,
            "as_of": today().isoformat(),
        },
        "trades": trades,
        "n_trades": len(trades),
        "actual_churn": round(actual_churn, 4),
        "iterations": iters,
        "constraint_status": {
            "all_variance_shares_within_cap": len(still_over) == 0,
            "still_over_cap": still_over,
            "churn_hit_cap": actual_churn >= max_churn - 1e-4,
        },
        "portfolio_before": {
            "annualized_vol": round(port_vol_before, 4),
            "top_3_variance_share": round(before_top3_vs, 4),
            "herfindahl": round(before_herf, 4),
            "max_variance_share": round(float(shares_before.max()), 4),
            "max_variance_share_name":
                ordered_tickers[int(np.argmax(shares_before))],
        },
        "portfolio_after": {
            "annualized_vol": round(port_vol_after, 4),
            "top_3_variance_share": round(after_top3_vs, 4),
            "herfindahl": round(after_herf, 4),
            "max_variance_share": round(float(shares_after.max()), 4),
            "max_variance_share_name":
                ordered_tickers[int(np.argmax(shares_after))],
        },
        "generated_at": utcnow_iso(),
        "caveats": [
            "Not tax-aware. Selling appreciated positions incurs capital "
            "gains; the tool ignores this. If lots matter, apply the "
            "trade list through a tax-lot-aware execution layer.",
            "Not liquidity-aware. Trade dollar amounts do not consider "
            "ADV, spread, or market impact. Verify with slippage-cost "
            "before executing large trades in illiquid names.",
            "Descriptive rebalancing against a risk cap. This is not a "
            "return-maximizing optimizer and does not use forward return "
            "estimates.",
            "Covariance is estimated with shrinkage but still relies on "
            "the last N trading days. Regime shifts can change the "
            "covariance shape faster than the estimator adapts.",
        ],
    }


# ---------- Renderer ----------

def _fmt_pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    if signed:
        return f"{'+' if x >= 0 else ''}{x * 100:.{decimals}f}%"
    return f"{x * 100:.{decimals}f}%"


def _fmt_dollar(x):
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    ax = abs(x)
    if ax >= 1000:
        return f"{sign}${ax:,.0f}"
    return f"{sign}${ax:,.2f}"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    before = payload["portfolio_before"]
    after = payload["portfolio_after"]
    trades = payload["trades"]
    status = payload["constraint_status"]
    lines: list[str] = []

    lines.append(
        f"Portfolio Rebalance Recommendation — {params['as_of']}"
    )
    lines.append(
        f"Book value: {_fmt_dollar(params['book_value'])} · "
        f"{len(params['tickers'])} positions · "
        f"caps: variance-share <= {_fmt_pct(params['max_variance_share'])}, "
        f"weight <= {_fmt_pct(params['max_weight'])}, "
        f"churn <= {_fmt_pct(params['max_churn'])}"
    )
    lines.append("")

    # Before / After summary
    lines.append("Before / After:")
    lines.append(
        f"  Vol (ann):              "
        f"{_fmt_pct(before['annualized_vol'])} -> "
        f"{_fmt_pct(after['annualized_vol'])}"
    )
    lines.append(
        f"  Top-3 variance share:   "
        f"{_fmt_pct(before['top_3_variance_share'])} -> "
        f"{_fmt_pct(after['top_3_variance_share'])}"
    )
    lines.append(
        f"  Max single-name var:    "
        f"{_fmt_pct(before['max_variance_share'])} "
        f"({before['max_variance_share_name']}) -> "
        f"{_fmt_pct(after['max_variance_share'])} "
        f"({after['max_variance_share_name']})"
    )
    lines.append(
        f"  Herfindahl:             "
        f"{before['herfindahl']:.3f} -> {after['herfindahl']:.3f}"
    )
    lines.append(
        f"  Actual churn:           {_fmt_pct(payload['actual_churn'])}"
    )
    lines.append("")

    # Trade tickets
    if not trades:
        lines.append("No trades recommended — portfolio already within all caps.")
    else:
        lines.append(f"Recommended trades ({payload['n_trades']}):")
        lines.append("")
        lines.append(
            f"{'Ticker':<8}{'Action':>6}  {'Dollar':>12}  "
            f"{'Δ wt':>7}  {'Weight':>18}  {'Var Share':>18}"
        )
        lines.append("-" * 78)
        for t in trades:
            wt_str = (f"{_fmt_pct(t['weight_before'])} -> "
                      f"{_fmt_pct(t['weight_after'])}")
            vs_str = (f"{_fmt_pct(t['variance_share_before'])} -> "
                      f"{_fmt_pct(t['variance_share_after'])}")
            action_upper = t['action'].upper()
            lines.append(
                f"{t['ticker']:<8}{action_upper:>6}  "
                f"{_fmt_dollar(t['delta_dollar']):>12}  "
                f"{t['delta_weight_pp']:+6.1f}%  "
                f"{wt_str:>18}  {vs_str:>18}"
            )
    lines.append("")

    # Status
    if not status["all_variance_shares_within_cap"]:
        lines.append(
            "STATUS: Constraints partially satisfied. Names still over "
            "variance-share cap after churn limit:"
        )
        for s in status["still_over_cap"]:
            lines.append(
                f"  {s['ticker']}: variance share "
                f"{_fmt_pct(s['variance_share'])}, weight "
                f"{_fmt_pct(s['weight'])}"
            )
        lines.append(
            "  To fully resolve, raise --max-churn or run again after "
            "the first rebalance settles."
        )
    else:
        lines.append("STATUS: All variance shares within cap after rebalance.")

    if status["churn_hit_cap"]:
        lines.append("  (Churn cap was binding — trades scaled down proportionally.)")

    lines.append("")
    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines)
