#!/usr/bin/env python3
"""
Reference implementation of the pitch-comps skill.

Takes a subject ticker, identifies peers via the curated override map (with
correlation and SIC fallbacks), pulls current multiples for the subject and
peers, computes summary stats, runs a regression-adjusted multiples view, and
emits two output layers from one analysis:

  Layer 1: canonical JSON matching skills/pitch-comps/output-schema.json
  Layer 2: Bloomberg RV / CapIQ-style comp table rendered to
           examples/pitch-comps-output.md

Usage:
    python3 examples/run-pitch-comps.py                # default: CRM
    python3 examples/run-pitch-comps.py CRM
    python3 examples/run-pitch-comps.py ORCL

Reads MASSIVE_API_KEY from env. Writes to examples/pitch-comps-output.md
(gitignored).
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

import numpy as np

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    resolve_price,
    resolve_output_format,
    emit_to_stdout,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()


# Curated peer-override map. Mirrors the methodology in
# skills/earnings-drilldown/references/peer-reaction.md; updates should land
# in both maps. See skills/pitch-comps/references/peer-selection.md for the
# rationale on why hand-curation beats SIC for the top names.
PEER_OVERRIDES = {
    # Software majors (the test case for this skill)
    "CRM":  ["ORCL", "SAP", "NOW", "WDAY", "ADBE", "INTU", "PANW", "CRWD"],
    "ORCL": ["CRM", "SAP", "MSFT", "ADBE", "NOW", "WDAY", "INTU"],
    "ADBE": ["CRM", "ORCL", "INTU", "NOW", "WDAY", "SAP"],
    "NOW":  ["CRM", "WDAY", "ADBE", "ORCL", "INTU", "PANW"],
    "WDAY": ["CRM", "NOW", "ADBE", "INTU", "ORCL"],
    "INTU": ["CRM", "ADBE", "ORCL", "NOW", "WDAY"],
    "PANW": ["CRWD", "FTNT", "ZS", "S", "CHKP", "OKTA"],
    "CRWD": ["PANW", "FTNT", "ZS", "S", "OKTA"],
    # Mega-cap tech
    "AAPL":  ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "NVDA":  ["AMD", "AVGO", "TSM", "MU", "ARM", "QCOM", "INTC"],
    "MSFT":  ["GOOGL", "AMZN", "META", "ORCL", "CRM", "AAPL"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "SNAP"],
    "META":  ["GOOGL", "SNAP", "PINS", "NFLX", "AMZN"],
    "AMZN":  ["GOOGL", "META", "MSFT", "AAPL", "SHOP", "WMT"],
    "TSLA":  ["NIO", "RIVN", "LCID", "F", "GM"],
    # Banks
    "JPM": ["BAC", "WFC", "C", "GS", "MS"],
    "GS":  ["MS", "JPM", "BAC", "C"],
    # Payments
    "V":  ["MA", "PYPL", "AXP"],
    "MA": ["V", "PYPL", "AXP"],
    # Pharma
    "LLY":  ["NVO", "PFE", "MRK", "ABBV", "BMY", "AMGN"],
    "MRK":  ["LLY", "PFE", "ABBV", "BMY", "AMGN", "JNJ"],
    "NVO":  ["LLY", "PFE", "MRK", "ABBV"],
    # Energy
    "XOM": ["CVX", "COP", "EOG", "OXY"],
    "CVX": ["XOM", "COP", "EOG", "OXY"],
}


def get_ticker_details(ticker):
    try:
        doc, _ = client.get(f"/v3/reference/tickers/{ticker}")
    except FetchError as exc:
        print(f"  WARN: ticker details for {ticker}: {exc}", file=sys.stderr)
        return None
    return doc.get("results")


def get_snapshot_price(ticker):
    """Walk the lastTrade -> min.c -> day.c -> prevDay.c chain via lib.

    Uses resolve_price() (D4/D5). The legacy waterfall mentioned a
    top-level `fmv` field that the snapshot response shape doesn't
    surface there, so it always returned None in practice.
    """
    try:
        doc, _ = client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    except FetchError as exc:
        print(f"  WARN: snapshot for {ticker}: {exc}", file=sys.stderr)
        return None
    return resolve_price(doc).price


def get_financials(ticker, limit=8):
    """Returns the raw list of up to `limit` quarterly rows, reverse chrono.

    Iterating consumers should drop rows where the income statement is empty
    (10-K fiscal-year-end rows often have only the balance sheet). See
    skills/pitch-comps/references/growth-and-profitability.md.
    """
    path = (f"/vX/reference/financials?ticker={ticker}"
            f"&timeframe=quarterly&limit={limit}&order=desc")
    try:
        doc, _ = client.get(path)
    except FetchError as exc:
        print(f"  WARN: financials for {ticker}: {exc}", file=sys.stderr)
        return []
    return doc.get("results") or []


def _val(node, key):
    """Pull `.value` from a Massive financials field, safe against null."""
    sub = (node or {}).get(key)
    if not sub:
        return None
    return sub.get("value")


def _first_non_null(rows, section, keys):
    """Walk quarterly rows (most-recent first) and pull the first non-null
    value for any of `keys` in `financials.<section>`. Returns (value, key).
    """
    for r in rows:
        fin = r.get("financials") or {}
        node = fin.get(section) or {}
        for k in keys:
            v = _val(node, k)
            if v is not None:
                return float(v), k
    return None, None


def compute_metrics_from_financials(rows):
    """Walk a list of quarterly rows and compute the TTM metrics we need.

    Returns a dict matching the schema's `metrics` object. Nulls where
    inputs are missing (don't impute zeros). See references for the rules.
    """
    out = {
        "revenue_ttm": None,
        "revenue_prior_ttm": None,
        "revenue_growth_ttm": None,
        "operating_income_ttm": None,
        "depreciation_amortization_ttm": None,
        "ebitda_ttm": None,
        "ebitda_margin": None,
        "diluted_eps_ttm": None,
        # EV component pieces (C11). long_term_debt kept for back-compat.
        "long_term_debt": None,
        "total_debt": None,
        "cash_and_equivalents": None,
        "operating_lease_liability": None,
        "minority_interest": None,
    }

    # Walk rows; collect quarters that have a revenue value (drops 10-K rows)
    rev_quarters = []
    for r in rows:
        fin = r.get("financials") or {}
        inc = fin.get("income_statement") or {}
        bs = fin.get("balance_sheet") or {}
        rev = _val(inc, "revenues")
        op = _val(inc, "operating_income_loss")
        da = _val(inc, "depreciation_and_amortization")
        eps = _val(inc, "diluted_earnings_per_share")
        ltd = _val(bs, "long_term_debt")
        rev_quarters.append({
            "end_date": r.get("end_date"),
            "rev": rev,
            "op": op,
            "da": da,
            "eps": eps,
            "ltd": ltd,
        })

    # The most recent LTD that's not null (balance sheet is mostly available)
    for q in rev_quarters:
        if q["ltd"] is not None:
            out["long_term_debt"] = float(q["ltd"])
            break

    # Filter to revenue-bearing quarters only
    with_rev = [q for q in rev_quarters if q["rev"] is not None]

    if len(with_rev) >= 4:
        ttm = with_rev[:4]
        out["revenue_ttm"] = float(sum(q["rev"] for q in ttm))
        op_vals = [q["op"] for q in ttm if q["op"] is not None]
        if len(op_vals) == 4:
            out["operating_income_ttm"] = float(sum(op_vals))
        elif op_vals:
            # Annualize from what we have
            out["operating_income_ttm"] = float(sum(op_vals) * (4.0 / len(op_vals)))
        da_vals = [q["da"] for q in ttm if q["da"] is not None]
        if len(da_vals) > 0:
            # Use what's available; fall back to zero contribution in EBITDA
            out["depreciation_amortization_ttm"] = float(sum(da_vals)
                                                          * (4.0 / len(da_vals)))
        if out["operating_income_ttm"] is not None:
            out["ebitda_ttm"] = (out["operating_income_ttm"]
                                  + (out["depreciation_amortization_ttm"] or 0.0))
            if out["revenue_ttm"] and out["revenue_ttm"] > 0:
                out["ebitda_margin"] = out["ebitda_ttm"] / out["revenue_ttm"]
        eps_vals = [q["eps"] for q in ttm if q["eps"] is not None]
        if len(eps_vals) == 4:
            out["diluted_eps_ttm"] = float(sum(eps_vals))

    if len(with_rev) >= 8:
        prior = with_rev[4:8]
        out["revenue_prior_ttm"] = float(sum(q["rev"] for q in prior))
        if (out["revenue_ttm"] is not None
                and out["revenue_prior_ttm"]
                and out["revenue_prior_ttm"] > 0):
            out["revenue_growth_ttm"] = (
                out["revenue_ttm"] / out["revenue_prior_ttm"]) - 1.0

    # ----- C11 EV component sourcing -----
    st_debt, _ = _first_non_null(rows, "balance_sheet",
                                  ("short_term_debt", "current_debt",
                                   "debt_current"))
    lt_debt = out["long_term_debt"]
    if lt_debt is not None and st_debt is not None:
        out["total_debt"] = float(lt_debt) + float(st_debt)
    elif lt_debt is not None:
        out["total_debt"] = float(lt_debt)
    elif st_debt is not None:
        out["total_debt"] = float(st_debt)

    cash, _ = _first_non_null(rows, "balance_sheet",
                               ("cash_and_cash_equivalents",
                                "cash_short_term_investments",
                                "cash"))
    if cash is not None:
        out["cash_and_equivalents"] = float(cash)

    lease_nc, _ = _first_non_null(rows, "balance_sheet",
                                   ("operating_lease_liabilities_noncurrent",
                                    "operating_lease_liability_noncurrent"))
    lease_c, _ = _first_non_null(rows, "balance_sheet",
                                  ("operating_lease_liabilities_current",
                                   "operating_lease_liability_current"))
    if lease_nc is not None or lease_c is not None:
        out["operating_lease_liability"] = float(lease_nc or 0.0) + float(lease_c or 0.0)

    minority, _ = _first_non_null(rows, "balance_sheet",
                                   ("minority_interest",
                                    "noncontrolling_interest",
                                    "redeemable_noncontrolling_interest"))
    if minority is not None:
        out["minority_interest"] = float(minority)

    return out


def compute_ev_components(market_cap, metrics, ticker):
    """C11: EV = mcap + total_debt - cash + operating_leases + minority_interest.

    Required: market_cap, total_debt, cash_and_equivalents. Missing any of
    them raises NotImplementedError so we don't silently emit the old
    overcounted EV. Optional fields default to 0 and populate
    `missing_fields` for the audit trail.
    """
    if market_cap is None:
        raise NotImplementedError(
            f"{ticker}: market_cap required for EV but missing")
    total_debt = metrics.get("total_debt")
    if total_debt is None:
        raise NotImplementedError(
            f"{ticker}: total_debt required for EV but missing "
            f"(checked short_term_debt + long_term_debt)")
    cash = metrics.get("cash_and_equivalents")
    if cash is None:
        raise NotImplementedError(
            f"{ticker}: cash_and_equivalents required for EV but missing "
            f"(checked cash_and_cash_equivalents, cash_short_term_investments)")

    missing = []
    leases = metrics.get("operating_lease_liability")
    if leases is None:
        missing.append("operating_lease_liability")
        leases = 0.0
    minority = metrics.get("minority_interest")
    if minority is None:
        missing.append("minority_interest")
        minority = 0.0

    ev = (float(market_cap) + float(total_debt) - float(cash)
           + float(leases) + float(minority))
    return {
        "mcap": float(market_cap),
        "total_debt": float(total_debt),
        "cash": float(cash),
        "operating_leases": float(leases),
        "minority": float(minority),
        "ev": ev,
        "missing_fields": missing,
    }


def compute_multiples(market_cap, price, metrics, ticker):
    """Apply the multiples per references/multiples-methodology.md.

    Returns (multiples, ev, ev_components). When EV cannot be computed
    (required component missing), ev_components is None and EV-based
    multiples are None.
    """
    out = {"ev_sales": None, "ev_ebitda": None, "p_e": None}
    ev_components = None
    ev = None
    try:
        ev_components = compute_ev_components(market_cap, metrics, ticker)
        ev = ev_components["ev"]
    except NotImplementedError as exc:
        print(f"  WARN: EV skipped for {ticker}: {exc}", file=sys.stderr)
    if ev is not None and metrics.get("revenue_ttm") and metrics["revenue_ttm"] > 0:
        out["ev_sales"] = ev / metrics["revenue_ttm"]
    if ev is not None and metrics.get("ebitda_ttm") and metrics["ebitda_ttm"] > 0:
        out["ev_ebitda"] = ev / metrics["ebitda_ttm"]
    if (price is not None
            and metrics.get("diluted_eps_ttm")
            and metrics["diluted_eps_ttm"] > 0):
        out["p_e"] = price / metrics["diluted_eps_ttm"]
    return out, ev, ev_components


def assemble_name(ticker, sources):
    """Pull all data for one name. Returns the schema's per-name dict shape."""
    det = get_ticker_details(ticker)
    sources.append({"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}",
                    "fetched_at": utcnow_iso(),
                    "context": f"ticker details for {ticker}"})
    if not det:
        return {
            "ticker": ticker,
            "name": ticker,
            "market_cap": None,
            "enterprise_value": None,
            "ev_components": None,
            "price": None,
            "sector": None,
            "multiples": {"ev_sales": None, "ev_ebitda": None, "p_e": None},
            "metrics": {},
            "data_status": "empty",
        }

    price = get_snapshot_price(ticker)
    sources.append({"endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                    "fetched_at": utcnow_iso(),
                    "context": f"current price for {ticker}"})

    rows = get_financials(ticker, limit=12)
    sources.append({"endpoint": "https://api.polygon.io/vX/reference/financials",
                    "fetched_at": utcnow_iso(),
                    "context": f"8q quarterly financials for {ticker}"})

    metrics = compute_metrics_from_financials(rows)
    mcap = det.get("market_cap")
    multiples, ev, ev_components = compute_multiples(mcap, price, metrics, ticker)

    if not rows or all(v is None for v in metrics.values()):
        data_status = "empty"
    elif any(v is None for v in multiples.values()):
        data_status = "partial"
    else:
        data_status = "full"

    return {
        "ticker": ticker,
        "name": det.get("name") or ticker,
        "market_cap": mcap,
        "enterprise_value": ev,
        "ev_components": ev_components,
        "price": price,
        "sector": det.get("sic_description"),
        "multiples": multiples,
        "metrics": metrics,
        "data_status": data_status,
    }


# ----- Summary stats -----

def summarize(values):
    """Median / mean / p25 / p75 / n. Drops Nones (never imputes zero)."""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"median": None, "mean": None, "p25": None, "p75": None, "n": 0}
    arr = np.array(vals, dtype=float)
    return {
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "n": n,
    }


# ----- Regression-adjusted multiples -----

def fit_implied(peers, subject, multiple_key,
                controls=("revenue_growth_ttm", "ebitda_margin"),
                outlier_cap=80.0):
    """OLS multiple ~ controls across peers; predict subject's implied multiple.

    Peers with `multiple_key > outlier_cap` are excluded from the regression
    (they remain in the displayed table). This handles PANW's 232x EV/EBITDA
    and similar accounting-anomaly multiples that pull OLS to nonsense.
    The cap is documented in references/regression-adjustment.md.
    """
    rows = []
    for p in peers:
        m = (p.get("multiples") or {}).get(multiple_key)
        if m is None or m <= 0 or m > outlier_cap:
            continue
        ctrl_vals = [(p.get("metrics") or {}).get(c) for c in controls]
        if any(v is None for v in ctrl_vals):
            continue
        rows.append((float(m), [float(v) for v in ctrl_vals]))
    if len(rows) < 4:
        return None, len(rows)
    y = np.array([r[0] for r in rows])
    X_cols = [np.ones(len(rows))]
    for i in range(len(controls)):
        X_cols.append(np.array([r[1][i] for r in rows]))
    X = np.column_stack(X_cols)
    coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    subj_ctrl = [(subject.get("metrics") or {}).get(c) for c in controls]
    if any(v is None for v in subj_ctrl):
        return None, len(rows)
    implied = float(coef[0] + sum(coef[i+1] * subj_ctrl[i]
                                    for i in range(len(controls))))
    actual = (subject.get("multiples") or {}).get(multiple_key)
    disc = ((actual / implied) - 1.0) if (actual and implied and implied > 0) else None
    y_pred = X @ coef
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
    return {
        "implied": round(implied, 2),
        "actual": round(float(actual), 2) if actual is not None else None,
        "discount_or_premium": round(disc, 4) if disc is not None else None,
        "coefficients": {
            "intercept": round(float(coef[0]), 4),
            **{c: round(float(coef[i+1]), 6) for i, c in enumerate(controls)},
        },
        "r_squared": round(r2, 4) if r2 is not None else None,
    }, len(rows)


def generate_read(subject_ticker, regression_results, peers, subject,
                  low_n=False):
    """One-sentence banker take. References/rendering.md spells out the form.

    When low_n is True (regression fit on < 8 peers), prefer the raw
    median-vs-actual framing because the regression coefficients are
    too noisy to drive the headline. The block is still shown in the
    table, just not in the read.
    """
    # When low_n, fall straight to the cohort-median framing
    if low_n:
        # Compare subject's three multiples to peer medians and pick the
        # one with the largest |gap|
        gaps = []
        label_map = {"ev_sales": "EV/Sales",
                      "ev_ebitda": "EV/EBITDA",
                      "p_e": "P/E"}
        for key in ("ev_sales", "ev_ebitda", "p_e"):
            subj = (subject.get("multiples") or {}).get(key)
            peer_vals = [(p["multiples"] or {}).get(key) for p in peers
                          if (p["multiples"] or {}).get(key) is not None
                          and (p["multiples"] or {}).get(key) > 0]
            if subj and peer_vals and len(peer_vals) >= 3:
                med = float(np.median(peer_vals))
                gap = (subj / med) - 1.0
                gaps.append((key, subj, med, gap))
        if gaps:
            # Median gap across the three multiples
            med_gap = float(np.median([g[3] for g in gaps]))
            if med_gap <= -0.10:
                headline = f"{subject_ticker} screens cheap vs the peer cohort on raw multiples."
            elif med_gap >= 0.10:
                headline = f"{subject_ticker} screens rich vs the peer cohort on raw multiples."
            else:
                headline = f"{subject_ticker} trades roughly in line with peer cohort medians."
            # Driver: largest |gap|
            driver = max(gaps, key=lambda g: abs(g[3]))
            disc_pct = round(driver[3] * 100)
            tag = "discount" if disc_pct < 0 else "premium"
            return (f"{headline} The biggest gap is on {label_map[driver[0]]} "
                    f"({driver[1]:.1f}x vs peer median {driver[2]:.1f}x, a "
                    f"{abs(disc_pct)}% {tag}); n=5-7 peers makes the regression-"
                    f"adjusted view indicative, not definitive.")
    # Cohort headline based on median discount/premium across multiples
    discs = [r["discount_or_premium"] for r in regression_results.values()
             if r and r.get("discount_or_premium") is not None]
    if discs:
        med_disc = float(np.median(discs))
        if med_disc <= -0.10:
            headline = f"{subject_ticker} screens cheap on growth-adjusted multiples."
        elif med_disc >= 0.10:
            headline = f"{subject_ticker} screens rich on growth-adjusted multiples."
        else:
            headline = f"{subject_ticker} trades roughly in line with peers on growth-adjusted multiples."
        # Driver: multiple with largest |discount|
        driver_key, driver = max(
            ((k, r) for k, r in regression_results.items()
              if r and r.get("discount_or_premium") is not None),
            key=lambda kv: abs(kv[1]["discount_or_premium"]),
            default=(None, None),
        )
        if driver_key and abs(driver["discount_or_premium"]) >= 0.05:
            disc_pct = round(driver["discount_or_premium"] * 100)
            tag = "discount" if disc_pct < 0 else "premium"
            label_map = {"ev_sales": "EV/Sales",
                          "ev_ebitda": "EV/EBITDA",
                          "p_e": "P/E"}
            driver_label = label_map.get(driver_key, driver_key)
            # Tag the explanation off the underlying metric gap vs peers
            subj_margin = (subject.get("metrics") or {}).get("ebitda_margin")
            subj_growth = (subject.get("metrics") or {}).get("revenue_growth_ttm")
            peer_margins = [p["metrics"].get("ebitda_margin") for p in peers
                            if p["metrics"].get("ebitda_margin") is not None]
            peer_growths = [p["metrics"].get("revenue_growth_ttm") for p in peers
                            if p["metrics"].get("revenue_growth_ttm") is not None]
            explanation = ""
            if driver_key == "ev_ebitda" and peer_margins and subj_margin is not None:
                med_pm = float(np.median(peer_margins))
                if subj_margin < med_pm - 0.03:
                    explanation = "margin gap vs peers"
                elif subj_margin > med_pm + 0.03:
                    explanation = "margin premium vs peers"
            elif driver_key == "p_e" and peer_growths and subj_growth is not None:
                med_pg = float(np.median(peer_growths))
                if subj_growth < med_pg - 0.03:
                    explanation = "growth gap vs peers"
                elif subj_growth > med_pg + 0.03:
                    explanation = "growth premium vs peers"
            tail = (
                f" The {tag} is concentrated on {driver_label}"
                f" ({explanation})." if explanation else
                f" The {tag} is concentrated on {driver_label}."
            )
            return headline + tail
        return headline
    # No regression results: fall back to median-vs-actual on EV/Sales
    subj_ev_s = (subject.get("multiples") or {}).get("ev_sales")
    peer_ev_s = [p["multiples"].get("ev_sales") for p in peers
                  if p["multiples"].get("ev_sales") is not None]
    if subj_ev_s and peer_ev_s:
        med = float(np.median(peer_ev_s))
        diff_pct = round((subj_ev_s / med - 1) * 100)
        tag = "discount" if diff_pct < 0 else "premium"
        return (f"{subject_ticker} trades at {subj_ev_s:.1f}x EV/Sales vs the peer "
                f"median {med:.1f}x; the {tag} sits at {abs(diff_pct)}% before "
                f"adjusting for growth and margin.")
    return f"{subject_ticker} comp set built; insufficient peer data for a regression read."


# ----- CLI -----

ap = argparse.ArgumentParser(description="pitch-comps reference implementation")
ap.add_argument("ticker", nargs="?", default="CRM",
                help="Subject ticker (default: CRM)")
ap.add_argument("--peers", type=str, default=None,
                help="Comma-separated peer override (skips the curated map)")
ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
args = ap.parse_args()
fmt = resolve_output_format(args.format)

subject_ticker = args.ticker.upper()

# ----- Peer selection -----

if args.peers:
    peers_list = [p.strip().upper() for p in args.peers.split(",") if p.strip()]
    peer_selection_method = "curated_override"  # user-supplied counts as override
elif subject_ticker in PEER_OVERRIDES:
    peers_list = PEER_OVERRIDES[subject_ticker]
    peer_selection_method = "curated_override"
else:
    # Last-resort SIC fallback (skipping correlation here for simplicity; the
    # correlation path is documented in references/peer-selection.md and is a
    # clean PR extension).
    det = get_ticker_details(subject_ticker)
    if not det:
        print(f"ERROR: subject {subject_ticker} not found", file=sys.stderr)
        sys.exit(1)
    sic = det.get("sic_code")
    if not sic:
        print(f"ERROR: no SIC code for {subject_ticker}; can't peer-select",
              file=sys.stderr)
        sys.exit(1)
    print(f"Peer SIC fallback on SIC {sic}; querying reference tickers...",
          file=sys.stderr)
    path = f"/v3/reference/tickers?market=stocks&active=true&type=CS&sic_code={sic}&limit=50"
    try:
        doc, _ = client.get(path)
        rows = doc.get("results") or []
    except FetchError as exc:
        print(f"ERROR: SIC fallback fetch failed: {exc}", file=sys.stderr)
        sys.exit(1)
    peers_list = [r["ticker"] for r in rows
                  if r.get("ticker") and r["ticker"] != subject_ticker][:8]
    peer_selection_method = "sic_fallback"

# ----- Pull data -----

print(f"Building comp set for {subject_ticker}: {len(peers_list)} peers",
      file=sys.stderr)
sources = []
print(f"  Fetching subject {subject_ticker}...", file=sys.stderr)
subject = assemble_name(subject_ticker, sources)

peer_objs = []
for tk in peers_list:
    print(f"  Fetching peer {tk}...", file=sys.stderr)
    peer_objs.append(assemble_name(tk, sources))
    time.sleep(0.15)  # polite throttle even on paid

# ----- Summary stats over peers (subject excluded) -----

summary = {}
for key in ("ev_sales", "ev_ebitda", "p_e"):
    summary[key] = summarize([(p["multiples"] or {}).get(key) for p in peer_objs])
summary["revenue_growth_ttm"] = summarize(
    [(p["metrics"] or {}).get("revenue_growth_ttm") for p in peer_objs])
summary["ebitda_margin"] = summarize(
    [(p["metrics"] or {}).get("ebitda_margin") for p in peer_objs])

# ----- Regression-adjusted multiples -----

reg_results = {}
n_used_max = 0
for key in ("ev_sales", "ev_ebitda", "p_e"):
    result, n_used = fit_implied(peer_objs, subject, key)
    if result is not None:
        reg_results[key] = result
        n_used_max = max(n_used_max, n_used)

regression_adjusted = {
    "controls": ["revenue_growth_ttm", "ebitda_margin"],
    "n_peers_used": n_used_max,
    "low_n_warning": n_used_max < 8,
    "results": reg_results,
}

# ----- Read -----

read_line = generate_read(subject_ticker, reg_results, peer_objs, subject,
                            low_n=regression_adjusted["low_n_warning"])

# ----- Tier detection -----

tier = "A"
tier_caveats = []

# Count of peers with full multiples
n_full = sum(1 for p in peer_objs if p["data_status"] in ("full", "partial")
             and any((p["multiples"] or {}).get(k) is not None
                     for k in ("ev_sales", "ev_ebitda", "p_e")))

# ----- Payload -----

payload = {
    "tier": tier,
    "tier_caveats": tier_caveats,
    "mode": "table",
    "run_at": NOW_UTC.isoformat(),
    "subject": subject,
    "peers": peer_objs,
    "peer_selection": {
        "method": peer_selection_method,
        "n_peers": len(peer_objs),
        "n_peers_with_full_multiples": n_full,
    },
    "summary": summary,
    "regression_adjusted": regression_adjusted,
    "read": read_line,
    "sources": sources,
}


# ----- Renderer -----

def fmt_mult(x):
    if x is None or (isinstance(x, float) and x != x):  # NaN check
        return "n/a"
    return f"{x:.1f}x"


def fmt_pct(x, signed=True, decimals=1):
    if x is None:
        return "n/a"
    if signed:
        return f"{x*100:+.{decimals}f}%"
    return f"{x*100:.{decimals}f}%"


def fmt_mcap_ev(x):
    if x is None:
        return "n/a"
    b = x / 1e9
    if abs(b) >= 100:
        return f"${b:,.0f}B"
    return f"${b:,.1f}B"


def short_name(name, ticker):
    if not name:
        return ticker
    # First word, strip "Inc.", "Corp", "Holdings" etc., title-case
    cleaned = name.split(",")[0].split("Inc")[0].split("Corp")[0]
    cleaned = cleaned.split("Holdings")[0].split("Group")[0]
    cleaned = cleaned.strip()
    # Cap at 14 chars
    return cleaned[:14] if cleaned else ticker


def render(payload):
    lines = []
    subj = payload["subject"]
    sel = payload["peer_selection"]
    lines.append(
        f"{subj['ticker']}: comp set as of {TODAY.isoformat()} · "
        f"{sel['n_peers']} peers selected via {sel['method']}"
    )
    if payload["tier"] == "B":
        lines.append("Tier B run (free Basic, peer fanout rate-limited). "
                     "Re-run on Stocks Starter for full fanout.")
    lines.append("")
    lines.append(
        f"Subject: {subj['ticker']} ({subj['name']})  "
        f"MCap {fmt_mcap_ev(subj['market_cap'])}  "
        f"EV {fmt_mcap_ev(subj['enterprise_value'])}"
    )
    lines.append("")

    # Build the table body
    headers = ["Ticker", "Name", "EV/Sales", "EV/EBITDA", "P/E", "Rev Growth", "EBITDA Mgn"]

    def row_for(name_dict, is_subject=False):
        mults = name_dict.get("multiples") or {}
        mets = name_dict.get("metrics") or {}
        label = name_dict["ticker"]
        if is_subject:
            label = f"{name_dict['ticker']} (subject)"
        return [
            label,
            "" if is_subject else short_name(name_dict.get("name"), name_dict["ticker"]),
            fmt_mult(mults.get("ev_sales")),
            fmt_mult(mults.get("ev_ebitda")),
            fmt_mult(mults.get("p_e")),
            fmt_pct(mets.get("revenue_growth_ttm"), signed=True, decimals=0)
                if mets.get("revenue_growth_ttm") is not None else "n/a",
            fmt_pct(mets.get("ebitda_margin"), signed=False, decimals=0)
                if mets.get("ebitda_margin") is not None else "n/a",
        ]

    body = [row_for(subj, is_subject=True)]
    for p in payload["peers"]:
        body.append(row_for(p, is_subject=False))

    # Summary rows
    summ = payload["summary"]

    def summary_row(label, formatter, suffix_for=None):
        cells = [label, ""]
        for key in ("ev_sales", "ev_ebitda", "p_e"):
            b = summ.get(key) or {}
            cells.append(formatter(b.get("median") if suffix_for is None
                                    else b.get(suffix_for)))
        for key in ("revenue_growth_ttm", "ebitda_margin"):
            b = summ.get(key) or {}
            val = b.get("median") if suffix_for is None else b.get(suffix_for)
            signed = (key == "revenue_growth_ttm")
            cells.append(fmt_pct(val, signed=signed, decimals=0)
                          if val is not None else "n/a")
        return cells

    # Median row
    median_row = ["Median", ""]
    for key in ("ev_sales", "ev_ebitda", "p_e"):
        median_row.append(fmt_mult((summ.get(key) or {}).get("median")))
    median_row.append(fmt_pct((summ.get("revenue_growth_ttm") or {}).get("median"),
                                signed=True, decimals=0)
                       if (summ.get("revenue_growth_ttm") or {}).get("median") is not None
                       else "n/a")
    median_row.append(fmt_pct((summ.get("ebitda_margin") or {}).get("median"),
                                signed=False, decimals=0)
                       if (summ.get("ebitda_margin") or {}).get("median") is not None
                       else "n/a")

    mean_row = ["Mean", ""]
    for key in ("ev_sales", "ev_ebitda", "p_e"):
        mean_row.append(fmt_mult((summ.get(key) or {}).get("mean")))
    mean_row.append(fmt_pct((summ.get("revenue_growth_ttm") or {}).get("mean"),
                              signed=True, decimals=0)
                     if (summ.get("revenue_growth_ttm") or {}).get("mean") is not None
                     else "n/a")
    mean_row.append(fmt_pct((summ.get("ebitda_margin") or {}).get("mean"),
                              signed=False, decimals=0)
                     if (summ.get("ebitda_margin") or {}).get("mean") is not None
                     else "n/a")

    def range_cell(b, formatter):
        lo, hi = b.get("p25"), b.get("p75")
        if lo is None or hi is None:
            return "n/a"
        if formatter == "mult":
            return f"{lo:.1f}-{hi:.1f}x"
        if formatter == "pct_signed":
            return f"{lo*100:+.0f}-{hi*100:+.0f}%"
        if formatter == "pct":
            return f"{lo*100:.0f}-{hi*100:.0f}%"
        return "n/a"

    pct_row = ["25/75 %ile", ""]
    for key in ("ev_sales", "ev_ebitda", "p_e"):
        pct_row.append(range_cell(summ.get(key) or {}, "mult"))
    pct_row.append(range_cell(summ.get("revenue_growth_ttm") or {}, "pct_signed"))
    pct_row.append(range_cell(summ.get("ebitda_margin") or {}, "pct"))

    # Combine all rows and compute widths
    table_rows = [headers] + body + [median_row, mean_row, pct_row]
    widths = [max(len(r[i]) for r in table_rows) for i in range(len(headers))]

    def fmt_row(cells):
        parts = []
        for i, c in enumerate(cells):
            if i <= 1:
                parts.append(c.ljust(widths[i]))
            else:
                parts.append(c.rjust(widths[i]))
        return "  ".join(parts).rstrip()

    # Render
    lines.append(fmt_row(headers))
    lines.append(fmt_row(body[0]))  # subject
    # Divider
    div = "-" * (sum(widths) + 2 * (len(widths) - 1))
    lines.append(div)
    for r in body[1:]:
        lines.append(fmt_row(r))
    lines.append(div)
    lines.append(fmt_row(median_row))
    lines.append(fmt_row(mean_row))
    lines.append(fmt_row(pct_row))
    lines.append("")

    # Regression-adjusted block
    reg = payload["regression_adjusted"]
    if reg["results"]:
        lines.append("Regression-adjusted (controls for growth + EBITDA margin)")
        label_map = {"ev_sales": "EV/Sales", "ev_ebitda": "EV/EBITDA", "p_e": "P/E"}
        any_rendered = False
        for key in ("ev_sales", "ev_ebitda", "p_e"):
            r = reg["results"].get(key)
            if not r:
                continue
            implied = r.get("implied")
            actual = r.get("actual")
            disc = r.get("discount_or_premium")
            r2 = r.get("r_squared")
            if implied is None or actual is None or disc is None:
                continue
            # Skip non-meaningful implied values (negative, or absurdly large)
            if implied <= 0 or abs(disc) > 5.0:
                continue
            tag = "discount" if disc < 0 else "premium"
            arrow = "→"
            label = label_map[key]
            lines.append(
                f"- Implied {label}: {implied:>5.1f}x  vs subject "
                f"{actual:.1f}x  {arrow} subject trades at "
                f"{abs(disc)*100:.0f}% {tag}"
            )
            any_rendered = True
        if not any_rendered:
            lines.append(
                "- Regression produced no meaningful implied multiples"
                " (low n and high coefficient variance)."
            )
        if reg.get("low_n_warning"):
            lines.append(
                f"Regression note: n={reg['n_peers_used']} peers, DoF tight; "
                f"coefficients indicative."
            )
        lines.append("")

    lines.append(f"Read: {payload['read']}")
    return "\n".join(lines)


rendered = render(payload)

# ----- Write output -----

out_name = "pitch-comps-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# pitch-comps run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Subject: {subject_ticker}\n")
    fout.write(f"Peer selection: {peer_selection_method}\n")
    fout.write(f"Tier: {tier}\n\n")
    fout.write("## Layer 1: canonical JSON (live data)\n\n")
    fout.write("```json\n")
    fout.write(json.dumps(payload, indent=2, default=str))
    fout.write("\n```\n\n")
    fout.write("## Layer 2: rendered table (live data)\n\n")
    fout.write("```\n")
    fout.write(rendered)
    fout.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
