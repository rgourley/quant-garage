"""
fixed-income-context: rates and credit view via ETF proxies.

Every equity valuation implicitly assumes something about rates and
credit spreads. This skill surfaces the current rates/credit picture
so equity workflows have that anchor.

Uses ETF proxies for rate exposure so the whole thing runs on any
Massive Stocks plan without a FRED integration:

- SHV (0-1y Treasury) as the very-short-end anchor
- SHY (1-3y Treasury)
- IEF (7-10y Treasury)
- TLT (20y+ Treasury)
- TIP (Treasury Inflation-Protected)
- LQD (Investment-Grade Corporate)
- HYG (High-Yield Corporate)
- AGG (Broad Aggregate)

Computes 1d/5d/20d/60d/120d returns, current price vs 200-day SMA,
rolling correlation between HYG/LQD (credit stress proxy) and SPY,
plus derived spread readings:

- **HYG/LQD spread** (a credit-risk premium proxy). Rising = HY
  underperforming IG = credit stress building.
- **TLT/IEF spread** (a duration slope proxy). Rising TLT vs IEF =
  long end leading = curve steepening / rates falling.

    from quant_garage.skills.fixed_income_context import run, render
    payload = run()
    print(render(payload))
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
)


# Proxy set. Order matters for the render layer (short duration first,
# then intermediate, long, TIPS, credit).
FI_PROXIES: dict[str, dict] = {
    "SHV":  {"name": "0-1y Treasury (SHV)",       "kind": "duration_short",  "duration_yrs": 0.4},
    "SHY":  {"name": "1-3y Treasury (SHY)",       "kind": "duration_short",  "duration_yrs": 1.9},
    "IEF":  {"name": "7-10y Treasury (IEF)",      "kind": "duration_intermediate", "duration_yrs": 7.5},
    "TLT":  {"name": "20y+ Treasury (TLT)",       "kind": "duration_long",   "duration_yrs": 17.0},
    "TIP":  {"name": "TIPS (TIP)",                "kind": "inflation_protected", "duration_yrs": 6.5},
    "LQD":  {"name": "Investment-Grade (LQD)",    "kind": "credit_ig",       "duration_yrs": 8.3},
    "HYG":  {"name": "High-Yield (HYG)",          "kind": "credit_hy",       "duration_yrs": 3.4},
    "AGG":  {"name": "Broad Agg (AGG)",           "kind": "aggregate",       "duration_yrs": 6.1},
}

WINDOWS_DAYS = [1, 5, 20, 60, 120]


def _fetch_daily(
    client: MassiveClient, ticker: str,
    from_date: date, to_date: date,
) -> list[dict]:
    try:
        body, _ = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{from_date.isoformat()}/{to_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 5000},
        )
    except FetchError:
        return []
    return body.get("results") or []


def _close_series(bars: list[dict]) -> tuple[list[date], np.ndarray]:
    dates = [
        datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        for b in bars
    ]
    closes = np.array([float(b["c"]) for b in bars], dtype=float)
    return dates, closes


def _pct_return(closes: np.ndarray, lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    base = closes[-lookback - 1]
    end = closes[-1]
    if base <= 0:
        return None
    return float(end / base - 1)


def _sma(closes: np.ndarray, window: int) -> float | None:
    if len(closes) < window:
        return None
    return float(np.mean(closes[-window:]))


def _rolling_correlation(
    a: np.ndarray, b: np.ndarray, window: int = 60,
) -> float | None:
    """Correlation of log returns over the last `window` bars."""
    if len(a) < window + 1 or len(b) < window + 1:
        return None
    ra = np.diff(np.log(a[-window - 1:]))
    rb = np.diff(np.log(b[-window - 1:]))
    if len(ra) != len(rb):
        return None
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _percentile_of_current(closes: np.ndarray, lookback: int = 252) -> float | None:
    """Percentile rank of the latest close within its trailing window."""
    if len(closes) < lookback + 1:
        lookback = len(closes) - 1
    if lookback < 20:
        return None
    window = closes[-lookback - 1: -1]
    cur = closes[-1]
    return float((window <= cur).sum() / len(window) * 100)


def _classify_regime(
    tlt_20d: float | None, hyg_20d: float | None, lqd_20d: float | None,
    hyg_lqd_spread_delta_20d: float | None,
) -> tuple[str, str]:
    """Derive a plain-English regime label + short read."""
    # Credit stress: HYG lagging LQD over 20d
    credit_stressed = (
        hyg_lqd_spread_delta_20d is not None
        and hyg_lqd_spread_delta_20d < -0.02
    )
    # Rates rally: TLT up strongly
    rates_rally = tlt_20d is not None and tlt_20d > 0.03
    rates_selloff = tlt_20d is not None and tlt_20d < -0.03
    hy_leading = hyg_20d is not None and hyg_20d > 0.01

    if credit_stressed and rates_rally:
        return "risk_off", (
            "Risk-off: credit spreads widening (HYG lagging LQD) while "
            "long-duration Treasuries rally. Classic flight-to-quality."
        )
    if credit_stressed:
        return "credit_stress", (
            "Credit stress building: HY underperforming IG. Rates side "
            "not confirming yet; watch TLT."
        )
    if rates_rally and hy_leading:
        return "goldilocks", (
            "Goldilocks: rates rallying AND credit tightening (HY "
            "leading). Consistent with easing expectations and no "
            "credit fear."
        )
    if rates_selloff and hy_leading:
        return "reflation", (
            "Reflation: rates selling off (yields rising) but HY "
            "leading IG. Growth-on with rate pressure."
        )
    if rates_selloff:
        return "rate_pressure", (
            "Rates under pressure: long-end Treasuries selling off. "
            "Watch for equity-multiple compression on growth names."
        )
    return "neutral", (
        "No clean rates/credit signal. Duration and credit both in "
        "range; check equity workflows for standalone reads."
    )


def run(
    lookback_days: int = 252,
    correlation_window: int = 60,
    benchmark: str = "SPY",
    client: MassiveClient | None = None,
) -> dict:
    """Rates and credit view via ETF proxies.

    Args:
        lookback_days: history depth for percentile ranks. Default 252.
        correlation_window: bars for the HYG/SPY rolling correlation.
            Default 60.
        benchmark: SPY (or another equity benchmark) for the credit-
            equity correlation. Default SPY.
        client: reuse an existing MassiveClient.
    """
    client = client or MassiveClient()
    today_d = today()
    fetch_start = today_d - timedelta(days=int(lookback_days * 1.7))

    # Pull all proxies + benchmark. One range-aggs call per ticker.
    per_ticker: dict[str, tuple[list[date], np.ndarray]] = {}
    missing: list[str] = []
    print(f"Fetching {len(FI_PROXIES)} FI proxies + {benchmark}...",
          file=sys.stderr)
    for t in list(FI_PROXIES) + [benchmark]:
        bars = _fetch_daily(client, t, fetch_start, today_d)
        if not bars:
            missing.append(t)
            continue
        per_ticker[t] = _close_series(bars)

    if benchmark not in per_ticker:
        raise RuntimeError(
            f"benchmark {benchmark} unavailable; check MASSIVE_API_KEY"
        )

    # Per-proxy read
    proxies_out: list[dict] = []
    for ticker, meta in FI_PROXIES.items():
        if ticker not in per_ticker:
            continue
        _, closes = per_ticker[ticker]
        row = {
            "ticker": ticker,
            "name": meta["name"],
            "kind": meta["kind"],
            "duration_yrs": meta["duration_yrs"],
            "current_price": round(float(closes[-1]), 2),
        }
        for w in WINDOWS_DAYS:
            r = _pct_return(closes, w)
            row[f"return_{w}d_pct"] = (
                round(r * 100, 2) if r is not None else None
            )
        row["sma_50"] = round(_sma(closes, 50) or 0, 2)
        row["sma_200"] = round(_sma(closes, 200) or 0, 2)
        row["above_sma_50"] = (
            row["current_price"] > row["sma_50"] if row["sma_50"] else None
        )
        row["above_sma_200"] = (
            row["current_price"] > row["sma_200"] if row["sma_200"] else None
        )
        row["price_percentile_252d"] = (
            round(_percentile_of_current(closes, 252) or 0.0, 1)
        )
        proxies_out.append(row)

    # Spread derivations
    def _closes(t: str) -> np.ndarray | None:
        return per_ticker[t][1] if t in per_ticker else None

    def _spread_delta(a: str, b: str, window: int) -> float | None:
        """(A_return - B_return) over the last `window` bars.
        Negative HYG-LQD delta = credit widening (HYG underperforming).
        """
        ca = _closes(a)
        cb = _closes(b)
        if ca is None or cb is None:
            return None
        ra = _pct_return(ca, window)
        rb = _pct_return(cb, window)
        if ra is None or rb is None:
            return None
        return ra - rb

    spreads: dict = {
        "hyg_lqd_credit_spread_delta_5d_pct": None,
        "hyg_lqd_credit_spread_delta_20d_pct": None,
        "hyg_lqd_credit_spread_delta_60d_pct": None,
        "tlt_ief_duration_spread_delta_20d_pct": None,
    }
    for w in (5, 20, 60):
        s = _spread_delta("HYG", "LQD", w)
        spreads[f"hyg_lqd_credit_spread_delta_{w}d_pct"] = (
            round(s * 100, 3) if s is not None else None
        )
    s_dur = _spread_delta("TLT", "IEF", 20)
    spreads["tlt_ief_duration_spread_delta_20d_pct"] = (
        round(s_dur * 100, 3) if s_dur is not None else None
    )

    # Credit-equity correlation
    hyg_bench_corr = None
    if "HYG" in per_ticker and benchmark in per_ticker:
        hyg_bench_corr = _rolling_correlation(
            per_ticker["HYG"][1], per_ticker[benchmark][1],
            window=correlation_window,
        )

    # Regime read
    tlt_20d = next(
        (p.get("return_20d_pct") for p in proxies_out if p["ticker"] == "TLT"),
        None,
    )
    tlt_20d_dec = tlt_20d / 100 if tlt_20d is not None else None
    hyg_20d = next(
        (p.get("return_20d_pct") for p in proxies_out if p["ticker"] == "HYG"),
        None,
    )
    hyg_20d_dec = hyg_20d / 100 if hyg_20d is not None else None
    lqd_20d = next(
        (p.get("return_20d_pct") for p in proxies_out if p["ticker"] == "LQD"),
        None,
    )
    lqd_20d_dec = lqd_20d / 100 if lqd_20d is not None else None
    hyg_lqd_delta = spreads.get("hyg_lqd_credit_spread_delta_20d_pct")
    hyg_lqd_delta_dec = (
        hyg_lqd_delta / 100 if hyg_lqd_delta is not None else None
    )
    regime_label, regime_read = _classify_regime(
        tlt_20d_dec, hyg_20d_dec, lqd_20d_dec, hyg_lqd_delta_dec,
    )

    return {
        "scan_params": {
            "lookback_days": lookback_days,
            "correlation_window": correlation_window,
            "benchmark": benchmark,
            "as_of": today_d.isoformat(),
            "missing_tickers": missing,
        },
        "proxies": proxies_out,
        "spreads": spreads,
        "credit_equity_correlation_60d": (
            round(hyg_bench_corr, 3) if hyg_bench_corr is not None else None
        ),
        "regime": {
            "label": regime_label,
            "read": regime_read,
        },
        "generated_at": utcnow_iso(),
        "caveats": [
            "ETF-proxy read, not raw yields. Reflects total-return "
            "prices (which move inversely to yields for duration ETFs) "
            "not the yield itself. For actual yields wire in FRED.",
            "Credit spread is proxied as HYG return minus LQD return "
            "over the same window. Not a direct OAS reading. "
            "Directionally correct for stress detection; not tradeable "
            "as a spread quote.",
            "Regime label is heuristic. Real macro classification "
            "belongs in a dedicated macro engine; this is a "
            "context-setting surface for equity workflows.",
        ],
    }


# ---------- Render ----------

def _fmt_pct(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.{decimals}f}%"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    proxies = payload["proxies"]
    spreads = payload["spreads"]
    regime = payload["regime"]
    corr = payload.get("credit_equity_correlation_60d")
    lines: list[str] = []

    lines.append(
        f"Fixed-Income Context — {params['as_of']}\n"
        f"ETF proxies · benchmark {params['benchmark']} · "
        f"correlation window {params['correlation_window']}d"
    )
    lines.append("")

    lines.append(f"Regime: {regime['label'].upper()}")
    lines.append(f"  {regime['read']}")
    lines.append("")

    # Proxy table
    lines.append(
        f"{'Ticker':<7}{'Segment':<30}{'Price':>9}"
        f"{'1d':>7}{'5d':>7}{'20d':>7}{'60d':>7}{'120d':>7}  {'%ile252':>7}"
    )
    lines.append("-" * 96)
    for p in proxies:
        pct = p.get("price_percentile_252d")
        pct_str = f"{pct:.0f}" if pct is not None else "n/a"
        lines.append(
            f"{p['ticker']:<7}{p['name'][:30]:<30}"
            f"{p['current_price']:>9.2f}"
            f"{_fmt_pct(p.get('return_1d_pct'), 1):>7}"
            f"{_fmt_pct(p.get('return_5d_pct'), 1):>7}"
            f"{_fmt_pct(p.get('return_20d_pct'), 1):>7}"
            f"{_fmt_pct(p.get('return_60d_pct'), 1):>7}"
            f"{_fmt_pct(p.get('return_120d_pct'), 1):>7}"
            f"  {pct_str:>7}"
        )
    lines.append("")

    # Spreads block
    lines.append("Spread deltas (HY-IG credit; long-short duration):")
    for label, key in [
        ("HYG-LQD 5d",  "hyg_lqd_credit_spread_delta_5d_pct"),
        ("HYG-LQD 20d", "hyg_lqd_credit_spread_delta_20d_pct"),
        ("HYG-LQD 60d", "hyg_lqd_credit_spread_delta_60d_pct"),
        ("TLT-IEF 20d", "tlt_ief_duration_spread_delta_20d_pct"),
    ]:
        val = spreads.get(key)
        lines.append(f"  {label:<14} {_fmt_pct(val, 2)}")
    lines.append("")

    if corr is not None:
        lines.append(
            f"HYG-{params['benchmark']} rolling 60d correlation: {corr:+.3f}"
        )
        if corr > 0.5:
            lines.append(
                "  Credit-equity correlation high — equity risk-off "
                "would drag credit."
            )
        elif corr < 0.2:
            lines.append(
                "  Credit-equity correlation low — credit signal is "
                "quasi-independent of equities. Useful macro anchor."
            )
        else:
            lines.append(
                "  Credit-equity correlation moderate."
            )
        lines.append("")

    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    if params.get("missing_tickers"):
        lines.append("")
        lines.append(
            f"Missing tickers on this key: "
            f"{', '.join(params['missing_tickers'])}"
        )

    return "\n".join(lines)
