"""
pairs-scanner as an importable library function.

Tests every pair in a basket for cointegration on log-prices, estimates
the Ornstein-Uhlenbeck half-life of the spread, computes the current
z-score, and flags tradeable pairs.

    from quant_garage.skills.pairs_scanner import run, render
    payload = run(["KO","PEP","MDLZ","MO"], lookback_days=252)

Methodology:

1. Pull daily closes for every ticker over `lookback_days * 1.6 + 21`
   calendar days. Intersect dates so every ticker has a close on every
   sample date.
2. For each pair: correlation of log returns (prefilter). Below
   `min_correlation`, skip the pair.
3. Engle-Granger 2-step cointegration on log prices in both directions;
   pick the direction whose residual has the more negative ADF t-stat
   (more stationary). Compare against MacKinnon 2010 critical values for
   N=2 cointegration with constant.
4. OU half-life from OLS of Δresidual on lagged residual.
5. Current spread z-score = (residual_last - mean) / std.
6. Out-of-sample stability: split the sample 70/30, fit beta on the
   first 70%, apply it to the last 30%, compare the residual std ratio.
   Flag `stable` when OS std < 1.5x IS std, else `regime_shift`.

Reads MASSIVE_API_KEY from env. Stocks Starter minimum (unlimited REST
for daily aggs).
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone, timedelta
from itertools import combinations
from typing import Iterable

from .. import MassiveClient, FetchError, today, utcnow_iso


# Engle-Granger critical values (MacKinnon 2010) for cointegration
# testing on residuals from an OLS regression with constant, N=2
# variables. These differ from standard ADF critical values because
# the residual is not directly observed.
EG_CRIT_1PCT = -3.90
EG_CRIT_5PCT = -3.34
EG_CRIT_10PCT = -3.04

# Minimum sample size for a cointegration test to be worth reporting.
MIN_OBSERVATIONS = 60

# Out-of-sample split fraction (0.7 = 70% in-sample, 30% out-of-sample).
OS_SPLIT_FRACTION = 0.7

# OS residual std ratio above this flips the stability flag.
OS_INSTABILITY_THRESHOLD = 1.5


_AGGS_CACHE: dict[str, list[dict]] = {}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_daily_aggs(
    client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources,
) -> list[dict]:
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        _AGGS_CACHE[ticker] = []
        return []
    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None:
            continue
        if close <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily closes for {ticker}",
    )
    return rows


# ----- Math -----

def _align_series(rows_by_ticker: dict[str, list[dict]]) -> tuple[list[str], dict[str, list[float]]]:
    date_sets = [set(r["date"] for r in rows) for rows in rows_by_ticker.values() if rows]
    if not date_sets:
        return ([], {})
    common = sorted(set.intersection(*date_sets))
    aligned: dict[str, list[float]] = {}
    for t, rows in rows_by_ticker.items():
        d2c = {r["date"]: r["close"] for r in rows}
        aligned[t] = [d2c[d] for d in common]
    return common, aligned


def _ols(x: list[float], y: list[float]) -> tuple[float, float]:
    """OLS y = alpha + beta*x. Returns (beta, alpha)."""
    n = len(x)
    if n < 2:
        return (0.0, 0.0)
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = sum((xi - mx) ** 2 for xi in x)
    if den == 0:
        return (0.0, my)
    beta = num / den
    alpha = my - beta * mx
    return (beta, alpha)


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    denx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    deny = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)


def _log_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _adf_tstat(series: list[float]) -> float:
    """
    Dickey-Fuller t-statistic on a mean-zero residual series.

    Model: Δe_t = γ · e_{t-1} + ε_t  (no constant, no augmenting lags).
    The residual comes from a regression with intercept, so E[e] ≈ 0
    already; a second constant would over-fit. Lag-1 DF is adequate for
    daily-close residuals; augmenting lags didn't shift buckets in the
    ALLO / KO-PEP smoke tests during development.

    Test H0: γ = 0 (unit root) vs H1: γ < 0 (stationary).
    Compare t-stat against MacKinnon 2010 critical values via
    `_eg_pvalue_bucket`.
    """
    n = len(series)
    if n < MIN_OBSERVATIONS // 3:
        return 0.0
    de = [series[i] - series[i - 1] for i in range(1, n)]
    e_lag = series[:-1]
    num = sum(l * d for l, d in zip(e_lag, de))
    den = sum(l * l for l in e_lag)
    if den == 0:
        return 0.0
    gamma = num / den
    resid = [de[i] - gamma * e_lag[i] for i in range(len(de))]
    dof = len(de) - 1
    if dof <= 0:
        return 0.0
    sigma2 = sum(r * r for r in resid) / dof
    se_gamma = math.sqrt(sigma2 / den) if den > 0 else float("inf")
    if se_gamma <= 0 or math.isinf(se_gamma):
        return 0.0
    return gamma / se_gamma


def _eg_pvalue_bucket(t_stat: float) -> tuple[str, float]:
    """
    Bucket the Engle-Granger t-statistic against MacKinnon critical values.
    Returns (label, upper-bound p-value).
    """
    if t_stat <= EG_CRIT_1PCT:
        return ("significant_1pct", 0.01)
    if t_stat <= EG_CRIT_5PCT:
        return ("significant_5pct", 0.05)
    if t_stat <= EG_CRIT_10PCT:
        return ("significant_10pct", 0.10)
    return ("not_significant", 0.50)


def _ou_half_life(series: list[float]) -> float | None:
    """
    Ornstein-Uhlenbeck half-life estimate in trading days.

    Fit discrete-time: Δe_t = a + b · e_{t-1} + ε_t, so b = -θ.
    half_life = ln(2) / θ. Returns None when θ ≤ 0 (no mean reversion)
    or the estimate falls outside [0.5, 365] days (typically means the
    fit is unstable).
    """
    n = len(series)
    if n < MIN_OBSERVATIONS // 3:
        return None
    de = [series[i] - series[i - 1] for i in range(1, n)]
    e_lag = series[:-1]
    beta, _ = _ols(e_lag, de)
    if beta >= 0:
        return None
    theta = -beta
    if theta <= 0:
        return None
    hl = math.log(2) / theta
    if hl < 0.5 or hl > 365:
        return None
    return round(hl, 1)


def _pair_diagnostics(
    a_ticker: str,
    b_ticker: str,
    a_closes: list[float],
    b_closes: list[float],
    min_correlation: float,
) -> dict:
    """
    Full diagnostic dict for one pair. Runs Engle-Granger in both
    directions and picks the one with the more negative ADF t-stat.
    Returns `{"considered": False, "reason": ...}` when the correlation
    prefilter rejects the pair.
    """
    n = len(a_closes)
    if n < MIN_OBSERVATIONS or len(b_closes) != n:
        return {
            "pair": f"{a_ticker}-{b_ticker}",
            "considered": False,
            "reason": f"insufficient_history ({n} bars)",
        }

    a_ret = _log_returns(a_closes)
    b_ret = _log_returns(b_closes)
    rho_returns = _pearson(a_ret, b_ret)
    if abs(rho_returns) < min_correlation:
        return {
            "pair": f"{a_ticker}-{b_ticker}",
            "considered": False,
            "reason": f"correlation_below_threshold (rho={rho_returns:.2f})",
            "correlation_log_returns": round(rho_returns, 3),
        }

    la = [math.log(c) for c in a_closes]
    lb = [math.log(c) for c in b_closes]

    beta_ba, alpha_ba = _ols(la, lb)
    resid_ba = [lb[i] - alpha_ba - beta_ba * la[i] for i in range(n)]
    t_ba = _adf_tstat(resid_ba)

    beta_ab, alpha_ab = _ols(lb, la)
    resid_ab = [la[i] - alpha_ab - beta_ab * lb[i] for i in range(n)]
    t_ab = _adf_tstat(resid_ab)

    if t_ba <= t_ab:
        dependent, independent = b_ticker, a_ticker
        beta, alpha = beta_ba, alpha_ba
        resid = resid_ba
        t_stat = t_ba
    else:
        dependent, independent = a_ticker, b_ticker
        beta, alpha = beta_ab, alpha_ab
        resid = resid_ab
        t_stat = t_ab

    bucket, p_upper = _eg_pvalue_bucket(t_stat)
    half_life = _ou_half_life(resid)

    mean_r = sum(resid) / len(resid)
    var_r = sum((r - mean_r) ** 2 for r in resid) / max(len(resid) - 1, 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 0.0
    z_current = ((resid[-1] - mean_r) / std_r) if std_r > 0 else 0.0

    # OS stability: refit beta on IS, apply to OS, compare residual stds.
    split = int(len(la) * OS_SPLIT_FRACTION)
    stability_label = "insufficient_os_sample"
    os_std_ratio: float | None = None
    if split >= MIN_OBSERVATIONS and (len(la) - split) >= 20:
        if dependent == b_ticker:
            x_is, y_is = la[:split], lb[:split]
            x_os, y_os = la[split:], lb[split:]
        else:
            x_is, y_is = lb[:split], la[:split]
            x_os, y_os = lb[split:], la[split:]
        b_is, a_is = _ols(x_is, y_is)
        r_is = [y_is[i] - a_is - b_is * x_is[i] for i in range(len(x_is))]
        r_os = [y_os[i] - a_is - b_is * x_os[i] for i in range(len(x_os))]
        m_is = sum(r_is) / len(r_is)
        m_os = sum(r_os) / len(r_os)
        s_is = math.sqrt(sum((r - m_is) ** 2 for r in r_is) / max(len(r_is) - 1, 1))
        s_os = math.sqrt(sum((r - m_os) ** 2 for r in r_os) / max(len(r_os) - 1, 1))
        if s_is > 0:
            os_std_ratio = round(s_os / s_is, 2)
            stability_label = "stable" if os_std_ratio < OS_INSTABILITY_THRESHOLD else "regime_shift"
        else:
            stability_label = "insufficient_os_sample"

    return {
        "pair": f"{dependent}-{independent}",
        "considered": True,
        "dependent": dependent,
        "independent": independent,
        "n_observations": n,
        "correlation_log_returns": round(rho_returns, 3),
        "hedge_ratio_beta": round(beta, 4),
        "intercept_alpha": round(alpha, 4),
        "adf_tstat": round(t_stat, 3),
        "cointegration_bucket": bucket,
        "pvalue_upper_bound": p_upper,
        "half_life_days": half_life,
        "spread_mean": round(mean_r, 4),
        "spread_std": round(std_r, 4),
        "z_current": round(z_current, 2),
        "os_std_ratio": os_std_ratio,
        "stability_label": stability_label,
    }


# ----- Public API -----

def run(
    basket: Iterable[str] | str,
    lookback_days: int = 252,
    min_correlation: float = 0.6,
    min_pvalue: float = 0.05,
    min_halflife_days: float = 2.0,
    max_halflife_days: float = 60.0,
    z_entry: float = 2.0,
    client: MassiveClient | None = None,
) -> dict:
    """Screen every pair in `basket` for cointegration and mean reversion.

    Args:
        basket: comma-separated string or iterable of tickers.
        lookback_days: trading-day window for the cointegration fit
            (default 252 = ~1 year).
        min_correlation: |Pearson rho| on log returns below which a pair
            is skipped (default 0.6).
        min_pvalue: EG cointegration p-value bucket ceiling for the
            tradeable flag (default 0.05).
        min_halflife_days: reject pairs with faster half-life (default 2).
        max_halflife_days: reject pairs with slower half-life (default 60).
        z_entry: minimum |z_current| to flag a pair tradeable (default 2.0).
        client: reuse an existing MassiveClient.
    """
    if isinstance(basket, str):
        tickers = [t.strip().upper() for t in basket.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in basket if t and t.strip()]
    tickers = list(dict.fromkeys(tickers))
    if len(tickers) < 2:
        raise ValueError("basket must contain at least 2 tickers")
    if lookback_days < MIN_OBSERVATIONS:
        raise ValueError(f"lookback_days must be >= {MIN_OBSERVATIONS}")
    if not (0.0 <= min_correlation <= 1.0):
        raise ValueError("min_correlation must be in [0, 1]")
    if min_halflife_days <= 0 or max_halflife_days <= min_halflife_days:
        raise ValueError("half-life bounds must satisfy 0 < min < max")

    client = client or MassiveClient()
    sources = _Sources()
    calendar_days = int(lookback_days * 1.6) + 21

    rows_by_ticker: dict[str, list[dict]] = {}
    for t in tickers:
        rows_by_ticker[t] = _fetch_daily_aggs(client, t, calendar_days, sources)
        time.sleep(0.02)

    dates, aligned = _align_series(rows_by_ticker)

    # Trim to the most recent `lookback_days` bars if we have more.
    if len(dates) > lookback_days:
        dates = dates[-lookback_days:]
        aligned = {t: closes[-lookback_days:] for t, closes in aligned.items()}

    tier_caveats: list[str] = []
    missing = [t for t in tickers if not rows_by_ticker.get(t)]
    if missing:
        tier_caveats.append(
            f"No daily-aggregates data returned for {', '.join(missing)}; those tickers dropped from the scan."
        )
    if len(dates) < MIN_OBSERVATIONS:
        tier_caveats.append(
            f"Only {len(dates)} aligned trading days across the basket; too few to trust any cointegration result."
        )

    universe = [t for t in tickers if t in aligned and t not in missing]
    diagnostics: list[dict] = []
    for a, b in combinations(universe, 2):
        diag = _pair_diagnostics(a, b, aligned[a], aligned[b], min_correlation)
        # Set tradeable flag on considered pairs
        if diag.get("considered"):
            tradeable_reasons: list[str] = []
            if diag["pvalue_upper_bound"] > min_pvalue:
                tradeable_reasons.append(f"cointegration weak (bucket={diag['cointegration_bucket']})")
            hl = diag.get("half_life_days")
            if hl is None:
                tradeable_reasons.append("half-life undefined (no mean reversion)")
            elif hl < min_halflife_days:
                tradeable_reasons.append(f"half-life {hl}d below {min_halflife_days}d floor")
            elif hl > max_halflife_days:
                tradeable_reasons.append(f"half-life {hl}d above {max_halflife_days}d ceiling")
            if abs(diag["z_current"]) < z_entry:
                tradeable_reasons.append(f"z {diag['z_current']:+.2f} below |{z_entry}| entry threshold")
            if diag["stability_label"] == "regime_shift":
                tradeable_reasons.append(f"regime shift (OS std {diag['os_std_ratio']}x IS)")
            diag["tradeable"] = len(tradeable_reasons) == 0
            diag["tradeable_rejections"] = tradeable_reasons
        diagnostics.append(diag)

    tradeable = [d for d in diagnostics if d.get("tradeable") is True]
    tradeable.sort(key=lambda d: abs(d["z_current"]), reverse=True)

    considered_but_rejected = [
        d for d in diagnostics
        if d.get("considered") is True and d.get("tradeable") is False
    ]
    considered_but_rejected.sort(key=lambda d: d["adf_tstat"])

    skipped_correlation = [d for d in diagnostics if d.get("considered") is False]

    tier_caveats.append(
        "Cointegration is a linear property that breaks in regime shifts (M&A, earnings, sector rotation). Half-life is the historical mean-reversion tempo, not a forecast."
    )
    if any(d.get("stability_label") == "regime_shift" for d in diagnostics if d.get("considered")):
        tier_caveats.append(
            "One or more pairs flagged 'regime_shift' via 70/30 out-of-sample residual std ratio. Treat those with extra scepticism even when the ADF bucket is significant."
        )

    return {
        "skill": "pairs-scanner",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "basket": tickers,
        "lookback_days": lookback_days,
        "n_aligned_bars": len(dates),
        "parameters": {
            "min_correlation": min_correlation,
            "min_pvalue": min_pvalue,
            "min_halflife_days": min_halflife_days,
            "max_halflife_days": max_halflife_days,
            "z_entry": z_entry,
            "os_split_fraction": OS_SPLIT_FRACTION,
            "os_instability_threshold": OS_INSTABILITY_THRESHOLD,
        },
        "n_pairs_total": len(diagnostics),
        "n_pairs_considered": sum(1 for d in diagnostics if d.get("considered")),
        "n_pairs_tradeable": len(tradeable),
        "tradeable": tradeable,
        "considered_but_rejected": considered_but_rejected,
        "skipped_correlation": skipped_correlation,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_hl(hl: float | None) -> str:
    return "n/a" if hl is None else f"{hl:.1f}d"


def _fmt_z(z: float | None) -> str:
    if z is None:
        return "n/a"
    return f"{z:+.2f}"


def _bucket_label(bucket: str) -> str:
    return {
        "significant_1pct": "1%",
        "significant_5pct": "5%",
        "significant_10pct": "10%",
        "not_significant": "n.s.",
    }.get(bucket, bucket)


def render(payload: dict) -> str:
    lines: list[str] = []
    n_basket = len(payload["basket"])
    n_pairs = payload["n_pairs_total"]
    n_trade = payload["n_pairs_tradeable"]
    n_bars = payload["n_aligned_bars"]

    lines.append(f"Pairs Scanner: {n_basket} names, {n_pairs} pairs, {n_trade} tradeable ({payload['as_of']})")
    lines.append(f"Lookback: {payload['lookback_days']} trading days ({n_bars} aligned bars) · Engle-Granger + OU half-life")
    lines.append("")

    if payload["tradeable"]:
        lines.append("TRADEABLE (widest spreads first)")
        headers = f"{'Pair':<14} {'Beta':>7} {'ADF-t':>7} {'p':>5} {'Half-life':>10} {'Z':>7} {'Stability':>14}"
        lines.append(headers)
        lines.append("-" * len(headers))
        for d in payload["tradeable"]:
            row = (
                f"{d['pair']:<14} "
                f"{d['hedge_ratio_beta']:>7.3f} "
                f"{d['adf_tstat']:>7.2f} "
                f"{_bucket_label(d['cointegration_bucket']):>5} "
                f"{_fmt_hl(d['half_life_days']):>10} "
                f"{_fmt_z(d['z_current']):>7} "
                f"{d['stability_label']:>14}"
            )
            lines.append(row)
    else:
        lines.append("TRADEABLE: none. All considered pairs failed one or more filters (see below).")

    if payload["considered_but_rejected"]:
        lines.append("")
        lines.append("CONSIDERED BUT REJECTED (sorted by ADF t-stat, most-stationary first)")
        headers = f"{'Pair':<14} {'ADF-t':>7} {'p':>5} {'Half-life':>10} {'Z':>7} {'Reason':<40}"
        lines.append(headers)
        lines.append("-" * len(headers))
        for d in payload["considered_but_rejected"]:
            reason = d.get("tradeable_rejections") or []
            reason_str = "; ".join(reason)[:40]
            lines.append(
                f"{d['pair']:<14} "
                f"{d['adf_tstat']:>7.2f} "
                f"{_bucket_label(d['cointegration_bucket']):>5} "
                f"{_fmt_hl(d['half_life_days']):>10} "
                f"{_fmt_z(d['z_current']):>7} "
                f"{reason_str:<40}"
            )

    if payload["skipped_correlation"]:
        lines.append("")
        lines.append(
            f"SKIPPED ({len(payload['skipped_correlation'])} pairs failed the |rho| >= "
            f"{payload['parameters']['min_correlation']} prefilter or lacked history)"
        )

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines)
