"""
portfolio-macro-scenario as an importable library function.

risk-report and portfolio-review are descriptive of the past: what the
book's vol, beta, VaR, and drawdown have BEEN. portfolio-macro-scenario is
prescriptive under a scenario the operator names: "given my book, what
happens if rates keep rising, the dollar keeps rallying, oil spikes, or
gold falls."

    from quant_garage.skills.portfolio_macro_scenario import run, render
    payload = run(book_path="examples/sample-book.csv", rates_bp=50, dxy_pct=2)
    print(render(payload))

Method:
  1. Pull daily aggs for every position ticker plus the four macro factor
     ETFs: TLT (rates), UUP (dollar/DXY), USO (oil), GLD (gold).
  2. Per-position multivariate OLS regression of daily returns on the
     four factor returns (numpy.linalg.lstsq with an intercept).
  3. Translate shocks to factor ETF returns; a rates shock is applied
     via TLT effective duration (~17 years).
  4. Position P&L = position_value * sum(beta * shock). Aggregate book
     P&L with a rough +/-1.64 sigma band (independence assumed).
  5. Rank positions by absolute P&L contribution; rank factors by
     aggregate contribution.
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
)


# The four macro factor ETFs, in the fixed design-matrix column order.
FACTORS: dict[str, str] = {
    "TLT": "rates (20+ year Treasuries)",
    "UUP": "dollar (US Dollar Index)",
    "USO": "oil (WTI crude)",
    "GLD": "gold",
}
FACTOR_ORDER = list(FACTORS.keys())

# TLT effective duration used to convert a parallel rate shock into a
# TLT price return. True duration drifts near 16-18 years.
TLT_DURATION = 17.0

# Minimum aligned observations to trust a per-position regression.
N_MIN = 60

_RATE_LIMIT_COOLDOWN_SECONDS = 13


class _State:
    """Per-run state so caches and rate-limit flags don't leak across calls."""
    def __init__(self, sleep_between: float) -> None:
        self.client = MassiveClient()
        self.today = today()
        self.aggs_cache: dict[str, list[dict]] = {}
        self.rate_limited: set[str] = set()
        self.sleep_between = sleep_between


# ----- HTTP -----

def _fetch_daily_aggs(state: _State, ticker: str, calendar_days: int) -> list[dict]:
    """Daily {date, close} records, ascending. Cached per-state.

    Detects RateLimited specifically, cools down and retries once. If it
    still fails, flags the ticker so the caller can caveat loudly rather
    than silently returning [].
    """
    if ticker in state.aggs_cache:
        return state.aggs_cache[ticker]

    end = state.today
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        doc, _ = state.client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = state.client.get(path, params)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} after cooldown: {exc}",
                  file=sys.stderr)
            state.rate_limited.add(ticker)
            state.aggs_cache[ticker] = []
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        state.aggs_cache[ticker] = []
        return []
    finally:
        if state.sleep_between > 0:
            time.sleep(state.sleep_between)

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    state.aggs_cache[ticker] = rows
    return rows


# ----- Book parsing -----

def load_positions(path: str) -> list[dict]:
    """Read the book CSV. Columns: ticker,shares[,cost_basis,as_of_date]."""
    positions: list[dict] = []
    with open(path) as f:
        for row in csv.DictReader(f):
            ticker = row["ticker"].strip().upper()
            shares = float(row["shares"])
            cost_basis = float(row["cost_basis"]) if row.get("cost_basis") else None
            positions.append({
                "ticker": ticker,
                "shares": shares,
                "cost_basis": cost_basis,
                "as_of_date": row.get("as_of_date"),
            })
    return positions


# ----- Returns + alignment -----

def _daily_returns(rows: list[dict]) -> dict[str, float]:
    """Close-to-close simple daily returns. Returns {date: return}."""
    out: dict[str, float] = {}
    prev = None
    for r in rows:
        c = r["close"]
        if prev is not None and prev > 0 and c > 0:
            out[r["date"]] = (c / prev) - 1.0
        prev = c
    return out


def _aligned_matrix(
    pos_ret: dict[str, float],
    factor_ret: dict[str, dict[str, float]],
    lookback: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Align one position's returns with the four factor return series."""
    common: set[str] | None = set(pos_ret.keys())
    for f in FACTOR_ORDER:
        common &= set(factor_ret.get(f, {}).keys())
    dates = sorted(common)
    if len(dates) > lookback:
        dates = dates[-lookback:]
    if not dates:
        return np.array([]), np.array([]), 0
    y = np.array([pos_ret[d] for d in dates], dtype=float)
    X = np.array(
        [[factor_ret[f][d] for f in FACTOR_ORDER] for d in dates],
        dtype=float,
    )
    return y, X, len(dates)


# ----- Regression -----

def _regress(y: np.ndarray, X: np.ndarray) -> dict:
    """Multivariate OLS with intercept via lstsq. Returns betas, residual_std, r_squared."""
    n = X.shape[0]
    k = X.shape[1] + 1
    design = np.column_stack([np.ones(n), X])
    coef, _res, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coef
    resid = y - fitted
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(n - k, 1)
    residual_std = float(np.sqrt(ss_res / dof))
    betas = {FACTOR_ORDER[i]: float(coef[i + 1]) for i in range(len(FACTOR_ORDER))}
    return {"betas": betas, "residual_std": residual_std, "r_squared": float(r_squared)}


# ----- Scenario -----

def _factor_shocks(rates_bp: float, dxy_pct: float, oil_pct: float, gld_pct: float) -> dict[str, float]:
    return {
        "TLT": -(rates_bp / 10000.0) * TLT_DURATION,
        "UUP": dxy_pct / 100.0,
        "USO": oil_pct / 100.0,
        "GLD": gld_pct / 100.0,
    }


def _scenario_phrase(rates_bp: float, dxy_pct: float, oil_pct: float, gld_pct: float) -> str:
    parts: list[str] = []
    if rates_bp:
        parts.append(f"{rates_bp:+g}bp rates")
    if dxy_pct:
        parts.append(f"{dxy_pct:+g}% DXY")
    if oil_pct:
        parts.append(f"{oil_pct:+g}% oil")
    if gld_pct:
        parts.append(f"{gld_pct:+g}% gold")
    if not parts:
        return "a flat scenario (all shocks zero)"
    return " / ".join(parts)


# ----- Public API -----

def run(
    book_path: str,
    rates_bp: float = 0.0,
    dxy_pct: float = 0.0,
    oil_pct: float = 0.0,
    gld_pct: float = 0.0,
    lookback: int = 252,
    sleep: float = 0.0,
) -> dict:
    """Shock a book by the four macro factors and return per-position + book P&L."""
    state = _State(sleep)
    lookback = int(lookback)
    calendar_days = int(max(lookback, 252) * 1.6) + 14

    positions = load_positions(book_path)
    if not positions:
        raise ValueError("book is empty: no positions parsed")

    pos_tickers = [p["ticker"] for p in positions]
    shocks = _factor_shocks(rates_bp, dxy_pct, oil_pct, gld_pct)

    rows: dict[str, list[dict]] = {}
    sources: list[dict] = []
    to_pull = list(FACTOR_ORDER) + [t for t in pos_tickers if t not in FACTOR_ORDER]
    for t in to_pull:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows[t] = _fetch_daily_aggs(state, t, calendar_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily closes for {t}",
        })

    factor_ret = {f: _daily_returns(rows.get(f, [])) for f in FACTOR_ORDER}

    tier_caveats: list[str] = []
    if state.rate_limited:
        tier_caveats.append(
            f"RATE LIMIT: {len(state.rate_limited)} series "
            f"({', '.join(sorted(state.rate_limited))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Any "
            f"position or factor that depends on them is UNKNOWN, not zero. Free "
            f"Basic tier caps at 5 calls/min: pass sleep=13 or upgrade to Stocks Starter."
        )

    missing_factors = [f for f in FACTOR_ORDER if len(factor_ret[f]) < N_MIN]
    if missing_factors:
        tier_caveats.append(
            f"Factor ETFs with insufficient history ({', '.join(missing_factors)}) "
            f"cannot anchor the regression; results aborted or degraded. Betas on "
            f"those factors are unreliable."
        )

    sensitivities: list[dict] = []
    excluded: list[dict] = []
    book_value = 0.0
    for p in positions:
        t = p["ticker"]
        tr = rows.get(t, [])
        price = tr[-1]["close"] if tr else None
        if price is None:
            excluded.append({"ticker": t, "reason": "no_price", "n_obs": 0})
            tier_caveats.append(f"{t}: no price data; excluded from the scenario")
            continue
        position_value = p["shares"] * price

        pos_ret = _daily_returns(tr)
        y, X, n = _aligned_matrix(pos_ret, factor_ret, lookback)
        if n < N_MIN or missing_factors:
            excluded.append({
                "ticker": t,
                "reason": "insufficient_history",
                "n_obs": int(n),
                "min_required": N_MIN,
            })
            tier_caveats.append(
                f"{t}: only {n} aligned days (min {N_MIN}); excluded from the "
                f"scenario regression"
            )
            continue

        reg = _regress(y, X)
        betas = reg["betas"]
        factor_contrib_ret = {f: betas[f] * shocks[f] for f in FACTOR_ORDER}
        expected_return = float(sum(factor_contrib_ret.values()))
        pnl = position_value * expected_return
        factor_contrib_pnl = {
            f: position_value * factor_contrib_ret[f] for f in FACTOR_ORDER
        }
        pnl_std = position_value * reg["residual_std"]

        book_value += position_value
        sensitivities.append({
            "ticker": t,
            "shares": p["shares"],
            "price": round(float(price), 4),
            "position_value_usd": round(float(position_value), 2),
            "n_obs": int(n),
            "betas": {f: round(betas[f], 4) for f in FACTOR_ORDER},
            "r_squared": round(reg["r_squared"], 4),
            "residual_std": round(reg["residual_std"], 6),
            "expected_return": round(expected_return, 6),
            "pnl_usd": round(float(pnl), 2),
            "pnl_std_usd": round(float(pnl_std), 2),
            "factor_contributions_usd": {
                f: round(float(factor_contrib_pnl[f]), 2) for f in FACTOR_ORDER
            },
        })

    if not sensitivities:
        raise ValueError(
            "no positions survived the history filter; cannot run the scenario"
        )

    expected_pnl = float(sum(s["pnl_usd"] for s in sensitivities))
    expected_return_pct = expected_pnl / book_value if book_value > 0 else 0.0
    ci_std = float(np.sqrt(sum(s["pnl_std_usd"] ** 2 for s in sensitivities)))
    Z90 = 1.64
    ci_low = expected_pnl - Z90 * ci_std
    ci_high = expected_pnl + Z90 * ci_std

    dominant_positions = sorted(
        (
            {
                "ticker": s["ticker"],
                "pnl_usd": s["pnl_usd"],
                "contribution_pct": (
                    round(s["pnl_usd"] / expected_pnl, 4)
                    if abs(expected_pnl) > 1e-9 else None
                ),
            }
            for s in sensitivities
        ),
        key=lambda d: abs(d["pnl_usd"]),
        reverse=True,
    )

    factor_pnl = {
        f: float(sum(s["factor_contributions_usd"][f] for s in sensitivities))
        for f in FACTOR_ORDER
    }
    dominant_factors = sorted(
        (
            {
                "factor": f,
                "label": FACTORS[f],
                "shock_return": round(shocks[f], 6),
                "pnl_usd": round(factor_pnl[f], 2),
                "contribution_pct": (
                    round(factor_pnl[f] / expected_pnl, 4)
                    if abs(expected_pnl) > 1e-9 else None
                ),
            }
            for f in FACTOR_ORDER
        ),
        key=lambda d: abs(d["pnl_usd"]),
        reverse=True,
    )

    tier_caveats.append(
        "Betas are historical and unstable: they are estimated over the "
        "lookback and will not hold exactly out of sample."
    )
    tier_caveats.append(
        "The four factor ETFs are collinear (rates, dollar, oil, gold co-move); "
        "individual betas can be noisy even when the aggregate fit is good."
    )
    tier_caveats.append(
        "Shocks are applied linearly through the betas; large moves have "
        "convexity and second-order effects this does not capture."
    )
    tier_caveats.append(
        f"The rate shock is converted to a TLT return via an assumed effective "
        f"duration of {TLT_DURATION:g} years; the true duration drifts."
    )
    tier_caveats.append(
        "The CI band assumes independent residuals across names, so the true "
        "~90% band is wider than reported."
    )

    take = _compose_take(
        rates_bp, dxy_pct, oil_pct, gld_pct,
        expected_pnl, expected_return_pct, book_value,
        dominant_positions, dominant_factors, sensitivities,
    )

    return {
        "skill": "portfolio-macro-scenario",
        "as_of": state.today.isoformat(),
        "fetched_at": utcnow_iso(),
        "lookback_days": lookback,
        "factors": FACTOR_ORDER,
        "tlt_duration": TLT_DURATION,
        "scenario": {
            "rates_bp": rates_bp,
            "dxy_pct": dxy_pct,
            "oil_pct": oil_pct,
            "gld_pct": gld_pct,
            "factor_shocks": {f: round(shocks[f], 6) for f in FACTOR_ORDER},
            "phrase": _scenario_phrase(rates_bp, dxy_pct, oil_pct, gld_pct),
        },
        "book": {
            "n_positions": len(sensitivities),
            "n_excluded": len(excluded),
            "book_value_usd": round(book_value, 2),
        },
        "sensitivities": sensitivities,
        "book_pnl": {
            "expected_pnl_usd": round(expected_pnl, 2),
            "expected_return_pct": round(expected_return_pct, 6),
            "ci_confidence": 0.90,
            "ci_std_usd": round(ci_std, 2),
            "ci_low_usd": round(ci_low, 2),
            "ci_high_usd": round(ci_high, 2),
            "ci_return_low_pct": round(ci_low / book_value, 6) if book_value > 0 else None,
            "ci_return_high_pct": round(ci_high / book_value, 6) if book_value > 0 else None,
        },
        "dominant_positions": dominant_positions,
        "dominant_factors": dominant_factors,
        "excluded": excluded,
        "take": take,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }


# ----- Take -----

def _fmt_usd_k(x: float) -> str:
    sign = "-" if x < 0 else "+"
    return f"{sign}{abs(x) / 1000.0:,.1f}K"


def _compose_take(
    rates_bp: float, dxy_pct: float, oil_pct: float, gld_pct: float,
    expected_pnl: float,
    expected_return_pct: float,
    book_value: float,
    dominant_positions: list[dict],
    dominant_factors: list[dict],
    sensitivities: list[dict],
) -> str:
    verb = "loses" if expected_pnl < 0 else "gains"
    ret_txt = f"{abs(expected_return_pct) * 100:.1f}%"
    pnl_txt = _fmt_usd_k(expected_pnl)

    driver = ""
    if dominant_positions:
        top = dominant_positions[0]
        top_sens = next(
            (s for s in sensitivities if s["ticker"] == top["ticker"]), None
        )
        fac_desc = ""
        if top_sens:
            fc = top_sens["factor_contributions_usd"]
            lead_f = max(FACTOR_ORDER, key=lambda f: abs(fc[f]))
            beta_sign = "+" if top_sens["betas"][lead_f] >= 0 else "-"
            fac_word = {
                "TLT": "duration", "UUP": "dollar", "USO": "oil", "GLD": "gold",
            }[lead_f]
            fac_desc = f"{top['ticker']}'s {beta_sign}{fac_word} beta"
        driver = f", driven by {fac_desc}" if fac_desc else ""

    dom_factor = dominant_factors[0]["label"].split(" ")[0] if dominant_factors else ""
    tail = f" and the {dom_factor}-sensitive names" if dom_factor else ""

    return (
        f"Under {_scenario_phrase(rates_bp, dxy_pct, oil_pct, gld_pct)}, "
        f"the book {verb} an estimated {ret_txt} ({pnl_txt}){driver}{tail}."
    )


# ----- Renderer -----

def _fmt_pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    val = x * 100.0
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}{abs(val):.{decimals}f}%"
    return f"{val:.{decimals}f}%"


def _fmt_usd(x):
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else "+"
    return f"{sign}${abs(x):,.0f}"


def render(payload: dict) -> str:
    sens = payload["sensitivities"]
    sc = payload["scenario"]
    bp = payload["book_pnl"]
    lines: list[str] = []

    lines.append(
        f"Portfolio Macro Scenario ({sc['phrase']}) as of {payload['as_of']}"
    )
    lines.append(
        f"Lookback {payload['lookback_days']}d · "
        f"{payload['book']['n_positions']} positions · "
        f"Book ${payload['book']['book_value_usd']:,.0f}"
    )
    lines.append("")

    shock_txt = "  ".join(
        f"{f} {_fmt_pct(sc['factor_shocks'][f], signed=True)}"
        for f in payload["factors"]
    )
    lines.append(f"Factor shocks (ETF return): {shock_txt}")
    lines.append("")

    header = (
        f"{'Position':<8}"
        + "".join(f"{'b_' + f:>8}" for f in payload["factors"])
        + f"{'R^2':>7}{'ExpRet':>9}{'P&L':>12}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for s in sens:
        row = f"{s['ticker']:<8}"
        for f in payload["factors"]:
            row += f"{s['betas'][f]:>8.2f}"
        row += f"{s['r_squared']:>7.2f}"
        row += f"{_fmt_pct(s['expected_return'], signed=True):>9}"
        row += f"{_fmt_usd(s['pnl_usd']):>12}"
        lines.append(row)
    lines.append("")

    lines.append(
        f"Book expected P&L: {_fmt_usd(bp['expected_pnl_usd'])} "
        f"({_fmt_pct(bp['expected_return_pct'], signed=True)})"
    )
    lines.append(
        f"  ~90% band: {_fmt_usd(bp['ci_low_usd'])} to "
        f"{_fmt_usd(bp['ci_high_usd'])} "
        f"(+/- {_fmt_usd(1.64 * bp['ci_std_usd']).lstrip('+')}, "
        f"1.64 sigma, independence assumed)"
    )
    lines.append("")

    lines.append("Dominant positions (by absolute P&L contribution):")
    for d in payload["dominant_positions"][:5]:
        share = (
            f" ({_fmt_pct(d['contribution_pct'], signed=True)} of book P&L)"
            if d["contribution_pct"] is not None else ""
        )
        lines.append(f"  {d['ticker']:<8} {_fmt_usd(d['pnl_usd']):>12}{share}")
    lines.append("")
    lines.append("Dominant factors (by aggregate P&L contribution):")
    for d in payload["dominant_factors"]:
        share = (
            f" ({_fmt_pct(d['contribution_pct'], signed=True)} of book P&L)"
            if d["contribution_pct"] is not None else ""
        )
        lines.append(
            f"  {d['factor']:<5} {d['label']:<28} {_fmt_usd(d['pnl_usd']):>12}{share}"
        )
    lines.append("")

    lines.append("Take: " + payload["take"])

    if payload.get("excluded"):
        lines.append("")
        lines.append("Excluded positions:")
        for e in payload["excluded"]:
            lines.append(f"  - {e['ticker']}: {e['reason']} (n_obs={e['n_obs']})")

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)
