#!/usr/bin/env python3
"""
Reference implementation of the factor-research skill.

Runs a multi-factor IC + decile-spread study on a US equity universe
and emits two output layers from one analysis:

  Layer 1: canonical JSON matching skills/factor-research/output-schema.json
  Layer 2: rendered FactSet-style factor research table to
           examples/factor-research-output.md

Usage:
    python3 examples/run-factor-research.py                       # default 5y top-500
    python3 examples/run-factor-research.py --universe-size 200   # smaller universe
    python3 examples/run-factor-research.py --years 3             # shorter window
    python3 examples/run-factor-research.py --interface rest      # force REST grouped
    python3 examples/run-factor-research.py --interface flat-files

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/factor-research-output.md (gitignored).

Universe-wide multi-year price data is exactly what flat-files are for.
Default run pulls daily aggregates via flat-files (one S3 day-bucket per
trading day = ~750 files for a 3y window), parallelized 16 workers,
rate-limit-free. Via REST, the equivalent grouped-daily endpoint returns
all ~10,000 US stocks for a single date in one call, so we use the same
"one call per trading day" pattern when flat-files entitlement is missing.

Real runtime:
  - Flat-files cold (no on-disk cache): 8-15 minutes for a 5y top-500
  - REST grouped cold: 10-20 minutes (rate-limit dependent on plan)
  - Warm (parquet cache hit): under 2 minutes either way

That is the legitimate cost of universe-wide work. Knowing in 15 minutes
whether a factor proposition works is faster than the alternative.
"""
import os
import sys
import json
import math
import time
import argparse
from io import BytesIO
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    newey_west_se,
    resolve_output_format,
    emit_to_stdout,
)


TODAY = today()

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache", "factor-research")
os.makedirs(CACHE_DIR, exist_ok=True)


client = MassiveClient()
# Used for the flat-files S3 probe (which uses MASSIVE_API_KEY as the S3 key+secret).
# Read here only to pass into boto3; routine HTTP goes through `client`.
_KEY = client.api_key


# ----- HTTP / S3 helpers -----


def fetch_rest(path, params=None):
    """Single GET via the shared client. Returns parsed JSON only.
    Discards fetched_at because the historical signature of this function
    didn't surface it; callers that need per-call provenance use the
    sources block at the bottom of the run.
    """
    try:
        body, _ = client.get(path, params)
    except FetchError as e:
        raise RuntimeError(f"{e.status_code} on {path}: {e}")
    return body


def fetch_all_rest(path, params=None, hard_cap=2000):
    out = []
    try:
        for page, _ in client.paginate(path, params):
            out.extend(page)
            if len(out) >= hard_cap:
                break
    except FetchError as e:
        raise RuntimeError(f"{e.status_code} on {path}: {e}")
    return out


def probe_flat_files():
    """Return True when the key has flat-files entitlement. False on 403.

    Flat-files lives behind an S3-compatible boto3 client, not a REST host,
    so MassiveClient does not wrap it. The probe stays on raw boto3.
    """
    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            endpoint_url="https://files.polygon.io",
            aws_access_key_id=_KEY,
            aws_secret_access_key=_KEY,
            config=Config(signature_version="s3v4"),
        )
        # Pick a known-good recent weekday
        probe_date = TODAY - timedelta(days=4)
        while probe_date.weekday() >= 5:  # Sat=5, Sun=6
            probe_date -= timedelta(days=1)
        key = f"us_stocks_sip/day_aggs_v1/{probe_date.year:04d}/{probe_date.month:02d}/{probe_date.isoformat()}.csv.gz"
        s3.head_object(Bucket="flatfiles", Key=key)
        return True, s3
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Forbidden" in msg or "InvalidAccessKey" in msg:
            return False, None
        # Unknown error: report and treat as unavailable
        print(f"WARN: flat-files probe failed: {msg[:200]}", file=sys.stderr)
        return False, None


def fetch_day_flatfile(s3, d):
    """Read a day-bucket from flat-files. Returns dataframe with normalized columns."""
    key = f"us_stocks_sip/day_aggs_v1/{d.year:04d}/{d.month:02d}/{d.isoformat()}.csv.gz"
    cache_path = os.path.join(CACHE_DIR, f"day_{d.isoformat()}.parquet")
    if os.path.exists(cache_path):
        return pd.read_parquet(cache_path)
    try:
        resp = s3.get_object(Bucket="flatfiles", Key=key)
    except Exception:
        return None
    raw = resp["Body"].read()
    df = pd.read_csv(BytesIO(raw), compression="gzip")
    # Flat-files schema: ticker, volume, open, close, high, low, window_start, transactions
    if "ticker" in df.columns and "close" in df.columns:
        df = df[["ticker", "close", "volume"]].rename(columns={"close": "c", "volume": "v"})
    df["date"] = d.isoformat()
    df.to_parquet(cache_path)
    return df


def fetch_day_rest(d):
    """REST grouped-daily fallback. Returns same shape as fetch_day_flatfile."""
    cache_path = os.path.join(CACHE_DIR, f"day_{d.isoformat()}.parquet")
    if os.path.exists(cache_path):
        return pd.read_parquet(cache_path)
    path = f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}?adjusted=true"
    try:
        doc = fetch_rest(path)
    except RuntimeError as e:
        # Holiday / no trading day
        return None
    rows = doc.get("results") or []
    if not rows:
        return None
    df = pd.DataFrame(rows)[["T", "c", "v"]].rename(columns={"T": "ticker"})
    df["date"] = d.isoformat()
    df.to_parquet(cache_path)
    return df


def is_trading_day(d):
    # Naive: M-F only. Holidays show up empty and get dropped in the loader.
    return d.weekday() < 5


def trading_days(start, end):
    out = []
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


# ----- Universe construction -----


def _build_factor_universe(target_size):
    """Pull top-N by current market cap. Returns list of {ticker, name, market_cap}.

    Renamed from `build_universe` (N6) to avoid shadowing
    `lib.quant_garage.build_universe`, which builds a point-in-time
    universe from grouped aggs. The factor-research script needs the
    enriched, market-cap-ranked variant, so we keep a script-local
    helper rather than swapping in the lib version."""
    print(f"Building universe (top {target_size} by market cap)...", file=sys.stderr)
    cache_path = os.path.join(CACHE_DIR, f"universe_top_{target_size}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    # Paginate /v3/reference/tickers and enrich with ticker details for market_cap
    # The reference endpoint doesn't expose market_cap directly; we pull a wide
    # candidate set and fetch details to rank.
    path = "/v3/reference/tickers?market=stocks&active=true&type=CS&limit=1000"
    candidates = fetch_all_rest(path, hard_cap=2000)
    # Restrict to major exchanges and reasonable tickers
    major = {"XNAS", "XNYS", "ARCX", "BATS"}
    candidates = [r for r in candidates if r.get("primary_exchange") in major]
    print(f"  Candidate set after exchange filter: {len(candidates)}", file=sys.stderr)
    # Use snapshot to get day.dv (dollar volume) as a market-cap proxy quickly
    # plus details endpoint only for the top survivors
    # Simpler path: enrich each with ticker details (market_cap), parallelized.
    # That is N calls; for N=1500 on Stocks Business this is sub-minute.

    def get_detail(tk):
        try:
            d = fetch_rest(f"/v3/reference/tickers/{tk}")
            r = d.get("results") or {}
            mc = r.get("market_cap")
            name = r.get("name")
            return (tk, mc, name) if mc else None
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(get_detail, r["ticker"]) for r in candidates[:1500]]
        for f in as_completed(futures):
            r = f.result()
            if r:
                rows.append(r)
    rows.sort(key=lambda x: x[1], reverse=True)
    top = [{"ticker": tk, "market_cap": mc, "name": name}
           for tk, mc, name in rows[:target_size]]
    with open(cache_path, "w") as f:
        json.dump(top, f)
    return top


# ----- Daily price panel -----


def build_price_panel(universe, start, end, s3=None):
    """Pull daily aggregates for window, return pivoted DataFrame (date x ticker)
    of close prices for universe tickers only."""
    tickers = set(t["ticker"] for t in universe)
    days = trading_days(start, end)
    print(f"Pulling {len(days)} day-buckets for {len(tickers)} tickers...", file=sys.stderr)

    use_flat = s3 is not None

    def fetch_one(d):
        if use_flat:
            df = fetch_day_flatfile(s3, d)
            if df is None:
                # Fall back per-day to REST if flat-file is missing
                df = fetch_day_rest(d)
        else:
            df = fetch_day_rest(d)
        if df is None:
            return None
        # Filter to universe tickers only (massive bandwidth saver)
        return df[df["ticker"].isin(tickers)][["ticker", "c", "date"]]

    frames = []
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        for r in pool.map(fetch_one, days):
            done += 1
            if done % 50 == 0:
                elapsed = time.time() - t0
                print(f"  {done}/{len(days)} days fetched ({elapsed:.0f}s)", file=sys.stderr)
            if r is not None:
                frames.append(r)
    if not frames:
        raise RuntimeError("No daily data fetched")
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    pivot = panel.pivot_table(index="date", columns="ticker", values="c", aggfunc="last")
    pivot = pivot.sort_index()
    print(f"  panel shape: {pivot.shape} (days x tickers)", file=sys.stderr)
    return pivot


# ----- Fundamentals (value + quality) -----


# Shares-outstanding fallback chain (matches C12 in run-valuation-sanity-check.py).
# Income-statement keys, ordered from preferred (point-in-time, diluted) to last-resort.
_SHARES_FIELDS = (
    "weighted_average_diluted_shares_outstanding",
    "weighted_average_basic_shares_outstanding",
    "weighted_shares_outstanding",
    "share_class_shares_outstanding",
)


# C11.b-style fallback chains for the fundamentals fields the factor panel
# consumes. Massive's /vX/reference/financials has documented coverage gaps:
# many filings populate `net_income_loss` but not `net_income`, or `equity`
# but not `stockholders_equity` (or vice versa for older filings), and revenue
# is split between `revenues`, `revenue`, and (for some financial-services
# names) `net_revenue` / `total_revenue`. Without these chains, the factors
# silently drop coverage and the IC reflects a non-representative slice.
#
# Order matters: preferred field first, last-resort last. Each chain is paired
# with the balance_sheet / income_statement section it lives in.
_NET_INCOME_FIELDS = (
    "net_income_loss",                     # most common in Massive's feed
    "net_income",                          # canonical GAAP label
    "net_income_from_continuing_operations",  # fallback when discontinued ops split
)

_EQUITY_FIELDS = (
    "equity",                              # most common in Massive's feed
    "stockholders_equity",                 # canonical GAAP label
    "total_equity",
    "common_stockholders_equity",
)

_REVENUE_FIELDS = (
    "revenues",                            # most common in Massive's feed
    "revenue",
    "total_revenue",
    "net_revenue",                         # banks / insurance
)


def _pick_from_filing(fin, section, fields):
    """Walk `fields` in order and return the first non-null float value
    from `financials.<section>` of one filing. Returns (value, source_field)
    or (None, None). Negative values are allowed (net_income can be negative);
    zero is allowed for revenue but not for equity (caller checks).
    """
    node = (fin or {}).get(section) or {}
    for field in fields:
        sub = node.get(field) or {}
        v = sub.get("value")
        if v is None:
            continue
        try:
            return float(v), field
        except (TypeError, ValueError):
            continue
    return None, None


def _pick_shares_from_filing(fin):
    """Extract a shares value from one filing's `financials` blob.
    Returns (value, source_field) or (None, None)."""
    inc = (fin or {}).get("income_statement") or {}
    for field in _SHARES_FIELDS:
        node = inc.get(field) or {}
        v = node.get("value")
        if v is not None:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                return fv, field
    return None, None


def fetch_fundamentals(universe):
    """Pull up to ~5 years of quarterly filings per ticker for point-in-time use.

    C2/C4: prior implementation pulled a single most-recent annual filing per
    ticker, which forced both the value factor (mcap proxy) and the quality
    factor (ROE) to be either look-ahead-biased or constant across all
    historical months. The fix pulls the filing history once per ticker and
    lets callers select the latest filing with
    `period_of_report_date <= rebalance_date` at each monthly rebalance.

    Returns
    -------
    dict[ticker] -> list[dict] (most-recent first), where each dict has:
        period_of_report_date: ISO date string ('YYYY-MM-DD')
        shares: float or None  (via _SHARES_FIELDS fallback chain)
        shares_source: str or None
        book_equity: float or None  (via _EQUITY_FIELDS fallback chain)
        book_equity_source: str or None
        net_income: float or None  (via _NET_INCOME_FIELDS fallback chain)
        net_income_source: str or None
        revenue: float or None  (via _REVENUE_FIELDS fallback chain)
        revenue_source: str or None

    Cache is keyed by ticker; bump the filename when the schema changes
    so a stale single-annual cache from a prior run isn't reused.
    """
    print(f"Pulling point-in-time fundamentals for {len(universe)} names...", file=sys.stderr)
    # v2 cache file: prior v1 had only equity / net_income (no fallback chains,
    # no revenue, no source tags). Don't reuse it.
    cache_path = os.path.join(CACHE_DIR, "fundamentals_pit_v2.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    def get_one(tk):
        try:
            # quarterly cadence x 80 rows = up to ~20 years of history per ticker;
            # period_of_report_date filter is applied client-side at each rebalance.
            d = fetch_rest(
                f"/vX/reference/financials?ticker={tk}&timeframe=quarterly&limit=80&order=desc"
            )
            rows = d.get("results") or []
            filings = []
            for r in rows:
                porp = r.get("period_of_report_date")
                if not porp:
                    continue
                fin = r.get("financials") or {}
                equity_val, equity_src = _pick_from_filing(fin, "balance_sheet", _EQUITY_FIELDS)
                ni_val, ni_src = _pick_from_filing(fin, "income_statement", _NET_INCOME_FIELDS)
                rev_val, rev_src = _pick_from_filing(fin, "income_statement", _REVENUE_FIELDS)
                shares_val, shares_source = _pick_shares_from_filing(fin)
                filings.append({
                    "period_of_report_date": porp,
                    "shares": shares_val,
                    "shares_source": shares_source,
                    "book_equity": equity_val,
                    "book_equity_source": equity_src,
                    "net_income": ni_val,
                    "net_income_source": ni_src,
                    "revenue": rev_val,
                    "revenue_source": rev_src,
                })
            # Most-recent first (the API returns desc, but be explicit so
            # the lookup helpers can stop at the first hit).
            filings.sort(key=lambda x: x["period_of_report_date"], reverse=True)
            return tk, filings
        except Exception:
            return tk, []

    out = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(get_one, t["ticker"]): t["ticker"] for t in universe}
        done = 0
        for f in as_completed(futures):
            tk, filings = f.result()
            out[tk] = filings
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(universe)} fundamentals fetched", file=sys.stderr)
    with open(cache_path, "w") as f:
        json.dump(out, f)
    return out


def _latest_filing_before(filings, rebal_date):
    """Pick the most recent filing with period_of_report_date <= rebal_date.
    `filings` must be sorted most-recent first. Returns the filing dict or None."""
    if not filings:
        return None
    cutoff = rebal_date.strftime("%Y-%m-%d") if hasattr(rebal_date, "strftime") else str(rebal_date)[:10]
    for f in filings:
        if f["period_of_report_date"] <= cutoff:
            return f
    return None


# ----- Factor computation -----


def _winsorize_series(s, low=0.01, high=0.99):
    """Winsorize a pandas Series at given percentiles.

    Renamed from `winsorize` (N6) to avoid shadowing
    `lib.quant_garage.winsorize`. The lib version takes Sequence[float]
    and returns list[float]; this one operates on pandas Series and
    preserves the index, which the factor panel code depends on. The
    two are not signature-compatible, so we keep this local helper
    rather than swapping.
    """
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(low), s.quantile(high)
    return s.clip(lower=lo, upper=hi)


def compute_factor_panel(prices, fundamentals, universe):
    """Compute monthly factor scores. Returns (factors, raw, monthly_idx, coverage).

    `factors` and `raw` are dict[factor_name] -> DataFrame (month_end x ticker).
    `coverage` is list[dict] (one per rebalance date) capturing universe size,
    per-factor n_scored / coverage_pct, and per-factor dropped_for_reason
    counts. Surfaces the silent-failure mode where Massive's sparse financials
    feed drops names from value / quality / size at every rebalance.
    """
    # Size factor needs market_cap metadata from the universe row.
    mcap_lookup = {u["ticker"]: u.get("market_cap") for u in universe}

    # Monthly rebalance dates: last trading day of each month in the price index
    monthly_idx = prices.resample("ME").last().dropna(how="all").index
    print(f"Computing factors over {len(monthly_idx)} monthly rebalance dates...", file=sys.stderr)

    # Daily log returns
    log_ret = np.log(prices / prices.shift(1))

    factors = {}
    raw = {}

    # Per-rebalance coverage diagnostics. Indexed by rebalance date (ISO string).
    # Each rebalance gets its own dict so we can emit per_rebalance_coverage in
    # the canonical JSON output. Universe size = columns present in prices at
    # that rebalance (post continuous-trading filter).
    coverage_by_date = {}
    universe_size = len(prices.columns)

    def _init_coverage(t):
        key = pd.Timestamp(t).strftime("%Y-%m-%d")
        if key not in coverage_by_date:
            coverage_by_date[key] = {
                "rebalance_date": key,
                "universe_size": universe_size,
                "factor_coverage": {},
            }
        return coverage_by_date[key]

    def _record(t, factor_label, n_scored, dropped_for_reason):
        c = _init_coverage(t)
        c["factor_coverage"][factor_label] = {
            "n_scored": int(n_scored),
            "coverage_pct": round(n_scored / universe_size, 4) if universe_size else 0.0,
            "dropped_for_reason": {k: int(v) for k, v in dropped_for_reason.items() if v},
        }

    # Momentum: 12M-1M lookback. At rebalance date t, score = price[t-21] / price[t-252] - 1
    mom_scores, mom_raw = [], []
    for t in monthly_idx:
        try:
            t_loc = prices.index.get_loc(t)
        except KeyError:
            continue
        dropped = defaultdict(int)
        if t_loc < 252:
            # No name has 12M of history here; record all as dropped and skip
            _record(t, "momentum", 0, {"insufficient_history": universe_size})
            continue
        p_recent = prices.iloc[t_loc - 21]
        p_far = prices.iloc[t_loc - 252]
        score = (p_recent / p_far) - 1.0
        # Count dropped names (missing price 1M or 12M back)
        n_scored = int(score.notna().sum())
        n_missing = universe_size - n_scored
        if n_missing:
            dropped["missing_price_history"] = n_missing
        _record(t, "momentum", n_scored, dropped)
        raw_s = score.copy(); raw_s.name = t
        score = _winsorize_series(score)
        score.name = t
        mom_scores.append(score)
        mom_raw.append(raw_s)
    factors["Momentum (12-1M)"] = pd.DataFrame(mom_scores) if mom_scores else pd.DataFrame()
    raw["Momentum (12-1M)"] = pd.DataFrame(mom_raw) if mom_raw else pd.DataFrame()

    # Low-vol: 1 / realized_vol_252d (annualized stdev of daily log returns)
    lv_scores, lv_raw = [], []
    for t in monthly_idx:
        try:
            t_loc = prices.index.get_loc(t)
        except KeyError:
            continue
        dropped = defaultdict(int)
        if t_loc < 252:
            _record(t, "low_vol", 0, {"insufficient_history": universe_size})
            continue
        window = log_ret.iloc[t_loc - 252:t_loc]
        vol = window.std() * math.sqrt(252)
        score = 1.0 / vol.replace(0, np.nan)
        n_scored = int(score.notna().sum())
        n_missing = universe_size - n_scored
        if n_missing:
            dropped["insufficient_history"] = n_missing
        _record(t, "low_vol", n_scored, dropped)
        raw_s = score.copy(); raw_s.name = t
        score = _winsorize_series(score)
        score.name = t
        lv_scores.append(score)
        lv_raw.append(raw_s)
    factors["Low-Vol (1/realiz)"] = pd.DataFrame(lv_scores) if lv_scores else pd.DataFrame()
    raw["Low-Vol (1/realiz)"] = pd.DataFrame(lv_raw) if lv_raw else pd.DataFrame()

    # Size: log(market_cap). Negative-rank: smaller mcap scores higher (classic
    # size factor, "small minus big"). Uses point-in-time mcap = historical
    # shares × close at rebalance. Same drop semantics as value (missing filing
    # or missing shares = no score for this name this rebalance).
    sz_scores, sz_raw = [], []
    for t in monthly_idx:
        if t not in prices.index:
            continue
        p_t = prices.loc[t]
        scores = {}
        dropped = defaultdict(int)
        for tk in prices.columns:
            filings = fundamentals.get(tk) or []
            filing = _latest_filing_before(filings, t)
            if filing is None:
                dropped["missing_filing"] += 1
                continue
            shares_t = filing.get("shares")
            pt = p_t.get(tk)
            if shares_t is None:
                dropped["missing_shares"] += 1
                continue
            if pt is None or not (pt > 0):
                dropped["missing_price"] += 1
                continue
            mcap_t = float(shares_t) * float(pt)
            if mcap_t <= 0:
                dropped["non_positive_mcap"] += 1
                continue
            # Negate so "smaller cap = higher score" (small-minus-big convention)
            scores[tk] = -math.log(mcap_t)
        _record(t, "size", len(scores), dropped)
        if not scores:
            continue
        s = pd.Series(scores)
        raw_s = s.copy(); raw_s.name = t
        s = _winsorize_series(s)
        s.name = t
        sz_scores.append(s)
        sz_raw.append(raw_s)
    factors["Size (-log mcap)"] = pd.DataFrame(sz_scores) if sz_scores else pd.DataFrame()
    raw["Size (-log mcap)"] = pd.DataFrame(sz_raw) if sz_raw else pd.DataFrame()

    # Value: 1 / (P/B) = book_equity / market_cap, point-in-time at each rebalance.
    val_scores, val_raw = [], []
    for t in monthly_idx:
        if t not in prices.index:
            continue
        p_t = prices.loc[t]
        scores = {}
        dropped = defaultdict(int)
        for tk in prices.columns:
            filings = fundamentals.get(tk) or []
            filing = _latest_filing_before(filings, t)
            if filing is None:
                dropped["missing_filing"] += 1
                continue
            be = filing.get("book_equity")
            shares_t = filing.get("shares")
            pt = p_t.get(tk)
            if be is None:
                dropped["missing_equity"] += 1
                continue
            if shares_t is None:
                dropped["missing_shares"] += 1
                continue
            if pt is None or not (pt > 0):
                dropped["missing_price"] += 1
                continue
            if be <= 0:
                dropped["negative_or_zero_equity"] += 1
                continue
            if shares_t <= 0:
                dropped["non_positive_shares"] += 1
                continue
            mcap_t = float(shares_t) * float(pt)
            if mcap_t <= 0:
                dropped["non_positive_mcap"] += 1
                continue
            scores[tk] = float(be) / mcap_t
        _record(t, "value", len(scores), dropped)
        s = pd.Series(scores)
        raw_s = s.copy(); raw_s.name = t
        s = _winsorize_series(s)
        s.name = t
        val_scores.append(s)
        val_raw.append(raw_s)
    factors["Value (1/(P/B))"] = pd.DataFrame(val_scores) if val_scores else pd.DataFrame()
    raw["Value (1/(P/B))"] = pd.DataFrame(val_raw) if val_raw else pd.DataFrame()

    # Quality: ROE = net_income / book_equity, point-in-time at each rebalance.
    qual_scores, qual_raw = [], []
    for t in monthly_idx:
        scores = {}
        dropped = defaultdict(int)
        for tk in prices.columns:
            filings = fundamentals.get(tk) or []
            filing = _latest_filing_before(filings, t)
            if filing is None:
                dropped["missing_filing"] += 1
                continue
            ni = filing.get("net_income")
            be = filing.get("book_equity")
            if ni is None:
                dropped["missing_net_income"] += 1
                continue
            if be is None:
                dropped["missing_equity"] += 1
                continue
            if be <= 0:
                # Don't default to 0; ROE = NI / 0 explodes. Drop the name.
                dropped["negative_or_zero_equity"] += 1
                continue
            roe = float(ni) / float(be)
            # Same +150% / -100% sanity bounds the prior static version used.
            if roe > 1.5 or roe < -1.0:
                dropped["roe_out_of_bounds"] += 1
                continue
            scores[tk] = roe
        _record(t, "quality", len(scores), dropped)
        if not scores:
            continue
        s = pd.Series(scores)
        raw_s = s.copy(); raw_s.name = t
        s = _winsorize_series(s)
        s.name = t
        qual_scores.append(s)
        qual_raw.append(raw_s)
    factors["Quality (ROE)"] = pd.DataFrame(qual_scores) if qual_scores else pd.DataFrame()
    raw["Quality (ROE)"] = pd.DataFrame(qual_raw) if qual_raw else pd.DataFrame()

    # Coverage list ordered by rebalance date
    coverage = [coverage_by_date[k] for k in sorted(coverage_by_date.keys())]
    return factors, raw, monthly_idx, coverage


# ----- IC, decile, correlation -----


def forward_returns(prices, rebal_dates, horizon_months):
    """For each rebalance date t, compute return from t to t + horizon trading days."""
    # Convert horizon months to trading days (21/mo approximation)
    h_days = horizon_months * 21
    rets = {}
    idx = prices.index
    for t in rebal_dates:
        try:
            t_loc = idx.get_loc(t)
        except KeyError:
            continue
        fwd_loc = t_loc + h_days
        if fwd_loc >= len(idx):
            continue
        r = (prices.iloc[fwd_loc] / prices.iloc[t_loc]) - 1
        rets[t] = r
    return pd.DataFrame(rets).T


def compute_ics(factor_df, prices, horizons=(1, 3, 6, 12)):
    """For a given factor panel (month_end x ticker), compute mean IC, SE, t-stat per horizon.

    C3: forward returns at horizon k months on a MONTHLY rebalance overlap by
    k-1 months, so the per-month IC series is serially correlated. Treating it
    as iid (mean*sqrt(n)/std) inflates the t-stat by roughly sqrt(k) at long
    horizons. Fix: Newey-West HAC SE with lag = k - 1 (academic convention;
    for k=1 the lag is 0 = the iid case, identical to the old formula). The
    helper raises on degenerate inputs (n < 2 or non-positive long-run
    variance); on raise we report None for `t` and `ic_se` but still surface
    the count so the caller can flag "sample too small".

    Returns dict[horizon] -> {ic, t, ic_se, n_months, n_pairs_per_rebalance,
    ic_series}. `n_pairs_per_rebalance` is a list of {date, n_pairs, ic}
    surfacing the cross-section size that drove each per-month IC; thin
    cross-sections (e.g. quality factor with sparse fundamentals) inflate
    the noise on the aggregated number, and this is what reveals it.
    """
    out = {}
    rebal = factor_df.index
    for h in horizons:
        fwd = forward_returns(prices, rebal, h)
        common = factor_df.index.intersection(fwd.index)
        ics = []
        pair_counts = []
        for t in common:
            score = factor_df.loc[t]
            ret = fwd.loc[t]
            mask = score.notna() & ret.notna()
            n_pairs = int(mask.sum())
            if n_pairs < 30:
                # Still record the under-min observation so coverage is visible
                pair_counts.append({
                    "date": pd.Timestamp(t).strftime("%Y-%m-%d"),
                    "n_pairs": n_pairs,
                    "ic": None,
                })
                continue
            rho, _ = spearmanr(score[mask], ret[mask])
            if math.isnan(rho):
                pair_counts.append({
                    "date": pd.Timestamp(t).strftime("%Y-%m-%d"),
                    "n_pairs": n_pairs,
                    "ic": None,
                })
                continue
            ics.append(rho)
            pair_counts.append({
                "date": pd.Timestamp(t).strftime("%Y-%m-%d"),
                "n_pairs": n_pairs,
                "ic": float(rho),
            })
        n_months = len(ics)
        if n_months < 6:
            out[h] = {
                "ic": None,
                "t": None,
                "ic_se": None,
                "n_months": n_months,
                "n_pairs_per_rebalance": pair_counts,
                "ic_series": list(map(float, ics)),
            }
            continue
        mean_ic = float(np.mean(ics))
        nw_lag = max(0, h - 1)
        nw_lag = min(nw_lag, n_months - 1)
        try:
            ic_se = float(newey_west_se(ics, lag=nw_lag))
            t_stat = mean_ic / ic_se if ic_se > 0 else None
        except ValueError:
            ic_se = None
            t_stat = None
        out[h] = {
            "ic": mean_ic,
            "t": t_stat,
            "ic_se": ic_se,
            "n_months": n_months,
            "n_pairs_per_rebalance": pair_counts,
            "ic_series": list(map(float, ics)),
        }
    return out


def compute_decile_spread(factor_df, prices, horizon_months, n_deciles=10):
    """Annualized D10 - D1 spread; plus hit rate (% months D10 > D1)."""
    rebal = factor_df.index
    fwd = forward_returns(prices, rebal, horizon_months)
    common = factor_df.index.intersection(fwd.index)
    spreads = []
    wins = 0
    for t in common:
        score = factor_df.loc[t]
        ret = fwd.loc[t]
        mask = score.notna() & ret.notna()
        if mask.sum() < n_deciles * 5:
            continue
        s = score[mask]
        r = ret[mask]
        ranks = s.rank(method="first")
        decile = pd.qcut(ranks, n_deciles, labels=False, duplicates="drop")
        d_returns = r.groupby(decile).mean()
        if d_returns.empty or 0 not in d_returns.index or (n_deciles - 1) not in d_returns.index:
            continue
        spread = d_returns.loc[n_deciles - 1] - d_returns.loc[0]
        spreads.append(spread)
        if spread > 0:
            wins += 1
    if not spreads:
        return None, None
    # Arithmetic annualization
    annualized = float(np.mean(spreads) * (12.0 / horizon_months))
    hit_rate = wins / len(spreads)
    return annualized, hit_rate


def compute_signal_correlation(factor_dfs):
    """Mean cross-sectional Spearman correlation between factor signal pairs."""
    names = list(factor_dfs.keys())
    n = len(names)
    matrix = np.eye(n)
    # Use union of rebalance dates across all factors
    all_dates = None
    for df in factor_dfs.values():
        if df.empty:
            continue
        all_dates = df.index if all_dates is None else all_dates.union(df.index)
    if all_dates is None or len(all_dates) == 0:
        return names, matrix.tolist()
    for i in range(n):
        for j in range(i + 1, n):
            df_i = factor_dfs[names[i]]
            df_j = factor_dfs[names[j]]
            if df_i.empty or df_j.empty:
                continue
            corrs = []
            for t in all_dates:
                if t not in df_i.index or t not in df_j.index:
                    continue
                si = df_i.loc[t]
                sj = df_j.loc[t]
                mask = si.notna() & sj.notna()
                if mask.sum() < 30:
                    continue
                rho, _ = spearmanr(si[mask], sj[mask])
                if not math.isnan(rho):
                    corrs.append(rho)
            if corrs:
                mean_corr = float(np.mean(corrs))
                matrix[i][j] = mean_corr
                matrix[j][i] = mean_corr
    return names, matrix.tolist()


def current_deciles(factor_df, raw_df, n_top=5, factor_name="", universe_meta=None):
    """Return (top_5, bottom_5) at the most recent rebalance date.
    Ranks on factor_df (clamped) but displays from raw_df (unclamped) so the
    membership block shows real values, not the winsorization cap."""
    if factor_df.empty:
        return [], []
    last_t = factor_df.index[-1]
    s = factor_df.loc[last_t].dropna()
    if s.empty:
        return [], []
    raw_last = raw_df.loc[last_t] if (not raw_df.empty and last_t in raw_df.index) else s
    # Use clamped values to find tail names, raw to display the value
    bottom_names = s.sort_values(ascending=True).head(n_top).index
    top_names = s.sort_values(ascending=False).head(n_top).index
    name_lookup = {u["ticker"]: u.get("name") for u in (universe_meta or [])}
    def to_entry(ticker, factor_name):
        rv = raw_last.get(ticker)
        return {
            "ticker": ticker,
            "name": name_lookup.get(ticker),
            "value": float(rv) if rv is not None and not (isinstance(rv, float) and math.isnan(rv)) else None,
            "value_display": format_factor_value(factor_name, rv),
        }
    return [to_entry(t, factor_name) for t in top_names], \
           [to_entry(t, factor_name) for t in bottom_names]


def format_factor_value(factor_name, v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if "Momentum" in factor_name:
        if v >= 9.99:
            return ">+999%"
        if v <= -0.99:
            return "<-99%"
        return f"{v*100:+.0f}%"
    if "Value" in factor_name:
        # v is book/mc; invert to P/B for display
        pb = 1.0 / v if v > 0 else None
        if pb is None:
            return "n/a"
        if pb > 99:
            return f"P/B >99x"
        return f"P/B {pb:.1f}x"
    if "Quality" in factor_name:
        return f"ROE {v*100:.0f}%"
    if "Low-Vol" in factor_name:
        # v is 1/vol; invert for vol display
        vol = 1.0 / v if v > 0 else None
        return f"vol {vol*100:.0f}%" if vol else "n/a"
    return f"{v:.2f}"


# ----- Take generation -----


def build_take(factor_results, corr_matrix, corr_names):
    """Generate the one-paragraph take."""
    # Strongest by |t-stat|
    rated = [(f["name"], f.get("ic_tstat_1m"), f.get("ic_1m")) for f in factor_results]
    rated_sig = [r for r in rated if r[1] is not None]
    if not rated_sig:
        return "No factor produced a measurable IC in this window. Universe size or fundamentals coverage is likely too thin; re-run with --universe-size 500 or extend the window."
    rated_sig.sort(key=lambda x: -abs(x[1]))
    strongest = rated_sig[0]
    weakest_negative = None
    for r in rated_sig:
        if r[1] is not None and r[2] is not None and r[2] < 0:
            weakest_negative = r
            break
    # Highest off-diagonal |correlation|
    n = len(corr_names)
    max_pair = None
    max_abs = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            c = corr_matrix[i][j]
            if abs(c) > max_abs:
                max_abs = abs(c)
                max_pair = (corr_names[i], corr_names[j], c)
    sentences = []
    # Strongest
    if abs(strongest[1]) >= 2.0:
        sentences.append(
            f"{short_name(strongest[0])} is the strongest single factor in the current regime "
            f"(IC {strongest[2]:+.2f} 1M, t-stat {strongest[1]:+.1f})."
        )
    else:
        sentences.append(
            f"No factor is statistically significant in this window. The strongest signal "
            f"is {short_name(strongest[0])} at t-stat {strongest[1]:+.1f}, IC {strongest[2]:+.2f} 1M; "
            "treat as weak evidence, not actionable."
        )
    # Weakest / sign-flipped
    if weakest_negative:
        sentences.append(
            f"{short_name(weakest_negative[0])} IC is negative ({weakest_negative[2]:+.2f} 1M), "
            "consistent with a regime that rewards risk-taking, not the defensive style."
        )
    # Correlation
    if max_pair and abs(max_pair[2]) >= 0.3:
        sign = "positively" if max_pair[2] > 0 else "negatively"
        sentences.append(
            f"{short_name(max_pair[0])} and {short_name(max_pair[1])} are {sign} correlated "
            f"({max_pair[2]:+.2f}), so a combined sleeve gives "
            f"{'less' if max_pair[2] > 0 else 'more'} diversification than naive equal weight implies."
        )
    return " ".join(sentences)


def short_name(name):
    return name.split(" (")[0]


# ----- Rendering -----


def render(payload):
    lines = []
    udef = payload["universe_definition"]
    ws = pd.to_datetime(payload["window_start"]).strftime("%Y-%m")
    we = pd.to_datetime(payload["window_end"]).strftime("%Y-%m")
    years = max(1, round((pd.to_datetime(payload["window_end"]) - pd.to_datetime(payload["window_start"])).days / 365))
    lines.append(f"Factor research: {udef['label']} · {years}y window ({ws} → {we}) · {len(payload['factors'])} factors")
    if udef.get("survivorship_mode") == "biased":
        lines.append(f"Survivorship: {udef.get('survivorship_note', 'biased; see SKILL.md')}")
    lines.append("")

    # IC + decay block
    lines.append("Single-factor IC + decay")
    headers = ["Factor", "1M IC", "3M IC", "6M IC", "12M IC", "t-stat (1M)", "Sample"]
    body = []
    for f in payload["factors"]:
        body.append([
            f["name"],
            fmt_signed(f.get("ic_1m"), 2),
            fmt_signed(f.get("ic_3m"), 2),
            fmt_signed(f.get("ic_6m"), 2),
            fmt_signed(f.get("ic_12m"), 2),
            fmt_signed(f.get("ic_tstat_1m"), 1),
            f"{f.get('n_observations', 0):,}",
        ])
    lines.extend(md_table(headers, body, align=["left", "right", "right", "right", "right", "right", "right"]))
    lines.append("")

    # Decile spread block
    lines.append("Long-short decile spreads (D10 - D1, annualized)")
    headers = ["Factor", "1M", "3M", "12M", "Hit rate (12M)"]
    body = []
    for f in payload["factors"]:
        body.append([
            short_name(f["name"]),
            fmt_pct(f.get("decile_spread_1m")),
            fmt_pct(f.get("decile_spread_3m")),
            fmt_pct(f.get("decile_spread_12m")),
            fmt_int_pct(f.get("hit_rate_12m")),
        ])
    lines.extend(md_table(headers, body, align=["left", "right", "right", "right", "right"]))
    lines.append("")

    # Correlation matrix
    cm = payload["factor_correlation_matrix"]
    if cm["factor_names"]:
        lines.append("Factor correlation matrix (decile signals)")
        short_cols = [short_label(n) for n in cm["factor_names"]]
        long_rows = [short_name(n) for n in cm["factor_names"]]
        # Header row
        header = " " * 14 + "  ".join(c.rjust(6) for c in short_cols)
        lines.append(header)
        for i, rn in enumerate(long_rows):
            row_cells = "  ".join(fmt_corr(cm["matrix"][i][j]).rjust(6) for j in range(len(short_cols)))
            lines.append(f"{rn.ljust(14)}{row_cells}")
        lines.append("")

    # Current decile membership
    lines.append("Current decile membership (top + bottom 5 per factor)")
    lines.append("")
    factor_rows = payload["factors"]
    # Render in two row-groups: D10 then D1, three factors per group
    def render_group(group_factors, level_key, level_label):
        if not group_factors:
            return
        hdr_cells = []
        for f in group_factors:
            hdr_cells.append(f"{short_name(f['name']).upper()} ({level_label})")
        lines.append("    " + "    ".join(c.ljust(20) for c in hdr_cells))
        max_rows = max(len(f.get(level_key, [])) for f in group_factors)
        for i in range(max_rows):
            row_cells = []
            for f in group_factors:
                entries = f.get(level_key, [])
                if i < len(entries):
                    e = entries[i]
                    cell = f"{e['ticker']:<5} {e.get('value_display') or ''}"
                else:
                    cell = ""
                row_cells.append(cell)
            lines.append("    " + "    ".join(c.ljust(20) for c in row_cells))
        lines.append("")
    # Render in groups of 3 (D10 then D1 per group). Handles any number of
    # surviving factors after coverage drops.
    for start in range(0, len(factor_rows), 3):
        grp = factor_rows[start:start + 3]
        render_group(grp, "top_5_current", "D10")
        render_group(grp, "bottom_5_current", "D1")

    # Take
    lines.append(f"Take: {payload['take']}")
    return "\n".join(lines)


def short_label(name):
    n = short_name(name)
    if n.startswith("Momentum"):
        return "Mom"
    if n.startswith("Value"):
        return "Val"
    if n.startswith("Quality"):
        return "Qual"
    if n.startswith("Low-Vol"):
        return "LowVol"
    return n[:6]


def fmt_signed(v, decimals):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:+.{decimals}f}"


def fmt_pct(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v*100:+.1f}%"


def fmt_int_pct(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v*100:.0f}%"


def fmt_corr(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:+.2f}" if v != 1.0 else "1.00"


def md_table(headers, body, align):
    out = ["| " + " | ".join(headers) + " |"]
    sep = []
    for a in align:
        if a == "right":
            sep.append("---:")
        else:
            sep.append("---")
    out.append("|" + "|".join(sep) + "|")
    for row in body:
        out.append("| " + " | ".join(row) + " |")
    return out


# ----- CLI -----


def main():
    p = argparse.ArgumentParser(description="factor-research reference implementation")
    p.add_argument("--universe-size", type=int, default=200,
                   help="Top N by current market cap (default 200; 500 for full SP500-like run)")
    p.add_argument("--years", type=float, default=3.0,
                   help="Backtest window length in years (default 3)")
    p.add_argument("--end-date", type=str, default=None,
                   help="End date YYYY-MM-DD (default today)")
    p.add_argument("--interface", choices=["auto", "flat-files", "rest"], default="auto",
                   help="Data source for daily aggregates (auto probes flat-files, falls back to REST)")
    p.add_argument("--format", choices=["render", "json", "both"], default=None,
                   help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
    p.add_argument("--debug-factor", action="store_true",
                   help="Dump per-rebalance coverage + per-factor IC raw distribution + "
                        "Newey-West SE inputs per horizon to stderr. Use to investigate "
                        "edge-case numbers (thin cross-sections, sparse fundamentals).")
    args = p.parse_args()
    fmt = resolve_output_format(args.format)

    end_d = date.fromisoformat(args.end_date) if args.end_date else TODAY
    start_d = end_d - timedelta(days=int(args.years * 365))

    # Probe flat-files entitlement
    s3 = None
    interface_used = "rest"
    if args.interface in ("auto", "flat-files"):
        has_ff, s3_client = probe_flat_files()
        if has_ff:
            s3 = s3_client
            interface_used = "flat-files"
            print("flat-files: ENTITLED", file=sys.stderr)
        else:
            print("flat-files: NOT entitled (403). Falling back to REST grouped-daily.", file=sys.stderr)
            if args.interface == "flat-files":
                print("--interface flat-files requested but key not entitled. Exiting.", file=sys.stderr)
                sys.exit(2)

    # Universe
    universe = _build_factor_universe(args.universe_size)
    print(f"Universe: {len(universe)} names", file=sys.stderr)

    # Price panel
    prices = build_price_panel(universe, start_d, end_d, s3=s3)

    # Filter to names with continuous-ish trading history (>=80% of days)
    coverage = prices.notna().mean()
    keep = coverage[coverage >= 0.8].index
    dropped = len(prices.columns) - len(keep)
    prices = prices[keep]
    universe = [u for u in universe if u["ticker"] in keep]
    print(f"Continuous-trading filter dropped {dropped}; kept {len(universe)}", file=sys.stderr)

    # Fundamentals
    fundamentals = fetch_fundamentals(universe)
    n_with_fund = sum(1 for v in fundamentals.values() if v is not None)
    print(f"Fundamentals coverage: {n_with_fund}/{len(universe)}", file=sys.stderr)

    # Factors
    factor_panels, factor_raw, monthly_idx, per_rebalance_coverage = compute_factor_panel(
        prices, fundamentals, universe
    )

    # Threshold pass: per-factor coverage summary. Drops fire BEFORE IC compute
    # so we don't waste time on factors we'll omit. Caveats are collected for
    # the payload.
    coverage_caveats = []
    dropped_factors = set()
    for display_name, key in _COVERAGE_KEY.items():
        any_caveat, all_drop, n_rebal, median_pct, n_below = _summarize_factor_coverage(
            per_rebalance_coverage, key
        )
        if n_rebal == 0:
            continue
        if all_drop:
            dropped_factors.add(display_name)
            print(
                f"  {display_name}: ALL rebalances below {_COVERAGE_DROP_PCT*100:.0f}% "
                f"coverage (median {median_pct*100:.0f}%); omitting from IC summary.",
                file=sys.stderr,
            )
            continue
        if any_caveat:
            short = short_name(display_name)
            coverage_caveats.append(
                f"{short} factor coverage fell below {int(_COVERAGE_CAVEAT_PCT*100)}% "
                f"on {n_below} of {n_rebal} rebalances (median coverage "
                f"{int(round(median_pct*100))}%). {short} IC numbers reflect only the "
                f"subset of names with valid inputs; see per_rebalance_coverage for breakdown."
            )

    # IC + decile per factor
    factor_results = []
    for name, df in factor_panels.items():
        if name in dropped_factors:
            continue
        if df.empty:
            print(f"  {name}: empty panel, skipping", file=sys.stderr)
            continue
        ics = compute_ics(df, prices)
        ds_1m, hit_1m = compute_decile_spread(df, prices, 1)
        ds_3m, _ = compute_decile_spread(df, prices, 3)
        ds_12m, hit_12m = compute_decile_spread(df, prices, 12)
        raw_df = factor_raw.get(name, pd.DataFrame())
        top_5, bot_5 = current_deciles(df, raw_df, n_top=5, factor_name=name, universe_meta=universe)
        n_obs = int((df.notna()).sum().sum())
        factor_results.append({
            "name": name,
            "definition": _definition_for(name),
            "direction": "higher_is_better",
            "n_observations": n_obs,
            "ic_1m": ics[1]["ic"],
            "ic_3m": ics[3]["ic"],
            "ic_6m": ics[6]["ic"],
            "ic_12m": ics[12]["ic"],
            "ic_tstat_1m": ics[1]["t"],
            # C3: t-stat at each horizon now reflects Newey-West HAC SE with
            # lag = horizon - 1. Emit SE and t per horizon, and the per-horizon
            # month count, so consumers see the inflation that the old
            # mean*sqrt(n)/std formula hid.
            "ic_tstat_3m": ics[3]["t"],
            "ic_tstat_6m": ics[6]["t"],
            "ic_tstat_12m": ics[12]["t"],
            "ic_se_1m": ics[1]["ic_se"],
            "ic_se_3m": ics[3]["ic_se"],
            "ic_se_6m": ics[6]["ic_se"],
            "ic_se_12m": ics[12]["ic_se"],
            "n_months_1m": ics[1]["n_months"],
            "n_months_3m": ics[3]["n_months"],
            "n_months_6m": ics[6]["n_months"],
            "n_months_12m": ics[12]["n_months"],
            # Per-horizon per-rebalance pair counts so consumers can see when
            # a thin cross-section (e.g. quality with sparse fundamentals)
            # is driving an aggregated IC number.
            "n_pairs_per_rebalance": {
                "1m": ics[1]["n_pairs_per_rebalance"],
                "3m": ics[3]["n_pairs_per_rebalance"],
                "6m": ics[6]["n_pairs_per_rebalance"],
                "12m": ics[12]["n_pairs_per_rebalance"],
            },
            "decile_spread_1m": ds_1m,
            "decile_spread_3m": ds_3m,
            "decile_spread_12m": ds_12m,
            "hit_rate_12m": hit_12m,
            "top_5_current": top_5,
            "bottom_5_current": bot_5,
        })
        print(
            f"  {name}: IC1M={ics[1]['ic']}, t={ics[1]['t']}, "
            f"se={ics[1]['ic_se']}, n_months={ics[1]['n_months']}",
            file=sys.stderr,
        )

    # n_months < 6 caveat per factor per horizon. Newey-West HAC on a 3-rebalance
    # series isn't inference, it's optimism. Surface so consumers don't read
    # a t-stat that's a sample-size artifact.
    sample_caveats = []
    for fr in factor_results:
        thin_horizons = []
        for h_label in ("1m", "3m", "6m", "12m"):
            nm = fr.get(f"n_months_{h_label}", 0) or 0
            if 0 < nm < 6:
                thin_horizons.append(f"{h_label} (n_months={nm})")
        if thin_horizons:
            sample_caveats.append(
                f"{short_name(fr['name'])} IC t-stat from too few rebalance periods at: "
                + ", ".join(thin_horizons)
                + ". Sample too small for stable Newey-West inference."
            )

    # Correlation matrix on signals (only on factors that survived the drop threshold)
    surviving_panels = {k: v for k, v in factor_panels.items() if k not in dropped_factors}
    corr_names, corr_matrix = compute_signal_correlation(surviving_panels)

    # Take
    take = build_take(factor_results, corr_matrix, corr_names)

    run_at = utcnow_iso()
    sources = [
        {"endpoint": "https://api.polygon.io/v3/reference/tickers", "fetched_at": run_at,
         "context": "universe construction"},
        {"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}", "fetched_at": run_at,
         "context": "per-name market cap for universe ranking"},
        {"endpoint": "https://api.polygon.io/vX/reference/financials", "fetched_at": run_at,
         "context": "annual book equity + net income for value and quality factors"},
    ]
    if interface_used == "flat-files":
        sources.append({
            "endpoint": "s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz",
            "fetched_at": run_at,
            "context": "daily aggregates via flat-files (one S3 day-bucket per trading day)",
        })
    else:
        sources.append({
            "endpoint": "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}",
            "fetched_at": run_at,
            "context": "daily aggregates via REST grouped-daily (flat-files fallback)",
        })

    survivorship_note = (
        "Top 500 by CURRENT market cap is forward-looking biased for a backtest "
        "(NVDA was not a top-500 name in 2021). For a true point-in-time backtest, "
        "reconstruct the universe per period; queued as a clean PR extension."
    )

    base_caveats = [
        f"Interface used: {interface_used}",
        "Value and quality factors use point-in-time fundamentals: at each "
        "rebalance, the most recent quarterly filing with "
        "period_of_report_date <= rebalance_date drives shares-outstanding, "
        "book_equity, and net_income. Tickers without a pre-rebalance filing "
        "are excluded from the cross-section for that month only.",
        "IC t-stats use Newey-West HAC standard errors with lag = horizon - 1 "
        "months to correct for the overlap induced by k-month forward returns "
        "on a monthly rebalance. ic_se_<h>m and n_months_<h>m emitted per "
        "factor per horizon.",
        "Fundamentals fields use C11.b-style fallback chains: net_income → "
        "(net_income_loss, net_income, net_income_from_continuing_operations); "
        "book_equity → (equity, stockholders_equity, total_equity, "
        "common_stockholders_equity); revenue → (revenues, revenue, "
        "total_revenue, net_revenue). Missing required inputs drop the name "
        "from THAT factor's cross-section only; other factors retain it.",
    ]
    if dropped_factors:
        base_caveats.append(
            "Factors omitted from IC summary due to <"
            f"{int(_COVERAGE_DROP_PCT*100)}% coverage on ALL rebalances: "
            + ", ".join(sorted(short_name(d) for d in dropped_factors))
            + ". Publishing IC on the surviving slice would misrepresent "
            "the full universe."
        )

    payload = {
        "tier": "A",
        "tier_caveats": base_caveats + coverage_caveats + sample_caveats,
        "mode": "table",
        "run_at": run_at,
        "universe_definition": {
            "label": f"top {args.universe_size} by current market cap, US common stock",
            "size": len(universe),
            "survivorship_mode": "biased",
            "survivorship_note": survivorship_note,
        },
        "window_start": start_d.isoformat(),
        "window_end": end_d.isoformat(),
        "n_months": len(monthly_idx),
        "factors": factor_results,
        "factor_correlation_matrix": {
            "factor_names": corr_names,
            "matrix": [[round(v, 4) for v in row] for row in corr_matrix],
        },
        "factor_returns_sector_neutral": None,
        "per_rebalance_coverage": per_rebalance_coverage,
        "dropped_factors": sorted(dropped_factors),
        "coverage_thresholds": {
            "caveat_pct": _COVERAGE_CAVEAT_PCT,
            "drop_pct": _COVERAGE_DROP_PCT,
        },
        "take": take,
        "sources": sources,
    }

    if args.debug_factor:
        print("\n[--debug-factor] per_rebalance_coverage:", file=sys.stderr)
        print(json.dumps(per_rebalance_coverage, indent=2, default=str), file=sys.stderr)
        print("\n[--debug-factor] per-factor IC raw distribution + NW SE inputs:", file=sys.stderr)
        for fr in factor_results:
            print(f"\n  factor: {fr['name']}", file=sys.stderr)
            for h_label in ("1m", "3m", "6m", "12m"):
                pairs = fr["n_pairs_per_rebalance"][h_label]
                ic = fr.get(f"ic_{h_label}")
                ic_se = fr.get(f"ic_se_{h_label}")
                t = fr.get(f"ic_tstat_{h_label}")
                n_m = fr.get(f"n_months_{h_label}")
                print(
                    f"    horizon={h_label}: ic={ic}, se={ic_se}, t={t}, "
                    f"n_months={n_m}, n_rebalances_with_pairs={len(pairs)}",
                    file=sys.stderr,
                )
                # Compact per-rebalance pair-count + IC trace
                for p in pairs:
                    print(
                        f"      {p['date']}: n_pairs={p['n_pairs']}, ic={p['ic']}",
                        file=sys.stderr,
                    )

    rendered = render(payload)

    out_path = os.path.join(os.path.dirname(__file__), "factor-research-output.md")
    with open(out_path, "w") as fout:
        fout.write("# factor-research run\n\n")
        fout.write(f"Generated: {run_at}\n")
        fout.write(f"Interface: {interface_used}\n")
        fout.write(f"Universe: top {args.universe_size} by current market cap ({len(universe)} after continuous-trading filter)\n")
        fout.write(f"Window: {start_d} to {end_d}\n\n")
        fout.write("## Layer 1: canonical JSON (live data)\n\n")
        fout.write("```json\n")
        fout.write(json.dumps(payload, indent=2, default=str))
        fout.write("\n```\n\n")
        fout.write("## Layer 2: rendered factor research table (live data)\n\n")
        fout.write("```\n")
        fout.write(rendered)
        fout.write("\n```\n")

    print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
    emit_to_stdout(rendered, payload, fmt)


def _definition_for(name):
    if "Momentum" in name:
        return "12M return minus most recent 1M (academic-standard mom, skip-1)"
    if "Value" in name:
        return "1 / (P/B) = book_equity / market_cap (higher = cheaper)"
    if "Quality" in name:
        return "ROE = net_income / book_equity (point-in-time per rebalance)"
    if "Low-Vol" in name:
        return "1 / realized_vol_252d (annualized stdev of daily log returns)"
    if "Size" in name:
        return "-log(market_cap) (small-minus-big; smaller cap ranks higher)"
    return ""


# Map factor display name -> snake_case key used in per_rebalance_coverage.
# Kept central so the rendering / threshold-check code can look up coverage
# for any factor without baking the string transformation in two places.
_COVERAGE_KEY = {
    "Momentum (12-1M)": "momentum",
    "Low-Vol (1/realiz)": "low_vol",
    "Size (-log mcap)": "size",
    "Value (1/(P/B))": "value",
    "Quality (ROE)": "quality",
}


# Minimum coverage thresholds. Below CAVEAT_PCT we surface a tier_caveat;
# below DROP_PCT on *every* rebalance we omit the factor from IC summary.
#
# Drop threshold sits at 10% rather than 30%: the spec's intent is "n=30 of
# an intended n=400 is misleading enough to omit," which is 7.5%. Keeping
# the drop floor close to that and letting CAVEAT_PCT (50%) carry the
# middle band gives a richer middle: factors at 15-49% coverage stay in
# with a loud caveat, only near-empty factors get omitted entirely.
_COVERAGE_CAVEAT_PCT = 0.50
_COVERAGE_DROP_PCT = 0.10


def _summarize_factor_coverage(coverage, factor_key):
    """Return (any_below_caveat, all_below_drop, n_rebal, median_pct,
    n_below_caveat). Walks per-rebalance coverage and aggregates."""
    pcts = []
    for c in coverage:
        fc = c.get("factor_coverage", {}).get(factor_key)
        if fc is None:
            continue
        pcts.append(fc["coverage_pct"])
    if not pcts:
        return False, False, 0, None, 0
    n_below_caveat = sum(1 for p in pcts if p < _COVERAGE_CAVEAT_PCT)
    any_below_caveat = n_below_caveat > 0
    all_below_drop = all(p < _COVERAGE_DROP_PCT for p in pcts)
    median_pct = float(np.median(pcts))
    return any_below_caveat, all_below_drop, len(pcts), median_pct, n_below_caveat


if __name__ == "__main__":
    main()
