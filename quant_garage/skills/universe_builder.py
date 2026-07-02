"""
universe-builder as an importable library function.

Filtered, ranked equity universe from a candidate pool. Free-tier on-ramp:
default uses a 100-name curated seed; pass candidate_source='reference'
for the full 12k US stocks pool (needs Stocks Starter+).

    from quant_garage.skills.universe_builder import run, render
    payload = run(min_mcap=5e9, max_week_return=-0.05)
"""
from __future__ import annotations

import json
import sys
import math
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import Iterable

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    top_quartile_threshold,
    concentration_z_score,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()

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


def fetch_all(path, hard_cap=15000):
    """Paginate `path` and return (rows, truncated).

    `truncated` is True when the loop exited because `len(rows) >= hard_cap`
    rather than because next_url ran out. The real US common-stock universe
    is ~5000-8000 names and /v3/reference/tickers returns them
    alphabetically, so a cap that fires silently slices the universe at
    a letter boundary. Default 15000 sits safely above the real universe
    size; callers should surface a tier_caveat when truncated=True.
    """
    out = []
    truncated = False
    for results, _ in client.paginate(path):
        out.extend(results)
        if len(out) >= hard_cap:
            out = out[:hard_cap]
            truncated = True
            break
    return out, truncated


def get_ticker_details(ticker):
    try:
        doc, _ = client.get(f"/v3/reference/tickers/{ticker}")
    except FetchError:
        return None
    return doc.get("results")


def enrich_ticker_details(tickers, workers=16):
    """Fetch /v3/reference/tickers/{T} for each ticker in parallel.

    Massive's `?ticker.any_of=...` query is silently ignored by the list
    endpoint (probed 2026-06-24: list endpoint returns the alphabetical head
    no matter what filter you pass for batch lookup), and `type`, `sic_code`,
    and `market_cap` are null on the list endpoint anyway. The right move is
    per-ticker fetches in a parallel worker pool. On Business tier (100 req/s
    soft cap), a 16-worker pool handles 345 names in <30s.

    Returns dict[ticker] -> details (or None on failure).
    """
    out = {}
    if not tickers:
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(get_ticker_details, tk): tk for tk in tickers}
        for fut in as_completed(futures):
            tk = futures[fut]
            try:
                out[tk] = fut.result()
            except Exception:
                out[tk] = None
    return out


def get_grouped_aggs(d, max_walk=7):
    """Return (date_used, results) walking back over holidays / weekends."""
    cur = d
    for _ in range(max_walk):
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{cur.isoformat()}?adjusted=true"
        try:
            doc, _ = client.get(path)
        except FetchError:
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
        doc, _ = client.get(path)
    except FetchError:
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


# ----- Public API -----

def run(
    candidate_source: str = "curated",
    candidate_cap: int = 15000,
    min_mcap: float = 10e9,
    max_mcap: float | None = None,
    include_sectors: Iterable[str] | str | None = None,
    exclude_sectors: Iterable[str] | str | None = None,
    no_mom_filter: bool = False,
    mom_3m_top_quartile: bool = True,
    min_mom_3m: float | None = None,
    min_price: float | None = None,
    min_adv: float | None = None,
    max_week_return: float | None = None,
    include_types: str = "CS",
    ocf_yield_min: float | None = None,
    skip_financials: bool = False,
    top_n: int = 20,
    lookback_days: int = 63,
    rank_by: str = "composite",
    client_: MassiveClient | None = None,
) -> dict:
    """Build a filtered, ranked equity universe.

    Args:
        candidate_source: 'curated' (100-name free-tier seed) | 'reference' | 'grouped'.
        candidate_cap: hard ceiling on candidate pulls.
        min_mcap: minimum market cap USD.
        max_mcap: maximum market cap USD.
        include_sectors: sectors to include.
        exclude_sectors: sectors to exclude.
        no_mom_filter: skip momentum filter.
        mom_3m_top_quartile: filter to top-quartile 3M momentum.
        min_mom_3m: hard minimum on 3M momentum (decimal).
        min_price: minimum last close.
        min_adv: minimum 20-day ADV in shares.
        max_week_return: maximum 5-day return (for pullback screens).
        include_types: comma-separated Massive types to keep (default 'CS').
        ocf_yield_min: minimum operating CF yield.
        skip_financials: skip per-name financials pull.
        top_n: size of top-N pool for concentration check.
        lookback_days: momentum lookback (default 63 = ~3M).
        rank_by: 'composite' | 'pullback'.
    """
    global client, NOW_UTC, TODAY
    client = client_ or MassiveClient()
    NOW_UTC = datetime.now(timezone.utc)
    TODAY = today()

    def _csv_arg(v: Iterable[str] | str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return ",".join(v)

    args = SimpleNamespace(
        candidate_source=candidate_source,
        candidate_cap=candidate_cap,
        min_mcap=min_mcap,
        max_mcap=max_mcap,
        include_sectors=_csv_arg(include_sectors),
        exclude_sectors=_csv_arg(exclude_sectors),
        no_mom_filter=no_mom_filter,
        mom_3m_top_quartile=mom_3m_top_quartile,
        min_mom_3m=min_mom_3m,
        mom_3m_min=None,  # deprecated alias
        min_price=min_price,
        min_adv=min_adv,
        max_week_return=max_week_return,
        include_types=include_types,
        ocf_yield_min=ocf_yield_min,
        skip_financials=skip_financials,
        top_n=top_n,
        lookback_days=lookback_days,
        rank_by=rank_by,
    )

    # --mom-3m-min is the deprecated alias for --min-mom-3m. Warn on use and
    # fold into the canonical flag. The canonical one always wins if both are set.
    if args.mom_3m_min is not None:
        print("WARN: --mom-3m-min is deprecated; use --min-mom-3m (canonical, "
              "parallels --min-price / --min-adv)", file=sys.stderr)
        if args.min_mom_3m is None:
            args.min_mom_3m = args.mom_3m_min

    # When the user passes any of the screener-style thresholds, disable the
    # top-quartile default. They asked for an absolute screen, not a relative one.
    if (args.min_mom_3m is not None
            or args.min_price is not None
            or args.min_adv is not None
            or args.max_week_return is not None):
        args.mom_3m_top_quartile = False

    # Parse include-types: a non-empty set of Massive type codes to KEEP
    # (default 'CS'). Empty / "*" / "all" disables the filter.
    include_types_raw = (args.include_types or "").strip()
    if include_types_raw.lower() in ("", "*", "all"):
        include_types = None  # keep everything
    else:
        include_types = {t.strip().upper() for t in include_types_raw.split(",") if t.strip()}

    if args.no_mom_filter:
        args.mom_3m_top_quartile = False

    # Grouped source pulls thousands of names; financials would be one call
    # per survivor at 5/min, which is the wrong default for a broad screen.
    if args.candidate_source == "grouped" and args.ocf_yield_min is None:
        args.skip_financials = True

    include_sectors = set(args.include_sectors.split(",")) if args.include_sectors else None
    exclude_sectors = set(args.exclude_sectors.split(",")) if args.exclude_sectors else None

    # ----- Build candidate pool -----

    print(f"Building candidate pool ({args.candidate_source})...", file=sys.stderr)
    sources = []
    filter_chain = []

    # Persistent across branches so the grouped path can reuse what it pulled
    # to derive every price-based factor (price, ADV, 5d, 3M) without
    # re-fetching the same endpoint multiple times.
    grouped_history = None  # dict[date_iso] -> {ticker: {"c": close, "v": volume}}

    if args.candidate_source == "curated":
        raw_pool = CURATED_LARGE_CAPS[: args.candidate_cap]
        candidate_pool_size = len(raw_pool)
        tier = "B"
        tier_caveats = [
            "Tier B (Stocks Basic): curated 100-name seed used to stay under the 5/min rate cap.",
            "For the full ~12,000-name US stocks pool, re-run with --candidate-source reference (requires Stocks Starter+).",
        ]
    elif args.candidate_source == "reference":
        print("WARN: reference pool requires a paid plan to complete in reasonable time", file=sys.stderr)
        path = f"/v3/reference/tickers?market=stocks&active=true&type=CS&limit=1000"
        rows, candidate_truncated = fetch_all(path, hard_cap=args.candidate_cap)
        print(
            f"  Universe candidates pulled: {len(rows)}"
            f"{' (TRUNCATED at hard cap)' if candidate_truncated else ''}",
            file=sys.stderr,
        )
        sources.append({"endpoint": "https://api.polygon.io/v3/reference/tickers",
                        "fetched_at": utcnow_iso(),
                        "context": "candidate pool"})
        major = {"XNAS", "XNYS", "ARCX", "BATS"}
        rows = [r for r in rows if r.get("primary_exchange") in major]
        raw_pool = [r["ticker"] for r in rows][: args.candidate_cap]
        candidate_pool_size = len(raw_pool)
        tier = "A"
        tier_caveats = []
        if candidate_truncated:
            tier_caveats.append(
                f"Universe candidate pull truncated at --candidate-cap={args.candidate_cap} "
                "tickers. The real US common-stock universe may be larger; downstream "
                "screening reflects only the captured slice (/v3/reference/tickers "
                "returns rows alphabetically by symbol, so the cap maps to a "
                "ticker-letter boundary). Re-run with a higher --candidate-cap or "
                "investigate the truncation."
            )
    else:
        # Grouped source: pull /v2/aggs/grouped for ~25 trading days going back
        # ~95 calendar days. One ~10k-row response per day. This gives us close
        # price, volume, and the price series needed for ADV, 5d return, and
        # 3M return all from one endpoint, with no per-name fan-out.
        # The right choice for a broad mean-reversion / momentum screen.
        print("Pulling grouped-aggs candidate pool (this is the right move for broad screens)...",
              file=sys.stderr)
        end_d, end_aggs = get_grouped_aggs(TODAY - timedelta(days=1))
        if not end_aggs:
            print("ERROR: grouped-aggs endpoint returned no rows; aborting", file=sys.stderr)
            sys.exit(1)
        # Filter to common-stock-shaped tickers (drop warrants, units, preferreds).
        # Symbols containing "." or "/" are typically class shares or units.
        raw_pool = [a["T"] for a in end_aggs
                    if a.get("T") and "." not in a["T"] and "/" not in a["T"] and a.get("c")]
        candidate_pool_size = len(raw_pool)
        grouped_history = {end_d.isoformat(): {a["T"]: {"c": a["c"], "v": a.get("v", 0)} for a in end_aggs}}
        sources.append({"endpoint": "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}",
                        "fetched_at": utcnow_iso(),
                        "context": f"candidate pool from {end_d.isoformat()} grouped daily bars"})
        tier = "A"
        tier_caveats = [
            f"Grouped-aggs source: full US stocks pool from {end_d.isoformat()} ({candidate_pool_size} tickers).",
            "Tickers with '.' or '/' (warrants, units, class shares) filtered out.",
        ]

    filter_chain.append({
        "name": "candidate_pool",
        "predicate": f"{args.candidate_source} pool, US common stock",
        "survivors_count": candidate_pool_size,
        "cumulative_count": candidate_pool_size,
    })

    # ----- Fetch per-name details (mcap, sector). One pass, cached. -----
    # Skipped for the grouped source — fanning out 10k ticker-details calls
    # is the wrong shape for a broad screen, and most users running grouped
    # don't need market cap as a filter (they're price + volume + return based).

    details_cache = {}
    if args.candidate_source != "grouped":
        print(f"Fetching ticker details for {len(raw_pool)} names...", file=sys.stderr)
        for tk in raw_pool:
            det = get_ticker_details(tk)
            if det:
                details_cache[tk] = det
        sources.append({"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}",
                        "fetched_at": utcnow_iso(),
                        "context": "market cap + sector per name"})

    # Starting universe sector distribution for the concentration check
    starting_sector_counts = defaultdict(int)
    for tk, det in details_cache.items():
        sec = sic_to_sector(det.get("sic_code"), det.get("sic_description"))
        starting_sector_counts[sec] += 1
    starting_universe_total = len(details_cache) if details_cache else len(raw_pool)

    # Build the working "names" list of dicts
    names = []
    if args.candidate_source == "grouped":
        # No mcap/sector data; we fill the structure with the grouped close so
        # downstream filters see a "price" factor and the table can render.
        end_iso = next(iter(grouped_history))
        end_snap = grouped_history[end_iso]
        for tk in raw_pool:
            snap = end_snap.get(tk)
            if not snap:
                continue
            names.append({
                "ticker": tk,
                "name": None,
                "market_cap": None,
                "sector": "Unknown",
                "industry": None,
                "active": True,
                "factors": {"price": snap["c"]},
            })
    else:
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

    # ----- Filter: market cap (skipped when grouped source has no mcap) -----

    if args.candidate_source != "grouped":
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

    # ----- Filter: minimum price (last close) -----

    if args.min_price is not None:
        if args.candidate_source != "grouped":
            # Need a last close per name — fall back to grouped aggs for the most
            # recent trading day. One call covers the whole universe.
            if grouped_history is None:
                end_d_price, end_aggs_price = get_grouped_aggs(TODAY - timedelta(days=1))
                grouped_history = {end_d_price.isoformat():
                                   {a["T"]: {"c": a["c"], "v": a.get("v", 0)} for a in end_aggs_price}}
                sources.append({"endpoint": "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}",
                                "fetched_at": utcnow_iso(),
                                "context": f"last close for price filter: {end_d_price.isoformat()}"})
            end_iso_p = next(iter(grouped_history))
            end_snap_p = grouped_history[end_iso_p]
            for n in names:
                snap = end_snap_p.get(n["ticker"])
                if snap:
                    n["factors"]["price"] = snap["c"]
        pre = len(names)
        names = [n for n in names
                 if n["factors"].get("price") is not None
                 and n["factors"]["price"] >= args.min_price]
        filter_chain.append({
            "name": "min_price",
            "predicate": f"last close >= ${args.min_price:.2f}",
            "survivors_count": len(names),
            "cumulative_count": len(names),
        })

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

    # ----- Compute and filter momentum + week return + ADV -----
    # All four price-based factors (3M return, 5d return, last price, 20d ADV)
    # come from the same grouped-aggs sweep. One call per trading day, then
    # all the math is per-name lookups in dicts.

    needs_price_history = (
        args.mom_3m_top_quartile
        or args.min_mom_3m is not None
        or args.max_week_return is not None
        or args.min_adv is not None
    )

    if needs_price_history:
        print("Pulling grouped-aggs price history (1 call per trading day)...", file=sys.stderr)
        if grouped_history is None:
            grouped_history = {}

        # Anchor dates: end (today-1), 5 trading days back, 63 trading days back.
        # Calendar approximations: 5d ~= 7 calendar days, 63d ~= 91 calendar days.
        end_target = TODAY - timedelta(days=1)
        week_target = end_target - timedelta(days=8)
        mom_target = end_target - timedelta(days=int(args.lookback_days * 1.45))

        def ensure_day(target):
            """Walk back to a real trading day, populate grouped_history, return iso date."""
            d, rows = get_grouped_aggs(target)
            iso = d.isoformat()
            if iso not in grouped_history:
                grouped_history[iso] = {a["T"]: {"c": a["c"], "v": a.get("v", 0)} for a in rows}
                sources.append({"endpoint": "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}",
                                "fetched_at": utcnow_iso(),
                                "context": f"price history anchor: {iso}"})
            return iso

        end_iso = ensure_day(end_target)
        week_iso = ensure_day(week_target) if args.max_week_return is not None else None
        mom_iso = ensure_day(mom_target) if (args.mom_3m_top_quartile or args.min_mom_3m is not None) else None

        # For ADV, pull ~20 trading days of grouped aggs ending at end_iso.
        # 20 trading days ~= 28 calendar days; walk back day by day skipping
        # weekends/holidays. Cap at 30 calendar walk-backs.
        adv_days_iso = []
        if args.min_adv is not None:
            cur = end_target
            attempts = 0
            while len(adv_days_iso) < 20 and attempts < 30:
                iso = ensure_day(cur)
                if iso not in adv_days_iso:
                    adv_days_iso.append(iso)
                # parse back to date for next step
                y, m, dd = (int(x) for x in iso.split("-"))
                cur = date(y, m, dd) - timedelta(days=1)
                attempts += 1

        end_map = {tk: snap["c"] for tk, snap in grouped_history[end_iso].items() if snap["c"]}

        # 3M momentum
        if mom_iso:
            start_map = {tk: snap["c"] for tk, snap in grouped_history[mom_iso].items() if snap["c"]}
            for n in names:
                tk = n["ticker"]
                if tk in end_map and tk in start_map and start_map[tk] > 0:
                    n["factors"]["mom_3m"] = (end_map[tk] / start_map[tk]) - 1
                else:
                    n["factors"]["mom_3m"] = None

        # 5-day (week) return
        if week_iso:
            week_map = {tk: snap["c"] for tk, snap in grouped_history[week_iso].items() if snap["c"]}
            for n in names:
                tk = n["ticker"]
                if tk in end_map and tk in week_map and week_map[tk] > 0:
                    n["factors"]["week_return"] = (end_map[tk] / week_map[tk]) - 1
                else:
                    n["factors"]["week_return"] = None

        # 20-day ADV
        if adv_days_iso:
            for n in names:
                tk = n["ticker"]
                vols = []
                for iso in adv_days_iso:
                    snap = grouped_history[iso].get(tk)
                    if snap and snap["v"]:
                        vols.append(snap["v"])
                n["factors"]["adv_20d"] = sum(vols) / len(vols) if vols else None

        # Apply momentum filters
        if args.mom_3m_top_quartile:
            with_mom = [n for n in names if n["factors"].get("mom_3m") is not None]
            if with_mom:
                # M5 audit fix: top_quartile_threshold returns the exact 75th
                # percentile so callers index with `value >= threshold`. The
                # old `*0.75 - 1` index-arithmetic kept ~26% instead of 25%.
                mom_values = [n["factors"]["mom_3m"] for n in with_mom]
                cutoff = top_quartile_threshold(mom_values)
                names = [n for n in with_mom if n["factors"]["mom_3m"] >= cutoff]
                filter_chain.append({
                    "name": "mom_3m_top_quartile",
                    "predicate": f"3M momentum >= {cutoff*100:+.1f}% (top quartile)",
                    "survivors_count": len(names),
                    "cumulative_count": len(names),
                })
        if args.min_mom_3m is not None:
            names = [n for n in names
                     if n["factors"].get("mom_3m") is not None
                     and n["factors"]["mom_3m"] >= args.min_mom_3m]
            filter_chain.append({
                "name": "min_mom_3m",
                "predicate": f"3M momentum >= {args.min_mom_3m*100:+.1f}%",
                "survivors_count": len(names),
                "cumulative_count": len(names),
            })

        # Apply ADV filter
        if args.min_adv is not None:
            names = [n for n in names
                     if n["factors"].get("adv_20d") is not None
                     and n["factors"]["adv_20d"] >= args.min_adv]
            filter_chain.append({
                "name": "min_adv",
                "predicate": f"20d avg daily volume >= {args.min_adv:,.0f} shares",
                "survivors_count": len(names),
                "cumulative_count": len(names),
            })

        # Apply week-return filter (max — we want pullbacks at or below threshold).
        # Semantic: 0.0 means "week is down" (strict <, exclude flat names);
        # any negative threshold like -0.05 means "down 5% or more" (inclusive <=,
        # matches user intuition that '5% pullback' includes the -5.0% case).
        if args.max_week_return is not None:
            if args.max_week_return >= 0:
                names = [n for n in names
                         if n["factors"].get("week_return") is not None
                         and n["factors"]["week_return"] < args.max_week_return]
                pred_op = "<"
            else:
                names = [n for n in names
                         if n["factors"].get("week_return") is not None
                         and n["factors"]["week_return"] <= args.max_week_return]
                pred_op = "<="
            filter_chain.append({
                "name": "max_week_return",
                "predicate": f"5d return {pred_op} {args.max_week_return*100:+.1f}% (recently pulled back)",
                "survivors_count": len(names),
                "cumulative_count": len(names),
            })

    # ----- Enrichment pass: type / sector / market cap from ticker details -----
    # Runs AFTER the cheap price/volume/momentum filters when survivors are
    # typically 300-2,000 names. Without this pass, the grouped path has no
    # security-type info, so ETFs and leveraged products leak through a
    # "stock screen" (e.g. ETHD ProShares UltraShort Ether ETF, LNOK Defiance
    # 2X Long NOK ETF). Also fills in sector for the concentration check.
    #
    # Skipped when the curated/reference path already populated details_cache
    # (those paths fetch details up-front for market-cap filtering).

    needs_enrichment = (
        include_types is not None
        or args.candidate_source == "grouped"
    )
    if needs_enrichment and names:
        to_fetch = [n["ticker"] for n in names if n["ticker"] not in details_cache]
        if to_fetch:
            print(f"Enriching {len(to_fetch)} survivors with ticker details "
                  f"(type/sector/mcap)...", file=sys.stderr)
            fetched = enrich_ticker_details(to_fetch, workers=16)
            for tk, det in fetched.items():
                if det:
                    details_cache[tk] = det
            sources.append({"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}",
                            "fetched_at": utcnow_iso(),
                            "context": f"enrichment pass: type+sector+mcap for {len(to_fetch)} survivors"})

        # Backfill name fields from details_cache. Detail rows fetched late are
        # the source of truth for type/sector/market_cap on the grouped path.
        for n in names:
            det = details_cache.get(n["ticker"])
            if not det:
                continue
            if not n.get("name"):
                n["name"] = det.get("name")
            if n.get("market_cap") is None:
                n["market_cap"] = det.get("market_cap")
            sic = det.get("sic_code")
            sic_desc = det.get("sic_description")
            if sic and (n["sector"] == "Unknown" or not n["sector"]):
                n["sector"] = sic_to_sector(sic, sic_desc)
            if not n.get("industry"):
                n["industry"] = sic_desc
            n["type"] = det.get("type")

        # Apply the type filter (default CS-only).
        if include_types is not None:
            pre = len(names)
            # Names that fail to enrich (no detail row) are dropped under a CS-only
            # filter, since we can't verify they're common stock. This is the
            # right call: a stock-only screen shouldn't silently keep unknowns.
            names = [n for n in names if (n.get("type") or "") in include_types]
            filter_chain.append({
                "name": "include_types",
                "predicate": f"type in {sorted(include_types)}",
                "survivors_count": len(names),
                "cumulative_count": len(names),
            })

        # Recompute starting-universe sector distribution from the enriched
        # survivor cohort when we didn't have one up front (grouped path: there
        # was no sector data on the 12k starting pool, so the cohort we're
        # picking the top-20 FROM becomes the baseline). The curated/reference
        # paths already populated starting_sector_counts before filtering, so
        # leave those alone — their baseline IS the whole pre-filter pool.
        if not starting_sector_counts and names:
            for n in names:
                starting_sector_counts[n["sector"]] += 1
            starting_universe_total = len(names)


    # ----- Operating cash flow yield (filter or informational) -----

    if args.skip_financials:
        for n in names:
            n["factors"]["ocf_yield"] = None
    else:
        print(f"Computing OCF yield for {len(names)} survivors...", file=sys.stderr)
        for n in names:
            mcap = n.get("market_cap")
            ttm_ocf, _ = get_ttm_ocf(n["ticker"])
            if ttm_ocf is not None and mcap and mcap > 0:
                n["factors"]["ocf_yield"] = ttm_ocf / mcap
            else:
                n["factors"]["ocf_yield"] = None
        sources.append({"endpoint": "https://api.polygon.io/vX/reference/financials",
                        "fetched_at": utcnow_iso(),
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
    # Compose only the factors that actually have data. Grouped source has no
    # market cap or OCF yield, so those drop out. Mean-reversion screens
    # usually pair mom_3m (momentum that earned the dip) with -week_return
    # (the dip itself), so include both as a composite when present.

    candidate_factor_directions = {
        "mom_3m": +1,
        "ocf_yield": +1,
        "mcap_log": +1,
        "week_return": -1,  # negative is good for the dip-buy thesis
    }

    def has_factor(factor):
        return any(n["factors"].get(factor) is not None for n in names)

    factors_in_composite = []
    for f in ("mom_3m", "ocf_yield"):
        if has_factor(f):
            factors_in_composite.append(f)
    # week_return and mcap_log are conditionally included
    if has_factor("week_return") and args.max_week_return is not None:
        factors_in_composite.append("week_return")
    if any(n.get("market_cap") for n in names):
        factors_in_composite.append("mcap_log")
        for n in names:
            if n.get("market_cap") and n["market_cap"] > 0:
                n["factors"]["mcap_log"] = math.log10(n["market_cap"])
            else:
                n["factors"]["mcap_log"] = None

    factor_directions = {f: candidate_factor_directions[f] for f in factors_in_composite}

    factor_values = defaultdict(list)
    for n in names:
        for f in factors_in_composite:
            factor_values[f].append(n["factors"].get(f))

    z_by_factor = {f: safe_zscore(factor_values[f]) for f in factors_in_composite}

    n_factors = max(len(factors_in_composite), 1)
    for i, n in enumerate(names):
        fzscores = {f: z_by_factor[f][i] * factor_directions[f] for f in factors_in_composite}
        n["factor_zscores"] = fzscores
        n["composite_zscore"] = sum(fzscores.values()) / n_factors

    # Pullback strength = 3M momentum × -(week return). Higher is "stronger
    # uptrend, deeper recent pullback". Only meaningful when both factors exist.
    for n in names:
        mom = n["factors"].get("mom_3m")
        wk = n["factors"].get("week_return")
        if mom is not None and wk is not None:
            n["pullback_score"] = mom * (-wk)
        else:
            n["pullback_score"] = None

    if args.rank_by == "pullback" and any(n.get("pullback_score") is not None for n in names):
        names.sort(key=lambda n: (n.get("pullback_score") is not None, n.get("pullback_score") or 0),
                   reverse=True)
    else:
        names.sort(key=lambda n: n["composite_zscore"], reverse=True)
    for i, n in enumerate(names):
        n["rank"] = i + 1

    # ----- Concentration check -----
    # M6 audit fix: route through concentration_z_score() in
    # quant_garage.universe so curated / grouped / reference paths share
    # one definition. The helper takes top_set + universe_set and returns a
    # z-score per category. We rebuild universe_set from starting_sector_counts
    # (the cohort baseline established earlier in this script — same source as
    # before, just routed through the shared helper).

    print("Computing concentration check...", file=sys.stderr)
    top_n_size = args.top_n
    top_n = names[:top_n_size]
    top_sector_counts = defaultdict(int)
    for n in top_n:
        top_sector_counts[n["sector"]] += 1

    concentration_findings = []
    N = len(top_n)
    if N >= 10 and starting_universe_total > 0:
        # Reconstruct a universe-set list from the precomputed category counts.
        # The helper internally takes the Counter; expanding to a list keeps it
        # general (the helper accepts any Sequence).
        universe_set = []
        for sector, count in starting_sector_counts.items():
            universe_set.extend([sector] * count)
        # Top-set is the rolled-out sector tags of the top-N rows.
        top_set = []
        for sector, count in top_sector_counts.items():
            top_set.extend([sector] * count)

        z_scores = concentration_z_score(top_set=top_set, universe_set=universe_set)
        for sector, info in z_scores.items():
            p = info["p_universe"]
            if p <= 0 or p >= 1:
                continue
            std_devs = info["z_score"]
            if abs(std_devs) >= 2.0:
                concentration_findings.append({
                    "dimension": "sector",
                    "value": sector,
                    "count_in_topn": info["observed"],
                    "expected_count": round(info["expected"], 2),
                    "std_devs_overweight": round(std_devs, 2),
                    "top_n": N,
                })
    concentration_findings.sort(key=lambda f: abs(f["std_devs_overweight"]), reverse=True)

    # ----- Survivorship -----
    #
    # Honest labeling: 'clean' only when active=false tickers were actually
    # fetched. None of the candidate sources here (curated, reference,
    # grouped) pull active=false today, so the answer is always 'biased'.
    # Keep the branches structured so that when the active=false fetch
    # lands, flipping a single flag per source is enough.

    has_lookback = (
        args.mom_3m_top_quartile
        or args.min_mom_3m is not None
        or args.ocf_yield_min is not None
    )
    # Set to True only after a path that actually retrieves delisted names
    # (e.g. /v3/reference/tickers?active=false union'd into the candidate
    # pool). No path does this yet.
    fetched_inactive = False
    delisted_in_window = 0  # would be a real count once fetched_inactive=True

    if fetched_inactive and delisted_in_window > 0:
        survivorship_mode = "clean"
    else:
        survivorship_mode = "biased"

    if args.candidate_source == "curated":
        note = ("Curated seed list is current-only (active=true). Re-run with "
                "--candidate-source reference plus active=false expansion "
                "for a survivorship-clean cohort. The active=false fetch is "
                "queued for a follow-up sprint.")
    elif args.candidate_source == "reference":
        if has_lookback:
            note = ("Lookback predicates applied over today's active=true "
                    "reference pool; delisted-in-window names were not pulled, "
                    "so the cohort is forward-biased. The active=false fetch "
                    "is queued for a follow-up sprint.")
        else:
            note = ("Current-snapshot reference screen (active=true only); "
                    "the active=false fetch is queued for a follow-up sprint.")
    else:  # grouped
        if has_lookback:
            note = ("Lookback predicates applied over today's grouped-daily "
                    "active pool; delisted-in-window names were not pulled, "
                    "so the cohort is forward-biased.")
        else:
            note = ("Current-snapshot grouped-daily screen; no lookback "
                    "predicates, so no point-in-time replay is attempted.")

    survivorship = {
        "mode": survivorship_mode,
        "delisted_in_window": delisted_in_window,
        "note": note,
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
                "pullback_score": (round(n["pullback_score"], 6)
                                   if n.get("pullback_score") is not None else None),
            }
            for n in names
        ],
        "concentration": concentration_findings,
        "survivorship": survivorship,
        "sources": sources,
    }
    return payload


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


def fmt_adv(v):
    if v is None:
        return "n/a"
    if v >= 1e9:
        return f"{v/1e9:,.2f}B"
    if v >= 1e6:
        return f"{v/1e6:,.1f}M"
    if v >= 1e3:
        return f"{v/1e3:,.0f}K"
    return f"{v:,.0f}"


def fmt_price(v):
    if v is None:
        return "n/a"
    return f"${v:,.2f}"


def render_table(payload, want_week=False, want_adv=False, want_price=False, want_pullback=False):
    lines = []
    end = payload["universe_size_end"]
    start = payload["universe_size_start"]
    lines.append(f"Filter chain -> {end} names from {start:,}")
    if payload["tier"] != "A":
        lines.append("Tier B run (free Basic, curated 100-name seed). Re-run on Stocks Starter for the full pool.")
    lines.append("")

    # Cap displayed rows at 30 for the pullback/screener mode, 20 otherwise.
    row_cap = 30 if (want_pullback or want_week) else 20
    rows = payload["results"][:row_cap]
    if rows:
        headers = ["Rank", "Ticker"]
        if want_price:
            headers.append("Price")
        # Show MCap only when we have it for at least one row
        show_mcap = any(r.get("market_cap") for r in rows)
        if show_mcap:
            headers.append("MCap($B)")
        if want_adv:
            headers.append("ADV(20d)")
        headers.append("3M Mom")
        if want_week:
            headers.append("Wk Ret")
        # Only show OCF when at least one row has it
        show_ocf = any(r["factors"].get("ocf_yield") is not None for r in rows)
        if show_ocf:
            headers.append("OCF Yld")
        if want_pullback:
            headers.append("Pull")
        headers.append("Z-score")

        body = []
        for r in rows:
            f = r["factors"]
            row = [str(r["rank"]), r["ticker"]]
            if want_price:
                row.append(fmt_price(f.get("price")))
            if show_mcap:
                row.append(fmt_mcap(r["market_cap"]) if r.get("market_cap") else "n/a")
            if want_adv:
                row.append(fmt_adv(f.get("adv_20d")))
            row.append(fmt_pct(f.get("mom_3m")))
            if want_week:
                row.append(fmt_pct(f.get("week_return")))
            if show_ocf:
                row.append(fmt_pct(f.get("ocf_yield"), signed=False)
                           if f.get("ocf_yield") is not None else "n/a")
            if want_pullback:
                ps = r.get("pullback_score")
                row.append(f"{ps*100:.2f}" if ps is not None else "n/a")
            row.append(fmt_z(r["composite_zscore"]))
            body.append(row)
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
    note = surv.get("note", "")
    if surv["mode"] == "clean":
        delisted_n = surv.get("delisted_in_window") or 0
        lines.append(
            f"Survivorship: clean. {delisted_n} delisted name(s) retained "
            f"for the lookback window. {note}".rstrip()
        )
    elif surv["mode"] == "biased":
        lines.append(f"Survivorship: biased. {note}")
    else:
        lines.append(f"Survivorship: {surv['mode']}. {note}")

    return "\n".join(lines)


def render(payload: dict) -> str:
    """Public render wrapper — infers renderer flags from the payload's scan_params."""
    scan = payload.get("scan_params", {})
    return render_table(
        payload,
        want_week=scan.get("max_week_return") is not None,
        want_adv=scan.get("min_adv") is not None,
        want_price=scan.get("min_price") is not None,
        want_pullback=scan.get("rank_by") == "pullback",
    )
