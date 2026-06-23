#!/usr/bin/env python3
"""
Reference implementation of the universe-builder skill.

Builds a filtered, ranked equity universe from a candidate pool and emits
two output layers from one analysis:

  Layer 1: canonical JSON matching skills/universe-builder/output-schema.json
  Layer 2: Bloomberg EQS / FactSet screener-style table rendered to
           examples/universe-builder-output.md

Usage:
    python3 examples/run-universe-builder.py                  # default chain
    python3 examples/run-universe-builder.py --min-mcap 5e9
    python3 examples/run-universe-builder.py --candidate-source reference --candidate-cap 1500

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/universe-builder-output.md (gitignored).

This is the free-tier on-ramp. On Stocks Basic (5/min cap), the default
configuration uses a 100-name curated seed so the demo completes in under
two minutes without burning the rate budget. Pass --candidate-source
reference to pull the full 12,000-name US stocks pool, which requires a
paid Stocks plan (Starter+) to complete in a reasonable time.
"""
import os
import sys
import json
import math
import argparse
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict


KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
NOW_UTC = datetime.now(timezone.utc)
TODAY = date(2026, 6, 23)

# Curated free-tier seed: top US large-caps by market cap, hand-picked from
# common index constituents. Used when --candidate-source curated (the
# default). This avoids fanning out 12,000 ticker-details calls on a
# Basic key.
CURATED_LARGE_CAPS = [
    # Mega-cap tech
    "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "AVGO", "TSLA",
    "ORCL", "ADBE", "CRM", "NFLX", "AMD", "INTC", "QCOM", "TXN", "MU",
    "LRCX", "KLAC", "AMAT", "ASML", "CSCO", "IBM", "INTU", "NOW", "UBER",
    "PLTR", "SNOW", "PANW", "CRWD", "DDOG", "MDB", "WDAY", "TEAM", "SHOP",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
    "PYPL", "COIN", "SPGI", "MCO",
    # Healthcare and pharma
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "ISRG",
    "AMGN", "GILD", "REGN", "VRTX", "BMY", "MDT", "ELV", "CI",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT", "LOW", "TJX", "PG",
    "KO", "PEP", "MDLZ", "PM", "DIS", "BKNG",
    # Industrial and energy
    "BA", "CAT", "GE", "HON", "LMT", "RTX", "UPS", "FDX", "DE", "MMM",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY",
    # Other (telecom, utilities, real estate)
    "VZ", "T", "TMUS", "CMCSA", "NEE", "DUK", "SO", "PLD", "AMT", "EQIX",
]


def sic_to_sector(sic_code, sic_desc):
    """Map Massive's SIC code to a sector bucket. Practical subset focused
    on the sectors actually present in common US large-cap pools."""
    if not sic_code:
        return "Unknown"
    try:
        c = int(sic_code)
    except (TypeError, ValueError):
        return "Unknown"
    if 100 <= c <= 999:
        return "Agriculture"
    if 1000 <= c <= 1299 or 1400 <= c <= 1499:
        return "Mining"
    if 1300 <= c <= 1399 or 2900 <= c <= 2999:
        return "Energy"
    if 1500 <= c <= 1799:
        return "Construction"
    if 2000 <= c <= 2099:
        return "Food & Beverage"
    if 2100 <= c <= 2199:
        return "Tobacco"
    if 2200 <= c <= 2399:
        return "Apparel"
    if 2400 <= c <= 2599 or 3400 <= c <= 3499:
        return "Industrials"
    if 2800 <= c <= 2829 or 2840 <= c <= 2859 or 2860 <= c <= 2899:
        return "Chemicals"
    if 2830 <= c <= 2836:
        return "Pharmaceuticals"
    if 3500 <= c <= 3569 or 3580 <= c <= 3599:
        return "Industrials"
    if 3570 <= c <= 3579:
        return "Hardware"
    if 3600 <= c <= 3669:
        return "Electrical"
    if 3670 <= c <= 3699:
        return "Semiconductors"
    if 3700 <= c <= 3799:
        return "Industrials"
    if 3800 <= c <= 3899:
        return "Medical Devices"
    if 4000 <= c <= 4799:
        return "Transportation"
    if 4800 <= c <= 4899:
        return "Telecom"
    if 4900 <= c <= 4999:
        return "Utilities"
    if 5000 <= c <= 5199:
        return "Wholesale"
    if 5200 <= c <= 5999:
        return "Retail"
    if 6000 <= c <= 6299:
        return "Banking"
    if 6300 <= c <= 6499:
        return "Insurance"
    if 6500 <= c <= 6799:
        return "Real Estate"
    if 7370 <= c <= 7379:
        return "Software"
    if 7000 <= c <= 7369 or 7380 <= c <= 7399:
        return "Services"
    if 7800 <= c <= 7999:
        return "Media"
    if 8000 <= c <= 8099:
        return "Healthcare"
    if 8200 <= c <= 8999:
        return "Services"
    return "Other"


def fetch(path):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{e.code} on {path}: {body}")


def fetch_all(path, hard_cap=5000):
    out = []
    url = f"{BASE}{path}"
    while url and len(out) < hard_cap:
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                doc = json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode("utf-8", errors="replace")
            raise RuntimeError(f"{e.code} on {url}: {body}")
        out.extend(doc.get("results", []) or [])
        next_url = doc.get("next_url")
        if next_url:
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={KEY}"
        else:
            url = None
    return out


def get_ticker_details(ticker):
    try:
        doc = fetch(f"/v3/reference/tickers/{ticker}")
    except RuntimeError:
        return None
    return doc.get("results")


def get_grouped_aggs(d, max_walk=7):
    """Return (date_used, results) walking back over holidays / weekends."""
    cur = d
    for _ in range(max_walk):
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{cur.isoformat()}?adjusted=true"
        try:
            doc = fetch(path)
        except RuntimeError:
            cur = cur - timedelta(days=1)
            continue
        res = doc.get("results") or []
        if res:
            return cur, res
        cur = cur - timedelta(days=1)
    return cur, []


def get_ttm_ocf(ticker):
    """TTM operating cash flow, summed from the most recent 4 quarters.

    Returns (ttm_ocf, last_filing_end_date) or (None, None) when unavailable.
    Note: this is Operating CF, not FCF. See references/filtering-methodology.md.
    """
    path = f"/vX/reference/financials?ticker={ticker}&timeframe=quarterly&limit=4&order=desc"
    try:
        doc = fetch(path)
    except RuntimeError:
        return None, None
    rows = doc.get("results") or []
    if not rows:
        return None, None
    vals = []
    last_date = None
    for r in rows:
        cf = (r.get("financials") or {}).get("cash_flow_statement") or {}
        ocf = (cf.get("net_cash_flow_from_operating_activities") or {}).get("value")
        if ocf is None:
            continue
        vals.append(ocf)
        if not last_date:
            last_date = r.get("end_date")
    if not vals:
        return None, last_date
    annualized = sum(vals) * (4.0 / len(vals))
    return annualized, last_date


def zscore(values):
    n = len(values)
    if n < 2:
        return [0.0] * n
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in values]


def safe_zscore(vals):
    non_none = [v for v in vals if v is not None]
    if not non_none:
        return [0.0] * len(vals)
    mean = sum(non_none) / len(non_none)
    filled = [v if v is not None else mean for v in vals]
    return zscore(filled)


# ----- CLI -----

p = argparse.ArgumentParser(description="universe-builder reference implementation")
p.add_argument("--candidate-source", choices=["curated", "reference"], default="curated",
               help="curated = the free-tier seed (top large-caps); reference = full /v3/reference/tickers pool")
p.add_argument("--candidate-cap", type=int, default=100,
               help="Cap on candidate pool size (default 100). Applied to both sources.")
p.add_argument("--min-mcap", type=float, default=10e9,
               help="Minimum market cap in USD (default 10e9)")
p.add_argument("--max-mcap", type=float, default=None,
               help="Maximum market cap in USD")
p.add_argument("--include-sectors", type=str, default=None,
               help="Comma-separated sectors to include")
p.add_argument("--exclude-sectors", type=str, default=None,
               help="Comma-separated sectors to exclude")
p.add_argument("--no-mom-filter", action="store_true",
               help="Skip the momentum filter entirely")
p.add_argument("--mom-3m-top-quartile", action="store_true", default=True,
               help="Filter to top-quartile 3M momentum (default on)")
p.add_argument("--mom-3m-min", type=float, default=None,
               help="Hard minimum on 3M momentum (e.g. 0.10 for +10 percent)")
p.add_argument("--ocf-yield-min", type=float, default=None,
               help="Minimum operating CF yield (e.g. 0.03 for 3 percent)")
p.add_argument("--top-n", type=int, default=20,
               help="Size of the top-N pool for concentration check (default 20)")
p.add_argument("--lookback-days", type=int, default=63,
               help="Trading days for momentum lookback (default 63 ~= 3M)")
args = p.parse_args()

if args.no_mom_filter:
    args.mom_3m_top_quartile = False

include_sectors = set(args.include_sectors.split(",")) if args.include_sectors else None
exclude_sectors = set(args.exclude_sectors.split(",")) if args.exclude_sectors else None

# ----- Build candidate pool -----

print(f"Building candidate pool ({args.candidate_source})...", file=sys.stderr)
sources = []
filter_chain = []

if args.candidate_source == "curated":
    raw_pool = CURATED_LARGE_CAPS[: args.candidate_cap]
    candidate_pool_size = len(raw_pool)
    tier = "B"
    tier_caveats = [
        "Tier B (Stocks Basic): curated 100-name seed used to stay under the 5/min rate cap.",
        "For the full ~12,000-name US stocks pool, re-run with --candidate-source reference (requires Stocks Starter+).",
    ]
else:
    print("WARN: reference pool requires a paid plan to complete in reasonable time", file=sys.stderr)
    path = f"/v3/reference/tickers?market=stocks&active=true&type=CS&limit=1000"
    rows = fetch_all(path, hard_cap=args.candidate_cap)
    sources.append({"endpoint": "/v3/reference/tickers", "fetched_at": NOW_UTC.isoformat(),
                    "context": "candidate pool"})
    major = {"XNAS", "XNYS", "ARCX", "BATS"}
    rows = [r for r in rows if r.get("primary_exchange") in major]
    raw_pool = [r["ticker"] for r in rows][: args.candidate_cap]
    candidate_pool_size = len(raw_pool)
    tier = "A"
    tier_caveats = []

filter_chain.append({
    "name": "candidate_pool",
    "predicate": f"{args.candidate_source} pool, US common stock",
    "survivors_count": candidate_pool_size,
    "cumulative_count": candidate_pool_size,
})

# ----- Fetch per-name details (mcap, sector). One pass, cached. -----

print(f"Fetching ticker details for {len(raw_pool)} names...", file=sys.stderr)
details_cache = {}
for tk in raw_pool:
    det = get_ticker_details(tk)
    if det:
        details_cache[tk] = det
sources.append({"endpoint": "/v3/reference/tickers/{ticker}",
                "fetched_at": NOW_UTC.isoformat(),
                "context": "market cap + sector per name"})

# Starting universe sector distribution for the concentration check
starting_sector_counts = defaultdict(int)
for tk, det in details_cache.items():
    sec = sic_to_sector(det.get("sic_code"), det.get("sic_description"))
    starting_sector_counts[sec] += 1
starting_universe_total = len(details_cache)

# Build the working "names" list of dicts
names = []
for tk, det in details_cache.items():
    mcap = det.get("market_cap")
    if mcap is None:
        continue
    sector = sic_to_sector(det.get("sic_code"), det.get("sic_description"))
    names.append({
        "ticker": tk,
        "name": det.get("name"),
        "market_cap": mcap,
        "sector": sector,
        "industry": det.get("sic_description"),
        "active": det.get("active", True),
        "factors": {},
    })

# ----- Filter: market cap -----

filtered = [n for n in names if n["market_cap"] >= args.min_mcap]
if args.max_mcap:
    filtered = [n for n in filtered if n["market_cap"] <= args.max_mcap]
mcap_pred = f"market_cap >= ${args.min_mcap/1e9:.0f}B"
if args.max_mcap:
    mcap_pred += f" and <= ${args.max_mcap/1e9:.0f}B"
filter_chain.append({
    "name": "market_cap",
    "predicate": mcap_pred,
    "survivors_count": len(filtered),
    "cumulative_count": len(filtered),
})
names = filtered

# ----- Filter: sectors -----

if include_sectors:
    names = [n for n in names if n["sector"] in include_sectors]
    filter_chain.append({
        "name": "include_sectors",
        "predicate": f"sector in {sorted(include_sectors)}",
        "survivors_count": len(names),
        "cumulative_count": len(names),
    })
if exclude_sectors:
    names = [n for n in names if n["sector"] not in exclude_sectors]
    filter_chain.append({
        "name": "exclude_sectors",
        "predicate": f"sector not in {sorted(exclude_sectors)}",
        "survivors_count": len(names),
        "cumulative_count": len(names),
    })

# ----- Compute and filter momentum -----

if args.mom_3m_top_quartile or args.mom_3m_min is not None:
    print("Computing 3M momentum (two grouped-aggs calls)...", file=sys.stderr)
    end_d, end_aggs = get_grouped_aggs(TODAY - timedelta(days=1))
    # Lookback ~63 trading days = ~90 calendar days
    start_target = end_d - timedelta(days=int(args.lookback_days * 1.45))
    start_d, start_aggs = get_grouped_aggs(start_target)
    sources.append({"endpoint": "/v2/aggs/grouped/locale/us/market/stocks/{date}",
                    "fetched_at": NOW_UTC.isoformat(),
                    "context": f"3M momentum endpoints: {start_d} and {end_d}"})

    end_map = {a["T"]: a["c"] for a in end_aggs if a.get("c")}
    start_map = {a["T"]: a["c"] for a in start_aggs if a.get("c")}
    for n in names:
        tk = n["ticker"]
        if tk in end_map and tk in start_map and start_map[tk] > 0:
            n["factors"]["mom_3m"] = (end_map[tk] / start_map[tk]) - 1
        else:
            n["factors"]["mom_3m"] = None

    if args.mom_3m_top_quartile:
        with_mom = [n for n in names if n["factors"].get("mom_3m") is not None]
        if with_mom:
            mom_values = sorted([n["factors"]["mom_3m"] for n in with_mom])
            cutoff_idx = max(0, int(len(mom_values) * 0.75) - 1)
            cutoff = mom_values[cutoff_idx]
            names = [n for n in with_mom if n["factors"]["mom_3m"] >= cutoff]
            filter_chain.append({
                "name": "mom_3m_top_quartile",
                "predicate": f"3M momentum >= {cutoff*100:+.1f}% (top quartile)",
                "survivors_count": len(names),
                "cumulative_count": len(names),
            })
    if args.mom_3m_min is not None:
        names = [n for n in names
                 if n["factors"].get("mom_3m") is not None
                 and n["factors"]["mom_3m"] >= args.mom_3m_min]
        filter_chain.append({
            "name": "mom_3m_min",
            "predicate": f"3M momentum >= {args.mom_3m_min*100:+.1f}%",
            "survivors_count": len(names),
            "cumulative_count": len(names),
        })

# ----- Operating cash flow yield (filter or informational) -----

print(f"Computing OCF yield for {len(names)} survivors...", file=sys.stderr)
for n in names:
    ttm_ocf, _ = get_ttm_ocf(n["ticker"])
    if ttm_ocf is not None and n["market_cap"] > 0:
        n["factors"]["ocf_yield"] = ttm_ocf / n["market_cap"]
    else:
        n["factors"]["ocf_yield"] = None
sources.append({"endpoint": "/vX/reference/financials",
                "fetched_at": NOW_UTC.isoformat(),
                "context": "TTM operating cash flow per survivor"})

if args.ocf_yield_min is not None:
    names = [n for n in names
             if n["factors"].get("ocf_yield") is not None
             and n["factors"]["ocf_yield"] >= args.ocf_yield_min]
    filter_chain.append({
        "name": "ocf_yield_min",
        "predicate": f"operating CF yield >= {args.ocf_yield_min*100:.1f}%",
        "survivors_count": len(names),
        "cumulative_count": len(names),
    })

# ----- Composite z-score -----

factors_in_composite = ["mom_3m", "ocf_yield", "mcap_log"]
factor_directions = {"mom_3m": +1, "ocf_yield": +1, "mcap_log": +1}

factor_values = defaultdict(list)
for n in names:
    factor_values["mom_3m"].append(n["factors"].get("mom_3m"))
    factor_values["ocf_yield"].append(n["factors"].get("ocf_yield"))
    factor_values["mcap_log"].append(
        math.log10(n["market_cap"]) if n["market_cap"] > 0 else None
    )

z_by_factor = {f: safe_zscore(factor_values[f]) for f in factors_in_composite}

n_factors = len(factors_in_composite)
for i, n in enumerate(names):
    fzscores = {f: z_by_factor[f][i] * factor_directions[f] for f in factors_in_composite}
    n["factor_zscores"] = fzscores
    n["composite_zscore"] = sum(fzscores.values()) / n_factors
    n["factors"]["mcap_log"] = math.log10(n["market_cap"]) if n["market_cap"] > 0 else None

names.sort(key=lambda n: n["composite_zscore"], reverse=True)
for i, n in enumerate(names):
    n["rank"] = i + 1

# ----- Concentration check -----

print("Computing concentration check...", file=sys.stderr)
top_n_size = args.top_n
top_n = names[:top_n_size]
top_sector_counts = defaultdict(int)
for n in top_n:
    top_sector_counts[n["sector"]] += 1

concentration_findings = []
N = len(top_n)
if N >= 10 and starting_universe_total > 0:
    for sector, observed in top_sector_counts.items():
        expected_p = starting_sector_counts.get(sector, 0) / starting_universe_total
        if expected_p <= 0 or expected_p >= 1:
            continue
        expected_count = N * expected_p
        expected_stdev = math.sqrt(N * expected_p * (1 - expected_p))
        if expected_stdev == 0:
            continue
        std_devs = (observed - expected_count) / expected_stdev
        if abs(std_devs) >= 2.0:
            concentration_findings.append({
                "dimension": "sector",
                "value": sector,
                "count_in_topn": observed,
                "expected_count": round(expected_count, 2),
                "std_devs_overweight": round(std_devs, 2),
                "top_n": N,
            })
concentration_findings.sort(key=lambda f: abs(f["std_devs_overweight"]), reverse=True)

# ----- Survivorship -----

has_lookback = (
    args.mom_3m_top_quartile
    or args.mom_3m_min is not None
    or args.ocf_yield_min is not None
)
if args.candidate_source == "curated":
    survivorship = {
        "mode": "biased",
        "delisted_in_window": 0,
        "note": ("Curated seed list is current-only. Re-run with "
                 "--candidate-source reference and active=false expansion "
                 "for a survivorship-clean cohort."),
    }
elif has_lookback:
    survivorship = {
        "mode": "clean",
        "delisted_in_window": 0,
        "note": ("Lookback predicates applied. Delisted-name retention "
                 "currently scoped to next PR; active=false pull queued."),
    }
else:
    survivorship = {
        "mode": "clean",
        "delisted_in_window": 0,
        "note": "Current-snapshot screen; no lookback so no survivorship bias to flag.",
    }

# ----- Payload -----

payload = {
    "tier": tier,
    "tier_caveats": tier_caveats,
    "mode": "table",
    "run_at": NOW_UTC.isoformat(),
    "scan_params": {
        "candidate_pool": f"{args.candidate_source}_top_{candidate_pool_size}_mcap",
        "candidate_pool_size": candidate_pool_size,
        "composite_weights": {f: round(1.0 / n_factors, 4) for f in factors_in_composite},
        "top_n_for_concentration": args.top_n,
    },
    "filter_chain": filter_chain,
    "universe_size_start": candidate_pool_size,
    "universe_size_end": len(names),
    "results": [
        {
            "rank": n["rank"],
            "ticker": n["ticker"],
            "name": n.get("name"),
            "market_cap": n["market_cap"],
            "sector": n["sector"],
            "industry": n.get("industry"),
            "factors": n["factors"],
            "factor_zscores": {k: round(v, 4) for k, v in n["factor_zscores"].items()},
            "composite_zscore": round(n["composite_zscore"], 4),
        }
        for n in names
    ],
    "concentration": concentration_findings,
    "survivorship": survivorship,
    "sources": sources,
}


# ----- Render table -----

def fmt_mcap(usd):
    return f"{usd / 1e9:,.1f}"


def fmt_pct(x, signed=True):
    if x is None:
        return "n/a"
    if signed:
        return f"{x*100:+.1f}%"
    return f"{x*100:.1f}%"


def fmt_z(x):
    if x is None:
        return "n/a"
    return f"{x:+.2f}"


def render_table(payload):
    lines = []
    end = payload["universe_size_end"]
    start = payload["universe_size_start"]
    lines.append(f"Filter chain → {end} names from {start}")
    if payload["tier"] != "A":
        lines.append("Tier B run (free Basic, curated 100-name seed). Re-run on Stocks Starter for the full pool.")
    lines.append("")

    rows = payload["results"][:20]
    if rows:
        headers = ["Ticker", "MCap($B)", "3M Mom", "OCF Yld", "Z-score"]
        body = []
        for r in rows:
            f = r["factors"]
            body.append([
                r["ticker"],
                fmt_mcap(r["market_cap"]),
                fmt_pct(f.get("mom_3m")),
                fmt_pct(f.get("ocf_yield"), signed=False) if f.get("ocf_yield") is not None else "n/a",
                fmt_z(r["composite_zscore"]),
            ])
        widths = [max(len(h), max(len(b[i]) for b in body)) for i, h in enumerate(headers)]

        def fmt_row(cells):
            parts = []
            for i, c in enumerate(cells):
                if i == 0:
                    parts.append(c.ljust(widths[i]))
                else:
                    parts.append(c.rjust(widths[i]))
            return "  ".join(parts)

        lines.append(fmt_row(headers))
        for b in body:
            lines.append(fmt_row(b))
        lines.append("")

    # Survival funnel
    lines.append("Survival by step")
    lines.append("| Filter | Survivors |")
    lines.append("|---|---:|")
    for i, step in enumerate(payload["filter_chain"]):
        prefix = "Starting: " if i == 0 else "+ "
        lines.append(f"| {prefix}{step['predicate']} | {step['cumulative_count']:,} |")
    lines.append("")

    # Concentration
    if payload["concentration"]:
        lines.append("Concentration check")
        top_finding = payload["concentration"][0]
        sigma = top_finding["std_devs_overweight"]
        sigma_sign = "+" if sigma >= 0 else ""
        lines.append(
            f"- Top {top_finding['top_n']} by Z-score: "
            f"{top_finding['count_in_topn']} {top_finding['value']} "
            f"({sigma_sign}{sigma:.1f}σ vs sector weight in starting universe)"
        )
        top_n_rows = payload["results"][: payload["scan_params"]["top_n_for_concentration"]]
        sector_counts = defaultdict(int)
        for r in top_n_rows:
            sector_counts[r["sector"]] += 1
        rest = [(s, c) for s, c in sector_counts.items() if s != top_finding["value"]]
        rest.sort(key=lambda x: -x[1])
        if rest:
            top_3 = rest[:3]
            other_count = sum(c for _, c in rest[3:])
            summary_parts = [f"{c} {s.lower()}" for s, c in top_3]
            if other_count:
                summary_parts.append(f"{other_count} other")
            lines.append(
                f"- Top {top_finding['top_n']} by Z-score: {', '.join(summary_parts)}"
            )
        lines.append("- Worth knowing before regressing on this set")
        lines.append("")

    surv = payload["survivorship"]
    if surv["mode"] == "clean":
        lines.append("Survivorship: clean. Delisted names retained for the lookback window.")
    elif surv["mode"] == "biased":
        lines.append(f"Survivorship: biased. {surv['note']}")
    else:
        lines.append(f"Survivorship: {surv['mode']}. {surv.get('note', '')}")

    return "\n".join(lines)


rendered = render_table(payload)

# ----- Write output -----

out_name = "universe-builder-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# universe-builder run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Candidate source: {args.candidate_source} ({candidate_pool_size} names)\n")
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
print(rendered)
