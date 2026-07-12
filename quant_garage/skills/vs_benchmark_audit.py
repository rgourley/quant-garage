"""
vs-benchmark-audit as an importable library function.

Takes a book (weights per ticker), computes the daily portfolio return
series, and runs the full tearsheet with deflated Sharpe correction
plus rolling IC vs a benchmark. Answers "is this book actually adding
alpha, honestly?"

    from quant_garage.skills.vs_benchmark_audit import run, render
    payload = run("NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25", benchmark="SPY")
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Iterable, Mapping

import numpy as np

from .. import MassiveClient, FetchError, today, utcnow_iso
from ..performance import tearsheet
from ..backtest import rolling_ic_series


N_TRADING_MIN = 60
_AGGS_CACHE: dict[str, list[dict]] = {}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _parse_positions(positions: Mapping[str, float] | str) -> dict[str, float]:
    if isinstance(positions, str):
        out: dict[str, float] = {}
        for chunk in positions.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"bad positions entry {chunk!r}")
            t, w = chunk.split("=", 1)
            out[t.strip().upper()] = float(w.strip())
        return out
    return {k.strip().upper(): float(v) for k, v in positions.items()}


def _fetch_daily(client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources) -> list[dict]:
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&limit=50000"
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
        if ts_ms is None or close is None or close <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily aggs for {ticker}",
    )
    return rows


def run(
    positions: Mapping[str, float] | str,
    benchmark: str = "SPY",
    lookback_days: int = 504,
    ic_window: int = 63,
    n_trials_dsr: int = 1,
    client: MassiveClient | None = None,
) -> dict:
    """Full performance audit of a book vs benchmark.

    Args:
        positions: dict {ticker: weight} or 'TKR=w,TKR2=w' string.
        benchmark: benchmark ticker. Default SPY.
        lookback_days: window in trading days.
        ic_window: rolling IC window (default 63d = one quarter).
        n_trials_dsr: multiple-testing correction n_trials for DSR.
            Default 1 (no search correction). Pass N if this book was
            picked from N candidates.
        client: reuse an existing MassiveClient.
    """
    weights = _parse_positions(positions)
    if not weights:
        raise ValueError("provide at least one position")
    total_w = sum(abs(w) for w in weights.values())
    if total_w <= 0:
        raise ValueError("weights sum to zero")
    if lookback_days < 60:
        raise ValueError("lookback_days must be at least 60")

    client = client or MassiveClient()
    sources = _Sources()
    calendar_days = int(lookback_days * 1.6) + 21

    tickers = list(weights.keys())
    per_ticker_returns: dict[str, dict[str, float]] = {}
    for t in tickers + [benchmark]:
        rows = _fetch_daily(client, t, calendar_days, sources)
        if len(rows) < N_TRADING_MIN:
            continue
        recent = rows[-lookback_days - 1:] if len(rows) > lookback_days + 1 else rows
        r_by_date: dict[str, float] = {}
        for i in range(1, len(recent)):
            p0 = recent[i - 1]["close"]
            p1 = recent[i]["close"]
            if p0 > 0 and p1 > 0:
                r_by_date[recent[i]["date"]] = np.log(p1 / p0)
        per_ticker_returns[t] = r_by_date
        time.sleep(0.02)

    if benchmark not in per_ticker_returns:
        raise ValueError(f"benchmark {benchmark} history unavailable")

    missing = [t for t in tickers if t not in per_ticker_returns]
    used_tickers = [t for t in tickers if t in per_ticker_returns]
    if not used_tickers:
        raise ValueError("no positions had usable history")

    date_sets = [set(per_ticker_returns[t].keys()) for t in used_tickers + [benchmark]]
    common = sorted(set.intersection(*date_sets))
    if len(common) < N_TRADING_MIN:
        raise ValueError(f"only {len(common)} common trading days across the book + benchmark")

    tier_caveats: list[str] = []
    if missing:
        tier_caveats.append(
            f"Positions dropped due to missing history: {', '.join(missing)}."
        )

    # Portfolio daily returns (weighted average of position returns)
    port_returns = np.zeros(len(common))
    used_weights = {t: weights[t] for t in used_tickers}
    weight_sum = sum(abs(w) for w in used_weights.values())
    for t in used_tickers:
        w = used_weights[t] / weight_sum  # renormalize
        r = np.array([per_ticker_returns[t][d] for d in common])
        port_returns += w * r
    bench_returns = np.array([per_ticker_returns[benchmark][d] for d in common])

    tear = tearsheet(port_returns.tolist(), benchmark=bench_returns.tolist(), dsr_n_trials=n_trials_dsr)

    # Rolling IC of portfolio returns vs benchmark returns
    ic_series = rolling_ic_series(port_returns.tolist(), bench_returns.tolist(), window=ic_window)
    ic_mean = None
    ic_std = None
    if ic_series:
        ics = [e["ic_pearson"] for e in ic_series if e["ic_pearson"] is not None]
        if ics:
            ic_mean = round(float(np.mean(ics)), 4)
            ic_std = round(float(np.std(ics, ddof=1)), 4) if len(ics) > 1 else 0.0

    # Verdict
    dsr_sig = tear.get("deflated_sharpe_significant", False)
    ann_ret = tear.get("annualized_return", 0)
    beta = tear.get("beta", 0)
    alpha = tear.get("alpha_annualized", 0)
    if dsr_sig and alpha > 0.02:
        verdict = "real_alpha"
    elif alpha > 0 and tear.get("sharpe", 0) > 0.5:
        verdict = "possibly_alpha"
    elif beta > 0.8 and abs(alpha) < 0.02:
        verdict = "essentially_beta"
    elif ann_ret < 0:
        verdict = "underperforming"
    else:
        verdict = "no_edge_evident"

    if not dsr_sig:
        tier_caveats.append(
            "Naive Sharpe overstates skill under multiple-testing bias. "
            "Pass n_trials_dsr = N if this book was picked from N candidates."
        )

    return {
        "skill": "vs-benchmark-audit",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "positions_requested": weights,
        "positions_used": used_weights,
        "benchmark": benchmark,
        "lookback_days": int(lookback_days),
        "ic_window": int(ic_window),
        "n_obs_aligned": int(len(common)),
        "n_trials_dsr": int(n_trials_dsr),
        "tearsheet": tear,
        "rolling_ic_mean": ic_mean,
        "rolling_ic_std": ic_std,
        "verdict": verdict,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_VERDICT_TAG = {
    "real_alpha": "REAL ALPHA (deflated Sharpe significant)",
    "possibly_alpha": "POSSIBLY ALPHA (positive but not DSR-significant)",
    "essentially_beta": "ESSENTIALLY BETA (returns explained by benchmark)",
    "underperforming": "UNDERPERFORMING",
    "no_edge_evident": "NO EDGE EVIDENT",
}


def _fmt_pct(x, signed=False, dec=2):
    if x is None:
        return "n/a"
    sign = "+" if signed and x >= 0 else ""
    return f"{sign}{x*100:.{dec}f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    tear = payload["tearsheet"]
    bench = payload["benchmark"]

    lines.append(
        f"vs-benchmark audit: {len(payload['positions_used'])} positions vs {bench} · "
        f"{payload['n_obs_aligned']} obs · lookback {payload['lookback_days']}d"
    )
    lines.append(f"Verdict: {_VERDICT_TAG.get(payload['verdict'], payload['verdict'])}")
    lines.append("")

    lines.append("Return statistics")
    lines.append(f"  CAGR:              {_fmt_pct(tear['cagr'], signed=True)}")
    lines.append(f"  Annualized vol:    {_fmt_pct(tear['annualized_vol'])}")
    lines.append(f"  Sharpe (naive):    {tear['sharpe']:>6.2f}")
    dsr = tear.get("deflated_sharpe_pvalue")
    dsr_sig = tear.get("deflated_sharpe_significant")
    dsr_s = f"{dsr:.4f}" if dsr is not None else "n/a"
    lines.append(
        f"  Deflated Sharpe p: {dsr_s}"
        + (" (significant at 5%)" if dsr_sig else " (not significant)")
    )
    if tear.get("sortino") is not None:
        lines.append(f"  Sortino:           {tear['sortino']:>6.2f}")
    if tear.get("calmar") is not None:
        lines.append(f"  Calmar:            {tear['calmar']:>6.2f}")
    lines.append(f"  Max drawdown:      {_fmt_pct(tear['max_drawdown'])}")
    lines.append(f"  Ulcer index:       {tear['ulcer_index']:>6.2f}")
    if tear.get("tail_ratio") is not None:
        lines.append(f"  Tail ratio:        {tear['tail_ratio']:>6.2f}")
    if tear.get("profit_factor") is not None:
        lines.append(f"  Profit factor:     {tear['profit_factor']:>6.2f}")
    lines.append(f"  Hit rate (daily):  {_fmt_pct(tear['hit_rate'], dec=1)}")
    if tear.get("monthly_hit_rate") is not None:
        lines.append(f"  Hit rate (monthly): {_fmt_pct(tear['monthly_hit_rate'], dec=1)}")
    lines.append("")

    lines.append(f"vs {bench}")
    if tear.get("beta") is not None:
        lines.append(f"  Beta:              {tear['beta']:>6.2f}")
    if tear.get("alpha_annualized") is not None:
        lines.append(f"  Alpha (annualized): {_fmt_pct(tear['alpha_annualized'], signed=True)}")
    if tear.get("tracking_error_annualized") is not None:
        lines.append(f"  Tracking error:    {_fmt_pct(tear['tracking_error_annualized'])}")
    if payload.get("rolling_ic_mean") is not None:
        lines.append(
            f"  Rolling {payload['ic_window']}d IC vs {bench}: "
            f"mean {payload['rolling_ic_mean']:+.3f}, σ {payload['rolling_ic_std']:.3f}"
        )
    lines.append("")

    take_parts: list[str] = []
    if payload["verdict"] == "real_alpha":
        take_parts.append(
            f"Deflated Sharpe is significant (p={dsr:.4f}) and alpha is "
            f"{_fmt_pct(tear.get('alpha_annualized'), signed=True)}. "
            "This book adds honest alpha above the benchmark."
        )
    elif payload["verdict"] == "possibly_alpha":
        take_parts.append(
            "Positive alpha but the DSR-corrected p-value doesn't clear 5%. "
            "Signal may be there; sample is too small or too noisy to say honestly."
        )
    elif payload["verdict"] == "essentially_beta":
        take_parts.append(
            f"Book behaves like a leveraged benchmark (beta {tear.get('beta'):.2f}, "
            f"alpha {_fmt_pct(tear.get('alpha_annualized'), signed=True)}). "
            "You could replicate this exposure with SPY at lower cost."
        )
    elif payload["verdict"] == "underperforming":
        take_parts.append(
            f"CAGR {_fmt_pct(tear['cagr'], signed=True)} < 0. Book is losing money "
            "before considering the benchmark cost of capital."
        )
    else:
        take_parts.append(
            "No clear edge over the benchmark; naive Sharpe and DSR both muted."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines).rstrip()
