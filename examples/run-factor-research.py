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

from lib.quant_garage import MassiveClient, FetchError, today, utcnow_iso


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


def build_universe(target_size):
    """Pull top-N by current market cap. Returns list of {ticker, name, market_cap}."""
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


def fetch_fundamentals(universe):
    """One annual filing per ticker. Returns dict[ticker] -> {book_equity, net_income, shares}."""
    print(f"Pulling fundamentals for {len(universe)} names...", file=sys.stderr)
    cache_path = os.path.join(CACHE_DIR, "fundamentals.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    def get_one(tk):
        try:
            d = fetch_rest(f"/vX/reference/financials?ticker={tk}&timeframe=annual&limit=2&order=desc")
            rows = d.get("results") or []
            if not rows:
                return tk, None
            fin = rows[0].get("financials") or {}
            bs = fin.get("balance_sheet") or {}
            inc = fin.get("income_statement") or {}
            equity = (bs.get("equity") or {}).get("value")
            ni = (inc.get("net_income_loss") or {}).get("value")
            return tk, {"book_equity": equity, "net_income": ni}
        except Exception:
            return tk, None

    out = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(get_one, t["ticker"]): t["ticker"] for t in universe}
        done = 0
        for f in as_completed(futures):
            tk, v = f.result()
            out[tk] = v
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(universe)} fundamentals fetched", file=sys.stderr)
    with open(cache_path, "w") as f:
        json.dump(out, f)
    return out


# ----- Factor computation -----


def winsorize(s, low=0.01, high=0.99):
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(low), s.quantile(high)
    return s.clip(lower=lo, upper=hi)


def compute_factor_panel(prices, fundamentals, universe):
    """Compute monthly factor scores. Returns dict[factor_name] -> DataFrame (month_end x ticker)."""
    # Monthly rebalance dates: last trading day of each month in the price index
    monthly_idx = prices.resample("ME").last().dropna(how="all").index
    print(f"Computing factors over {len(monthly_idx)} monthly rebalance dates...", file=sys.stderr)

    # Daily log returns
    log_ret = np.log(prices / prices.shift(1))

    factors = {}

    # Raw (unclamped) values kept for display in the membership block
    raw = {}

    # Momentum: 12M-1M lookback. At rebalance date t, score = price[t-21] / price[t-252] - 1
    mom_scores, mom_raw = [], []
    for t in monthly_idx:
        try:
            t_loc = prices.index.get_loc(t)
        except KeyError:
            continue
        if t_loc < 252:
            continue
        p_recent = prices.iloc[t_loc - 21]  # ~1M back
        p_far = prices.iloc[t_loc - 252]    # ~12M back
        score = (p_recent / p_far) - 1.0
        raw_s = score.copy(); raw_s.name = t
        score = winsorize(score)
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
        if t_loc < 252:
            continue
        window = log_ret.iloc[t_loc - 252:t_loc]
        vol = window.std() * math.sqrt(252)
        score = 1.0 / vol.replace(0, np.nan)
        raw_s = score.copy(); raw_s.name = t
        score = winsorize(score)
        score.name = t
        lv_scores.append(score)
        lv_raw.append(raw_s)
    factors["Low-Vol (1/realiz)"] = pd.DataFrame(lv_scores) if lv_scores else pd.DataFrame()
    raw["Low-Vol (1/realiz)"] = pd.DataFrame(lv_raw) if lv_raw else pd.DataFrame()

    # Value: 1 / (P/B). For each month: book_value_per_share / price[t]
    # P/B = price * shares / book_equity, so 1/(P/B) = book_equity / (price * shares)
    # We don't have shares per ticker per month; using mcap proxy from current universe is a simplification.
    # Better proxy: 1/(P/B) ratio across tickers is rank-equivalent to book_equity / market_cap
    # because price*shares = market_cap. We use that ratio recomputed each month with the
    # current latest book_equity (the fundamental-lag caveat documented in SKILL.md).
    by_ticker_mc = {u["ticker"]: u["market_cap"] for u in universe}
    val_scores, val_raw = [], []
    for t in monthly_idx:
        # current-mcap normalization (price at t * shares_today ≈ current_mcap * price[t]/price[today])
        latest = prices.iloc[-1]
        p_t = prices.loc[t] if t in prices.index else None
        if p_t is None:
            continue
        # Implied per-period mcap = current_mcap * (price_t / price_latest)
        scores = {}
        for tk in prices.columns:
            f = fundamentals.get(tk)
            mc_now = by_ticker_mc.get(tk)
            if not f or not mc_now:
                continue
            be = f.get("book_equity")
            pt = p_t.get(tk)
            pl = latest.get(tk)
            if be is None or pt is None or pl is None or pl == 0 or be <= 0:
                continue
            mc_t = mc_now * (pt / pl)
            scores[tk] = be / mc_t if mc_t > 0 else None
        s = pd.Series(scores)
        raw_s = s.copy(); raw_s.name = t
        s = winsorize(s)
        s.name = t
        val_scores.append(s)
        val_raw.append(raw_s)
    factors["Value (1/(P/B))"] = pd.DataFrame(val_scores) if val_scores else pd.DataFrame()
    raw["Value (1/(P/B))"] = pd.DataFrame(val_raw) if val_raw else pd.DataFrame()

    # Quality: ROE = net_income / book_equity (no time-varying numerator from a single annual)
    # So the quality score is constant across months per ticker for v1.
    # Documented as the fundamental-lag caveat in SKILL.md.
    roe_static = {}
    for tk in prices.columns:
        f = fundamentals.get(tk)
        if not f:
            continue
        ni = f.get("net_income")
        be = f.get("book_equity")
        if ni is None or be is None or be <= 0:
            continue
        # Filter out names with extreme book/equity ratios (buyback-shrunken equity
        # like CL, MCD historically produce ROE > 200% which dominates the rank).
        # Sales/equity ratio > 10 flags a denominator collapse situation; we keep
        # those names in the universe for momentum/value/low-vol but exclude from
        # the quality factor specifically because the metric loses its meaning.
        roe = ni / be
        if roe > 1.5 or roe < -1.0:  # Cap at +150% / -100% sensible bounds
            continue
        roe_static[tk] = roe
    # Replicate across all months for the panel
    if roe_static:
        roe_s_raw = pd.Series(roe_static)
        roe_s = winsorize(roe_s_raw)
        qual_df = pd.DataFrame(
            np.tile(roe_s.values, (len(monthly_idx), 1)),
            index=monthly_idx,
            columns=roe_s.index,
        )
        qual_raw_df = pd.DataFrame(
            np.tile(roe_s_raw.values, (len(monthly_idx), 1)),
            index=monthly_idx,
            columns=roe_s_raw.index,
        )
    else:
        qual_df = pd.DataFrame()
        qual_raw_df = pd.DataFrame()
    factors["Quality (ROE)"] = qual_df
    raw["Quality (ROE)"] = qual_raw_df

    return factors, raw, monthly_idx


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
    """For a given factor panel (month_end x ticker), compute mean IC and t-stat per horizon."""
    out = {}
    rebal = factor_df.index
    for h in horizons:
        fwd = forward_returns(prices, rebal, h)
        common = factor_df.index.intersection(fwd.index)
        ics = []
        for t in common:
            score = factor_df.loc[t]
            ret = fwd.loc[t]
            mask = score.notna() & ret.notna()
            if mask.sum() < 30:
                continue
            rho, _ = spearmanr(score[mask], ret[mask])
            if not math.isnan(rho):
                ics.append(rho)
        if len(ics) < 6:
            out[h] = {"ic": None, "t": None, "n_months": len(ics)}
            continue
        mean_ic = float(np.mean(ics))
        std_ic = float(np.std(ics, ddof=1))
        t_stat = mean_ic * math.sqrt(len(ics)) / std_ic if std_ic > 0 else None
        out[h] = {"ic": mean_ic, "t": t_stat, "n_months": len(ics)}
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
    # Group of 3 for D10
    grp = factor_rows[:3]
    render_group(grp, "top_5_current", "D10")
    render_group(grp, "bottom_5_current", "D1")
    # 4th factor as own block
    if len(factor_rows) > 3:
        f = factor_rows[3]
        lines.append(f"    {short_name(f['name']).upper()} (D10)        {short_name(f['name']).upper()} (D1)")
        top = f.get("top_5_current") or []
        bot = f.get("bottom_5_current") or []
        for i in range(max(len(top), len(bot))):
            t_cell = f"{top[i]['ticker']:<5} {top[i].get('value_display') or ''}" if i < len(top) else ""
            b_cell = f"{bot[i]['ticker']:<5} {bot[i].get('value_display') or ''}" if i < len(bot) else ""
            lines.append(f"    {t_cell:<22}{b_cell}")
        lines.append("")

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
    args = p.parse_args()

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
    universe = build_universe(args.universe_size)
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
    factor_panels, factor_raw, monthly_idx = compute_factor_panel(prices, fundamentals, universe)

    # IC + decile per factor
    factor_results = []
    for name, df in factor_panels.items():
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
            "ic_se_1m": None,
            "decile_spread_1m": ds_1m,
            "decile_spread_3m": ds_3m,
            "decile_spread_12m": ds_12m,
            "hit_rate_12m": hit_12m,
            "top_5_current": top_5,
            "bottom_5_current": bot_5,
        })
        print(f"  {name}: IC1M={ics[1]['ic']}, t={ics[1]['t']}, n_months={ics[1]['n_months']}", file=sys.stderr)

    # Correlation matrix on signals
    corr_names, corr_matrix = compute_signal_correlation(factor_panels)

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

    payload = {
        "tier": "A",
        "tier_caveats": [
            f"Interface used: {interface_used}",
            "Fundamentals are single most-recent annual filing; for a true point-in-time "
            "factor, use the filing available as of each rebalance date (PR queued).",
        ],
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
        "take": take,
        "sources": sources,
    }

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
    print(rendered)


def _definition_for(name):
    if "Momentum" in name:
        return "12M return minus most recent 1M (academic-standard mom, skip-1)"
    if "Value" in name:
        return "1 / (P/B) = book_equity / market_cap (higher = cheaper)"
    if "Quality" in name:
        return "ROE = net_income_ttm / shareholders_equity (latest annual)"
    if "Low-Vol" in name:
        return "1 / realized_vol_252d (annualized stdev of daily log returns)"
    return ""


if __name__ == "__main__":
    main()
