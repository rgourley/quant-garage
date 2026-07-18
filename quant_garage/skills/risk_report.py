"""
risk-report as an importable library function.

Book in → full PM-facing risk report: annualized vol/return/Sharpe,
beta/TE/alpha vs benchmark, VaR + ES, max DD, worst-N stress days with
per-name attribution, per-position variance contribution, concentration.

    from quant_garage.skills.risk_report import run, render
    payload = run("NVDA=0.25,AMZN=0.25,GOOGL=0.25,META=0.25", benchmark="SPY")
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Iterable, Mapping

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    annualized_vol,
    ewma_vol,
    correlation_matrix,
    covariance_matrix,
    shrink_correlation,
    historical_var,
    parametric_var,
    expected_shortfall,
    max_drawdown,
    portfolio_returns,
    beta_and_tracking,
    position_variance_contributions,
    concentration_stats,
    worst_n_days,
    simulate_correlated_paths,
    percentile_summary,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()

# Per-run cache: one daily-aggs pull per ticker.
_AGGS_CACHE: dict[str, list[dict]] = {}

# Tickers that came back empty because the tier throttled us, not because
# history is missing. Tracked so the caller can surface a loud caveat
# rather than silently risk-reporting a truncated book.
_RATE_LIMITED: set[str] = set()

_RATE_LIMIT_COOLDOWN_SECONDS = 13

N_TRADING_MIN = 60


def _vol(returns, method: str, ewma_lambda: float = 0.94) -> float:
    """Dispatch to realized or EWMA annualized vol. Raises on unknown method."""
    if method == "realized":
        return float(annualized_vol(returns))
    if method == "ewma":
        return float(ewma_vol(returns, lambda_=ewma_lambda))
    raise ValueError(f"vol_method must be 'realized' or 'ewma', got {method!r}")


# ----- HTTP -----

def fetch_daily_aggs(ticker: str, lookback_days: int) -> list[dict]:
    """Pull daily aggregates for `ticker` covering lookback_days trading days.

    Overshoots by 1.6x in calendar days to handle weekends/holidays. Returns
    a list of {date, close} records sorted ascending by date.
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    end = TODAY
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} after cooldown: {exc}",
                  file=sys.stderr)
            _RATE_LIMITED.add(ticker)
            _AGGS_CACHE[ticker] = []
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        _AGGS_CACHE[ticker] = []
        return []

    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    return rows


# ----- Returns + alignment -----

def daily_log_returns(rows: list[dict]) -> dict[str, float]:
    """Compute close-to-close daily log returns. Returns {date: return}."""
    out: dict[str, float] = {}
    prev_close = None
    for r in rows:
        c = r["close"]
        if prev_close is not None and prev_close > 0 and c > 0:
            out[r["date"]] = float(np.log(c / prev_close))
        prev_close = c
    return out


def align_returns(per_ticker: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, list[float]]]:
    """Align per-ticker {date: return} dicts to the intersection of dates.

    Returns (sorted_dates, {ticker: [returns aligned to those dates]}).
    """
    if not per_ticker:
        return [], {}
    common_dates: set[str] | None = None
    for t, r in per_ticker.items():
        s = set(r.keys())
        common_dates = s if common_dates is None else common_dates & s
    common_dates = sorted(common_dates or [])
    aligned = {t: [per_ticker[t][d] for d in common_dates] for t in per_ticker}
    return common_dates, aligned


# ----- Book parsing -----

def parse_positions_string(raw: str) -> dict[str, float]:
    """Parse 'NVDA=0.25,AMZN=0.25,...' into {ticker: weight}."""
    out: dict[str, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"--positions entry missing '=': {chunk!r}")
        t, v = chunk.split("=", 1)
        try:
            out[t.strip().upper()] = float(v.strip())
        except ValueError as exc:
            raise SystemExit(f"--positions value not a number: {chunk!r}") from exc
    return out


def parse_book_file(path: str) -> tuple[dict[str, float], float]:
    """Read a book JSON file. Returns (weights_dict, cash_weight)."""
    with open(path, "r") as f:
        doc = json.load(f)
    positions = doc.get("positions") or []
    cash_weight = float(doc.get("cash_weight", 0.0))

    # Pass 1: detect whether shares+price mode or weight mode dominates.
    has_value_mode = any(
        ("shares" in p and "price" in p) for p in positions
    )
    weights: dict[str, float] = {}

    if has_value_mode:
        # Compute total value across rows that provide shares+price; cash
        # weight is treated as a relative-value share too.
        total_value = 0.0
        values: dict[str, float] = {}
        for p in positions:
            t = str(p["ticker"]).upper()
            if "shares" in p and "price" in p:
                v = float(p["shares"]) * float(p["price"])
            elif "weight" in p:
                # Mixed mode: weight rows get folded in at face value.
                v = float(p["weight"])
            else:
                raise SystemExit(
                    f"book row for {t} needs either shares+price or weight"
                )
            values[t] = v
            total_value += v
        # Cash is also a value if specified that way; assume cash_weight is
        # the final fractional cash (0..1) and ignore in the value sum.
        if total_value <= 0:
            raise SystemExit("book: total position value <= 0")
        # If cash_weight specified, scale risky positions to (1 - cash_weight).
        risky_share = max(0.0, 1.0 - cash_weight)
        for t, v in values.items():
            weights[t] = (v / total_value) * risky_share
    else:
        for p in positions:
            t = str(p["ticker"]).upper()
            if "weight" not in p:
                raise SystemExit(
                    f"book row for {t} missing 'weight' (and no shares+price)"
                )
            weights[t] = float(p["weight"])
    return weights, cash_weight


def parse_confidences(raw: str) -> list[float]:
    out: list[float] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            v = float(chunk)
        except ValueError as exc:
            raise SystemExit(f"--var-confidence value not a number: {chunk!r}") from exc
        if not (0.0 < v < 1.0):
            raise SystemExit(f"--var-confidence must be in (0, 1), got {v}")
        out.append(v)
    if not out:
        raise SystemExit("--var-confidence: need at least one value")
    return out


# ----- Formatting -----

def fmt_pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    val = x * 100.0
    if abs(val) < 0.5 * (10 ** -decimals):
        val = 0.0
    if signed:
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.{decimals}f}%"
    return f"{val:.{decimals}f}%"


# ----- Public API -----

def run(
    positions: Mapping[str, float] | str | None = None,
    book: str | None = None,
    benchmark: str = "SPY",
    lookback_days: int = 252,
    var_confidence: Iterable[float] | str = "0.95,0.99",
    stress_n: int = 5,
    shrinkage: float = 0.05,
    vol_method: str = "realized",
    ewma_lambda: float = 0.94,
    mc: bool = False,
    mc_simulation_days: int = 20,
    mc_n_paths: int = 10_000,
    mc_tail: str = "normal",
    mc_tail_df: float = 4.0,
    mc_seed: int | None = 42,
    client_: MassiveClient | None = None,
) -> dict:
    """Compute a full risk report for a fixed book.

    Provide EITHER positions (dict {ticker: weight} or "TKR=0.25,TKR2=0.25")
    OR book (path to a JSON file per examples/sample-book.json).
    """
    global client, NOW_UTC, TODAY
    client = client_ or MassiveClient()
    NOW_UTC = datetime.now(timezone.utc)
    TODAY = today()

    if positions is None and book is None:
        raise ValueError("provide positions=... or book=...")
    if isinstance(positions, str):
        positions_arg: str | None = positions
    elif positions is not None:
        positions_arg = ",".join(f"{k}={v}" for k, v in positions.items())
    else:
        positions_arg = None
    if isinstance(var_confidence, str):
        varconf_arg = var_confidence
    else:
        varconf_arg = ",".join(str(v) for v in var_confidence)

    if vol_method not in ("realized", "ewma"):
        raise ValueError(f"vol_method must be 'realized' or 'ewma', got {vol_method!r}")

    args = SimpleNamespace(
        positions=positions_arg,
        book=book,
        benchmark=benchmark,
        lookback_days=lookback_days,
        var_confidence=varconf_arg,
        stress_n=stress_n,
        shrinkage=shrinkage,
    )

    # --- Build the book ---
    if args.positions:
        weights = parse_positions_string(args.positions)
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
    else:
        weights, cash_weight = parse_book_file(args.book)

    if not weights:
        raise ValueError("no positions parsed")

    gross = float(sum(abs(w) for w in weights.values()))
    if gross > 1.0 + 1e-6:
        raise ValueError(
            f"position weights sum to {gross:.4f}, exceeds 1.0 (no leverage support)"
        )

    bench = args.benchmark.strip().upper()
    confidences = parse_confidences(args.var_confidence)
    if args.stress_n <= 0:
        raise ValueError("stress_n must be > 0")
    if args.lookback_days <= 0:
        raise ValueError("lookback_days must be > 0")

    tickers_req = sorted(weights.keys())
    print(
        f"Risk-report on {len(tickers_req)} positions: {','.join(tickers_req)} ; "
        f"benchmark={bench}, lookback={args.lookback_days}d, "
        f"VaR @ {confidences}, stress-N={args.stress_n}",
        file=sys.stderr,
    )


    # ----- Data pull -----

    sources: list[dict] = []
    per_ticker_returns: dict[str, dict[str, float]] = {}
    tier_caveats: list[str] = []
    excluded: list[dict] = []

    # Bench is pulled as just another ticker; it's separated later before the
    # book-level math.
    pull_set = list(tickers_req)
    if bench not in pull_set:
        pull_set.append(bench)

    for t in pull_set:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows = fetch_daily_aggs(t, args.lookback_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily aggs for {t}",
        })
        returns = daily_log_returns(rows)
        if len(returns) < N_TRADING_MIN:
            if t == bench:
                print(
                    f"ERROR: benchmark {bench} has only {len(returns)} aligned days "
                    f"(min {N_TRADING_MIN}); cannot compute beta or tracking",
                    file=sys.stderr,
                )
                sys.exit(1)
            excluded.append({
                "ticker": t,
                "reason": "insufficient_history",
                "n_obs": len(returns),
                "min_required": N_TRADING_MIN,
            })
            tier_caveats.append(
                f"{t}: insufficient history ({len(returns)} days < {N_TRADING_MIN} min); "
                f"excluded from risk-report"
            )
            continue
        per_ticker_returns[t] = returns
        time.sleep(0.05)

    # Position tickers that survived the history filter, in book order
    position_tickers = [t for t in tickers_req if t in per_ticker_returns]
    if len(position_tickers) == 0:
        print("ERROR: no position tickers passed the history filter", file=sys.stderr)
        sys.exit(1)
    if bench not in per_ticker_returns:
        print(f"ERROR: benchmark {bench} missing returns; aborting", file=sys.stderr)
        sys.exit(1)

    rate_limited_in_pull = [t for t in pull_set if t in _RATE_LIMITED]
    if rate_limited_in_pull:
        names = ", ".join(rate_limited_in_pull)
        tier_caveats.append(
            f"RATE LIMIT: {len(rate_limited_in_pull)} of {len(pull_set)} tickers "
            f"({names}) returned no data because the API rate limit was hit, not "
            f"because history is missing. Risk metrics were computed on the "
            f"remaining names; the throttled positions are NOT in the reported "
            f"beta, VaR, or drawdown. Free Basic tier caps at 5 calls/min: "
            f"upgrade to Stocks Starter or run in smaller batches."
        )

    # Drop any excluded weight; surface in caveats. Renormalize? No — preserve
    # the operator's intent. Excluded names just don't contribute to risk math.
    weights_used = {t: weights[t] for t in position_tickers}
    excluded_weight_total = sum(weights[t] for t in tickers_req if t not in position_tickers)
    if excluded_weight_total > 1e-9:
        tier_caveats.append(
            f"{excluded_weight_total*100:.1f}% of book weight excluded due to "
            f"insufficient history; cash bucket effectively grows by that amount"
        )

    # Trim each ticker's series to the last `lookback_days` observations
    for t in list(per_ticker_returns.keys()):
        series = per_ticker_returns[t]
        if len(series) > args.lookback_days:
            sorted_keys = sorted(series.keys())
            keep = set(sorted_keys[-args.lookback_days:])
            per_ticker_returns[t] = {d: v for d, v in series.items() if d in keep}

    aligned_dates, aligned_returns = align_returns(per_ticker_returns)
    if len(aligned_dates) < N_TRADING_MIN:
        print(
            f"ERROR: after aligning, only {len(aligned_dates)} overlapping trading "
            f"days remain; need at least {N_TRADING_MIN}",
            file=sys.stderr,
        )
        sys.exit(1)


    # ----- Per-name stats + cov matrix -----

    # Per-name annualized vol over the aligned window
    name_vols: dict[str, float] = {}
    for t in position_tickers:
        r = np.asarray(aligned_returns[t], dtype=float)
        name_vols[t] = round(_vol(r, vol_method, ewma_lambda), 4)

    # Correlation + cov on the position panel only (benchmark not part of cov)
    position_panel = {t: aligned_returns[t] for t in position_tickers}
    if len(position_tickers) >= 2:
        ordered_tickers, raw_corr = correlation_matrix(position_panel)
        shrunk = shrink_correlation(raw_corr, args.shrinkage)
        cov = covariance_matrix(name_vols, shrunk, ordered_tickers)
    else:
        # Single-name book: cov is 1x1
        ordered_tickers = list(position_tickers)
        sigma = name_vols[ordered_tickers[0]]
        cov = np.array([[sigma * sigma]], dtype=float)


    # ----- Portfolio returns (daily) -----

    # portfolio_returns from lib operates on aligned panels.
    panel_for_port = {t: np.asarray(aligned_returns[t], dtype=float) for t in position_tickers}
    port_ret = portfolio_returns(weights_used, panel_for_port)
    bench_ret = np.asarray(aligned_returns[bench], dtype=float)


    # ----- Portfolio stats -----

    port_vol_ann = _vol(port_ret, vol_method, ewma_lambda)
    port_mean_ann = float(np.mean(port_ret)) * 252.0
    sharpe_naive = port_mean_ann / port_vol_ann if port_vol_ann > 0 else 0.0

    bt = beta_and_tracking(port_ret, bench_ret)


    # ----- VaR + ES at each confidence -----

    var_by_conf: dict[str, dict] = {}
    port_mean_daily = float(np.mean(port_ret))
    port_std_daily = float(np.std(port_ret, ddof=1))
    for c in confidences:
        hv = historical_var(port_ret, c)
        pv = parametric_var(port_mean_daily, port_std_daily, c)
        es_hist = expected_shortfall(port_ret, c)
        # Parametric ES via the Gaussian closed form:
        #   ES_param = -(mean - std * phi(z) / (1 - confidence))
        # where z = Phi^-1(1 - confidence) (the lower-tail quantile).
        from scipy import stats as _stats
        z_lo = float(_stats.norm.ppf(1.0 - c))
        phi_z = float(_stats.norm.pdf(z_lo))
        es_param = float(-(port_mean_daily - port_std_daily * phi_z / (1.0 - c)))
        var_by_conf[f"{c:.2f}"] = {
            "historical": round(float(hv), 6),
            "parametric": round(float(pv), 6),
            "expected_shortfall_historical": round(float(es_hist), 6),
            "expected_shortfall_parametric": round(float(es_param), 6),
        }


    # ----- Max drawdown of the portfolio NAV -----

    # Reconstruct NAV from cumulative log returns.
    nav = np.exp(np.cumsum(port_ret))
    nav = np.concatenate([[1.0], nav])  # start at 1.0 on day 0
    nav_dates = [aligned_dates[0]] + aligned_dates  # day 0 borrowed from first date
    md = max_drawdown(nav)

    mdd_payload = {
        "peak_date": nav_dates[md["peak_index"]],
        "trough_date": nav_dates[md["trough_index"]],
        "drawdown_pct": round(float(md["drawdown_pct"]), 6),
        "duration_days": int(md["duration_periods"]),
        "recovered": bool(md["recovered"]),
        "recovery_date": (
            nav_dates[md["recovery_index"]] if md["recovery_index"] is not None else None
        ),
    }


    # ----- Worst-N stress days -----

    stress = worst_n_days(
        port_ret,
        aligned_dates,
        n=args.stress_n,
        per_name_returns={t: np.asarray(aligned_returns[t], dtype=float) for t in position_tickers},
        weights=weights_used,
    )
    # Annotate each stress day with the benchmark's return on that same date.
    date_to_idx = {d: i for i, d in enumerate(aligned_dates)}
    for s in stress:
        idx = date_to_idx.get(s["date"])
        if idx is not None:
            s["benchmark_return_pct"] = float(bench_ret[idx])


    # ----- Position variance contributions + per-position risk -----

    contribs = position_variance_contributions(weights_used, cov, ordered_tickers)

    # Per-position beta vs benchmark (single-regression each)
    position_risk: dict[str, dict] = {}
    for t in position_tickers:
        r = np.asarray(aligned_returns[t], dtype=float)
        try:
            bt_single = beta_and_tracking(r, bench_ret)
            b_to_bench = round(float(bt_single["beta"]), 4)
        except ValueError:
            b_to_bench = None
        position_risk[t] = {
            "weight": round(float(weights_used[t]), 6),
            "vol_annualized": round(float(name_vols[t]), 4),
            "variance_contribution_pct": round(float(contribs.get(t, 0.0)), 6),
            "beta_to_benchmark": b_to_bench,
        }


    # ----- Concentration -----

    conc = concentration_stats(weights_used)


    # ----- Always-on caveats -----

    tier_caveats.append(
        f"Historical VaR uses {len(aligned_dates)}-day window; tail estimates noisy when n={len(aligned_dates)}"
    )
    tier_caveats.append(
        "Parametric VaR assumes normality; almost always underestimates fat-tailed loss"
    )
    tier_caveats.append(
        f"Beta computed vs {bench}; results differ on a different benchmark"
    )
    tier_caveats.append(
        "Risk metrics are descriptive of past behavior, not predictions"
    )


    # ----- Assemble payload -----

    positions_out = [
        {"ticker": t, "weight": round(float(weights_used[t]), 6)}
        for t in position_tickers
    ]
    # Also keep excluded weights for transparency
    positions_excluded = [
        {"ticker": t, "requested_weight": round(float(weights[t]), 6)}
        for t in tickers_req if t not in position_tickers
    ]

    book_block = {
        "positions": positions_out,
        "positions_excluded": positions_excluded,
        "gross_exposure": round(float(sum(abs(w) for w in weights_used.values())), 6),
        "cash_weight": round(float(cash_weight + excluded_weight_total), 6),
        "n_positions": len(positions_out),
    }

    # ----- Monte Carlo path VaR (optional) -----

    mc_block = None
    if mc:
        if mc_tail not in ("normal", "student_t"):
            raise ValueError(f"mc_tail must be 'normal' or 'student_t', got {mc_tail!r}")
        weight_vec = np.array([weights_used[t] for t in ordered_tickers], dtype=float)
        mean_daily = np.array(
            [float(np.mean(aligned_returns[t])) for t in ordered_tickers],
            dtype=float,
        )
        paths = simulate_correlated_paths(
            mean_daily=mean_daily,
            cov_annualized=cov,
            n_paths=int(mc_n_paths),
            n_days=int(mc_simulation_days),
            tail=mc_tail,
            df=float(mc_tail_df),
            seed=mc_seed,
        )
        # Sum daily returns to a portfolio cumulative return per path.
        # port_daily[p, d] = weight_vec @ paths[p, d, :]
        port_daily = np.einsum("pdk,k->pd", paths, weight_vec)
        cum_ret = port_daily.sum(axis=1)  # (n_paths,) total return over horizon
        # Path-max drawdown per path
        nav_paths = np.cumprod(np.exp(port_daily), axis=1)  # (n_paths, n_days)
        running_peak = np.maximum.accumulate(nav_paths, axis=1)
        dd = (nav_paths / running_peak) - 1.0
        max_dd_per_path = dd.min(axis=1)

        summary = percentile_summary(cum_ret)
        dd_summary = percentile_summary(max_dd_per_path)

        var_by_conf_mc: dict[str, dict] = {}
        for c in confidences:
            var_c = float(-np.quantile(cum_ret, 1.0 - c))
            below = cum_ret[cum_ret <= -var_c]
            es_c = float(-np.mean(below)) if below.size > 0 else var_c
            var_by_conf_mc[f"{c:.2f}"] = {
                "path_var": round(var_c, 6),
                "path_expected_shortfall": round(es_c, 6),
            }

        p_loss_5 = float(np.mean(cum_ret < -0.05))
        p_loss_10 = float(np.mean(cum_ret < -0.10))
        p_loss_20 = float(np.mean(cum_ret < -0.20))
        p_gain_5 = float(np.mean(cum_ret > 0.05))
        p_gain_10 = float(np.mean(cum_ret > 0.10))

        mc_block = {
            "enabled": True,
            "n_paths": int(mc_n_paths),
            "simulation_days": int(mc_simulation_days),
            "tail": mc_tail,
            "tail_df": float(mc_tail_df) if mc_tail == "student_t" else None,
            "seed": mc_seed,
            "cumulative_return_distribution": {
                "mean": round(summary["mean"], 6),
                "std": round(summary["std"], 6),
                "p5": round(summary["p5"], 6),
                "p10": round(summary["p10"], 6),
                "p25": round(summary["p25"], 6),
                "p50": round(summary["p50"], 6),
                "p75": round(summary["p75"], 6),
                "p90": round(summary["p90"], 6),
                "p95": round(summary["p95"], 6),
            },
            "max_drawdown_distribution": {
                "p5": round(dd_summary["p5"], 6),
                "p10": round(dd_summary["p10"], 6),
                "p25": round(dd_summary["p25"], 6),
                "p50": round(dd_summary["p50"], 6),
                "p75": round(dd_summary["p75"], 6),
            },
            "path_var_by_confidence": var_by_conf_mc,
            "loss_probabilities": {
                "P_loss_gt_5pct": round(p_loss_5, 4),
                "P_loss_gt_10pct": round(p_loss_10, 4),
                "P_loss_gt_20pct": round(p_loss_20, 4),
            },
            "gain_probabilities": {
                "P_gain_gt_5pct": round(p_gain_5, 4),
                "P_gain_gt_10pct": round(p_gain_10, 4),
            },
        }
        tier_caveats.append(
            f"Monte Carlo: {mc_n_paths:,} paths × {mc_simulation_days}-day horizon under "
            f"{mc_tail} innovations (df={mc_tail_df if mc_tail == 'student_t' else 'n/a'}). "
            "Simulates from the fitted covariance; does not model regime shifts, jumps, "
            "or correlation breaks beyond what the sample already captured."
        )

    stats_block = {
        "annualized_vol": round(float(port_vol_ann), 6),
        "annualized_return": round(float(port_mean_ann), 6),
        "sharpe_naive": round(float(sharpe_naive), 4),
        "beta": round(float(bt["beta"]), 4),
        "alpha_annualized": round(float(bt["alpha_annualized"]), 6),
        "tracking_error_annualized": round(float(bt["tracking_error_annualized"]), 6),
        "correlation_to_benchmark": round(float(bt["correlation"]), 4),
        "r_squared": round(float(bt["r_squared"]), 4),
    }

    payload = {
        "skill": "risk-report",
        "as_of": TODAY.isoformat(),
        "fetched_at": NOW_UTC.isoformat(),
        "book": book_block,
        "lookback_days": int(args.lookback_days),
        "n_obs_aligned": int(len(aligned_dates)),
        "benchmark": bench,
        "vol_method": vol_method,
        "ewma_lambda": ewma_lambda if vol_method == "ewma" else None,
        "stats": stats_block,
        "var": {
            "lookback_days": int(len(aligned_dates)),
            "by_confidence": var_by_conf,
        },
        "max_drawdown": mdd_payload,
        "stress": {
            "mode": "worst_n_days",
            "n": int(args.stress_n),
            "scenarios": stress,
        },
        "position_risk": position_risk,
        "concentration": conc,
        "monte_carlo": mc_block,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }
    return payload


# ----- Renderer -----

def _format_pct_signed(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "n/a"
    val = x * 100.0
    sign = "+" if val >= 0 else "-"
    return f"{sign}{abs(val):.{decimals}f}%"


def render(payload: dict) -> str:
    book = payload["book"]
    stats_b = payload["stats"]
    var_b = payload["var"]
    mdd = payload["max_drawdown"]
    stress_b = payload["stress"]
    pos_risk = payload["position_risk"]
    conc_b = payload["concentration"]
    bench = payload["benchmark"]

    tickers = [p["ticker"] for p in book["positions"]]
    lines: list[str] = []

    # Header
    lines.append(
        f"Risk Report — {', '.join(tickers)} (gross {book['gross_exposure']*100:.1f}%)"
    )
    vol_tag = payload.get("vol_method", "realized")
    if vol_tag == "ewma":
        lam = payload.get("ewma_lambda")
        vol_desc = f"EWMA vol (λ={lam})"
    else:
        vol_desc = "realized vol"
    lines.append(
        f"Lookback {payload['lookback_days']}d · Benchmark {bench} · {vol_desc} · As of {payload['as_of']}"
    )
    lines.append("")

    # Portfolio statistics
    lines.append("Portfolio statistics:")
    lines.append(f"  Annualized vol         {fmt_pct(stats_b['annualized_vol'])}")
    lines.append(f"  Annualized return      {fmt_pct(stats_b['annualized_return'], signed=True)}")
    lines.append(f"  Sharpe (naive)         {stats_b['sharpe_naive']:>5.2f}")
    lines.append(f"  Beta vs {bench:<6}        {stats_b['beta']:>5.2f}")
    lines.append(f"  Tracking error         {fmt_pct(stats_b['tracking_error_annualized'])}")
    lines.append(f"  R² vs {bench:<6}         {stats_b['r_squared']:>5.2f}")
    lines.append("")

    # VaR table
    confs = sorted(var_b["by_confidence"].keys(), key=lambda x: float(x))
    lines.append("Value at Risk (1-day):")
    headers = " " * 24 + "  ".join(f"{int(float(c)*100):>5d}%" for c in confs)
    lines.append(headers)
    row_hist = "  Historical              " + "  ".join(
        _format_pct_signed(-var_b["by_confidence"][c]["historical"]).rjust(6) for c in confs
    )
    row_param = "  Parametric              " + "  ".join(
        _format_pct_signed(-var_b["by_confidence"][c]["parametric"]).rjust(6) for c in confs
    )
    row_es = "  Expected shortfall      " + "  ".join(
        _format_pct_signed(-var_b["by_confidence"][c]["expected_shortfall_historical"]).rjust(6) for c in confs
    )
    lines.append(row_hist)
    lines.append(row_param)
    lines.append(row_es + "   (historical, mean loss beyond VaR)")
    lines.append("")

    # Drawdown
    dur_txt = f"{mdd['duration_days']} days"
    recov_txt = "recovered" if mdd["recovered"] else "not recovered"
    if mdd["recovered"] and mdd["recovery_date"]:
        recov_txt = f"recovered {mdd['recovery_date']}"
    lines.append(
        f"Max drawdown ({payload['lookback_days']}d): "
        f"{_format_pct_signed(mdd['drawdown_pct'])} "
        f"from {mdd['peak_date']} to {mdd['trough_date']} "
        f"({dur_txt}, {recov_txt})."
    )
    lines.append("")

    # Worst-N
    lines.append(f"Worst {stress_b['n']} historical days for current book:")
    for s in stress_b["scenarios"]:
        bench_txt = ""
        if "benchmark_return_pct" in s:
            bench_txt = f"   ({bench} {_format_pct_signed(s['benchmark_return_pct'])})"
        contribs_txt = ""
        if "name_contributions" in s and s["name_contributions"]:
            top = s["name_contributions"][:4]
            contribs_txt = "   " + " · ".join(
                f"{c['ticker']} {_format_pct_signed(c['contribution_pct'], decimals=1).replace('%','pp')}"
                for c in top
            )
        lines.append(
            f"  {s['date']}   {_format_pct_signed(s['book_return_pct'])}{bench_txt}{contribs_txt}"
        )
    lines.append("")

    # Position contribution to variance
    lines.append("Position contribution to portfolio variance:")
    # Sort by variance contribution descending
    pos_sorted = sorted(
        pos_risk.items(), key=lambda kv: kv[1]["variance_contribution_pct"], reverse=True
    )
    for t, pr in pos_sorted:
        lines.append(
            f"  {t:<8}  {pr['variance_contribution_pct']*100:>5.1f}%   "
            f"(weight {pr['weight']*100:>4.1f}%, vol {pr['vol_annualized']*100:>4.1f}%)"
        )
    lines.append("")

    # Concentration
    lines.append(
        f"Concentration: top 5 = {conc_b['top_5_weight']*100:.0f}%, "
        f"Herfindahl {conc_b['herfindahl']:.2f} "
        f"(effective N = {conc_b['effective_n']:.1f})"
    )
    lines.append("")

    # Monte Carlo (optional)
    mc_b = payload.get("monte_carlo")
    if mc_b:
        lines.append(
            f"Monte Carlo path VaR ({mc_b['n_paths']:,} paths × {mc_b['simulation_days']}d, "
            f"{mc_b['tail']}{' df='+str(mc_b['tail_df']) if mc_b['tail'] == 'student_t' else ''})"
        )
        cum = mc_b["cumulative_return_distribution"]
        lines.append(
            f"  Cumulative return: p5 {fmt_pct(cum['p5'], signed=True)} · "
            f"p50 {fmt_pct(cum['p50'], signed=True)} · "
            f"p95 {fmt_pct(cum['p95'], signed=True)} "
            f"(mean {fmt_pct(cum['mean'], signed=True)}, σ {fmt_pct(cum['std'])})"
        )
        dd = mc_b["max_drawdown_distribution"]
        lines.append(
            f"  Path max-drawdown: median {fmt_pct(dd['p50'])} · "
            f"p10 (bad case) {fmt_pct(dd['p10'])} · "
            f"p5 (tail case) {fmt_pct(dd['p5'])}"
        )
        lines.append("  Path VaR by confidence:")
        for c_str, v in sorted(mc_b["path_var_by_confidence"].items()):
            c_pct = float(c_str) * 100
            lines.append(
                f"    {c_pct:.0f}%: VaR {fmt_pct(v['path_var'])} · "
                f"ES {fmt_pct(v['path_expected_shortfall'])}"
            )
        loss = mc_b["loss_probabilities"]
        gain = mc_b["gain_probabilities"]
        lines.append(
            f"  P(loss > 5%): {loss['P_loss_gt_5pct']*100:.1f}% · "
            f"P(loss > 10%): {loss['P_loss_gt_10pct']*100:.1f}% · "
            f"P(loss > 20%): {loss['P_loss_gt_20pct']*100:.1f}%"
        )
        lines.append(
            f"  P(gain > 5%): {gain['P_gain_gt_5pct']*100:.1f}% · "
            f"P(gain > 10%): {gain['P_gain_gt_10pct']*100:.1f}%"
        )
        lines.append("")

    # Take
    lines.append(generate_take(payload))

    # Caveats
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines)


def generate_take(payload: dict) -> str:
    """Adaptive Take. Reads what's actually true about the book and picks
    2-3 of the most striking facts to surface."""
    stats_b = payload["stats"]
    pos_risk = payload["position_risk"]
    conc_b = payload["concentration"]
    mdd = payload["max_drawdown"]
    bench = payload["benchmark"]

    parts: list[str] = []

    # Beta framing
    beta = stats_b["beta"]
    if beta > 1.5:
        parts.append(
            f"Book runs hot to {bench} (beta {beta:.2f}) — moves ~"
            f"{int(round(beta * 100))}% as much as the index"
        )
    elif beta < 0:
        parts.append(f"Negative beta to {bench} ({beta:.2f}); book hedges the index")
    elif beta < 0.3:
        parts.append(f"Low market beta ({beta:.2f}); book is mostly idiosyncratic")
    else:
        parts.append(f"Beta to {bench} = {beta:.2f}")

    # Top variance contributor (only if it's clearly outsized)
    if pos_risk:
        top_t, top_pr = max(
            pos_risk.items(), key=lambda kv: kv[1]["variance_contribution_pct"]
        )
        top_share = top_pr["variance_contribution_pct"]
        top_weight = top_pr["weight"]
        if top_share > 0.40 and top_share > top_weight + 0.05:
            parts.append(
                f"{top_t} dominates risk ({top_share*100:.0f}% of variance) despite "
                f"sitting at {top_weight*100:.0f}% weight — its risk share is "
                f"{(top_share/top_weight - 1)*100:.0f}% larger than its position size"
            )

    # Concentration
    if conc_b["herfindahl"] > 0.30:
        parts.append(
            f"Concentrated book (Herfindahl {conc_b['herfindahl']:.2f}, effective N "
            f"{conc_b['effective_n']:.1f})"
        )

    # Drawdown
    if mdd["drawdown_pct"] < -0.20:
        parts.append(
            f"Recent {_format_pct_signed(mdd['drawdown_pct'])} drawdown from "
            f"{mdd['peak_date']} to {mdd['trough_date']}"
            + ("; recovered" if mdd["recovered"] else "; not yet recovered")
        )

    # Tracking error
    te = stats_b["tracking_error_annualized"]
    if te < 0.05:
        parts.append(f"book closely tracks {bench} (tracking error {te*100:.1f}%)")

    # Cap to 3 clauses for readability
    if len(parts) > 3:
        parts = parts[:3]

    # Always end with the decision-frame closer
    closer = "Consider whether the variance share matches your conviction in those names specifically."
    return "Take: " + ". ".join(parts) + ". " + closer
