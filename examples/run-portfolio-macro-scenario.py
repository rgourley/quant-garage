#!/usr/bin/env python3
"""
Reference implementation of the portfolio-macro-scenario skill.

risk-report and portfolio-review are descriptive of the past: what the
book's vol, beta, VaR, and drawdown have BEEN. portfolio-macro-scenario is
prescriptive under a scenario the operator names: "given my book, what
happens if rates keep rising, the dollar keeps rallying, oil spikes, or
gold falls."

Method (details in references/methodology.md):
  1. Pull daily aggs for every position ticker plus the four macro factor
     ETFs: TLT (rates), UUP (dollar/DXY), USO (oil), GLD (gold). Compute
     daily returns aligned by date.
  2. For each position, run a multivariate OLS regression of the stock's
     daily returns on the four factor daily returns over the lookback,
     via numpy.linalg.lstsq with an intercept column. That yields four
     factor betas, a residual std, and an R^2.
  3. Translate the scenario shocks into factor ETF return shocks:
       dxy_pct  -> UUP return = dxy_pct / 100
       oil_pct  -> USO return = oil_pct / 100
       gld_pct  -> GLD return = gld_pct / 100
       rates_bp -> TLT return via effective duration:
                   TLT_return = -(rates_bp / 10000) * TLT_DURATION
                   with TLT_DURATION ~= 17. A +50bp shock => ~= -8.5%.
  4. Expected position return = sum(beta_factor * factor_shock_return).
     Position P&L = position_value * expected_return. Aggregate to a
     book-level expected P&L, with a rough +/-1.64-sigma (~90%) band
     propagated from the per-position regression residual std under an
     independence assumption.
  5. Rank positions by absolute contribution to book P&L, and factors by
     aggregate contribution, for the dominant-contributor lists.

Two output layers:
  Layer 1: canonical JSON matching
           skills/portfolio-macro-scenario/output-schema.json
  Layer 2: rendered table (sensitivity + per-position P&L + book P&L with
           CI + dominant contributors + take). See references/rendering.md.

Usage:
    python3 examples/run-portfolio-macro-scenario.py \\
      --book examples/sample-book.csv \\
      --rates-bp 50 --dxy-pct 2

    python3 examples/run-portfolio-macro-scenario.py \\
      --book examples/sample-book.csv --oil-pct 10 --gld-pct -5 \\
      --lookback 252 --format both

    python3 examples/run-portfolio-macro-scenario.py \\
      --book examples/sample-book.csv --rates-bp 50 --sleep 13  # Free Basic

Book CSV format matches examples/sample-book.csv:
    ticker,shares,cost_basis,as_of_date
Only ticker and shares are required; cost_basis and as_of_date are
optional and unused here. Position value is shares * latest close.

Reads MASSIVE_API_KEY from env. Runs on any stocks tier; on Free Basic
pass --sleep 13 to stay under the 5-calls/min cap.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import timezone, timedelta

import numpy as np

# Make `lib.quant_garage` importable when running from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
)

client = MassiveClient()
TODAY = today()

# The four macro factor ETFs, in the fixed design-matrix column order.
# Each is a liquid, US-listed proxy for one macro variable.
FACTORS: dict[str, str] = {
    "TLT": "rates (20+ year Treasuries)",
    "UUP": "dollar (US Dollar Index)",
    "USO": "oil (WTI crude)",
    "GLD": "gold",
}
FACTOR_ORDER = list(FACTORS.keys())

# Effective duration assumed for TLT when converting a parallel rate shock
# into a TLT price return. Documented in references/methodology.md. TLT's
# effective duration has hovered near 16-18 years; 17 is a round midpoint.
# TLT_return = -(rates_bp / 10000) * TLT_DURATION.
TLT_DURATION = 17.0

# Minimum aligned observations to trust a per-position regression.
N_MIN = 60

_AGGS_CACHE: dict[str, list[dict]] = {}
_RATE_LIMITED: set[str] = set()
_SLEEP_BETWEEN: float = 0.0
_RATE_LIMIT_COOLDOWN_SECONDS = 13


# ----- HTTP -----
# fetch_daily_aggs is reused verbatim from examples/run-macro-basket.py so
# the rate-limit handling stays identical across skills.

def fetch_daily_aggs(ticker: str, calendar_days: int) -> list[dict]:
    """Daily {date, close} records for `ticker`, ascending. Cached per run.

    Detects rate limits specifically (they are FetchError subclasses that
    otherwise look like "no data"), cools down once, retries, and flags the
    ticker so the caller can caveat loudly rather than silently returning [].
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    end = TODAY
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        doc, _ = client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path, params)
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
    finally:
        if _SLEEP_BETWEEN > 0:
            time.sleep(_SLEEP_BETWEEN)

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None:
            continue
        from datetime import datetime as _dt
        d = _dt.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    return rows


# ----- Book parsing -----

def load_positions(path: str) -> list[dict]:
    """Read the book CSV. Columns: ticker,shares[,cost_basis,as_of_date].

    Matches examples/sample-book.csv and the run-portfolio-mark loader.
    Only ticker and shares are required; the rest are carried through but
    unused by this skill.
    """
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

def daily_returns(rows: list[dict]) -> dict[str, float]:
    """Close-to-close simple daily returns. Returns {date: return}."""
    out: dict[str, float] = {}
    prev = None
    for r in rows:
        c = r["close"]
        if prev is not None and prev > 0 and c > 0:
            out[r["date"]] = (c / prev) - 1.0
        prev = c
    return out


def aligned_matrix(
    pos_ret: dict[str, float],
    factor_ret: dict[str, dict[str, float]],
    lookback: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Align one position's returns with the four factor return series.

    Returns (y, X, n) where y is the position return vector, X is the
    factor return matrix in FACTOR_ORDER (no intercept column yet), and n
    is the number of aligned observations, trimmed to the last `lookback`.
    """
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

def regress(y: np.ndarray, X: np.ndarray) -> dict:
    """Multivariate OLS of y on X with an intercept, via lstsq.

    Returns betas (per factor), residual_std, and r_squared. residual_std
    uses the n - k degrees-of-freedom correction (k = 5 params: intercept
    plus four factors).
    """
    n = X.shape[0]
    k = X.shape[1] + 1  # + intercept
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
    return {
        "betas": betas,
        "residual_std": residual_std,
        "r_squared": float(r_squared),
    }


# ----- Scenario -----

def factor_shocks(args: argparse.Namespace) -> dict[str, float]:
    """Translate the scenario flags into factor ETF return shocks."""
    return {
        "TLT": -(args.rates_bp / 10000.0) * TLT_DURATION,
        "UUP": args.dxy_pct / 100.0,
        "USO": args.oil_pct / 100.0,
        "GLD": args.gld_pct / 100.0,
    }


def scenario_phrase(args: argparse.Namespace) -> str:
    """Human-readable one-line description of the non-zero shocks."""
    parts: list[str] = []
    if args.rates_bp:
        parts.append(f"{args.rates_bp:+g}bp rates")
    if args.dxy_pct:
        parts.append(f"{args.dxy_pct:+g}% DXY")
    if args.oil_pct:
        parts.append(f"{args.oil_pct:+g}% oil")
    if args.gld_pct:
        parts.append(f"{args.gld_pct:+g}% gold")
    if not parts:
        return "a flat scenario (all shocks zero)"
    return " / ".join(parts)


# ----- Payload -----

def build_payload(args: argparse.Namespace) -> dict:
    global _SLEEP_BETWEEN
    if args.sleep and args.sleep > 0:
        _SLEEP_BETWEEN = args.sleep

    lookback = int(args.lookback)
    calendar_days = int(max(lookback, 252) * 1.6) + 14

    positions = load_positions(args.book)
    if not positions:
        raise SystemExit("book is empty: no positions parsed")

    pos_tickers = [p["ticker"] for p in positions]
    shocks = factor_shocks(args)

    # Pull factor ETFs first, then each position ticker.
    rows: dict[str, list[dict]] = {}
    sources: list[dict] = []
    to_pull = list(FACTOR_ORDER) + [t for t in pos_tickers if t not in FACTOR_ORDER]
    for t in to_pull:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows[t] = fetch_daily_aggs(t, calendar_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily closes for {t}",
        })

    factor_ret = {f: daily_returns(rows.get(f, [])) for f in FACTOR_ORDER}

    tier_caveats: list[str] = []
    if _RATE_LIMITED:
        tier_caveats.append(
            f"RATE LIMIT: {len(_RATE_LIMITED)} series "
            f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Any "
            f"position or factor that depends on them is UNKNOWN, not zero. Free "
            f"Basic tier caps at 5 calls/min: rerun with --sleep 13 or upgrade "
            f"to Stocks Starter."
        )

    missing_factors = [f for f in FACTOR_ORDER if len(factor_ret[f]) < N_MIN]
    if missing_factors:
        tier_caveats.append(
            f"Factor ETFs with insufficient history ({', '.join(missing_factors)}) "
            f"cannot anchor the regression; results aborted or degraded. Betas on "
            f"those factors are unreliable."
        )

    # Per-position regression + scenario P&L.
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

        pos_ret = daily_returns(tr)
        y, X, n = aligned_matrix(pos_ret, factor_ret, lookback)
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

        reg = regress(y, X)
        betas = reg["betas"]
        # Expected position return = sum(beta_factor * factor_shock_return).
        factor_contrib_ret = {f: betas[f] * shocks[f] for f in FACTOR_ORDER}
        expected_return = float(sum(factor_contrib_ret.values()))
        pnl = position_value * expected_return
        factor_contrib_pnl = {
            f: position_value * factor_contrib_ret[f] for f in FACTOR_ORDER
        }
        # Per-position P&L uncertainty from the regression residual, scaled
        # to the horizon of one shock step (one day). No sqrt(horizon)
        # scaling: the scenario is a single instantaneous move.
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
        raise SystemExit(
            "no positions survived the history filter; cannot run the scenario"
        )

    # Book-level aggregation.
    expected_pnl = float(sum(s["pnl_usd"] for s in sensitivities))
    expected_return_pct = expected_pnl / book_value if book_value > 0 else 0.0
    # Book-level std under independence: sqrt(sum of squared per-position
    # P&L stds). A simplification: it ignores residual correlation across
    # names, so the true band is wider. Documented in methodology.
    ci_std = float(np.sqrt(sum(s["pnl_std_usd"] ** 2 for s in sensitivities)))
    Z90 = 1.64
    ci_low = expected_pnl - Z90 * ci_std
    ci_high = expected_pnl + Z90 * ci_std

    # Dominant contributors: positions by absolute P&L contribution.
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

    # Factors by aggregate P&L contribution across the book.
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

    take = compose_take(
        args, expected_pnl, expected_return_pct, book_value,
        dominant_positions, dominant_factors, sensitivities,
    )

    return {
        "skill": "portfolio-macro-scenario",
        "as_of": TODAY.isoformat(),
        "fetched_at": utcnow_iso(),
        "lookback_days": lookback,
        "factors": FACTOR_ORDER,
        "tlt_duration": TLT_DURATION,
        "scenario": {
            "rates_bp": args.rates_bp,
            "dxy_pct": args.dxy_pct,
            "oil_pct": args.oil_pct,
            "gld_pct": args.gld_pct,
            "factor_shocks": {f: round(shocks[f], 6) for f in FACTOR_ORDER},
            "phrase": scenario_phrase(args),
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
    """Compact signed dollar amount in thousands, e.g. -14.4K."""
    sign = "-" if x < 0 else "+"
    return f"{sign}{abs(x) / 1000.0:,.1f}K"


def compose_take(
    args: argparse.Namespace,
    expected_pnl: float,
    expected_return_pct: float,
    book_value: float,
    dominant_positions: list[dict],
    dominant_factors: list[dict],
    sensitivities: list[dict],
) -> str:
    """One-sentence net-exposure summary of the book under the scenario."""
    verb = "loses" if expected_pnl < 0 else "gains"
    ret_txt = f"{abs(expected_return_pct) * 100:.1f}%"
    pnl_txt = _fmt_usd_k(expected_pnl)

    driver = ""
    if dominant_positions:
        top = dominant_positions[0]
        # Which factor drove this position most.
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
        f"Under {scenario_phrase(args)}, the book {verb} an estimated "
        f"{ret_txt} ({pnl_txt}){driver}{tail}."
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

    # Factor shock line.
    shock_txt = "  ".join(
        f"{f} {_fmt_pct(sc['factor_shocks'][f], signed=True)}"
        for f in payload["factors"]
    )
    lines.append(f"Factor shocks (ETF return): {shock_txt}")
    lines.append("")

    # Sensitivity table: position x factor beta, R^2, P&L.
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

    # Book-level P&L with CI band.
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

    # Dominant contributors.
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


# ----- CLI -----

def main() -> None:
    ap = argparse.ArgumentParser(description="portfolio-macro-scenario reference")
    ap.add_argument("--book", required=True,
                    help="Path to a book CSV (ticker,shares[,cost_basis,as_of_date]). "
                         "See examples/sample-book.csv.")
    ap.add_argument("--rates-bp", type=float, default=0.0,
                    help="Parallel rate shock in basis points. +50 = rates up 50bp. "
                         "Converted to a TLT return via effective duration.")
    ap.add_argument("--dxy-pct", type=float, default=0.0,
                    help="Dollar (DXY) shock in percent, applied as the UUP return.")
    ap.add_argument("--oil-pct", type=float, default=0.0,
                    help="Oil shock in percent, applied as the USO return.")
    ap.add_argument("--gld-pct", type=float, default=0.0,
                    help="Gold shock in percent, applied as the GLD return.")
    ap.add_argument("--lookback", type=int, default=252,
                    help="Trading days of daily returns for the regression. Default 252.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Free Basic caps at 5 "
                         "calls/min; use --sleep 13 on larger books.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT.")
    args = ap.parse_args()
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")
    if args.lookback <= 0:
        raise SystemExit("--lookback must be > 0")

    fmt = resolve_output_format(args.format)
    payload = build_payload(args)
    emit_to_stdout(render(payload), payload, fmt)


if __name__ == "__main__":
    main()
