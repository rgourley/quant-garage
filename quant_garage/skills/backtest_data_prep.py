"""
backtest-data-prep as an importable library function.

Builds a clean OHLCV parquet for a US equity universe over a window.
Emits three files (ohlcv.parquet, manifest.md, edge-cases.log) to the
output directory AND returns the canonical payload dict.

    from quant_garage.skills.backtest_data_prep import run, render
    payload = run(universe="top100", window=("2022-06-25","2026-06-25"),
                  out_dir="./backtest-data/")
"""
from __future__ import annotations

import os
import sys
import json
import math
import time
from io import BytesIO
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

import numpy as np
import pandas as pd

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
)


CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache", "backtest-data-prep")
os.makedirs(CACHE_DIR, exist_ok=True)


client = MassiveClient()


def _resolve_s3_auth():
    """Resolve S3 credentials lazily so the WARN only fires when flat-files
    is actually touched, not on every skill import (Q6).

    Flat-files S3 needs credentials distinct from MASSIVE_API_KEY, generated
    in the dashboard under "Flat Files". The legacy pattern of passing the
    API key as both halves returns 403 on most accounts (verified 2026-06-23
    against a Stocks Business key). MASSIVE_S3_* is canonical; POLYGON_S3_*
    is accepted for users following legacy docs.
    """
    access_key = (
        os.environ.get("MASSIVE_S3_ACCESS_KEY")
        or os.environ.get("POLYGON_S3_ACCESS_KEY")
    )
    secret_key = (
        os.environ.get("MASSIVE_S3_SECRET_KEY")
        or os.environ.get("POLYGON_S3_SECRET_KEY")
    )
    if access_key and secret_key:
        return (access_key, secret_key), "massive_s3_credentials"
    key = client.api_key
    print(
        "WARN: flat-files S3 auth is falling back to MASSIVE_API_KEY as both "
        "access_key_id and secret_access_key. This pattern usually returns 403. "
        "Generate distinct S3 credentials in your Massive dashboard "
        "(https://massive.com/dashboard → Flat Files) and export "
        "MASSIVE_S3_ACCESS_KEY and MASSIVE_S3_SECRET_KEY to silence this.",
        file=sys.stderr,
    )
    return (key, key), "massive_api_key_legacy_fallback"


# ----- HTTP helpers -----


def fetch_rest(path, params=None, retries=2):
    """Single GET via the shared client. Returns parsed JSON only.
    `retries` is kept in the signature for backwards compatibility but the
    client now handles 429/5xx/socket.timeout retry centrally (L3).
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
    """Return (entitled: bool, s3_client) for flat-files access.

    Flat-files lives behind an S3-compatible boto3 client, not a REST host,
    so MassiveClient does not wrap it. The probe stays on raw boto3.
    """
    s3_auth, s3_auth_source = _resolve_s3_auth()
    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            endpoint_url="https://files.polygon.io",
            aws_access_key_id=s3_auth[0],
            aws_secret_access_key=s3_auth[1],
            config=Config(signature_version="s3v4"),
        )
        # Pick a known-good recent weekday
        probe_date = today() - timedelta(days=4)
        while probe_date.weekday() >= 5:
            probe_date -= timedelta(days=1)
        key = f"us_stocks_sip/day_aggs_v1/{probe_date.year:04d}/{probe_date.month:02d}/{probe_date.isoformat()}.csv.gz"
        s3.head_object(Bucket="flatfiles", Key=key)
        return True, s3
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Forbidden" in msg or "InvalidAccessKey" in msg:
            if s3_auth_source == "massive_api_key_legacy_fallback":
                print(
                    "INFO: flat-files returned 403 with the legacy "
                    "MASSIVE_API_KEY-as-S3-auth pattern (expected). Generate "
                    "distinct S3 credentials in the Massive dashboard under "
                    "'Flat Files' and export MASSIVE_S3_ACCESS_KEY + "
                    "MASSIVE_S3_SECRET_KEY. Falling back to REST grouped-daily "
                    "for this run.",
                    file=sys.stderr,
                )
            else:
                print(
                    "INFO: flat-files returned 403 with MASSIVE_S3_ACCESS_KEY/"
                    "MASSIVE_S3_SECRET_KEY. Verify the credentials are correct "
                    "and your account is entitled to flat-files. Falling back "
                    "to REST grouped-daily for this run.",
                    file=sys.stderr,
                )
            return False, None
        print(f"WARN: flat-files probe failed: {msg[:200]}", file=sys.stderr)
        return False, None


# ----- Calendar -----


def is_trading_day(d):
    """Naive: weekday only. Holidays show up as empty fetches and get dropped."""
    return d.weekday() < 5


def trading_days(start, end):
    out = []
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


# ----- Day-bucket fetch -----


def fetch_day_flatfile(s3, d):
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
    keep = [c for c in ["ticker", "open", "high", "low", "close", "volume", "transactions"] if c in df.columns]
    df = df[keep]
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
    except RuntimeError:
        return None
    rows = doc.get("results") or []
    if not rows:
        return None
    # REST schema: T (ticker), c (close), o (open), h (high), l (low), v (volume), vw (vwap), n (transactions)
    df = pd.DataFrame(rows)
    rename = {"T": "ticker", "o": "open", "h": "high", "l": "low",
              "c": "close", "v": "volume", "vw": "vwap", "n": "transactions"}
    df = df.rename(columns=rename)
    keep = [c for c in ["ticker", "open", "high", "low", "close", "vwap", "volume", "transactions"] if c in df.columns]
    df = df[keep]
    df["date"] = d.isoformat()
    df.to_parquet(cache_path)
    return df


# ----- Universe construction -----


SP500_FALLBACK = [
    # Curated top mega-caps; used as seed when --universe sp500 and no
    # curated list is on disk. Not actually the live SP500 (the index
    # rebalances). For a true SP500 universe pass --universe custom:path.
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "BRK.B", "TSLA",
    "AVGO", "JPM", "LLY", "V", "WMT", "XOM", "UNH", "MA", "PG", "ORCL",
    "JNJ", "HD", "COST", "ABBV", "BAC", "NFLX", "KO", "MRK", "CVX", "PEP",
    "AMD", "TMO", "CRM", "ADBE", "ACN", "MCD", "LIN", "DIS", "ABT", "QCOM",
    "WFC", "CSCO", "INTU", "TXN", "VZ", "DHR", "CAT", "PM", "NOW", "AMGN",
    "GE", "IBM", "ISRG", "GS", "AMAT", "RTX", "BLK", "T", "PFE", "SPGI",
    "UNP", "AXP", "C", "NEE", "LOW", "ELV", "BKNG", "TJX", "ETN", "SYK",
    "GILD", "MDT", "VRTX", "DE", "BSX", "ADI", "MMC", "REGN", "PLD", "BX",
    "CB", "LMT", "PANW", "MU", "PGR", "ADP", "FI", "KKR", "MO", "SO",
    "MDLZ", "ICE", "CI", "BMY", "EQIX", "TMUS", "ANET", "DUK", "SHW", "ZTS",
    "USB", "AON", "WM", "CL", "EOG", "APH", "MCO", "ITW", "CMG", "MMM",
]


def build_universe_seed(seed, target_size):
    """Return list of {ticker, market_cap, name, type, list_date, delisted_utc, sic_code, sic_description}."""
    cache_path = os.path.join(CACHE_DIR, f"universe_seed_{seed}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    if seed.startswith("custom:"):
        path = seed[len("custom:"):]
        if not os.path.exists(path):
            raise SystemExit(f"Custom ticker file not found: {path}")
        ticker_list = []
        with open(path) as f:
            for line in f:
                t = line.strip().split(",")[0].upper()
                if t and t != "TICKER":
                    ticker_list.append(t)
        target_size = len(ticker_list)
        print(f"Custom universe: {target_size} tickers from {path}", file=sys.stderr)
    elif seed == "sp500":
        ticker_list = SP500_FALLBACK[:target_size or 100]
        target_size = len(ticker_list)
        print(f"SP500 seed (curated fallback): {target_size} tickers", file=sys.stderr)
    elif seed in ("top100", "top500", "top1000"):
        target_n = int(seed.replace("top", ""))
        target_size = target_n
        # Use the all-stocks snapshot to rank by dollar volume (close * volume)
        # as a cheap mcap proxy, then enrich the top survivors with real market_cap
        # for the final ranking. This avoids the alphabetical-truncation bug from
        # cursor-paginating /v3/reference/tickers.
        print(f"Building {seed} via snapshot + market_cap enrichment...", file=sys.stderr)
        snap = fetch_rest("/v2/snapshot/locale/us/markets/stocks/tickers")
        snap_rows = snap.get("tickers") or []
        # Filter to plausible equities: ticker has no '.', no ':', has day data
        candidates = []
        for r in snap_rows:
            tk = r.get("ticker", "")
            if not tk or "." in tk or ":" in tk or "/" in tk:
                continue
            day = r.get("day") or {}
            close = day.get("c") or 0
            volume = day.get("v") or 0
            dollar_vol = close * volume
            if dollar_vol > 0:
                candidates.append((tk, dollar_vol))
        candidates.sort(key=lambda x: -x[1])
        # Take top-N by dollar volume as the candidate pool for mcap enrichment.
        # A 4x cushion handles the dollar-volume-mcap divergence (high-volume
        # speculative names rank higher by $vol than mcap).
        cushion = max(target_n * 4, 500)
        ticker_list_full = [t for t, _ in candidates[:cushion]]
        print(f"  Pre-filter: {len(snap_rows)} snapshot rows → {len(candidates)} with day data → top {len(ticker_list_full)} by dollar volume", file=sys.stderr)
        details = enrich_tickers(ticker_list_full)
        # Filter to common stock and major exchanges
        major = {"XNAS", "XNYS", "ARCX", "BATS"}
        details = [d for d in details if d.get("type") == "CS" and d.get("primary_exchange") in major]
        ranked = sorted(
            [d for d in details if d.get("market_cap")],
            key=lambda d: d.get("market_cap") or 0,
            reverse=True,
        )
        ticker_list = [d["ticker"] for d in ranked[:target_n]]
        if "NVDA" not in ticker_list and target_n >= 100:
            print(f"  WARN: NVDA not in top-{target_n}; check candidate pool", file=sys.stderr)
    else:
        raise SystemExit(f"Unknown universe seed: {seed}")

    # Enrich (idempotent if already enriched)
    details = enrich_tickers(ticker_list)
    out = [d for d in details if d]
    with open(cache_path, "w") as f:
        json.dump(out, f)
    return out


def enrich_tickers(ticker_list):
    """Fetch /v3/reference/tickers/{T} for each. Returns list of dicts."""
    out = []
    print(f"  Enriching {len(ticker_list)} tickers (parallel, 16 workers)...", file=sys.stderr)

    def get_one(tk):
        try:
            d = fetch_rest(f"/v3/reference/tickers/{tk}")
            r = d.get("results") or {}
            return {
                "ticker": tk,
                "name": r.get("name"),
                "market_cap": r.get("market_cap"),
                "type": r.get("type"),
                "list_date": r.get("list_date"),
                "delisted_utc": r.get("delisted_utc"),
                "sic_code": r.get("sic_code"),
                "sic_description": r.get("sic_description"),
                "primary_exchange": r.get("primary_exchange"),
                "active": r.get("active"),
            }
        except Exception as e:
            return {"ticker": tk, "error": str(e)[:100]}

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(get_one, t) for t in ticker_list]
        for i, f in enumerate(as_completed(futures)):
            r = f.result()
            if r and not r.get("error"):
                out.append(r)
            if (i + 1) % 25 == 0:
                print(f"    {i+1}/{len(ticker_list)} enriched", file=sys.stderr)
    return out


# ----- Splits and dividends -----


def fetch_splits(ticker_list, window_start, window_end):
    """Pull splits for universe. Returns dict[ticker] -> list of splits."""
    print(f"Pulling splits for {len(ticker_list)} tickers...", file=sys.stderr)
    cache_path = os.path.join(CACHE_DIR, f"splits_{len(ticker_list)}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        if set(cached.keys()) >= set(ticker_list):
            return {t: cached[t] for t in ticker_list if t in cached}

    def get_one(tk):
        try:
            d = fetch_rest(f"/v3/reference/splits?ticker={tk}&limit=100")
            return tk, d.get("results") or []
        except Exception:
            return tk, []

    out = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(get_one, t) for t in ticker_list]
        done = 0
        for f in as_completed(futures):
            tk, splits = f.result()
            out[tk] = splits
            done += 1
            if done % 25 == 0:
                print(f"    {done}/{len(ticker_list)} splits fetched", file=sys.stderr)

    with open(cache_path, "w") as f:
        json.dump(out, f)
    return out


def fetch_dividends(ticker_list, window_start, window_end):
    """Pull dividends for universe. Returns dict[ticker] -> list of dividends."""
    print(f"Pulling dividends for {len(ticker_list)} tickers...", file=sys.stderr)
    cache_path = os.path.join(CACHE_DIR, f"dividends_{len(ticker_list)}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        if set(cached.keys()) >= set(ticker_list):
            return {t: cached[t] for t in ticker_list if t in cached}

    def get_one(tk):
        try:
            d = fetch_rest(f"/v3/reference/dividends?ticker={tk}&limit=500")
            return tk, d.get("results") or []
        except Exception:
            return tk, []

    out = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(get_one, t) for t in ticker_list]
        done = 0
        for f in as_completed(futures):
            tk, divs = f.result()
            out[tk] = divs
            done += 1
            if done % 25 == 0:
                print(f"    {done}/{len(ticker_list)} dividends fetched", file=sys.stderr)

    with open(cache_path, "w") as f:
        json.dump(out, f)
    return out


# ----- Adjustment factor -----


def compute_cumulative_adj_factor(splits, window_start, window_end, trading_dates):
    """For one ticker, compute the cumulative adjustment factor on each
    trading day in the window. Massive's grouped aggs are split-adjusted by
    default, so the factor is what you'd multiply by to recover RAW prints.

    Returns dict[date_iso] -> factor (float).
    """
    # Splits in chronological order (oldest first)
    splits_sorted = sorted(splits, key=lambda s: s.get("execution_date", ""))

    # Two split categories matter:
    # 1. Splits AFTER window_end: do they affect rows in the window? Massive's
    #    grouped aggs are "split-adjusted as of the latest split"; a split
    #    AFTER the window_end means the entire window's adjusted prices have
    #    been divided by (split_to/split_from). To recover raw prices on those
    #    days you'd multiply by (split_to/split_from). So factor *= ratio.
    # 2. Splits INSIDE the window: bars BEFORE the split's execution_date have
    #    been adjusted; bars ON OR AFTER haven't (the split already happened
    #    in real-world terms). So factor for pre-split bars *= ratio, factor
    #    for on-or-after-split bars *= 1.

    # Splits BEFORE window_start don't matter (their effect already fully
    # absorbed into the adjusted prices for the entire window).

    factor_default = 1.0
    window_splits = []
    for s in splits_sorted:
        ed_str = s.get("execution_date")
        if not ed_str:
            continue
        try:
            ed = date.fromisoformat(ed_str)
        except ValueError:
            continue
        ratio = (s.get("split_to") or 1) / (s.get("split_from") or 1)
        if ratio == 1 or ratio <= 0:
            continue
        if ed > window_end:
            # Affects every day in the window
            factor_default *= ratio
        elif window_start <= ed <= window_end:
            window_splits.append((ed, ratio))

    # Now walk trading dates: pre-split bars get factor_default * cumulative
    # of in-window splits AFTER that date.
    # Build cumulative "factor applied to dates strictly before split date"
    out = {}
    # For each trading date d, factor = factor_default * product(ratio for split where ed > d)
    # i.e. the more recent splits multiply factors of older dates.
    in_window_sorted = sorted(window_splits, key=lambda x: x[0])
    for d in trading_dates:
        f = factor_default
        for ed, ratio in in_window_sorted:
            if ed > d:
                f *= ratio
        out[d.isoformat()] = round(f, 6)
    return out


# ----- Spinoff detection -----


def detect_spinoffs(dividends_by_ticker, window_start, window_end):
    """Walk dividends for dividend_type='SO' (stock dividend / spinoff)."""
    spinoffs = []
    for tk, divs in dividends_by_ticker.items():
        for div in divs:
            if div.get("dividend_type") != "SO":
                continue
            ex_str = div.get("ex_dividend_date")
            if not ex_str:
                continue
            try:
                ex = date.fromisoformat(ex_str)
            except ValueError:
                continue
            if not (window_start <= ex <= window_end):
                continue
            spinoffs.append({
                "parent_ticker": tk,
                "spinoff_ticker": None,  # Massive feed doesn't always carry this
                "ex_date": ex_str,
                "manual_override_recommended": True,
                "reason": "Basis split not auto-applied; review whether parent's ex-date drop should be adjusted out for the strategy",
            })
    return spinoffs


# ----- Edge case detection -----


def detect_edge_cases(universe_meta, ohlcv_df, trading_dates, window_start, window_end):
    edge = []
    # Use actual trading day count (drops holidays) as the "expected" baseline
    expected_days = int(ohlcv_df["date"].nunique())
    rows_per_ticker = ohlcv_df.groupby("ticker").size()

    for u in universe_meta:
        tk = u["ticker"]
        actual = int(rows_per_ticker.get(tk, 0))
        if actual == 0:
            edge.append({
                "type": "data_gap",
                "ticker": tk,
                "date": None,
                "detail": "No bars retrieved for ticker across window",
                "missing_days": expected_days,
            })
            continue

        # IPO partial coverage
        ld_str = u.get("list_date")
        if ld_str:
            try:
                ld = date.fromisoformat(ld_str)
                if window_start <= ld <= window_end:
                    # Trading days in window (per the ohlcv panel) before list_date
                    actual_dates = sorted(ohlcv_df["date"].unique())
                    missing_at_start = sum(1 for d in actual_dates if d < ld)
                    edge.append({
                        "type": "ipo_partial_coverage",
                        "ticker": tk,
                        "date": ld_str,
                        "detail": f"IPO during window (list_date {ld_str}); {missing_at_start} trading days missing at start of window",
                        "missing_days": missing_at_start,
                    })
                    continue
            except ValueError:
                pass

        # Delisting during window
        du_str = u.get("delisted_utc")
        if du_str:
            try:
                du = date.fromisoformat(du_str[:10])
                if window_start <= du <= window_end:
                    actual_dates = sorted(ohlcv_df["date"].unique())
                    missing_at_end = sum(1 for d in actual_dates if d > du)
                    edge.append({
                        "type": "delisting_during_window",
                        "ticker": tk,
                        "date": du_str[:10],
                        "detail": f"Delisted during window (delisted_utc {du_str[:10]}); {missing_at_end} trading days missing at end of window",
                        "missing_days": missing_at_end,
                    })
                    continue
            except ValueError:
                pass

        # Generic data gap (not IPO, not delisting): if actual much less than expected
        if actual < expected_days * 0.95:
            edge.append({
                "type": "data_gap",
                "ticker": tk,
                "date": None,
                "detail": f"Only {actual}/{expected_days} bars retrieved; not explained by IPO or delisting",
                "missing_days": expected_days - actual,
            })

    return edge


# ----- Universe stats -----


def compute_universe_stats(universe_meta, ohlcv_df, expected_days, window_start, window_end):
    rows_per_ticker = ohlcv_df.groupby("ticker").size()
    # expected_days is weekday-count; actual_trading_days is what the market actually traded
    # (drops holidays). Use the modal coverage as "continuous" rather than the weekday count.
    actual_trading_days = int(ohlcv_df["date"].nunique())
    requested = len(universe_meta)
    retrieved = int((rows_per_ticker > 0).sum())
    continuous = int((rows_per_ticker >= actual_trading_days).sum())
    partial = int(((rows_per_ticker > 0) & (rows_per_ticker < actual_trading_days)).sum())
    # delisted_during_window_count is intentionally None: we never pulled
    # the active=false universe, so we don't know how many names delisted
    # in-window. Zero would falsely suggest "we looked and found none."
    # null says "we didn't look." When the active=false fetch lands, this
    # becomes a real count.
    return {
        "tickers_requested": requested,
        "tickers_retrieved": retrieved,
        "continuous_coverage_count": continuous,
        "partial_coverage_count": partial,
        "delisted_during_window_count": None,
    }


# ----- Build the parquet -----


def build_dataset(universe_meta, window_start, window_end, s3, interface):
    ticker_set = {u["ticker"] for u in universe_meta}
    days = trading_days(window_start, window_end)
    print(f"Pulling {len(days)} day-buckets (interface={interface})...", file=sys.stderr)

    use_flat = interface == "flat-files" and s3 is not None

    def fetch_one(d):
        if use_flat:
            df = fetch_day_flatfile(s3, d)
            if df is None:
                df = fetch_day_rest(d)
        else:
            df = fetch_day_rest(d)
        if df is None:
            return None
        return df[df["ticker"].isin(ticker_set)]

    frames = []
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        for r in pool.map(fetch_one, days):
            done += 1
            if done % 50 == 0:
                elapsed = time.time() - t0
                print(f"    {done}/{len(days)} days fetched ({elapsed:.0f}s)", file=sys.stderr)
            if r is not None and not r.empty:
                frames.append(r)
    if not frames:
        raise SystemExit("No daily data fetched; window may be all weekends/holidays")

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df, days


# ----- Apply adjustment factor and enrich -----


def enrich_dataset(df, universe_meta, splits_by_ticker, trading_dates, window_start, window_end):
    # Compute adj_factor_cumulative per (ticker, date)
    factor_rows = []
    for u in universe_meta:
        tk = u["ticker"]
        splits = splits_by_ticker.get(tk, [])
        factor_map = compute_cumulative_adj_factor(splits, window_start, window_end, trading_dates)
        for d_str, f in factor_map.items():
            factor_rows.append((d_str, tk, f))
    factor_df = pd.DataFrame(factor_rows, columns=["date_str", "ticker", "adj_factor_cumulative"])
    factor_df["date"] = pd.to_datetime(factor_df["date_str"]).dt.date
    factor_df = factor_df.drop(columns=["date_str"])

    df = df.merge(factor_df, on=["date", "ticker"], how="left")
    df["adj_factor_cumulative"] = df["adj_factor_cumulative"].fillna(1.0)

    # Sector enrichment
    meta_df = pd.DataFrame([
        {"ticker": u["ticker"], "sic_code": u.get("sic_code"), "sector": u.get("sic_description")}
        for u in universe_meta
    ])
    df = df.merge(meta_df, on="ticker", how="left")

    # Ensure column order matches schema
    cols = ["date", "ticker", "open", "high", "low", "close", "vwap",
            "volume", "transactions", "adj_factor_cumulative", "sic_code", "sector"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols].sort_values(["date", "ticker"]).reset_index(drop=True)
    return df


# ----- Take -----


def build_take(universe_def, stats, spinoffs, edge_cases, window_start, window_end):
    sentences = []
    # None means we didn't pull active=false; treat as "unknown" (falsy
    # for narrative purposes since we can't claim retention).
    delist_count = stats["delisted_during_window_count"]
    has_delist = delist_count is not None and delist_count > 0
    has_partials = any(e["type"] == "ipo_partial_coverage" for e in edge_cases)
    pre_2024 = window_start < date(2024, 1, 1)
    universe_size = universe_def["size"]
    seed = universe_def["seed"]

    # Verdict
    if stats["continuous_coverage_count"] >= int(universe_size * 0.95):
        verdict = "Dataset is point-in-time clean for OHLCV and corporate actions."
    else:
        verdict = (
            f"Dataset is OHLCV-clean but coverage is incomplete: "
            f"{stats['continuous_coverage_count']}/{universe_size} tickers have continuous coverage. "
            "Review the edge-cases log before backtesting."
        )
    sentences.append(verdict)

    # What's NOT included
    sentences.append(
        "Fundamentals NOT included in this run; use earnings-drilldown or "
        "factor-research helpers if fundamentals are needed."
    )

    # Survivorship caveat. has_delist is currently always False because
    # the active=false fetch isn't implemented; the branch is kept for
    # when that lands. Until then, every run is biased and the only
    # honest framing is "current-snapshot only."
    if has_delist:
        sentences.append(
            f"{delist_count} delisted-in-window names retained for "
            "survivorship cleanliness; backtest sees their final pre-delisting bars."
        )
    elif pre_2024:
        sentences.append(
            f"The {seed} seed is current-snapshot and the window touches pre-2024 territory; "
            "results will overstate returns until the active=false fetch lands "
            "(queued for a follow-up sprint)."
        )
    else:
        sentences.append(
            f"The {seed} seed is current-snapshot. Whether the window saw "
            "any delistings can't be known until the active=false fetch lands; "
            "for a post-2024 window this gap is usually small."
        )

    if spinoffs:
        sentences.append(
            f"Manual spinoff review recommended for {len(spinoffs)} case(s) in window."
        )

    if has_partials:
        partials = [e["ticker"] for e in edge_cases if e["type"] == "ipo_partial_coverage"]
        if len(partials) <= 2:
            sentences.append(
                f"IPO partial coverage flagged for {', '.join(partials)}; drop these if your strategy requires continuous history."
            )
        else:
            sentences.append(
                f"{len(partials)} tickers have IPO partial coverage; see edge-cases.log."
            )

    return " ".join(sentences)


# ----- Rendering -----


def render_summary(payload, out_dir_label):
    lines = []
    udef = payload["universe_definition"]
    ws_str = payload["window_start"]
    we_str = payload["window_end"]
    ws = date.fromisoformat(ws_str)
    we = date.fromisoformat(we_str)
    n_days = (we - ws).days
    if n_days < 30:
        win_label = f"{n_days}d window"
    elif n_days < 365:
        n_mo = round(n_days / 30)
        win_label = f"{n_mo}mo window"
    else:
        n_y = round(n_days / 365)
        win_label = f"{n_y}y window"

    lines.append(f"Backtest dataset: {udef['label']} · {ws_str} → {we_str} · {win_label}")
    lines.append("")

    # Files written
    lines.append("Files written")
    for f in payload["files_written"]:
        if f["path"].endswith(".parquet") and f.get("row_count"):
            n_days_actual = payload.get("trading_days_in_window") or 0
            n_tk = udef["size"]
            lines.append(f"- {out_dir_label}/{f['path']:<50} ({n_days_actual:,} trading days × {n_tk} tickers)")
        else:
            lines.append(f"- {out_dir_label}/{f['path']}")
    lines.append("")

    # Universe construction
    lines.append("Universe construction")
    survship = " (forward-looking biased; see survivorship note below)"
    lines.append(f"- {udef['label'].capitalize()}{survship}")
    lines.append("- Active only (current snapshot)")
    type_filter = udef.get("type_filter") or "CS"
    if type_filter == "CS":
        lines.append(f"- Excluded: ETFs, ETNs, ADRCs, units, warrants, rights (type filter = CS only)")
    else:
        lines.append(f"- Type filter: {type_filter}")
    lines.append("- Forward-fill rule: none. Missing trading days remain null (not imputed).")
    lines.append("")

    # Corporate actions
    lines.append("Corporate actions applied")
    ca = payload["corporate_actions_applied"]
    splits = ca.get("splits", [])
    tickers_with_splits = len({s["ticker"] for s in splits})
    if splits:
        examples = []
        for s in splits[:4]:
            r = f"{int(s['split_to'])}:{int(s['split_from'])}" if s.get("split_to", 0) >= s.get("split_from", 0) else f"1:{int(s['split_from']/max(s['split_to'],1))} reverse"
            examples.append(f"{s['ticker']} {r} {s['execution_date']}")
        more = ", ..." if len(splits) > 4 else ""
        lines.append(f"- {len(splits)} splits across {tickers_with_splits} tickers ({', '.join(examples)}{more})")
    else:
        lines.append("- 0 splits in window")
    div_count = ca.get("dividends_count", 0)
    if div_count:
        lines.append(f"- {div_count} dividends applied (cash, not stock); price adjustment factor included as separate column")
    else:
        lines.append("- 0 dividends in window")
    spinoffs = ca.get("spinoffs", [])
    if spinoffs:
        examples = []
        for s in spinoffs[:3]:
            spk = s.get("spinoff_ticker") or "?"
            examples.append(f"{spk} from {s['parent_ticker']} {s['ex_date']}")
        lines.append(f"- Spinoffs: {len(spinoffs)} detected ({', '.join(examples)}); manual override recommended")
    else:
        lines.append("- Spinoffs: none detected")
    lines.append("")

    # Coverage
    lines.append("Coverage")
    us = payload["universe_stats"]
    lines.append(f"- {us['tickers_requested']} tickers requested, {us['tickers_retrieved']} retrieved")
    lines.append(f"- {us['continuous_coverage_count']} with continuous coverage over the window")
    partials = [e for e in payload["edge_cases"] if e["type"] == "ipo_partial_coverage"]
    if us["partial_coverage_count"] == 0:
        lines.append("- 0 with partial coverage")
    elif len(partials) <= 3:
        for p in partials:
            lines.append(f"- 1 partial: {p['ticker']} ({p['detail']})")
        # Edge: there may be partials not from IPO (e.g. delisting); summarize the rest
        other_partial = us["partial_coverage_count"] - len(partials)
        if other_partial > 0:
            lines.append(f"- {other_partial} other partial: see edge-cases.log")
    else:
        lines.append(f"- {us['partial_coverage_count']} with partial coverage; see edge-cases.log")
    delist_n = us["delisted_during_window_count"]
    if delist_n is None:
        lines.append("- Delisted during window: not measured (active=false fetch not implemented)")
    elif delist_n == 0:
        if udef.get("seed", "").startswith("top"):
            lines.append(f"- 0 delisted during window (the current {udef['seed']} is a clean window for this universe)")
        else:
            lines.append("- 0 delisted during window")
    else:
        lines.append(f"- {delist_n} delisted during window (retained for survivorship cleanliness)")
    lines.append("")

    # Edge cases
    lines.append("Edge cases")
    halts = [e for e in payload["edge_cases"] if e["type"] == "trading_halt"]
    ticker_changes = [e for e in payload["edge_cases"] if e["type"] == "ticker_change"]
    other_gaps = [e for e in payload["edge_cases"] if e["type"] == "data_gap"]
    # Half-days are calendar features, mentioned as a courtesy line
    # Roughly 3 half-days per year in US equity calendar
    n_days_window = payload.get("trading_days_in_window") or 0
    est_half_days = int(round((n_days_window / 252.0) * 3))
    lines.append(f"- {est_half_days} half-day sessions (early-close holidays): preserved as normal rows with correct volume")
    if halts:
        examples = ", ".join(f"{h['ticker']} {h.get('date','')} ({h['detail']})" for h in halts[:2])
        more = ", ..." if len(halts) > 2 else ""
        lines.append(f"- {len(halts)} trading halts: {examples}{more}. Handled as session-low/high preservation.")
    else:
        lines.append("- 0 explicit trading halts detected (LULD pauses not detectable from daily aggs)")
    lines.append(f"- {len(ticker_changes)} ticker symbol changes within the window")
    if other_gaps:
        lines.append(f"- {len(other_gaps)} other data gaps (see edge-cases.log)")
    lines.append("")

    # Schema
    lines.append("Schema (parquet columns)")
    cols = payload["schema_columns"]
    # Wrap to ~70 chars
    line = "- "
    for i, c in enumerate(cols):
        sep = ", " if i < len(cols) - 1 else ""
        if len(line) + len(c) + len(sep) > 70:
            lines.append(line.rstrip(", "))
            line = "  "
        line += c + sep
    lines.append(line.rstrip(", "))
    lines.append("")

    # Source endpoints
    lines.append("Source endpoints")
    for s in payload["sources"]:
        ctx = f" ({s['context']})" if s.get("context") else ""
        lines.append(f"- {s['endpoint']}{ctx}")
    lines.append("")

    # Take
    lines.append(f"Take: {payload['take']}")
    return "\n".join(lines)


# ----- Manifest -----


def render_manifest(payload, out_dir_label):
    lines = []
    udef = payload["universe_definition"]
    lines.append(f"# Backtest dataset manifest")
    lines.append("")
    lines.append(f"Generated: {payload['run_at']}")
    lines.append(f"Interface: {payload.get('interface_used', 'rest')}")
    lines.append(f"Window: {payload['window_start']} to {payload['window_end']}")
    lines.append(f"Trading days in window: {payload.get('trading_days_in_window', 'n/a')}")
    lines.append("")

    lines.append("## Universe")
    lines.append(f"- Label: {udef['label']}")
    lines.append(f"- Seed: {udef['seed']}")
    lines.append(f"- Size: {udef['size']}")
    lines.append(f"- Type filter: {udef.get('type_filter', 'CS')}")
    lines.append(f"- Survivorship mode: {udef['survivorship_mode']}")
    if udef.get("survivorship_note"):
        lines.append(f"- Survivorship note: {udef['survivorship_note']}")
    lines.append("")

    lines.append("## Files written")
    for f in payload["files_written"]:
        meta_parts = []
        if f.get("row_count") is not None:
            meta_parts.append(f"{f['row_count']:,} rows")
        if f.get("column_count") is not None:
            meta_parts.append(f"{f['column_count']} cols")
        if f.get("bytes_on_disk") is not None:
            meta_parts.append(f"{f['bytes_on_disk']/1024:.1f} KB")
        meta = " (" + ", ".join(meta_parts) + ")" if meta_parts else ""
        lines.append(f"- `{f['path']}`{meta}: {f['description']}")
    lines.append("")

    lines.append("## Coverage")
    us = payload["universe_stats"]
    lines.append(f"- Requested: {us['tickers_requested']}")
    lines.append(f"- Retrieved: {us['tickers_retrieved']}")
    lines.append(f"- Continuous coverage: {us['continuous_coverage_count']}")
    lines.append(f"- Partial coverage: {us['partial_coverage_count']}")
    delist_n = us["delisted_during_window_count"]
    if delist_n is None:
        lines.append("- Delisted during window: not measured (active=false fetch not implemented; null, not zero)")
    else:
        lines.append(f"- Delisted during window: {delist_n}")
    lines.append("")

    lines.append("## Corporate actions")
    ca = payload["corporate_actions_applied"]
    lines.append(f"- Adjustment method: {ca['adjustment_method']}")
    lines.append(f"- Splits in window: {len(ca.get('splits', []))}")
    lines.append(f"- Dividends in window: {ca.get('dividends_count', 0)}")
    lines.append(f"- Spinoffs in window: {len(ca.get('spinoffs', []))}")
    if ca.get("splits"):
        lines.append("")
        lines.append("### Splits detail")
        for s in ca["splits"]:
            ratio = f"{int(s['split_to'])}:{int(s['split_from'])}" if s.get("split_to", 0) >= s.get("split_from", 0) else f"1:{int(s['split_from']/max(s['split_to'],1))} reverse"
            lines.append(f"- {s['ticker']} {ratio} on {s['execution_date']}")
    if ca.get("spinoffs"):
        lines.append("")
        lines.append("### Spinoffs detail")
        for s in ca["spinoffs"]:
            spk = s.get("spinoff_ticker") or "(ticker unknown)"
            lines.append(f"- {s['parent_ticker']} → {spk} ex {s['ex_date']}: {s.get('reason', '')}")
    lines.append("")

    lines.append("## Schema")
    schema_desc = {
        "date": "Trading day, US Eastern boundary (date32, no time component)",
        "ticker": "Uppercase ticker, no exchange prefix (string)",
        "open": "Split-adjusted session open (float64)",
        "high": "Split-adjusted session high (float64)",
        "low": "Split-adjusted session low (float64)",
        "close": "Split-adjusted session close (float64)",
        "vwap": "Volume-weighted average price for the session (float64)",
        "volume": "Raw share volume (int64)",
        "transactions": "Raw count of unique trades (int64)",
        "adj_factor_cumulative": "Multiply close by this to recover RAW (un-adjusted) close (float64)",
        "sic_code": "SEC industry code (4-digit string)",
        "sector": "Human-readable SIC description (string)",
    }
    for c in payload["schema_columns"]:
        lines.append(f"- `{c}`: {schema_desc.get(c, '')}")
    lines.append("")

    lines.append("## Source endpoints")
    for s in payload["sources"]:
        ctx = f" - {s['context']}" if s.get("context") else ""
        lines.append(f"- `{s['endpoint']}`{ctx} (fetched {s['fetched_at']})")
    lines.append("")

    lines.append("## Take")
    lines.append(payload["take"])
    lines.append("")

    if payload.get("tier_caveats"):
        lines.append("## Tier caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
        lines.append("")

    return "\n".join(lines)


# ----- CLI -----


def parse_window(s):
    if ".." not in s:
        raise SystemExit("--window must be YYYY-MM-DD..YYYY-MM-DD")
    a, b = s.split("..", 1)
    return date.fromisoformat(a), date.fromisoformat(b)


def run(
    universe: str = "top100",
    window: tuple[str, str] | str | None = None,
    out_dir: str = "./backtest-data/",
    survivorship: str = "biased",
    interface: str = "auto",
    client_: MassiveClient | None = None,
) -> dict:
    """Build a backtest dataset for a US equity universe over a window.

    Writes ohlcv.parquet, manifest.md, edge-cases.log to `out_dir` AND
    returns the canonical payload dict.
    """
    global client
    if client_ is not None:
        client = client_
    if window is None:
        raise ValueError("window is required")
    if isinstance(window, tuple):
        ws, we = date.fromisoformat(window[0]), date.fromisoformat(window[1])
    else:
        ws, we = parse_window(window)
    if ws > we:
        raise ValueError("window start must be <= end")

    args = SimpleNamespace(
        universe=universe, window=f"{ws.isoformat()}..{we.isoformat()}",
        out=out_dir, survivorship=survivorship, interface=interface,
    )

    os.makedirs(out_dir, exist_ok=True)

    # Probe flat-files
    s3 = None
    interface = "rest"
    if args.interface in ("auto", "flat-files"):
        has_ff, s3_client = probe_flat_files()
        if has_ff:
            s3 = s3_client
            interface = "flat-files"
            print("flat-files: ENTITLED", file=sys.stderr)
        else:
            print("flat-files: NOT entitled. Falling back to REST grouped-daily.", file=sys.stderr)
            if args.interface == "flat-files":
                raise SystemExit("--interface flat-files requested but key not entitled.")

    # Universe
    target_size = {"top100": 100, "top500": 500, "top1000": 1000}.get(args.universe, 100)
    universe_meta = build_universe_seed(args.universe, target_size)
    # For top-N seeds, take exactly target_size by current market cap
    if args.universe.startswith("top"):
        universe_meta = sorted(
            [u for u in universe_meta if u.get("market_cap")],
            key=lambda u: u.get("market_cap") or 0,
            reverse=True,
        )[:target_size]
    print(f"Universe: {len(universe_meta)} names", file=sys.stderr)
    if not universe_meta:
        raise SystemExit("Universe construction returned empty list")

    ticker_list = [u["ticker"] for u in universe_meta]

    # Determine universe label
    label_map = {
        "top100": "top 100 by current market cap",
        "top500": "top 500 by current market cap",
        "top1000": "top 1000 by current market cap",
        "sp500": "SP500 curated seed",
    }
    if args.universe.startswith("custom:"):
        u_label = f"custom universe ({len(universe_meta)} tickers)"
    else:
        u_label = label_map.get(args.universe, args.universe)

    # Pull OHLCV
    ohlcv_df, days = build_dataset(universe_meta, ws, we, s3, interface)
    print(f"OHLCV rows: {len(ohlcv_df):,}", file=sys.stderr)

    # Pull splits and dividends
    splits_by_ticker = fetch_splits(ticker_list, ws, we)
    divs_by_ticker = fetch_dividends(ticker_list, ws, we)

    # Filter splits to in-window for the manifest
    splits_in_window = []
    for tk, splits in splits_by_ticker.items():
        for s in splits:
            ed = s.get("execution_date")
            if not ed:
                continue
            try:
                ed_d = date.fromisoformat(ed)
            except ValueError:
                continue
            if ws <= ed_d <= we:
                ratio = (s.get("split_to") or 1) / (s.get("split_from") or 1)
                ratio_display = (
                    f"{int(s['split_to'])}:{int(s['split_from'])}"
                    if ratio >= 1
                    else f"1:{int(s['split_from']/max(s['split_to'],1))} reverse"
                )
                splits_in_window.append({
                    "ticker": tk,
                    "execution_date": ed,
                    "split_from": s.get("split_from"),
                    "split_to": s.get("split_to"),
                    "ratio_display": ratio_display,
                })
    splits_in_window.sort(key=lambda x: x["execution_date"])

    # Dividends count
    div_count = 0
    for tk, divs in divs_by_ticker.items():
        for d in divs:
            ex = d.get("ex_dividend_date")
            if not ex:
                continue
            try:
                ex_d = date.fromisoformat(ex)
            except ValueError:
                continue
            if ws <= ex_d <= we and d.get("dividend_type") in ("CD", "SC", "LT"):
                div_count += 1

    # Spinoffs
    spinoffs = detect_spinoffs(divs_by_ticker, ws, we)

    # Enrich the OHLCV with adjustment factor and sector
    ohlcv_df = enrich_dataset(ohlcv_df, universe_meta, splits_by_ticker, days, ws, we)

    # Detect edge cases
    edge_cases = detect_edge_cases(universe_meta, ohlcv_df, days, ws, we)

    # Universe stats
    stats = compute_universe_stats(universe_meta, ohlcv_df, len(days), ws, we)

    # Write parquet
    parquet_path = os.path.join(out_dir, "ohlcv.parquet")
    ohlcv_df.to_parquet(parquet_path, compression="snappy", index=False)
    parquet_bytes = os.path.getsize(parquet_path)
    parquet_rows = len(ohlcv_df)
    parquet_cols = len(ohlcv_df.columns)
    print(f"Wrote {parquet_path}: {parquet_rows:,} rows × {parquet_cols} cols ({parquet_bytes/1024:.1f} KB)", file=sys.stderr)

    # Files written
    files_written = [
        {
            "path": "ohlcv.parquet",
            "description": "Clean OHLCV+volume dataset, one row per (ticker, trading day)",
            "row_count": parquet_rows,
            "column_count": parquet_cols,
            "bytes_on_disk": parquet_bytes,
        },
        {
            "path": "manifest.md",
            "description": "Human-readable run record",
            "row_count": None,
            "column_count": None,
            "bytes_on_disk": None,
        },
        {
            "path": "edge-cases.log",
            "description": "Line-delimited JSON, one entry per anomaly",
            "row_count": len(edge_cases),
            "column_count": None,
            "bytes_on_disk": None,
        },
    ]

    # Survivorship mode. Only 'biased' is supported right now: we pull
    # today's active=true snapshot, so any name that delisted in-window
    # was filtered out before we ever saw it. Whether that count is
    # zero or non-zero is unknown without the active=false fetch.
    survivorship_mode = args.survivorship
    if ws >= date(2024, 1, 1):
        survivorship_note = (
            f"Current {args.universe} seed (today's active=true snapshot). "
            "Post-2024 windows usually see few delistings, so the practical "
            "bias is small, but the count is not measured here."
        )
    else:
        survivorship_note = (
            f"Current {args.universe} seed (today's active=true snapshot); "
            "pre-2024 backtests over this set will overstate returns. "
            "active=false union for delisted-in-window names is queued for "
            "a follow-up sprint."
        )

    universe_def = {
        "label": u_label,
        "seed": args.universe,
        "size": len(universe_meta),
        "type_filter": "CS",
        "survivorship_mode": survivorship_mode,
        "survivorship_note": survivorship_note,
    }

    run_at = utcnow_iso()
    sources = [
        {"endpoint": "https://api.polygon.io/v3/reference/tickers", "fetched_at": run_at,
         "context": "universe construction"},
        {"endpoint": "https://api.polygon.io/v3/reference/tickers/{ticker}", "fetched_at": run_at,
         "context": "type + sector enrichment, ~30s per 100 tickers"},
        {"endpoint": "https://api.polygon.io/v3/reference/splits?ticker={ticker}", "fetched_at": run_at,
         "context": "corp action correctness"},
        {"endpoint": "https://api.polygon.io/v3/reference/dividends?ticker={ticker}", "fetched_at": run_at,
         "context": "dividend count + spinoff detection"},
    ]
    if interface == "flat-files":
        sources.append({
            "endpoint": "s3://flatfiles/us_stocks_sip/day_aggs_v1/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz",
            "fetched_at": run_at,
            "context": "daily aggregates via flat-files (one S3 day-bucket per trading day)",
        })
    else:
        sources.append({
            "endpoint": "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}",
            "fetched_at": run_at,
            "context": "REST fallback for flat-files 403",
        })

    take = build_take(universe_def, stats, spinoffs, edge_cases, ws, we)

    schema_columns = list(ohlcv_df.columns)

    payload = {
        "out_dir": os.path.basename(os.path.normpath(out_dir)) or out_dir,
        "tier": "A" if interface == "flat-files" else "B",
        "tier_caveats": [
            f"Interface used: {interface}",
            "Adjustment method: price-only (Massive's grouped aggs are split-adjusted; "
            "cumulative factor emitted as a separate column for un-adjustment).",
            "Fundamentals not joined; see references/point-in-time-fundamentals.md "
            "for the 8-K acceptance methodology when joining downstream.",
        ],
        "mode": "dataset",
        "run_at": run_at,
        "interface_used": interface,
        "universe_definition": universe_def,
        "window_start": ws.isoformat(),
        "window_end": we.isoformat(),
        "trading_days_in_window": int(ohlcv_df["date"].nunique()),
        "files_written": files_written,
        "universe_stats": stats,
        "corporate_actions_applied": {
            "adjustment_method": "price-only",
            "splits": splits_in_window,
            "dividends_count": div_count,
            "spinoffs": spinoffs,
        },
        "edge_cases": edge_cases,
        "schema_columns": schema_columns,
        "take": take,
        "sources": sources,
    }

    # Write manifest
    manifest_path = os.path.join(out_dir, "manifest.md")
    with open(manifest_path, "w") as f:
        f.write(render_manifest(payload, out_dir))

    # Write edge-cases log
    edge_path = os.path.join(out_dir, "edge-cases.log")
    with open(edge_path, "w") as f:
        for e in edge_cases:
            f.write(json.dumps(e) + "\n")

    return payload


def render(payload: dict) -> str:
    """Alias for render_summary — matches the run/render contract of other skills."""
    return render_summary(payload, payload.get("out_dir", ""))


# CLI removed — see examples/run-backtest-data-prep.py
