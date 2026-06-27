#!/usr/bin/env python3
"""
Reference implementation of the valuation-sanity-check skill.

Takes a subject ticker plus the analyst's thesis (target price, assumed
revenue growth, assumed EBITDA margin, horizon years). Pulls the live
state of the name, builds the peer set using pitch-comps' override map,
computes target-implied multiples vs peer 25-75 band, compares assumed
growth and margin to peer band, runs a simplified reverse-DCF to back
out the revenue CAGR the current price already implies given the
assumed margin, and emits two output layers:

  Layer 1: canonical JSON matching skills/valuation-sanity-check/output-schema.json
  Layer 2: sell-side flash-note rendered to
           examples/valuation-sanity-check-output.md

Usage:
    python3 examples/run-valuation-sanity-check.py NVDA \\
        --target-price 250 \\
        --assumed-growth 0.28 \\
        --assumed-margin 0.60 \\
        --horizon 5

Reads MASSIVE_API_KEY from env. Writes to
examples/valuation-sanity-check-output.md (gitignored).
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
    da_annualized,
    operating_income_annualized,
    sample_empirical,
    sample_normal,
    spearman_sensitivity,
    percentile_summary,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()


# Shared peer-override map. Mirrors pitch-comps and earnings-drilldown.
# Updates should land in all three (see references/peer-selection.md).
PEER_OVERRIDES = {
    "CRM":  ["ORCL", "SAP", "NOW", "WDAY", "ADBE", "INTU", "PANW", "CRWD"],
    "ORCL": ["CRM", "SAP", "MSFT", "ADBE", "NOW", "WDAY", "INTU"],
    "ADBE": ["CRM", "ORCL", "INTU", "NOW", "WDAY", "SAP"],
    "NOW":  ["CRM", "WDAY", "ADBE", "ORCL", "INTU", "PANW"],
    "WDAY": ["CRM", "NOW", "ADBE", "INTU", "ORCL"],
    "INTU": ["CRM", "ADBE", "ORCL", "NOW", "WDAY"],
    "PANW": ["CRWD", "FTNT", "ZS", "S", "CHKP", "OKTA"],
    "CRWD": ["PANW", "FTNT", "ZS", "S", "OKTA"],
    "AAPL":  ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "NVDA":  ["AMD", "AVGO", "TSM", "MU", "ARM", "QCOM", "INTC"],
    "MSFT":  ["GOOGL", "AMZN", "META", "ORCL", "CRM", "AAPL"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "SNAP"],
    "META":  ["GOOGL", "SNAP", "PINS", "NFLX", "AMZN"],
    "AMZN":  ["GOOGL", "META", "MSFT", "AAPL", "SHOP", "WMT"],
    "TSLA":  ["NIO", "RIVN", "LCID", "F", "GM"],
    "JPM": ["BAC", "WFC", "C", "GS", "MS"],
    "GS":  ["MS", "JPM", "BAC", "C"],
    "V":  ["MA", "PYPL", "AXP"],
    "MA": ["V", "PYPL", "AXP"],
    "LLY":  ["NVO", "PFE", "MRK", "ABBV", "BMY", "AMGN"],
    "MRK":  ["LLY", "PFE", "ABBV", "BMY", "AMGN", "JNJ"],
    "NVO":  ["LLY", "PFE", "MRK", "ABBV"],
    "XOM": ["CVX", "COP", "EOG", "OXY"],
    "CVX": ["XOM", "COP", "EOG", "OXY"],
}

# Hardcoded constants documented in references/.
WACC = 0.09       # See references/reverse-dcf.md
TAX_PROXY = 0.21  # See references/multiple-sanity.md (P/E section)


# ----- HTTP -----

def get_ticker_details(ticker):
    try:
        doc, _ = client.get(f"/v3/reference/tickers/{ticker}")
    except FetchError as exc:
        print(f"  WARN: ticker details for {ticker}: {exc}", file=sys.stderr)
        return None
    return doc.get("results")


def get_snapshot_price(ticker):
    """Walk the lastTrade -> min.c -> day.c -> prevDay.c chain via lib (D4/D5)."""
    try:
        doc, _ = client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    except FetchError as exc:
        print(f"  WARN: snapshot for {ticker}: {exc}", file=sys.stderr)
        return None
    return resolve_price(doc).price


def get_financials(ticker, limit=8):
    path = (f"/vX/reference/financials?ticker={ticker}"
            f"&timeframe=quarterly&limit={limit}&order=desc")
    try:
        doc, _ = client.get(path)
    except FetchError as exc:
        print(f"  WARN: financials for {ticker}: {exc}", file=sys.stderr)
        return []
    return doc.get("results") or []


def _val(node, key):
    sub = (node or {}).get(key)
    if not sub:
        return None
    return sub.get("value")


# ----- Per-name metrics -----

_SHARES_FIELDS = (
    "weighted_average_diluted_shares_outstanding",
    "weighted_average_basic_shares_outstanding",
    "weighted_shares_outstanding",
    "share_class_shares_outstanding",
)


def _pick_shares(rows):
    """Walk quarterly rows and pick the first available shares value
    using the documented fallback chain. Returns (value, source_field)."""
    for field in _SHARES_FIELDS:
        for r in rows:
            fin = r.get("financials") or {}
            inc = fin.get("income_statement") or {}
            v = _val(inc, field)
            if v is not None and float(v) > 0:
                return float(v), field
    return None, None


def _first_non_null(rows, section, keys):
    """Walk quarterly rows (most-recent first) and pull the first non-null
    value for any of `keys` in `financials.<section>`. Returns (value, key)."""
    for r in rows:
        fin = r.get("financials") or {}
        node = fin.get(section) or {}
        for k in keys:
            v = _val(node, k)
            if v is not None:
                return float(v), k
    return None, None


def compute_metrics_from_financials(rows):
    """TTM metrics from quarterly financials. Duplicates pitch-comps logic."""
    out = {
        "revenue_ttm": None,
        "revenue_prior_ttm": None,
        "revenue_growth_ttm": None,
        "operating_income_ttm": None,
        "op_income_source": "unavailable",
        "depreciation_amortization_ttm": None,
        "da_source": "unavailable",
        "da_reported": False,  # C7: was the underlying D&A actually reported?
        "ebitda_ttm": None,
        "ebitda_margin": None,
        "diluted_eps_ttm": None,
        # EV component pieces (C11). long_term_debt kept for back-compat.
        "long_term_debt": None,
        "total_debt": None,
        # debt_source: 'reported_total' | 'synthesized_ltd_plus_std' |
        # 'long_term_only' | 'unavailable_defaulted_to_zero'
        "debt_source": None,
        "cash_and_equivalents": None,
        # cash_source: a real field name (e.g. 'cash_and_cash_equivalents') or
        # 'unavailable_defaulted_to_zero'
        "cash_source": None,
        "operating_lease_liability": None,
        "minority_interest": None,
        # Share-count source (C12)
        "shares_outstanding": None,
        "shares_source": None,
    }
    rev_quarters = []
    for r in rows:
        fin = r.get("financials") or {}
        inc = fin.get("income_statement") or {}
        bs = fin.get("balance_sheet") or {}
        rev_quarters.append({
            "end_date": r.get("end_date"),
            "rev": _val(inc, "revenues"),
            "op": _val(inc, "operating_income_loss"),
            "da": _val(inc, "depreciation_and_amortization"),
            "eps": _val(inc, "diluted_earnings_per_share"),
            "ltd": _val(bs, "long_term_debt"),
        })
    for q in rev_quarters:
        if q["ltd"] is not None:
            out["long_term_debt"] = float(q["ltd"])
            break
    with_rev = [q for q in rev_quarters if q["rev"] is not None]
    if len(with_rev) >= 4:
        ttm = with_rev[:4]
        out["revenue_ttm"] = float(sum(q["rev"] for q in ttm))
        # H5: route operating income + D&A through the shared lib
        # helper so this script and pitch-comps produce identical
        # numbers for the same input financials. Source tags ('LTM' vs
        # 'Q4' vs 'unavailable') land in the per-ticker JSON output.
        op_ttm, op_source = operating_income_annualized(rows)
        out["operating_income_ttm"] = op_ttm
        out["op_income_source"] = op_source
        da_ttm, da_source = da_annualized(rows)
        out["depreciation_amortization_ttm"] = da_ttm
        out["da_source"] = da_source
        # C7: was the underlying D&A actually reported on any quarter?
        out["da_reported"] = da_source != "unavailable"
        if out["operating_income_ttm"] is not None and out["da_reported"]:
            # C7: only compute EBITDA when D&A actually exists in the filings.
            # Otherwise leaving it None signals "EBITDA not measurable" so
            # downstream comparisons drop this name rather than silently
            # comparing EBIT to peer EBITDA.
            out["ebitda_ttm"] = (out["operating_income_ttm"]
                                  + out["depreciation_amortization_ttm"])
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
    # Debt fallback chain (spec 2026-06-26 EV fallback fix):
    #   reported total_debt → synthesized (LTD + STD) → long_term_only → 0.
    # Source tag emitted in metrics["debt_source"] so consumers know which
    # path was used (and tier_caveats can fire when too many peers fell back).
    reported_total, _ = _first_non_null(rows, "balance_sheet", ("total_debt",))
    lt_debt = out["long_term_debt"]
    st_debt, _ = _first_non_null(rows, "balance_sheet",
                                  ("short_term_debt", "current_debt",
                                   "debt_current",
                                   "short_term_borrowings",
                                   "current_portion_of_long_term_debt"))
    if reported_total is not None:
        out["total_debt"] = float(reported_total)
        out["debt_source"] = "reported_total"
    elif lt_debt is not None and st_debt is not None:
        out["total_debt"] = float(lt_debt) + float(st_debt)
        out["debt_source"] = "synthesized_ltd_plus_std"
    elif lt_debt is not None:
        out["total_debt"] = float(lt_debt)
        out["debt_source"] = "long_term_only"
    elif st_debt is not None:
        out["total_debt"] = float(st_debt)
        out["debt_source"] = "short_term_only"
    # else: leave None; compute_ev_components defaults to 0 + tags source.

    # Cash fallback chain (spec 2026-06-26 EV fallback fix):
    #   cash → cash_and_cash_equivalents → cash_and_short_term_investments → 0
    # AAPL surfaces this: their balance_sheet has no bare 'cash' field; the
    # value lives under 'cash_and_cash_equivalents' or
    # 'cash_and_short_term_investments'. Keep 'cash_short_term_investments'
    # (no leading 'and_') for any rows that emit the older name.
    cash, cash_field = _first_non_null(rows, "balance_sheet",
                                        ("cash",
                                         "cash_and_cash_equivalents",
                                         "cash_and_short_term_investments",
                                         "cash_short_term_investments"))
    if cash is not None:
        out["cash_and_equivalents"] = float(cash)
        out["cash_source"] = cash_field

    # operating_lease_liability — optional, current + noncurrent
    lease_nc, _ = _first_non_null(rows, "balance_sheet",
                                   ("operating_lease_liabilities_noncurrent",
                                    "operating_lease_liability_noncurrent"))
    lease_c, _ = _first_non_null(rows, "balance_sheet",
                                  ("operating_lease_liabilities_current",
                                   "operating_lease_liability_current"))
    if lease_nc is not None or lease_c is not None:
        out["operating_lease_liability"] = float(lease_nc or 0.0) + float(lease_c or 0.0)

    # minority_interest — optional
    minority, _ = _first_non_null(rows, "balance_sheet",
                                   ("minority_interest",
                                    "noncontrolling_interest",
                                    "redeemable_noncontrolling_interest"))
    if minority is not None:
        out["minority_interest"] = float(minority)

    # ----- C12 share count source -----
    shares_val, shares_field = _pick_shares(rows)
    if shares_val is not None:
        out["shares_outstanding"] = shares_val
        out["shares_source"] = shares_field

    return out


def compute_ev_components(market_cap, metrics, ticker):
    """EV = mcap + total_debt - cash + operating_leases + minority_interest.

    Required: market_cap. If it's missing we still raise — there's no
    sensible default for equity value.

    Fallback fix (2026-06-26): cash and total_debt no longer raise. Massive's
    /vX/reference/financials does not reliably populate either field
    (AAPL has no 'cash' or 'total_debt' at all; only 'long_term_debt'),
    so strictly requiring them silently dropped most peers from EV/EBITDA
    comparison and broke --mc mode (exit_multiple n=0). They now default
    to 0 with an explicit source tag so the audit trail records which
    path was used. Tier caveats fire upstream when too many peers in a
    run fell back.
    """
    if market_cap is None:
        raise NotImplementedError(
            f"{ticker}: market_cap required for EV but missing")

    missing = []

    cash = metrics.get("cash_and_equivalents")
    cash_source = metrics.get("cash_source")
    if cash is None:
        cash = 0.0
        cash_source = "unavailable_defaulted_to_zero"
        missing.append("cash")

    total_debt = metrics.get("total_debt")
    debt_source = metrics.get("debt_source")
    if total_debt is None:
        total_debt = 0.0
        debt_source = "unavailable_defaulted_to_zero"
        missing.append("total_debt")
    elif debt_source == "long_term_only":
        missing.append("short_term_debt")

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
        "debt_source": debt_source,
        "cash": float(cash),
        "cash_source": cash_source,
        "operating_leases": float(leases),
        "minority": float(minority),
        "ev": ev,
        "missing_fields": missing,
    }


def compute_multiples(market_cap, price, metrics, ticker):
    """Apply the multiples per pitch-comps multiples-methodology.

    Returns (multiples_dict, ev_components_dict_or_None). When EV cannot
    be computed because a required component is missing, ev_components is
    None and EV-based multiples are None; the price-based P/E is still
    attempted.
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
    det = get_ticker_details(ticker)
    sources.append({"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}",
                    "fetched_at": utcnow_iso(),
                    "context": f"ticker details for {ticker}"})
    if not det:
        return None
    price = get_snapshot_price(ticker)
    sources.append({"endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                    "fetched_at": utcnow_iso(),
                    "context": f"current price for {ticker}"})
    rows = get_financials(ticker, limit=12)
    sources.append({"endpoint": "https://api.polygon.io/vX/reference/financials",
                    "fetched_at": utcnow_iso(),
                    "context": f"8q quarterly financials for {ticker}"})
    metrics = compute_metrics_from_financials(rows)

    # C12: prefer weighted-diluted shares from financials. Falls back to the
    # ticker-details `share_class_shares_outstanding` field only if every
    # financials-derived source is null (small/illiquid names sometimes
    # have no shares fields on the financials response).
    shares = metrics.get("shares_outstanding")
    shares_source = metrics.get("shares_source")
    if shares is None:
        det_class = det.get("share_class_shares_outstanding")
        det_weighted = det.get("weighted_shares_outstanding")
        if det_weighted is not None:
            shares = det_weighted
            shares_source = "ticker_details.weighted_shares_outstanding"
        elif det_class is not None:
            shares = det_class
            shares_source = "ticker_details.share_class_shares_outstanding"

    # Recompute mcap from corrected share count × price. The ticker-details
    # `market_cap` field is reported off Class A only for dual-class names
    # (GOOGL: 5.82B Class A vs 12.2B total), which understates the cap by
    # the inactive-class share count. Falling back to the API value when we
    # have no price + shares of our own.
    if shares is not None and price is not None:
        mcap = float(shares) * float(price)
    else:
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
        "shares_outstanding": shares,
        "shares_source": shares_source,
        "multiples": multiples,
        "metrics": metrics,
        "data_status": data_status,
    }


# ----- Percentile band -----

def percentile_band(values):
    """p25, p50, p75 of non-null values."""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return None, None, None, 0
    arr = np.array(vals, dtype=float)
    return (float(np.percentile(arr, 25)),
            float(np.percentile(arr, 50)),
            float(np.percentile(arr, 75)),
            n)


def status_for(value, p25, p75):
    if value is None or p25 is None or p75 is None:
        return "n_a"
    if value > p75:
        return "above"
    if value < p25:
        return "below"
    return "in_line"


# ----- Reverse-DCF -----

def reverse_dcf_implied_cagr(current_ev, revenue_ttm, assumed_margin,
                              exit_multiple, wacc, horizon):
    """Solve for g s.t. PV(rev*(1+g)^h * margin * exit_mult @ wacc) = current_EV.

    Closed-form: (1+g)^h = current_EV * (1+wacc)^h
                            / (rev * margin * exit_mult)
    """
    if not (current_ev and revenue_ttm and assumed_margin and exit_multiple
            and horizon):
        return None
    numer = current_ev * ((1.0 + wacc) ** horizon)
    denom = revenue_ttm * assumed_margin * exit_multiple
    if denom <= 0:
        return None
    ratio = numer / denom
    if ratio <= 0:
        return None
    return (ratio ** (1.0 / horizon)) - 1.0


def fair_value_at_peer_median(subject, peer_median_cagr, assumed_margin,
                                exit_multiple, wacc, horizon, ev_net_non_mcap,
                                shares):
    """Back fair value per share from a peer-median EV/EBITDA exit multiple.

    `ev_net_non_mcap` is the non-equity part of EV (total_debt - cash +
    operating_leases + minority_interest). We unwind the same components
    used in current_ev so fair_mcap = fair_ev - ev_net_non_mcap stays in
    parity with the corrected EV math (C11).
    """
    if not (peer_median_cagr is not None and assumed_margin and exit_multiple
            and horizon and shares and shares > 0):
        return None
    rev = subject["metrics"].get("revenue_ttm")
    if not rev:
        return None
    fair_rev = rev * ((1.0 + peer_median_cagr) ** horizon)
    fair_ebitda = fair_rev * assumed_margin
    fair_ev_horizon = fair_ebitda * exit_multiple
    fair_pv = fair_ev_horizon / ((1.0 + wacc) ** horizon)
    fair_mcap = fair_pv - (ev_net_non_mcap or 0.0)
    return fair_mcap / shares


# ----- Monte Carlo fair-value distribution -----

def compute_mc_fair_value(
    *,
    growth_values,
    margin_values,
    exit_multiple_values,
    revenue_ttm,
    wacc,
    horizon,
    ev_net_non_mcap,
    shares,
    n_samples,
    distribution,
    seed,
    current_price,
    target_price,
):
    """Sample a joint draw of (growth, margin, exit_multiple) and compute the
    induced fair-value distribution.

    Returns a dict ready to drop into the `monte_carlo` JSON block, OR a
    `{reason: 'insufficient_peers', ...}` dict if any driver has < 5 valid
    values. Drivers are sampled INDEPENDENTLY; correlation caveat is
    surfaced in the caller's tier_caveats.

    Factored out so it can be tested with synthetic inputs (no live API).
    """
    def _n_valid(vs):
        return sum(1 for v in vs if v is not None and np.isfinite(float(v)))

    n_g = _n_valid(growth_values)
    n_m = _n_valid(margin_values)
    n_x = _n_valid(exit_multiple_values)

    if n_g < 5 or n_m < 5 or n_x < 5:
        return {
            "reason": "insufficient_peers",
            "n_peers_growth": n_g,
            "n_peers_margin": n_m,
            "n_peers_exit_multiple": n_x,
            "samples": None,
        }

    sampler = sample_empirical if distribution == "peer" else sample_normal
    # Use distinct seeds per driver so callers passing seed=42 don't get
    # perfectly co-moving samples across drivers.
    seed_g = seed
    seed_m = (seed + 1) if seed is not None else None
    seed_x = (seed + 2) if seed is not None else None

    g_samples = sampler(growth_values, n=n_samples, seed=seed_g)
    m_samples = sampler(margin_values, n=n_samples, seed=seed_m)
    x_samples = sampler(exit_multiple_values, n=n_samples, seed=seed_x)

    # Per-draw fair value. Uses the SAME formula as fair_value_at_peer_median.
    revenue_h = revenue_ttm * np.power(1.0 + g_samples, horizon)
    ebitda_h = revenue_h * m_samples
    ev_h = ebitda_h * x_samples
    ev_pv = ev_h / ((1.0 + wacc) ** horizon)
    fv_samples = (ev_pv - (ev_net_non_mcap or 0.0)) / shares

    fv_summary = percentile_summary(fv_samples)
    fv_summary = {k: (round(v, 4) if isinstance(v, float) else v)
                  for k, v in fv_summary.items()}

    # Percentile of current/target price within the fv distribution.
    def _pct_of(price):
        if price is None:
            return None
        finite = fv_samples[np.isfinite(fv_samples)]
        if finite.size == 0:
            return None
        return float(np.mean(finite <= price) * 100.0)

    current_pct = _pct_of(current_price)
    target_pct = _pct_of(target_price)

    sensitivity = spearman_sensitivity(
        {"growth": g_samples, "margin": m_samples, "exit_multiple": x_samples},
        fv_samples,
    )
    sensitivity = [
        {"driver": s["driver"], "rho": round(s["rho"], 3),
         "abs_rho": round(s["abs_rho"], 3)}
        for s in sensitivity
    ]

    def _driver_block(values, n_valid):
        cleaned = np.asarray(
            [float(v) for v in values
             if v is not None and np.isfinite(float(v))],
            dtype=float,
        )
        source = "peer_empirical" if distribution == "peer" else "peer_normal_fit"
        return {
            "source": source,
            "n_peers": n_valid,
            "p25": round(float(np.percentile(cleaned, 25)), 4),
            "p50": round(float(np.percentile(cleaned, 50)), 4),
            "p75": round(float(np.percentile(cleaned, 75)), 4),
        }

    return {
        "samples": n_samples,
        "seed": seed,
        "distribution": distribution,
        "fv_per_share": fv_summary,
        "current_price_percentile": round(current_pct, 1) if current_pct is not None else None,
        "target_price_percentile": round(target_pct, 1) if target_pct is not None else None,
        "sensitivity": sensitivity,
        "drivers_used": {
            "growth": _driver_block(growth_values, n_g),
            "margin": _driver_block(margin_values, n_m),
            "exit_multiple": _driver_block(exit_multiple_values, n_x),
        },
        "tier_caveats": [
            ("Drivers sampled independently; true peer growth/margin show "
             "rho ~ 0.3-0.5 historically, so tail percentiles may be slightly "
             "understated"),
            (f"WACC held constant at {wacc*100:.1f}%; consider sensitivity "
             "analysis at +/- 100bps separately"),
        ],
    }


# ----- Take + read generation -----

def fmt_pp(x, decimals=0):
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.{decimals}f}pp"


def fmt_pct(x, signed=False, decimals=0):
    if x is None:
        return "n/a"
    val = x * 100
    # Avoid "-0%" for near-zero values
    if abs(val) < 0.5 * (10 ** -decimals):
        val = 0.0
    if signed:
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.{decimals}f}%"
    return f"{val:.{decimals}f}%"


def fmt_price(x):
    if x is None:
        return "n/a"
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 100:
        return f"${x:,.1f}"
    return f"${x:,.2f}"


def fmt_mult(x):
    if x is None:
        return "n/a"
    if abs(x) > 1000:
        return ">1000x"
    return f"{x:.1f}x"


def generate_take(ticker, analyst, growth_sanity, margin_sanity, mult_checks):
    """Build the bold take.

    Direction matters: target-implied multiples ABOVE peer band = stretched
    (target demands premium multiples). BELOW peer band = conservative
    (target works at sub-cohort multiples). Growth/margin ABOVE peer band
    = thesis bets on outperformance. The verdict line classifies the
    target as `stretched`, `conservative`, or `mixed` based on the
    direction of the gaps, not just the count.
    """
    horizon = analyst["horizon_years"]
    growth_pp = analyst["assumed_growth"] * 100
    margin_pp = analyst["assumed_margin"] * 100
    peer_growth = growth_sanity.get("peer_p50")
    peer_margin = margin_sanity.get("peer_p50")
    growth_delta = growth_sanity.get("delta_pp")
    margin_delta = margin_sanity.get("delta_pp")

    # Count stretched (target multiples ABOVE band, or growth/margin ABOVE band)
    # vs conservative (target multiples BELOW band) signals
    stretched = 0
    conservative = 0
    for c in mult_checks:
        if c.get("status") == "above":
            stretched += 1
        elif c.get("status") == "below":
            conservative += 1
    # Growth/margin above peer band = ambitious thesis (counts as stretched
    # because the analyst is more bullish on fundamentals than the cohort)
    if growth_sanity.get("status") == "above":
        stretched += 1
    if margin_sanity.get("status") == "above":
        stretched += 1
    if growth_sanity.get("status") == "below":
        conservative += 1
    if margin_sanity.get("status") == "below":
        conservative += 1

    line1 = (f"Target requires {ticker} growing {growth_pp:.0f}% CAGR for "
              f"{horizon} years at {margin_pp:.0f}% EBITDA margin.")
    if peer_growth is not None and peer_margin is not None:
        line2 = (f"Peer median is {peer_growth*100:.0f}% / "
                 f"{peer_margin*100:.0f}%. The assumption is "
                 f"{fmt_pp(growth_delta)} on growth and "
                 f"{fmt_pp(margin_delta)} on margin.")
    else:
        line2 = "Peer median unavailable for direct delta comparison."

    # Verdict
    if stretched >= 3 and conservative == 0:
        line3 = ("Defensible only if you believe the structural moat plus the "
                 "growth/margin gap vs the cohort both persist; thesis carries "
                 "the target, not the math.")
    elif stretched >= 2 and conservative <= 1:
        # Mixed but tilted stretched
        dims = []
        if growth_sanity.get("status") == "above":
            dims.append("growth premium")
        if margin_sanity.get("status") == "above":
            dims.append("margin premium")
        if any(c.get("status") == "above" for c in mult_checks):
            dims.append("multiple premium")
        if dims:
            line3 = (f"Defensible if you accept the "
                     f"{' and '.join(dims[:2])}.")
        else:
            line3 = "Defensible against the cohort on most dimensions."
    elif conservative >= 3 and stretched == 0:
        line3 = ("Target sits at or below the peer cohort on every "
                 "valuation lens; the math is conservative against the "
                 "comp set.")
    elif conservative >= 2 and stretched <= 1:
        line3 = ("Target's implied multiples sit at or below the cohort; "
                 "the gap usually means the thesis is undemanding for the "
                 "growth and margin assumed.")
    elif stretched + conservative >= 3:
        # Genuinely mixed (some above, some below)
        line3 = ("Mixed read: some assumptions stretch vs the cohort, "
                 "others sit conservative. Net depends on which gap matters "
                 "more for the thesis.")
    elif stretched + conservative == 0:
        line3 = "Assumptions sit inside the peer cohort on every dimension."
    else:
        line3 = "Mostly in line with the cohort; one dimension carries the gap."
    return f"{line1} {line2} {line3}"


def generate_read(ticker, stretched, conservative, implied_cagr,
                    peer_median_cagr, fair_value, current_price, target_price):
    """Closing read, anchored on fair-value-at-peer-median vs target."""
    air = None
    if implied_cagr is not None and peer_median_cagr is not None:
        air = (implied_cagr - peer_median_cagr) * 100
    fv_str = fmt_price(fair_value)

    # Compare fair value to current + target to anchor the read
    fv_above_target = (fair_value is not None and target_price is not None
                        and fair_value > target_price)
    fv_above_current = (fair_value is not None and current_price is not None
                          and fair_value > current_price)
    fv_below_current = (fair_value is not None and current_price is not None
                          and fair_value < current_price)

    if fv_above_target:
        return (f"Read: Even at peer-median growth and the assumed margin, "
                f"fair value lands at ~{fv_str}, above the target. The "
                f"target understates what the cohort math would support; "
                f"check whether the analyst's exit-multiple assumption is "
                f"too conservative or the growth assumption is too low.")
    if fv_above_current and not fv_above_target:
        return (f"Read: At peer-median growth (same margin) fair value is "
                f"~{fv_str}, between current and target. The cushion to "
                f"target is real but rests on the analyst's growth premium "
                f"vs the cohort delivering.")
    if stretched >= 3 and conservative == 0 and fv_below_current:
        return (f"Read: Model assumes {ticker} outperforms peers by a wide "
                f"margin on every defensible dimension. Trim growth to "
                f"peer-median and fair value drops to ~{fv_str}, below "
                f"the current price. Target is a thesis, not a sanity-"
                f"check survivor.")
    if stretched + conservative >= 3 and fv_below_current:
        return (f"Read: Target requires the analyst's premium assumptions "
                f"to deliver. Trim growth to peer median and fair value "
                f"drops to ~{fv_str}; the cushion is thin.")
    if conservative >= 2 and stretched == 0:
        return (f"Read: Assumptions sit conservative against the cohort. "
                f"Fair value at peer-median growth and the assumed margin "
                f"is ~{fv_str}; the target is defensible.")
    if fair_value is not None:
        return (f"Read: Assumptions cluster around the cohort. Fair value "
                f"at peer-median growth (same margin) is ~{fv_str}; the "
                f"target lives or dies on whether the standout dimension "
                f"delivers.")
    return (f"Read: Insufficient peer data for a full reverse-DCF anchor; "
            f"the sanity check above shows where the assumptions sit vs "
            f"the cohort.")


# ----- CLI -----

ap = argparse.ArgumentParser(description="valuation-sanity-check reference")
ap.add_argument("ticker", nargs="?", default="NVDA",
                help="Subject ticker (default: NVDA)")
ap.add_argument("--target-price", type=float, required=True,
                help="Analyst's target share price (USD)")
ap.add_argument("--assumed-growth", type=float, required=True,
                help="Assumed revenue CAGR over the horizon (decimal, e.g. 0.28)")
ap.add_argument("--assumed-margin", type=float, required=True,
                help="Assumed steady-state EBITDA margin (decimal, e.g. 0.60)")
ap.add_argument("--horizon", type=int, default=5,
                help="Forecast horizon in years (default 5)")
ap.add_argument("--peers", type=str, default=None,
                help="Comma-separated peer override (skips curated map)")
ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
ap.add_argument("--mc", action="store_true",
                help="Run Monte Carlo fair-value distribution (default off)")
ap.add_argument("--mc-samples", type=int, default=10000,
                help="MC sample count (default 10000, min 1000, max 100000)")
ap.add_argument("--mc-distribution", choices=["peer", "normal"], default="peer",
                help="'peer' = empirical resample (default); 'normal' = N(mu,sigma) fit")
ap.add_argument("--mc-seed", type=int, default=None,
                help="Seed for reproducible MC runs")
args = ap.parse_args()
fmt = resolve_output_format(args.format)

# Validate MC sample count
if args.mc:
    if args.mc_samples < 1000 or args.mc_samples > 100000:
        print(
            f"ERROR: --mc-samples must be in [1000, 100000], got {args.mc_samples}",
            file=sys.stderr,
        )
        sys.exit(1)

subject_ticker = args.ticker.upper()

# ----- Peer selection -----

if args.peers:
    peers_list = [p.strip().upper() for p in args.peers.split(",") if p.strip()]
    peer_selection_method = "curated_override"
elif subject_ticker in PEER_OVERRIDES:
    peers_list = PEER_OVERRIDES[subject_ticker]
    peer_selection_method = "curated_override"
else:
    det = get_ticker_details(subject_ticker)
    if not det:
        print(f"ERROR: subject {subject_ticker} not found", file=sys.stderr)
        sys.exit(1)
    sic = det.get("sic_code")
    if not sic:
        print(f"ERROR: no SIC code for {subject_ticker}", file=sys.stderr)
        sys.exit(1)
    print(f"Peer SIC fallback on SIC {sic}...", file=sys.stderr)
    path = (f"/v3/reference/tickers?market=stocks&active=true&type=CS"
            f"&sic_code={sic}&limit=50")
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

print(f"Sanity-checking {subject_ticker}: target=${args.target_price}, "
      f"growth={args.assumed_growth:.0%}, margin={args.assumed_margin:.0%}, "
      f"horizon={args.horizon}y, peers={len(peers_list)}",
      file=sys.stderr)
sources = []
print(f"  Fetching subject {subject_ticker}...", file=sys.stderr)
subject = assemble_name(subject_ticker, sources)
if not subject:
    print(f"ERROR: subject {subject_ticker} data unavailable", file=sys.stderr)
    sys.exit(1)

peer_objs = []
for tk in peers_list:
    print(f"  Fetching peer {tk}...", file=sys.stderr)
    p = assemble_name(tk, sources)
    if p is not None:
        peer_objs.append(p)
    time.sleep(0.15)


# ----- Analyst-inputs derived fields -----

shares = subject.get("shares_outstanding")
shares_source = subject.get("shares_source")
ltd = subject["metrics"].get("long_term_debt") or 0.0
current_price = subject.get("price")
current_mcap = subject.get("market_cap")
current_ev = subject.get("enterprise_value")
revenue_ttm = subject["metrics"].get("revenue_ttm")

target_price = args.target_price
target_mcap = (target_price * shares) if shares else None

# Target EV uses the same component net (debt - cash + leases + minority)
# as current EV. Components missing on the subject already triggered a
# warning during assemble_name; here we treat any missing component as 0
# rather than failing the whole script (the subject's current_ev would
# also be None in that case, which is already a degraded run).
_metrics = subject["metrics"]
_ev_net_non_mcap = (
    float(_metrics.get("total_debt") or 0.0)
    - float(_metrics.get("cash_and_equivalents") or 0.0)
    + float(_metrics.get("operating_lease_liability") or 0.0)
    + float(_metrics.get("minority_interest") or 0.0)
)
target_ev = (target_mcap + _ev_net_non_mcap) if target_mcap is not None else None
implied_upside = ((target_price / current_price) - 1.0) if current_price else None

if revenue_ttm is not None:
    target_revenue_horizon = revenue_ttm * ((1.0 + args.assumed_growth)
                                              ** args.horizon)
    target_ebitda_horizon = target_revenue_horizon * args.assumed_margin
else:
    target_revenue_horizon = None
    target_ebitda_horizon = None


# ----- Multiple sanity -----

# C7: tag peers missing D&A and exclude them from the EBITDA distribution.
# Without D&A their "ebitda_ttm" would have been EBIT, and comparing the
# subject's true EV/EBITDA to a peer cohort built off EV/EBIT is the bug
# the audit caught. With the C7 metrics fix above, ebitda_ttm is already
# None for these peers; this loop just makes the exclusion explicit on the
# per-peer audit trail and tier_caveats.
for p in peer_objs:
    da_reported = (p.get("metrics") or {}).get("da_reported")
    if not da_reported:
        p["excluded_from_ebitda_comp"] = True
        p["ebitda_exclusion_reason"] = "missing_da"
    else:
        p["excluded_from_ebitda_comp"] = False

n_excluded_ebitda = sum(1 for p in peer_objs if p.get("excluded_from_ebitda_comp"))

# Peer distributions on current multiples
peer_ev_sales = [(p["multiples"] or {}).get("ev_sales") for p in peer_objs]
peer_ev_ebitda = [(p["multiples"] or {}).get("ev_ebitda") for p in peer_objs
                   if not p.get("excluded_from_ebitda_comp")]
peer_p_e = [(p["multiples"] or {}).get("p_e") for p in peer_objs]

p25_es, p50_es, p75_es, n_es = percentile_band(peer_ev_sales)
p25_ev, p50_ev, p75_ev, n_ev = percentile_band(peer_ev_ebitda)
p25_pe, p50_pe, p75_pe, n_pe = percentile_band(peer_p_e)

# Target-implied multiples
implied_ev_sales = None
implied_ev_ebitda = None
implied_p_e = None
if target_ev is not None and target_revenue_horizon and target_revenue_horizon > 0:
    implied_ev_sales = target_ev / target_revenue_horizon
if target_ev is not None and target_ebitda_horizon and target_ebitda_horizon > 0:
    implied_ev_ebitda = target_ev / target_ebitda_horizon
if (target_revenue_horizon and target_ebitda_horizon and shares
        and shares > 0):
    target_net_income_horizon = (target_revenue_horizon
                                   * args.assumed_margin
                                   * (1.0 - TAX_PROXY))
    target_eps_horizon = target_net_income_horizon / shares
    if target_eps_horizon > 0:
        implied_p_e = target_price / target_eps_horizon

multiple_sanity = [
    {
        "name": "ev_sales",
        "implied_value": round(implied_ev_sales, 2) if implied_ev_sales else None,
        "peer_p25": round(p25_es, 2) if p25_es else None,
        "peer_p50": round(p50_es, 2) if p50_es else None,
        "peer_p75": round(p75_es, 2) if p75_es else None,
        "status": status_for(implied_ev_sales, p25_es, p75_es),
        "n_peers_in_distribution": n_es,
    },
    {
        "name": "ev_ebitda",
        "implied_value": round(implied_ev_ebitda, 2) if implied_ev_ebitda else None,
        "peer_p25": round(p25_ev, 2) if p25_ev else None,
        "peer_p50": round(p50_ev, 2) if p50_ev else None,
        "peer_p75": round(p75_ev, 2) if p75_ev else None,
        "status": status_for(implied_ev_ebitda, p25_ev, p75_ev),
        "n_peers_in_distribution": n_ev,
    },
    {
        "name": "p_e",
        "implied_value": round(implied_p_e, 2) if implied_p_e else None,
        "peer_p25": round(p25_pe, 2) if p25_pe else None,
        "peer_p50": round(p50_pe, 2) if p50_pe else None,
        "peer_p75": round(p75_pe, 2) if p75_pe else None,
        "status": status_for(implied_p_e, p25_pe, p75_pe),
        "n_peers_in_distribution": n_pe,
    },
]


# ----- Growth and margin sanity -----

peer_growth_vals = [(p["metrics"] or {}).get("revenue_growth_ttm")
                     for p in peer_objs]
peer_margin_vals = [(p["metrics"] or {}).get("ebitda_margin") for p in peer_objs]

p25_g, p50_g, p75_g, n_g = percentile_band(peer_growth_vals)
p25_m, p50_m, p75_m, n_m = percentile_band(peer_margin_vals)

growth_sanity = {
    "assumed": args.assumed_growth,
    "peer_p25": round(p25_g, 4) if p25_g is not None else None,
    "peer_p50": round(p50_g, 4) if p50_g is not None else None,
    "peer_p75": round(p75_g, 4) if p75_g is not None else None,
    "delta_pp": round((args.assumed_growth - p50_g) * 100, 1) if p50_g is not None else None,
    "status": status_for(args.assumed_growth, p25_g, p75_g),
    "n_peers_in_distribution": n_g,
}
margin_sanity = {
    "assumed": args.assumed_margin,
    "peer_p25": round(p25_m, 4) if p25_m is not None else None,
    "peer_p50": round(p50_m, 4) if p50_m is not None else None,
    "peer_p75": round(p75_m, 4) if p75_m is not None else None,
    "delta_pp": round((args.assumed_margin - p50_m) * 100, 1) if p50_m is not None else None,
    "status": status_for(args.assumed_margin, p25_m, p75_m),
    "n_peers_in_distribution": n_m,
}


# ----- Reverse-DCF -----

exit_multiple = p50_ev  # peer median EV/EBITDA
implied_cagr = reverse_dcf_implied_cagr(
    current_ev=current_ev,
    revenue_ttm=revenue_ttm,
    assumed_margin=args.assumed_margin,
    exit_multiple=exit_multiple,
    wacc=WACC,
    horizon=args.horizon,
)
peer_median_cagr = p50_g  # TTM-as-CAGR proxy (documented)
air_pp = None
if implied_cagr is not None and peer_median_cagr is not None:
    air_pp = (implied_cagr - peer_median_cagr) * 100

fv_at_median = fair_value_at_peer_median(
    subject=subject,
    peer_median_cagr=peer_median_cagr,
    assumed_margin=args.assumed_margin,
    exit_multiple=exit_multiple,
    wacc=WACC,
    horizon=args.horizon,
    ev_net_non_mcap=_ev_net_non_mcap,
    shares=shares,
)

reverse_dcf = {
    "current_ev": current_ev,
    "wacc_assumption": WACC,
    "exit_multiple_assumption": round(exit_multiple, 2) if exit_multiple else None,
    "implied_cagr": round(implied_cagr, 4) if implied_cagr is not None else None,
    "peer_median_cagr": round(peer_median_cagr, 4) if peer_median_cagr is not None else None,
    "air_pp": round(air_pp, 2) if air_pp is not None else None,
    "fair_value_at_peer_median": round(fv_at_median, 2) if fv_at_median is not None else None,
}


# ----- Monte Carlo (optional) -----

monte_carlo_block = None
if args.mc:
    # Reuse the SAME peer driver pools the point estimate consumes.
    # `peer_ev_ebitda` already excludes peers without D&A (C7 fix).
    mc_growth_vals = [v for v in peer_growth_vals if v is not None]
    mc_margin_vals = [v for v in peer_margin_vals if v is not None]
    mc_exit_vals = [v for v in peer_ev_ebitda if v is not None]

    if revenue_ttm is None or shares is None or shares <= 0:
        monte_carlo_block = {
            "reason": "subject_data_missing",
            "samples": None,
        }
    else:
        monte_carlo_block = compute_mc_fair_value(
            growth_values=mc_growth_vals,
            margin_values=mc_margin_vals,
            exit_multiple_values=mc_exit_vals,
            revenue_ttm=revenue_ttm,
            wacc=WACC,
            horizon=args.horizon,
            ev_net_non_mcap=_ev_net_non_mcap,
            shares=shares,
            n_samples=args.mc_samples,
            distribution=args.mc_distribution,
            seed=args.mc_seed,
            current_price=current_price,
            target_price=target_price,
        )


# ----- peers_used summary -----

peers_used = []
for p in peer_objs:
    peers_used.append({
        "ticker": p["ticker"],
        "name": p["name"],
        "ev_sales": (p["multiples"] or {}).get("ev_sales"),
        "ev_ebitda": (p["multiples"] or {}).get("ev_ebitda"),
        "p_e": (p["multiples"] or {}).get("p_e"),
        "revenue_growth_ttm": (p["metrics"] or {}).get("revenue_growth_ttm"),
        "ebitda_margin": (p["metrics"] or {}).get("ebitda_margin"),
        "data_status": p["data_status"],
        "ev_components": p.get("ev_components"),
        "shares_source": p.get("shares_source"),
        "excluded_from_ebitda_comp": p.get("excluded_from_ebitda_comp", False),
        "ebitda_exclusion_reason": p.get("ebitda_exclusion_reason"),
    })


# ----- Take + read -----

# Count direction-aware signals: stretched (target multiples above band, or
# growth/margin above band) vs conservative (target multiples below band, or
# growth/margin below band).
stretched = 0
conservative = 0
for c in multiple_sanity:
    if c.get("status") == "above":
        stretched += 1
    elif c.get("status") == "below":
        conservative += 1
for c in (growth_sanity, margin_sanity):
    if c.get("status") == "above":
        stretched += 1
    elif c.get("status") == "below":
        conservative += 1

take = generate_take(subject_ticker, {
    "target_price": target_price,
    "assumed_growth": args.assumed_growth,
    "assumed_margin": args.assumed_margin,
    "horizon_years": args.horizon,
}, growth_sanity, margin_sanity, multiple_sanity)

read = generate_read(subject_ticker, stretched, conservative,
                      implied_cagr, peer_median_cagr,
                      reverse_dcf["fair_value_at_peer_median"],
                      current_price, target_price)


# ----- Payload -----

tier_caveats = []
if n_excluded_ebitda > 0:
    tier_caveats.append(
        f"{n_excluded_ebitda} peer(s) excluded from EV/EBITDA distribution "
        f"due to missing D&A on filings (would have compared subject EBITDA "
        f"to peer EBIT)."
    )
if subject.get("ev_components") and subject["ev_components"].get("missing_fields"):
    miss = ", ".join(subject["ev_components"]["missing_fields"])
    tier_caveats.append(
        f"Subject EV defaulted optional components to 0: {miss}."
    )

# EV fallback tier_caveat: count peers whose EV math used a non-reported
# source for cash or debt. Fires at >= 30% share so the user sees the
# data-source constraint without being spammed for one stray peer.
_ev_peers = [p for p in peer_objs if p.get("ev_components")]
_ev_total = len(_ev_peers)
_ev_fallback = 0
for p in _ev_peers:
    ev = p["ev_components"]
    debt_src = ev.get("debt_source")
    cash_src = ev.get("cash_source")
    if debt_src != "reported_total" or cash_src in (None, "unavailable_defaulted_to_zero"):
        _ev_fallback += 1
if _ev_total > 0 and (_ev_fallback / _ev_total) >= 0.30:
    tier_caveats.append(
        f"EV math uses fallback inputs on {_ev_fallback}/{_ev_total} peers "
        f"(Massive financials don't reliably populate cash + total_debt). "
        f"EV-based multiples may be slightly overstated for those names; "
        f"see ev_components.* for the source tag per peer."
    )

payload = {
    "tier": "A",
    "tier_caveats": tier_caveats,
    "mode": "note",
    "run_at": NOW_UTC.isoformat(),
    "subject": {
        "ticker": subject_ticker,
        "name": subject["name"],
        "current_price": current_price,
        "current_mcap": current_mcap,
        "current_ev": current_ev,
        "ev_components": subject.get("ev_components"),
        "shares_outstanding": shares,
        "shares_source": shares_source,
        "long_term_debt": subject["metrics"].get("long_term_debt"),
        "revenue_ttm": revenue_ttm,
        "ebitda_margin_ttm": subject["metrics"].get("ebitda_margin"),
        "revenue_growth_ttm": subject["metrics"].get("revenue_growth_ttm"),
        "sector": subject.get("sector"),
    },
    "analyst_inputs": {
        "target_price": target_price,
        "assumed_growth": args.assumed_growth,
        "assumed_margin": args.assumed_margin,
        "horizon_years": args.horizon,
        "implied_upside_pct": implied_upside,
        "target_mcap": target_mcap,
        "target_ev": target_ev,
        "target_revenue_horizon": target_revenue_horizon,
        "target_ebitda_horizon": target_ebitda_horizon,
    },
    "multiple_sanity": multiple_sanity,
    "growth_sanity": growth_sanity,
    "margin_sanity": margin_sanity,
    "reverse_dcf": reverse_dcf,
    "peers_used": peers_used,
    "peer_selection": {
        "method": peer_selection_method,
        "n_peers": len(peer_objs),
    },
    "take": take,
    "read": read,
    "sources": sources,
}

if monte_carlo_block is not None:
    payload["monte_carlo"] = monte_carlo_block
    # Surface the MC-specific tier caveats up to the top-level list so
    # the rendered note keeps a single source of caveats.
    for caveat in monte_carlo_block.get("tier_caveats", []) or []:
        tier_caveats.append(caveat)


# ----- Renderer -----

def status_label(s):
    return {
        "above": "ABOVE",
        "below": "BELOW",
        "in_line": "IN LINE",
        "n_a": "N/A",
    }.get(s, "N/A")


def render(payload):
    subj = payload["subject"]
    an = payload["analyst_inputs"]
    sel = payload["peer_selection"]
    lines = []

    # Header
    lines.append(f"{subj['ticker']} · Valuation sanity check as of {TODAY.isoformat()}")
    if sel["method"] != "curated_override":
        lines.append(f"Peer set: {sel['n_peers']} names via {sel['method']}")
    lines.append("")

    # Target + current line
    upside = an.get("implied_upside_pct")
    upside_str = fmt_pct(upside, signed=True, decimals=1) if upside is not None else "n/a"
    lines.append(
        f"Target: {fmt_price(an['target_price'])} · "
        f"Current: {fmt_price(subj['current_price'])} · "
        f"Implied upside {upside_str}"
    )
    lines.append("")

    # Take
    lines.append(f"Take: {payload['take']}")
    lines.append("")

    # Multiple sanity
    lines.append("Multiple sanity (target-implied vs peer band)")
    label_map = {"ev_sales": "Implied EV/Sales",
                  "ev_ebitda": "Implied EV/EBITDA",
                  "p_e": "Implied P/E"}
    for entry in payload["multiple_sanity"]:
        label = label_map[entry["name"]]
        impl = entry.get("implied_value")
        p25 = entry.get("peer_p25")
        p75 = entry.get("peer_p75")
        if p25 is not None and p75 is not None:
            band = f"[{fmt_mult(p25)} - {fmt_mult(p75)}]"
        else:
            band = "[n/a]"
        n = entry.get("n_peers_in_distribution") or 0
        n_caveat = f"  (n={n})" if n < 4 and n > 0 else ""
        lines.append(
            f"- {label:<18} {fmt_mult(impl):>7}  vs peer 25/75 band  "
            f"{band:<18}  -> {status_label(entry['status'])}{n_caveat}"
        )
    lines.append("")

    # Growth sanity
    lines.append("Growth sanity (assumed vs peer band)")
    gs = payload["growth_sanity"]
    if gs.get("peer_p25") is not None and gs.get("peer_p75") is not None:
        band = f"[{fmt_pct(gs['peer_p25'], signed=True)}, {fmt_pct(gs['peer_p75'], signed=True)}]"
    else:
        band = "[n/a]"
    lines.append(
        f"- Revenue growth (over horizon, TTM proxy):  "
        f"{fmt_pct(gs.get('assumed'), signed=True):<6}  "
        f"vs peer band  {band:<22}  -> {status_label(gs['status'])}"
    )
    lines.append("")

    # Margin sanity
    lines.append("Margin sanity (assumed vs peer band)")
    ms = payload["margin_sanity"]
    if ms.get("peer_p25") is not None and ms.get("peer_p75") is not None:
        band = f"[{fmt_pct(ms['peer_p25'])}, {fmt_pct(ms['peer_p75'])}]"
    else:
        band = "[n/a]"
    lines.append(
        f"- EBITDA margin:  {fmt_pct(ms.get('assumed')):<5}  "
        f"vs peer band  {band:<18}  -> {status_label(ms['status'])}"
    )
    lines.append("")

    # Reverse-DCF
    rd = payload["reverse_dcf"]
    if rd.get("implied_cagr") is not None:
        lines.append(f"Reverse-DCF view (at current {fmt_price(subj['current_price'])})")
        assumed_margin_pct = fmt_pct(an["assumed_margin"])
        lines.append(
            f"- Implied {an['horizon_years']}yr revenue CAGR "
            f"({assumed_margin_pct} margin floor): {fmt_pct(rd['implied_cagr'])}"
        )
        if rd.get("peer_median_cagr") is not None:
            lines.append(
                f"- vs peer median {an['horizon_years']}yr CAGR (TTM proxy):  "
                f"{fmt_pct(rd['peer_median_cagr'])}"
            )
        if rd.get("air_pp") is not None:
            lines.append(
                f"- Air in current price:  {fmt_pp(rd['air_pp'], decimals=1)} CAGR"
            )
        if rd.get("fair_value_at_peer_median") is not None:
            lines.append(
                f"- Fair value at peer-median growth (same margin):  "
                f"{fmt_price(rd['fair_value_at_peer_median'])}"
            )
        lines.append("")

    # Monte Carlo (only when --mc was passed and we got a valid block)
    mc = payload.get("monte_carlo")
    if mc is not None:
        if mc.get("samples") is None:
            reason = mc.get("reason", "unavailable")
            if reason == "insufficient_peers":
                lines.append(
                    f"Monte Carlo skipped: insufficient peers "
                    f"(growth n={mc.get('n_peers_growth')}, "
                    f"margin n={mc.get('n_peers_margin')}, "
                    f"exit n={mc.get('n_peers_exit_multiple')}; min 5 each)."
                )
            else:
                lines.append(f"Monte Carlo skipped: {reason}.")
            lines.append("")
        else:
            fv = mc["fv_per_share"]
            dist_label = mc["distribution"]
            label_map = {"peer": "peer-empirical", "normal": "peer-normal-fit"}
            lines.append(
                f"Fair value distribution (n={mc['samples']}, "
                f"{label_map.get(dist_label, dist_label)}):"
            )
            lines.append(
                f"  p10 {fmt_price(fv['p10'])}     p50 {fmt_price(fv['p50'])}"
            )
            lines.append(
                f"  p25 {fmt_price(fv['p25'])}     p75 {fmt_price(fv['p75'])}"
            )
            lines.append(
                f"  mean {fmt_price(fv['mean'])}    p90 {fmt_price(fv['p90'])}"
            )
            lines.append("")
            cur_pct = mc.get("current_price_percentile")
            tgt_pct = mc.get("target_price_percentile")
            if cur_pct is not None and subj.get("current_price") is not None:
                lines.append(
                    f"Current price {fmt_price(subj['current_price'])} -> "
                    f"{cur_pct:.0f}th percentile of fair-value distribution."
                )
            if tgt_pct is not None:
                lines.append(
                    f"Target price {fmt_price(an['target_price'])} -> "
                    f"{tgt_pct:.0f}th percentile."
                )
            lines.append("")

            # Adaptive translation line based on where current price sits.
            if cur_pct is not None:
                if cur_pct < 25:
                    translation = (
                        "Translation: at the consensus drivers your name is "
                        "priced for the bottom quintile of outcomes. Bull case "
                        "requires top-quartile growth AND top-quartile multiple."
                    )
                elif cur_pct > 75:
                    translation = (
                        "Translation: priced above the IQR of plausible "
                        "outcomes - pricing in upside that requires top-"
                        "quartile execution."
                    )
                else:
                    translation = (
                        "Translation: priced inside the IQR of plausible "
                        "outcomes. The cohort math supports the current tape "
                        "without requiring tail assumptions."
                    )
                lines.append(translation)
                lines.append("")

            # Sensitivity bar chart (~12 char max).
            sens = mc.get("sensitivity") or []
            if sens:
                lines.append("Sensitivity:")
                max_abs = max((s["abs_rho"] for s in sens), default=0.0)
                bar_max = 12
                for s in sens:
                    name = s["driver"]
                    rho = s["rho"]
                    bar_len = (int(round((s["abs_rho"] / max_abs) * bar_max))
                               if max_abs > 0 else 0)
                    bar = "█" * bar_len
                    lines.append(
                        f"  {name:<14}   rho={rho:+.2f}   {bar}"
                    )
                lines.append("")

    # Read
    lines.append(payload["read"])
    return "\n".join(lines)


rendered = render(payload)


# ----- Write output -----

out_name = "valuation-sanity-check-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# valuation-sanity-check run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Subject: {subject_ticker}\n")
    fout.write(f"Target: ${args.target_price}  Growth: {args.assumed_growth:.0%}  "
                f"Margin: {args.assumed_margin:.0%}  Horizon: {args.horizon}y\n")
    fout.write(f"Peer selection: {peer_selection_method}\n\n")
    fout.write("## Layer 1: canonical JSON (live data)\n\n")
    fout.write("```json\n")
    fout.write(json.dumps(payload, indent=2, default=str))
    fout.write("\n```\n\n")
    fout.write("## Layer 2: rendered flash note (live data)\n\n")
    fout.write("```\n")
    fout.write(rendered)
    fout.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
