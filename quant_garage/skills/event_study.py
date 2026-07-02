"""
event-study as an importable library function.

Three input modes:

  Single:        run(ticker="NVDA", event_date="2026-05-20", event_class="earnings")
  Cross-section: run(tickers="AAPL,NVDA,MSFT", event_class="earnings", period="most_recent")
  Aggregate:     run(tickers="AAPL,NVDA,MSFT", event_class="earnings",
                     window=("2025-06-01","2026-06-24"))

Event classes:
  earnings              (Benzinga preferred, SEC EDGAR 8-K item 2.02 fallback)
  dividend_changes      (cash amount diff vs prior >= 1%)
  large_volume_spike    (volume > 3 sigma trailing 30d, 5d cooldown)
"""
from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from .. import (
    MassiveClient,
    today,
    utcnow_iso,
    is_significant,
    base_rate,
)
from ..timezones import utc_to_et

# SEC EDGAR is NOT a Massive endpoint, so it stays on raw urllib with the
# personal User-Agent required by SEC's fair-use policy.
SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}
TODAY = today()

client = MassiveClient()


# -------- HTTP helpers --------


def fetch_all(path: str, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Collect every page from the Massive client and return (results, last_fetched_at)."""
    out: list[dict[str, Any]] = []
    last_fetched = utcnow_iso()
    for page, fetched_at in client.paginate(path, params):
        out.extend(page)
        last_fetched = fetched_at
    return out, last_fetched


def fetch_sec(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# -------- Provenance tracking --------

# Collects (endpoint, fetched_at, context) tuples as the script runs so the
# sources[] block in the payload reflects actual call times (M8).
_sources: list[dict[str, str]] = []


def record_source(endpoint: str, fetched_at: str, context: str) -> None:
    _sources.append({
        "endpoint": endpoint,
        "fetched_at": fetched_at,
        "context": context,
    })


# -------- Stats helpers --------


def mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def std_sample(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    if m is None:
        return None
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def t_stat_one_sample(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    s = std_sample(xs)
    if m is None or s is None or s <= 0:
        return None
    se = s / math.sqrt(n)
    return m / se if se > 0 else None


def percentile_of(xs: list[float], value: float) -> float:
    if not xs:
        return 0.5
    rank = sum(1 for x in xs if x <= value)
    return rank / (len(xs) + 1)


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    dx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    dy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# -------- Aggregate pulls (cached) --------


_aggs_cache: dict[str, dict[str, dict[str, Any]]] = {}


def get_daily_aggs(ticker: str, from_date: str, to_date: str) -> dict[str, dict[str, Any]]:
    """Return a dict keyed by ISO date string -> aggregate dict."""
    cache_key = f"{ticker}:{from_date}:{to_date}"
    if cache_key in _aggs_cache:
        return _aggs_cache[cache_key]
    print(f"  fetching daily aggs {ticker} {from_date}..{to_date}", file=sys.stderr)
    rows, fetched_at = fetch_all(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    record_source(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
        fetched_at,
        f"Daily closes for {ticker} ({from_date}..{to_date})",
    )
    out: dict[str, dict[str, Any]] = {}
    for a in rows:
        d = datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date()
        out[d.isoformat()] = a
    _aggs_cache[cache_key] = out
    return out


# -------- Session classifier --------


def classify_session(time_str: str | None) -> str:
    """time_str is HH:MM:SS in ET. Maps to BMO / AMC / DMH / unknown."""
    if not time_str:
        return "unknown"
    try:
        hh, mm, _ = time_str.split(":")
        hh = int(hh)
        mm = int(mm)
    except (ValueError, AttributeError):
        return "unknown"
    minutes = hh * 60 + mm
    if minutes < 9 * 60 + 30:
        return "BMO"
    if minutes >= 16 * 60:
        return "AMC"
    return "DMH"


def classify_session_from_utc(utc_iso: str) -> tuple[str, str]:
    """Convert a UTC ISO timestamp (EDGAR acceptanceDateTime) to (ET_date, session_label).

    Uses zoneinfo via utc_to_et so the AMC/BMO/Intraday boundaries land on the
    correct ET clock year-round, including across DST transitions (H1).
    """
    iso = utc_iso
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    dt_utc = datetime.fromisoformat(iso)
    dt_et = utc_to_et(dt_utc)
    et_date = dt_et.date().isoformat()
    minutes = dt_et.hour * 60 + dt_et.minute
    if minutes < 9 * 60 + 30:
        return et_date, "BMO"
    if minutes >= 16 * 60:
        return et_date, "AMC"
    return et_date, "DMH"


# -------- Event resolvers --------


_benzinga_cache: dict[str, list[dict[str, Any]]] = {}
_edgar_cache: dict[str, list[dict[str, Any]]] = {}
_cik_cache: dict[str, str | None] = {}

# Per-step diagnostics so the "No events matched" failure mode can name
# the exact step that dropped events. Keyed by ticker, then by resolver
# stage (e.g. "earnings", "dividend_changes", "large_volume_spike"). The
# caller reads this after invoking the resolver so existing return shapes
# stay intact.
_RESOLVER_DIAGNOSTICS: dict[str, dict[str, dict[str, Any]]] = {}


def record_diag(ticker: str, stage: str, info: dict[str, Any]) -> None:
    _RESOLVER_DIAGNOSTICS.setdefault(ticker, {})[stage] = info


# SEC publishes a free canonical ticker -> CIK mapping. Used as a fallback
# when Massive's /v3/reference/tickers returns no cik field (common for
# smaller / newer listings). Cached in-process for the run.
_SEC_TICKER_MAP_CACHE: dict[str, str] | None = None
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def fetch_sec_ticker_map() -> dict[str, str]:
    """Return TICKER -> 10-digit zero-padded CIK from SEC's public mapping.

    Free, no-auth, ~17k entries. Cached at module level for the run.
    """
    global _SEC_TICKER_MAP_CACHE
    if _SEC_TICKER_MAP_CACHE is not None:
        return _SEC_TICKER_MAP_CACHE
    try:
        doc = fetch_sec(SEC_TICKER_MAP_URL)
        record_source(
            SEC_TICKER_MAP_URL,
            utcnow_iso(),
            "SEC canonical ticker->CIK map (fallback for Massive)",
        )
    except Exception as e:
        print(f"  SEC ticker map: {e}", file=sys.stderr)
        _SEC_TICKER_MAP_CACHE = {}
        return _SEC_TICKER_MAP_CACHE
    out: dict[str, str] = {}
    # doc is keyed by stringified index (e.g. "0", "1", ...) per SEC's format
    for _, row in doc.items():
        t = str(row.get("ticker", "")).upper()
        cik = row.get("cik_str")
        if t and cik is not None:
            out[t] = str(cik).zfill(10)
    _SEC_TICKER_MAP_CACHE = out
    return out


def benzinga_earnings(ticker: str) -> list[dict[str, Any]]:
    if ticker in _benzinga_cache:
        return _benzinga_cache[ticker]
    print(f"  Benzinga earnings {ticker}", file=sys.stderr)
    try:
        rows, fetched_at = fetch_all(
            "/benzinga/v1/earnings",
            {
                "ticker": ticker,
                "limit": 40,
                "order": "desc",
                "sort": "date",
                "date.lte": TODAY.isoformat(),
            },
        )
        record_source(
            "/benzinga/v1/earnings?ticker={ticker}",
            fetched_at,
            f"Benzinga earnings history for {ticker}",
        )
    except Exception as e:
        # Benzinga add-on may be missing on this key, or the endpoint may be
        # transiently unavailable. Fall through to EDGAR rather than erroring
        # out the entire study. The caller will switch to Tier B.
        print(f"  Benzinga earnings {ticker}: {e}", file=sys.stderr)
        rows = []
    # filter to confirmed past prints
    rows = [r for r in rows if r.get("date_status") != "projected"]
    _benzinga_cache[ticker] = rows
    return rows


def get_cik(ticker: str) -> str | None:
    """Pull the zero-padded 10-digit CIK for a ticker.

    Primary: Massive /v3/reference/tickers/{T}.cik. Fallback: SEC's free
    canonical company_tickers.json when Massive returns no cik (common
    for smaller / newer listings).
    """
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    massive_ok = False
    try:
        body, fetched_at = client.get(f"/v3/reference/tickers/{ticker}")
        record_source(
            f"/v3/reference/tickers/{ticker}",
            fetched_at,
            f"Ticker metadata for {ticker} (CIK lookup)",
        )
        massive_ok = True
    except Exception as e:
        print(f"  ticker reference {ticker}: {e}", file=sys.stderr)
        body = {}
    results = (body.get("results") or {}) if isinstance(body, dict) else {}
    cik_raw = results.get("cik") if isinstance(results, dict) else None
    if cik_raw:
        padded = str(cik_raw).zfill(10)
        _cik_cache[ticker] = padded
        return padded
    # Massive returned no CIK (either the request failed or the field is
    # missing). Try SEC's free canonical map.
    sec_map = fetch_sec_ticker_map()
    fallback = sec_map.get(ticker.upper())
    if fallback:
        print(
            f"  CIK fallback: {ticker} -> {fallback} via SEC ticker map "
            f"(massive_ok={massive_ok})",
            file=sys.stderr,
        )
        _cik_cache[ticker] = fallback
        return fallback
    _cik_cache[ticker] = None
    return None


# 8-K items accepted as earnings-class signals. 2.02 is the standard
# "Results of Operations and Financial Condition" item. 7.01 (Reg FD)
# and 8.01 (Other Events) are softer signals biotechs and small caps
# sometimes use for earnings-equivalent disclosures. Each event is
# tagged with its item_code + signal_strength so downstream consumers
# can treat soft signals more cautiously.
EDGAR_EARNINGS_ITEMS = ("2.02", "7.01", "8.01")
EDGAR_STRONG_ITEMS = ("2.02",)


def edgar_earnings(ticker: str) -> list[dict[str, Any]]:
    """Return earnings prints sourced from SEC EDGAR 8-K filings.

    Accepts items 2.02 (standard earnings release), 7.01 (Reg FD), and
    8.01 (Other Events). Each row is tagged with its item_code and a
    signal_strength of "strong" (2.02) or "soft" (7.01/8.01).

    Output rows expose the same shape that resolve_earnings consumes
    (date, time, fiscal_period, fiscal_year, company_name, eps_surprise_percent).
    Surprise / estimates are unavailable from EDGAR alone, so those keys are
    None and the resulting events are Tier B.
    """
    diag: dict[str, Any] = {
        "method": "edgar_8k_items_" + "_".join(EDGAR_EARNINGS_ITEMS),
        "cik_found": False,
        "raw_filing_count": 0,
        "raw_8k_count": 0,
        "matched_filter_count": 0,
        "observed_item_codes": [],
        "failure_reason": None,
    }
    if ticker in _edgar_cache:
        # Reuse cached diagnostics if present
        cached_diag = _RESOLVER_DIAGNOSTICS.get(ticker, {}).get("edgar_earnings")
        if cached_diag:
            record_diag(ticker, "edgar_earnings", cached_diag)
        return _edgar_cache[ticker]
    cik_padded = get_cik(ticker)
    if not cik_padded:
        diag["failure_reason"] = "cik_not_found"
        record_diag(ticker, "edgar_earnings", diag)
        _edgar_cache[ticker] = []
        return []
    diag["cik_found"] = True
    diag["cik"] = cik_padded
    print(f"  SEC EDGAR 8-Ks {ticker} (CIK {cik_padded})", file=sys.stderr)
    sec_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        doc = fetch_sec(sec_url)
        sec_fetched_at = utcnow_iso()
        record_source(
            sec_url,
            sec_fetched_at,
            f"SEC EDGAR submissions for {ticker} (8-K items "
            + "/".join(EDGAR_EARNINGS_ITEMS) + ")",
        )
    except Exception as e:
        print(f"  SEC EDGAR {ticker}: {e}", file=sys.stderr)
        diag["failure_reason"] = f"edgar_fetch_error: {e}"
        record_diag(ticker, "edgar_earnings", diag)
        _edgar_cache[ticker] = []
        return []
    recent = (doc.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    items_col = recent.get("items") or [""] * len(forms)
    acc_col = recent.get("acceptanceDateTime") or [""] * len(forms)
    company_name = doc.get("name")
    diag["raw_filing_count"] = len(forms)

    observed_items: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    raw_8k_count = 0
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        raw_8k_count += 1
        items_list = [it.strip() for it in (items_col[i] or "").split(",") if it.strip()]
        for code in items_list:
            observed_items[code] = observed_items.get(code, 0) + 1
        matched_item = next((c for c in items_list if c in EDGAR_EARNINGS_ITEMS), None)
        if matched_item is None:
            continue
        acc_dt = acc_col[i]
        if not acc_dt:
            continue
        # EDGAR acceptanceDateTime is UTC; AAPL's last 6 prints verified at 16:30 ET
        # (commit d47e67a notes this in AUDIT N1). Z suffix is implicit.
        utc_iso = acc_dt if acc_dt.endswith("Z") else acc_dt + "Z"
        et_date, session = classify_session_from_utc(utc_iso)
        # Convert session ("BMO"/"AMC"/"DMH") into the HH:MM:SS bucket the
        # classifier expects so the downstream renderer keeps a consistent shape.
        time_str = {"BMO": "07:00:00", "AMC": "16:30:00", "DMH": "12:00:00"}[session]
        signal_strength = "strong" if matched_item in EDGAR_STRONG_ITEMS else "soft"
        out.append({
            "date": et_date,
            "time": time_str,
            "date_status": "confirmed",
            "company_name": company_name,
            "fiscal_period": None,
            "fiscal_year": None,
            "eps_surprise_percent": None,
            "estimated_eps": None,
            "previous_eps": None,
            "item_code": matched_item,
            "signal_strength": signal_strength,
            "_source": "edgar",
        })
    diag["raw_8k_count"] = raw_8k_count
    diag["matched_filter_count"] = len(out)
    diag["observed_item_codes"] = sorted(observed_items.keys())
    if not out:
        if raw_8k_count == 0:
            diag["failure_reason"] = "no_8k_filings"
        else:
            diag["failure_reason"] = "no_matching_item_codes"
    record_diag(ticker, "edgar_earnings", diag)
    # newest-first to match Benzinga's ordering
    out.sort(key=lambda r: r["date"], reverse=True)
    _edgar_cache[ticker] = out
    return out


def resolve_earnings(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of event tuples for the earnings class.

    Tier A path: Benzinga returns rows. Use surprise %, EPS estimates, etc.
    Tier B path: Benzinga returns empty (key missing the add-on, or the
    endpoint is briefly unavailable). Fall back to SEC EDGAR 8-K items
    2.02 / 7.01 / 8.01 for print dates. No surprise data, no consensus EPS.
    """
    rows = benzinga_earnings(ticker)
    source = "benzinga" if rows else None
    record_diag(ticker, "benzinga_earnings", {
        "method": "benzinga_earnings",
        "raw_row_count": len(rows),
        "failure_reason": None if rows else "benzinga_empty_or_unavailable",
    })
    tier = "A" if rows else "B"
    if not rows:
        rows = edgar_earnings(ticker)
        source = "edgar"

    diag: dict[str, Any] = {
        "method": f"resolve_earnings:{source}",
        "raw_event_count": len(rows),
        "after_date_filter_count": 0,
        "failure_reason": None,
    }

    if not rows:
        diag["failure_reason"] = (
            "no_events_from_any_source"
            if not _RESOLVER_DIAGNOSTICS.get(ticker, {}).get("edgar_earnings", {}).get("failure_reason")
            else "no_events_from_any_source"
        )
        record_diag(ticker, "earnings", diag)
        return []

    out = []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        if event_date and d != event_date:
            continue
        if window:
            if d < window[0] or d > window[1]:
                continue
        meta: dict[str, Any] = {
            "fiscal_period": r.get("fiscal_period"),
            "fiscal_year": r.get("fiscal_year"),
            "surprise_eps_pct": r.get("eps_surprise_percent"),
            "estimated_eps": r.get("estimated_eps"),
            "previous_eps": r.get("previous_eps"),
            "company_name": r.get("company_name"),
            "release_time_et": r.get("time"),
        }
        # Propagate EDGAR-only tags so the signal class is visible
        # downstream (single-event take, JSON output, renderer).
        if "item_code" in r:
            meta["item_code"] = r.get("item_code")
        if "signal_strength" in r:
            meta["signal_strength"] = r.get("signal_strength")
        out.append({
            "ticker": ticker,
            "event_date": d,
            "event_session": classify_session(r.get("time")),
            "event_metadata": meta,
            "_tier": tier,
        })
    diag["after_date_filter_count"] = len(out)
    if not out:
        if event_date or window:
            diag["failure_reason"] = "all_filtered_by_date"
        else:
            diag["failure_reason"] = "no_events_after_resolver"
    record_diag(ticker, "earnings", diag)
    # ascending by date so prior-history is easy to slice
    out.sort(key=lambda e: e["event_date"])
    return out


def resolve_dividend_changes(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    print(f"  dividends {ticker}", file=sys.stderr)
    diag: dict[str, Any] = {
        "method": "massive_v3_reference_dividends",
        "raw_action_count": 0,
        "regular_count": 0,
        "change_qualifying_count": 0,
        "after_date_filter_count": 0,
        "failure_reason": None,
    }
    try:
        rows, fetched_at = fetch_all(
            "/v3/reference/dividends",
            {"ticker": ticker, "limit": 40, "order": "asc", "sort": "ex_dividend_date"},
        )
        record_source(
            "/v3/reference/dividends?ticker={ticker}",
            fetched_at,
            f"Cash dividend history for {ticker}",
        )
    except Exception as e:
        print(f"  dividends {ticker}: {e}", file=sys.stderr)
        diag["failure_reason"] = f"dividends_fetch_error: {e}"
        record_diag(ticker, "dividend_changes", diag)
        return []
    diag["raw_action_count"] = len(rows)
    # Exclude special-cash dividends from the baseline
    regular = [
        r for r in rows
        if r.get("dividend_type") not in ("SC",)
        and r.get("cash_amount") is not None
    ]
    diag["regular_count"] = len(regular)
    out = []
    qualifying = 0
    prior = None
    for r in regular:
        amt = float(r["cash_amount"])
        ex = r.get("ex_dividend_date")
        if not ex:
            continue
        if prior is None:
            prior = amt
            continue
        change_pct = (amt - prior) / prior if prior > 0 else 0
        if abs(change_pct) >= 0.01:
            qualifying += 1
            if event_date and ex != event_date:
                prior = amt
                continue
            if window and (ex < window[0] or ex > window[1]):
                prior = amt
                continue
            out.append({
                "ticker": ticker,
                "event_date": ex,
                "event_session": "BMO",
                "event_metadata": {
                    "prior_amount": prior,
                    "new_amount": amt,
                    "change_pct": change_pct,
                    "change_direction": "hike" if change_pct > 0 else "cut",
                },
                "_tier": "A",
            })
        prior = amt
    diag["change_qualifying_count"] = qualifying
    diag["after_date_filter_count"] = len(out)
    if not out:
        if len(rows) == 0:
            diag["failure_reason"] = "no_dividend_history"
        elif qualifying == 0:
            diag["failure_reason"] = "no_changes_above_1pct"
        elif event_date or window:
            diag["failure_reason"] = "all_filtered_by_date"
    record_diag(ticker, "dividend_changes", diag)
    return out


def resolve_volume_spike(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    diag: dict[str, Any] = {
        "method": "daily_aggs_z_score_gt_3",
        "trading_day_count": 0,
        "raw_spike_count": 0,
        "after_date_filter_count": 0,
        "failure_reason": None,
    }
    # Pull a wide buffer for both volume history and the 30d trailing stats.
    # The full-history pass (window=None) needs to span far enough back that
    # downstream window-filtered queries find spikes. Default: 2 years.
    if window:
        from_d = (date.fromisoformat(window[0]) - timedelta(days=60)).isoformat()
        to_d = min(date.fromisoformat(window[1]), TODAY).isoformat()
    else:
        from_d = (TODAY - timedelta(days=365 * 2)).isoformat()
        to_d = TODAY.isoformat()
    try:
        aggs = get_daily_aggs(ticker, from_d, to_d)
    except Exception as e:
        print(f"  volume spike aggs {ticker}: {e}", file=sys.stderr)
        diag["failure_reason"] = f"aggs_fetch_error: {e}"
        record_diag(ticker, "large_volume_spike", diag)
        return []
    dates_sorted = sorted(aggs.keys())
    diag["trading_day_count"] = len(dates_sorted)
    out = []
    raw_spikes = 0
    cooldown_until_idx = -1
    for i, d in enumerate(dates_sorted):
        if i < 30:
            continue
        if i <= cooldown_until_idx:
            continue
        trailing = [aggs[dates_sorted[j]].get("v", 0) for j in range(i - 30, i)]
        m = mean(trailing)
        s = std_sample(trailing)
        vol_today = aggs[d].get("v", 0)
        if not m or not s or s <= 0:
            continue
        z = (vol_today - m) / s
        if z <= 3.0:
            continue
        raw_spikes += 1
        # Apply event_date / window filters
        if event_date and d != event_date:
            continue
        if window and (d < window[0] or d > window[1]):
            continue
        out.append({
            "ticker": ticker,
            "event_date": d,
            "event_session": "DMH",
            "event_metadata": {
                "volume": vol_today,
                "trailing_30d_mean": m,
                "trailing_30d_std": s,
                "z_score": z,
            },
            "_tier": "A",
        })
        cooldown_until_idx = i + 5
    diag["raw_spike_count"] = raw_spikes
    diag["after_date_filter_count"] = len(out)
    if not out:
        if len(dates_sorted) == 0:
            diag["failure_reason"] = "no_daily_aggs"
        elif raw_spikes == 0:
            diag["failure_reason"] = "no_z_score_above_3"
        elif event_date or window:
            diag["failure_reason"] = "all_filtered_by_date"
    record_diag(ticker, "large_volume_spike", diag)
    return out


RESOLVERS = {
    "earnings": resolve_earnings,
    "dividend_changes": resolve_dividend_changes,
    "large_volume_spike": resolve_volume_spike,
}


# -------- Return computation --------


def next_trading_day(dates: list[str], anchor: str, offset: int) -> str | None:
    """Find the trading day `offset` steps from anchor in the sorted dates list."""
    try:
        idx = dates.index(anchor)
    except ValueError:
        idx = next((i for i, td in enumerate(dates) if td > anchor), None)
        if idx is None:
            return None
        idx -= 1
        if idx < 0:
            idx = 0
    target = idx + offset
    if 0 <= target < len(dates):
        return dates[target]
    return None


def compute_event_returns(
    event: dict[str, Any],
    ticker_aggs: dict[str, dict[str, Any]],
    spy_aggs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build event_window_returns and abnormal_returns blocks per the schema."""
    ticker_dates = sorted(ticker_aggs.keys())
    if not ticker_dates:
        return {"event_window_returns": None, "abnormal_returns": None}

    event_date = event["event_date"]
    session = event["event_session"]

    # Determine T0 per the session convention.
    if session == "AMC":
        # T0 is the trading day of the press release; T+1 is next session.
        t0 = next_trading_day(ticker_dates, event_date, 0)
        if t0 is None or t0 != event_date:
            # event_date may not be a trading day; snap to nearest prior
            t0 = max((d for d in ticker_dates if d <= event_date), default=None)
    else:
        # BMO and DMH: T0 is the trading day BEFORE the event.
        t0 = max((d for d in ticker_dates if d < event_date), default=None)
        if t0 is None and event_date in ticker_aggs:
            t0 = event_date

    if t0 is None or t0 not in ticker_aggs:
        return {"event_window_returns": None, "abnormal_returns": None}

    t0_close = ticker_aggs[t0]["c"]
    spy_t0_close = spy_aggs.get(t0, {}).get("c")

    # Find T+1 closes up front so post-announcement drift (T+1 → T+horizon)
    # can be computed alongside the event-inclusive CAR (T0 → T+horizon).
    # Per the 2026-06-26 audit (C5), measuring drift from the pre-event close
    # mislabels the announcement reaction as drift; the two windows must be
    # reported as separate fields.
    t1_date = next_trading_day(ticker_dates, t0, 1)
    t1_close = ticker_aggs.get(t1_date, {}).get("c") if t1_date else None
    spy_t1_close = spy_aggs.get(t1_date, {}).get("c") if t1_date else None

    horizons = []
    ar = {}
    for label, offset in [("T+1", 1), ("T+3", 3), ("T+5", 5)]:
        td = next_trading_day(ticker_dates, t0, offset)
        if not td or td not in ticker_aggs:
            horizons.append({
                "horizon": label, "date": None, "close": None,
                "raw_return_pct": None, "spy_close": None,
                "spy_return_pct": None,
            })
            ar[f"ar_{label.lower().replace('+', '')}_pct"] = None
            if offset > 1:
                ar[f"post_announce_drift_{label.lower().replace('+', '')}_pct"] = None
            continue
        close = ticker_aggs[td]["c"]
        raw_ret = (close - t0_close) / t0_close if t0_close else None
        spy_close = spy_aggs.get(td, {}).get("c")
        spy_ret = (
            (spy_close - spy_t0_close) / spy_t0_close
            if (spy_close is not None and spy_t0_close)
            else None
        )
        abn = (
            raw_ret - spy_ret
            if (raw_ret is not None and spy_ret is not None)
            else None
        )
        horizons.append({
            "horizon": label,
            "date": td,
            "close": close,
            "raw_return_pct": raw_ret,
            "spy_close": spy_close,
            "spy_return_pct": spy_ret,
        })
        ar[f"ar_{label.lower().replace('+', '')}_pct"] = abn

        # Post-announcement drift: T+1 close → T+horizon close, SPY-adjusted.
        # Defined only for horizons strictly after T+1.
        if offset > 1:
            if t1_close and spy_t1_close and spy_close is not None:
                drift_raw = (close - t1_close) / t1_close
                drift_spy = (spy_close - spy_t1_close) / spy_t1_close
                drift_abn = drift_raw - drift_spy
            else:
                drift_abn = None
            ar[f"post_announce_drift_{label.lower().replace('+', '')}_pct"] = drift_abn

    ar["car_t5_pct"] = ar.get("ar_t5_pct")
    # Naming convenience: rendered output uses the explicit "event-inclusive"
    # label. Keep the existing `ar_*` / `car_t5_pct` fields for backward
    # compatibility and add a clearer alias per the C5 schema.
    ar["event_inclusive_t5_pct"] = ar.get("ar_t5_pct")
    return {
        "t0_date": t0,
        "event_window_returns": {
            "t0_close": t0_close,
            "spy_t0_close": spy_t0_close,
            "t1_close": t1_close,
            "spy_t1_close": spy_t1_close,
            "horizons": horizons,
        },
        "abnormal_returns": ar,
    }


# -------- Per-subject t-stat vs history --------


def t_stat_vs_history(
    subject: dict[str, Any],
    all_events_for_ticker: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare subject's T+5 CAR to its name's prior reaction distribution."""
    this_event = subject["event_date"]
    prior = [
        e for e in all_events_for_ticker
        if e["event_date"] < this_event
        and e.get("_t5_car") is not None
    ]
    prior_t5 = [e["_t5_car"] for e in prior]
    n = len(prior_t5)
    this_car = subject["abnormal_returns"].get("car_t5_pct")
    if n == 0 or this_car is None:
        return None
    m = mean(prior_t5)
    s = std_sample(prior_t5)
    z = (this_car - m) / s if (m is not None and s and s > 0) else None
    pct = percentile_of(prior_t5, this_car)
    underpowered = n < 8

    # Direction concurrence (earnings class only)
    direction_concurrence = None
    surprise = subject["event_metadata"].get("surprise_eps_pct") if subject.get("event_metadata") else None
    if surprise is not None:
        prior_with_surprise = [
            e for e in prior
            if e["event_metadata"].get("surprise_eps_pct") is not None
        ]
        if prior_with_surprise:
            matches = sum(
                1 for e in prior_with_surprise
                if (
                    (e["event_metadata"]["surprise_eps_pct"] >= 0
                     and (e["_t5_car"] or 0) >= 0)
                    or (e["event_metadata"]["surprise_eps_pct"] < 0
                        and (e["_t5_car"] or 0) < 0)
                )
            )
            direction_concurrence = f"{matches}/{len(prior_with_surprise)}"

    return {
        "prior_n": n,
        "prior_mean_t5_car_pct": m,
        "prior_std_t5_car_pct": s,
        "this_event_t5_car_pct": this_car,
        "z_score": z,
        "percentile": pct,
        "underpowered": underpowered,
        "direction_concurrence": direction_concurrence,
    }


# -------- Cross-section / aggregate summary --------


def build_summary(subjects: list[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    cars = [s["abnormal_returns"]["car_t5_pct"] for s in subjects
            if s["abnormal_returns"] and s["abnormal_returns"].get("car_t5_pct") is not None]
    if not cars:
        return None
    n = len(cars)
    m = mean(cars)
    med = median(cars)
    s = std_sample(cars)
    t = t_stat_one_sample(cars)

    horizon_breakdown = []
    for label, key in [("T+1", "ar_t1_pct"), ("T+3", "ar_t3_pct"), ("T+5", "ar_t5_pct")]:
        vals = [
            sub["abnormal_returns"].get(key) for sub in subjects
            if sub["abnormal_returns"] and sub["abnormal_returns"].get(key) is not None
        ]
        if not vals:
            continue
        ts = t_stat_one_sample(vals)
        entry = {
            "horizon": label,
            "mean_ar_pct": mean(vals),
            "median_ar_pct": median(vals),
            "std_ar_pct": std_sample(vals),
            "t_stat": ts,
            "n": len(vals),
            # Per C6: df-aware critical t (n=8 → 2.36, not 2.0) via
            # is_significant(). The `n >= 8` floor is a separate
            # sample-size guard and remains in place.
            "significant": (
                is_significant(ts, len(vals)) and len(vals) >= 8
                if ts is not None else False
            ),
        }
        # Post-announcement drift cross-section (T+1 → T+horizon) per C5.
        # Defined only for horizons strictly after T+1.
        if label != "T+1":
            dkey = f"post_announce_drift_{label.lower().replace('+', '')}_pct"
            drift_vals = [
                sub["abnormal_returns"].get(dkey) for sub in subjects
                if sub["abnormal_returns"] and sub["abnormal_returns"].get(dkey) is not None
            ]
            if drift_vals:
                d_ts = t_stat_one_sample(drift_vals)
                entry["mean_post_announce_drift_pct"] = mean(drift_vals)
                entry["median_post_announce_drift_pct"] = median(drift_vals)
                entry["drift_t_stat"] = d_ts
                entry["drift_n"] = len(drift_vals)
                entry["drift_significant"] = (
                    is_significant(d_ts, len(drift_vals)) and len(drift_vals) >= 8
                    if d_ts is not None else False
                )
        horizon_breakdown.append(entry)

    # Surprise vs reaction (earnings, Tier A)
    surprise_block = None
    surprises = []
    surprise_cars = []
    for sub in subjects:
        sp = sub["event_metadata"].get("surprise_eps_pct") if sub.get("event_metadata") else None
        car = sub["abnormal_returns"].get("car_t5_pct") if sub["abnormal_returns"] else None
        if sp is not None and car is not None:
            surprises.append(sp)
            surprise_cars.append(car)
    if len(surprises) >= 3:
        rho = pearson(surprises, surprise_cars)
        if rho is not None:
            surprise_block = {
                "rho": rho,
                "n": len(surprises),
                "r_squared": rho ** 2,
            }

    # Regime check (aggregate mode only, n >= 8)
    regime_block = None
    if mode == "aggregate" and n >= 8:
        sorted_subj = sorted(
            [s for s in subjects if s["abnormal_returns"]
             and s["abnormal_returns"].get("car_t5_pct") is not None],
            key=lambda x: x["event_date"]
        )
        recent_cars = [s["abnormal_returns"]["car_t5_pct"] for s in sorted_subj[-4:]]
        full_mean = m
        recent_mean = mean(recent_cars)
        se_full = (s / math.sqrt(n)) if (s and n) else None
        delta = recent_mean - full_mean if recent_mean is not None else None
        regime_block = {
            "recent_n": len(recent_cars),
            "recent_mean_t5_car_pct": recent_mean,
            "full_window_mean_t5_car_pct": full_mean,
            "delta_pp": delta,
            "regime_shift_flag": (
                abs(delta) > se_full
                if (delta is not None and se_full) else False
            ),
        }

    return {
        "n_subjects": n,
        "n_tickers": len({sub["ticker"] for sub in subjects}),
        "mean_t5_car_pct": m,
        "median_t5_car_pct": med,
        "std_t5_car_pct": s,
        "t_stat_avg_vs_zero": t,
        # Per C6: df-aware critical t via is_significant(). The `n >= 8`
        # floor is a separate sample-size guard and remains in place.
        "significant": (is_significant(t, n) and n >= 8) if t is not None else False,
        "horizon_breakdown": horizon_breakdown,
        "percentiles": {
            "p10_pct": percentile(cars, 0.10),
            "p25_pct": percentile(cars, 0.25),
            "p50_pct": percentile(cars, 0.50),
            "p75_pct": percentile(cars, 0.75),
            "p90_pct": percentile(cars, 0.90),
        },
        "surprise_reaction_correlation": surprise_block,
        "regime_check": regime_block,
    }


# -------- Universe base rate (M10) --------


# Mega-cap default universe. Used when --with-base-rate is set without
# a custom --base-rate-universe, so the live pull stays bounded.
UNIVERSE_BASE_RATE_DEFAULT = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "V", "UNH", "WMT", "PG", "MA", "HD", "XOM",
]


def compute_universe_base_rate(
    event_class: str,
    universe_tickers: list[str],
    pull_from: str,
    pull_to: str,
    spy_aggs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Pull recent events for `universe_tickers`, compute the same per-event
    abnormal-return metrics, and return base-rate stats for each metric.

    Heavy: per ticker this hits the resolver (Benzinga/EDGAR) + daily aggs.
    Only call when the operator opts in with --with-base-rate.
    """
    metric_buckets: dict[str, list[float]] = {
        "ar_t1_pct": [],
        "ar_t3_pct": [],
        "car_t5_pct": [],
        "post_announce_drift_t3_pct": [],
        "post_announce_drift_t5_pct": [],
    }
    tickers_pulled = 0
    events_seen = 0
    for t in universe_tickers:
        try:
            evs = RESOLVERS[event_class](t, event_date=None, window=None)
        except Exception as e:
            print(f"  base-rate: resolver failed for {t}: {e}", file=sys.stderr)
            continue
        if not evs:
            continue
        try:
            ticker_aggs = get_daily_aggs(t, pull_from, pull_to)
        except Exception as e:
            print(f"  base-rate: aggs failed for {t}: {e}", file=sys.stderr)
            continue
        tickers_pulled += 1
        for e in evs:
            res = compute_event_returns(e, ticker_aggs, spy_aggs)
            ar = res.get("abnormal_returns") or {}
            for key in metric_buckets:
                v = ar.get(key)
                if v is not None:
                    metric_buckets[key].append(v)
            events_seen += 1

    if events_seen == 0:
        return None

    return {
        "universe_tickers": list(universe_tickers),
        "n_tickers_with_data": tickers_pulled,
        "n_events": events_seen,
        "by_metric": {
            metric: base_rate(values) for metric, values in metric_buckets.items()
        },
    }


# -------- Take generation --------


def generate_take_single(subject: dict[str, Any]) -> str:
    car = subject["abnormal_returns"].get("car_t5_pct")
    hist = subject.get("t_stat_vs_history")
    meta = subject.get("event_metadata") or {}
    signal_strength = meta.get("signal_strength")
    item_code = meta.get("item_code")
    # Soft EDGAR signals (7.01 Reg FD, 8.01 Other Events) may not actually
    # be earnings prints — prefix the take so the operator sees the caveat
    # before any return statistic.
    soft_prefix = ""
    if signal_strength == "soft":
        soft_prefix = (
            f"[soft 8-K item {item_code}: may not be a true earnings release] "
        )
    if car is None:
        return soft_prefix + "Event window returns unavailable (insufficient price data)."
    car_pp = f"{car * 100:+.1f}pp"
    if hist and hist.get("z_score") is not None and not hist["underpowered"]:
        z = hist["z_score"]
        if abs(z) >= 1.5:
            return soft_prefix + (
                f"{car_pp} abnormal return over T+1 to T+5; "
                f"z-score {z:+.2f} vs {hist['prior_n']}-event history, significant."
            )
        prior_m = hist["prior_mean_t5_car_pct"]
        return soft_prefix + (
            f"{car_pp} abnormal return; in line with "
            f"{hist['prior_n']}-event prior mean of {prior_m * 100:+.1f}% "
            f"(z {z:+.2f})."
        )
    if hist:
        prior_m = hist["prior_mean_t5_car_pct"]
        return soft_prefix + (
            f"{car_pp} abnormal return; {hist['prior_n']} prior events "
            f"(underpowered, prior mean {prior_m * 100:+.1f}%)."
        )
    return soft_prefix + f"{car_pp} abnormal return; no prior history available for comparison."


def generate_take_cross_section(summary: dict[str, Any], event_class: str) -> str:
    if summary is None:
        return "Cross-section had no usable events."
    mean_car = summary["mean_t5_car_pct"]
    t = summary["t_stat_avg_vs_zero"]
    n = summary["n_subjects"]
    surprise = summary.get("surprise_reaction_correlation")
    parts = []
    if surprise and abs(surprise["rho"]) > 0.5 and surprise["n"] >= 5:
        parts.append(
            f"Surprise explains {surprise['r_squared'] * 100:.0f}% "
            f"of T+5 CAR variation (ρ={surprise['rho']:+.2f})."
        )
    if summary["significant"]:
        parts.append(
            f"Avg T+5 CAR {mean_car * 100:+.1f}%, t-stat {t:.2f}, "
            f"significant at n={n}."
        )
    elif t is not None:
        parts.append(
            f"Cross-section average isn't significant at n={n} "
            f"(avg {mean_car * 100:+.1f}%, t-stat {t:.2f})."
        )
    if not parts:
        parts.append(
            f"Mixed signal: avg T+5 {mean_car * 100:+.1f}%, n={n}."
        )
    return " ".join(parts)


def generate_take_aggregate(summary: dict[str, Any], event_class: str) -> str:
    if summary is None:
        return "Aggregate had no usable events."
    mean_car = summary["mean_t5_car_pct"]
    t = summary["t_stat_avg_vs_zero"]
    n = summary["n_subjects"]
    regime = summary.get("regime_check")
    if regime and regime["regime_shift_flag"]:
        return (
            f"Regime has shifted: recent 4 events avg "
            f"{regime['recent_mean_t5_car_pct'] * 100:+.1f}% vs "
            f"full-window {regime['full_window_mean_t5_car_pct'] * 100:+.1f}% "
            f"(n={n}). Cite recent, not headline."
        )
    if summary["significant"]:
        return (
            f"Event class has tradeable signal: avg T+5 CAR "
            f"{mean_car * 100:+.1f}%, t-stat {t:.2f}, n={n}."
        )
    surprise = summary.get("surprise_reaction_correlation")
    if surprise and abs(surprise["rho"]) > 0.5:
        return (
            f"Mean reaction not significant (avg {mean_car * 100:+.1f}%, "
            f"t-stat {t:.2f}, n={n}) but surprise explains "
            f"{surprise['r_squared'] * 100:.0f}% of cross-section variation."
        )
    return (
        f"No tradeable signal at n={n}: avg T+5 CAR "
        f"{mean_car * 100:+.1f}%, t-stat {t:.2f}."
    )


# -------- Renderers --------


def fmt_signed_pct(x: float | None, dec: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{dec}f}%"


def fmt_signed_pp(x: float | None, dec: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{dec}f}pp"


def event_label(event_class: str, meta: dict[str, Any]) -> str:
    if event_class == "earnings":
        fp = meta.get("fiscal_period", "?")
        fy = meta.get("fiscal_year", "?")
        if fp is None and fy is None:
            return "earnings"
        return f"{fp} FY{fy} earnings"
    if event_class == "dividend_changes":
        d = meta.get("change_direction", "change")
        return f"dividend {d} ${meta.get('prior_amount', 0):.4f} -> ${meta.get('new_amount', 0):.4f}"
    if event_class == "large_volume_spike":
        return f"volume spike (z={meta.get('z_score', 0):.1f})"
    return event_class


def render_single(payload: dict[str, Any]) -> str:
    subject = payload["subjects"][0]
    lines = []
    label = event_label(payload["event_class"], subject["event_metadata"])
    session = subject["event_session"]
    session_txt = "" if session == "unknown" else f" {session}"
    lines.append(f"{subject['ticker']} · {label} · {subject['event_date']}{session_txt}")
    lines.append("")
    lines.append(f"Take: {payload['take']}")
    lines.append("")

    ewr = subject["event_window_returns"]
    if ewr:
        lines.append("Event window (SPY-adjusted)")
        lines.append(f"- T0 close:    ${ewr['t0_close']:.2f}")
        ar = subject["abnormal_returns"]
        for h in ewr["horizons"]:
            close = h.get("close")
            raw = h.get("raw_return_pct")
            spy = h.get("spy_return_pct")
            label_ar = "CAR" if h["horizon"] == "T+5" else "abnormal"
            key = f"ar_{h['horizon'].lower().replace('+', '')}_pct"
            abn = ar.get(key)
            close_s = f"${close:.2f}" if close is not None else "n/a"
            lines.append(
                f"- {h['horizon']} close:   {close_s} "
                f"({fmt_signed_pct(raw)}, market {fmt_signed_pct(spy)}, "
                f"{label_ar} {fmt_signed_pct(abn)})"
            )
        lines.append("")

        # Post-announcement drift: T+1 → T+horizon, SPY-adjusted. Reported
        # separately from the event-inclusive CAR so the announcement
        # reaction (T0 → T+1) is not mislabeled as drift (C5).
        drift_keys = [
            (h["horizon"], f"post_announce_drift_{h['horizon'].lower().replace('+', '')}_pct")
            for h in ewr["horizons"] if h["horizon"] != "T+1"
        ]
        drift_lines = []
        ar_t1 = ar.get("ar_t1_pct")
        for hlabel, dkey in drift_keys:
            d = ar.get(dkey)
            if d is None:
                continue
            inclusive_key = f"ar_{hlabel.lower().replace('+', '')}_pct"
            inclusive = ar.get(inclusive_key)
            if inclusive is not None and ar_t1 is not None:
                drift_lines.append(
                    f"- Drift T+1→{hlabel}: {fmt_signed_pct(d)} "
                    f"(CAR {fmt_signed_pct(inclusive)} minus announcement {fmt_signed_pct(ar_t1)})"
                )
            else:
                drift_lines.append(f"- Drift T+1→{hlabel}: {fmt_signed_pct(d)}")
        if drift_lines:
            lines.append("Post-announcement drift (T+1 → T+horizon, SPY-adjusted)")
            lines.extend(drift_lines)
            lines.append("")

    hist = subject.get("t_stat_vs_history")
    if hist:
        ec_label = payload["event_class"].replace("_", " ")
        lines.append(
            f"Historical comparison (last {hist['prior_n']} "
            f"{subject['ticker']} {ec_label} reactions)"
        )
        lines.append(
            f"- Mean T+5 CAR:        {fmt_signed_pct(hist['prior_mean_t5_car_pct'])}"
        )
        lines.append(
            f"- Std dev:             {fmt_signed_pct(hist['prior_std_t5_car_pct'])}"
        )
        z = hist.get("z_score")
        pct = hist.get("percentile")
        this_pp = fmt_signed_pp(hist["this_event_t5_car_pct"])
        if z is not None and pct is not None:
            lines.append(
                f"- This event:          {this_pp} "
                f"({pct * 100:.0f}th pct, {z:+.2f}σ vs prior mean)"
            )
        if hist.get("direction_concurrence"):
            lines.append(
                f"- Direction concur:    {hist['direction_concurrence']} "
                f"priors aligned with surprise sign"
            )
        if hist.get("underpowered"):
            lines.append("- Note: prior_n < 8, distribution test is underpowered.")
        lines.append("")

    # M10: universe base-rate block, when present. Renders the same
    # CAR / drift metrics across the universe so the single-ticker
    # numbers above have an anchor.
    ub = payload.get("universe_base_rate")
    if ub is not None:
        lines.append("Universe base rate (same event class)")
        if ub.get("reason"):
            hint = ub.get("hint")
            lines.append(f"- Skipped: {ub['reason']}" + (f". {hint}" if hint else ""))
        else:
            n_events = ub.get("n_events", 0)
            n_tickers = ub.get("n_tickers_with_data", 0)
            lines.append(
                f"- Pulled across {n_tickers} tickers, {n_events} events."
            )
            by_metric = ub.get("by_metric") or {}
            ar = subject["abnormal_returns"] or {}
            metric_labels = [
                ("ar_t1_pct", "T+1 abnormal"),
                ("ar_t3_pct", "T+3 abnormal"),
                ("car_t5_pct", "T+5 CAR"),
                ("post_announce_drift_t3_pct", "Drift T+1→T+3"),
                ("post_announce_drift_t5_pct", "Drift T+1→T+5"),
            ]
            for key, label in metric_labels:
                br = by_metric.get(key) or {}
                if not br or br.get("n", 0) == 0:
                    continue
                this_v = ar.get(key)
                this_s = fmt_signed_pct(this_v) if this_v is not None else "n/a"
                lines.append(
                    f"- {label}: this {this_s} · universe median "
                    f"{fmt_signed_pct(br.get('median'))}, p25 "
                    f"{fmt_signed_pct(br.get('p25'))}, p75 "
                    f"{fmt_signed_pct(br.get('p75'))} (n={br.get('n')})"
                )
        lines.append("")

    if payload["tier_caveats"]:
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


def render_cross_section(payload: dict[str, Any]) -> str:
    subjects = payload["subjects"]
    summary = payload["summary"]
    tickers = ",".join(sorted({s["ticker"] for s in subjects}))
    period = payload.get("window", {}).get("period_label") or "selected period"
    lines = []
    lines.append(
        f"Event study: {tickers} · {payload['event_class']} · "
        f"{period} · {len(subjects)} events"
    )
    lines.append("")

    class_col = {
        "earnings": "Surprise",
        "dividend_changes": "Change",
        "large_volume_spike": "Vol z",
    }.get(payload["event_class"], "Magnitude")

    show_concur = payload["event_class"] == "earnings" and payload["tier"] == "A"
    header = f"| Ticker | {class_col} | T+1 Abn | T+5 CAR | t-stat (vs hist) |"
    sep = "|--------|--------:|--------:|--------:|-----------------:|"
    if show_concur:
        header += " Concur |"
        sep += "-------:|"
    lines.append(header)
    lines.append(sep)
    underpowered_marks = False
    for s in sorted(subjects, key=lambda x: x["ticker"]):
        meta = s["event_metadata"]
        if payload["event_class"] == "earnings":
            mag = fmt_signed_pct(meta.get("surprise_eps_pct"))
        elif payload["event_class"] == "dividend_changes":
            mag = fmt_signed_pct(meta.get("change_pct"))
        elif payload["event_class"] == "large_volume_spike":
            z_val = meta.get("z_score")
            mag = f"{z_val:.1f}" if z_val is not None else "n/a"
        else:
            mag = "n/a"
        ar = s["abnormal_returns"]
        t1 = fmt_signed_pct(ar.get("ar_t1_pct"))
        car = fmt_signed_pct(ar.get("car_t5_pct"))
        hist = s.get("t_stat_vs_history")
        if hist and hist.get("z_score") is not None:
            tstat_s = f"{hist['z_score']:+.2f}"
            if hist["underpowered"]:
                tstat_s += "*"
                underpowered_marks = True
        else:
            tstat_s = "n/a"
        row = f"| {s['ticker']} | {mag} | {t1} | {car} | {tstat_s} |"
        if show_concur:
            row += f" {hist.get('direction_concurrence', 'n/a') if hist else 'n/a'} |"
        lines.append(row)
    if underpowered_marks:
        lines.append("")
        lines.append("* underpowered (prior_n < 8)")
    lines.append("")

    if summary:
        lines.append("Cross-section")
        lines.append(
            f"- Avg T+5 CAR:    {fmt_signed_pct(summary['mean_t5_car_pct'])}"
        )
        lines.append(
            f"- Median:         {fmt_signed_pct(summary['median_t5_car_pct'])}"
        )
        t = summary["t_stat_avg_vs_zero"]
        sig = "significant" if summary["significant"] else "not significant"
        lines.append(
            f"- t-stat (avg vs 0): {t:.2f} ({sig} at n={summary['n_subjects']})"
        )
        sc = summary.get("surprise_reaction_correlation")
        if sc:
            lines.append(
                f"- Surprise vs reaction ρ: {sc['rho']:+.2f} "
                f"(R² = {sc['r_squared'] * 100:.0f}%, n={sc['n']})"
            )
        lines.append("")

    lines.append(f"Take: {payload['take']}")

    if payload["tier_caveats"]:
        lines.append("")
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


def render_aggregate(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    tickers = ",".join(sorted({s["ticker"] for s in payload["subjects"]}))
    w = payload.get("window") or {}
    lines = []
    lines.append(
        f"Event study: {tickers} · {payload['event_class']} · "
        f"{w.get('from_date', '?')} to {w.get('to_date', '?')} · "
        f"{summary['n_subjects'] if summary else 0} events"
    )
    lines.append("")

    if summary:
        lines.append("Aggregate abnormal returns (SPY-adjusted)")
        for hb in summary["horizon_breakdown"]:
            t_s = f"t-stat {hb['t_stat']:+.2f}" if hb.get("t_stat") is not None else "t-stat n/a"
            lines.append(
                f"- {hb['horizon']} avg:   {fmt_signed_pct(hb['mean_ar_pct'])} "
                f"(median {fmt_signed_pct(hb['median_ar_pct'])}, "
                f"{t_s}, n={hb['n']})"
            )
            # Post-announcement drift line (T+1 → T+horizon), reported
            # alongside the event-inclusive CAR per C5.
            if hb.get("mean_post_announce_drift_pct") is not None:
                d_t = hb.get("drift_t_stat")
                d_ts = f"t-stat {d_t:+.2f}" if d_t is not None else "t-stat n/a"
                lines.append(
                    f"- {hb['horizon']} drift (T+1→{hb['horizon']}): "
                    f"{fmt_signed_pct(hb['mean_post_announce_drift_pct'])} "
                    f"(median {fmt_signed_pct(hb['median_post_announce_drift_pct'])}, "
                    f"{d_ts}, n={hb['drift_n']})"
                )
        p = summary["percentiles"]
        lines.append(
            f"- T+5 distribution: p10 {fmt_signed_pct(p['p10_pct'])} "
            f"p25 {fmt_signed_pct(p['p25_pct'])} "
            f"p50 {fmt_signed_pct(p['p50_pct'])} "
            f"p75 {fmt_signed_pct(p['p75_pct'])} "
            f"p90 {fmt_signed_pct(p['p90_pct'])}"
        )
        lines.append("")

        regime = summary.get("regime_check")
        if regime:
            lines.append("Regime check")
            lines.append(
                f"- Recent (last 4): {fmt_signed_pct(regime['recent_mean_t5_car_pct'])}"
            )
            lines.append(
                f"- Full window:     {fmt_signed_pct(regime['full_window_mean_t5_car_pct'])} "
                f"(n={summary['n_subjects']})"
            )
            flag = "REGIME SHIFT" if regime["regime_shift_flag"] else "within 1 SE"
            lines.append(f"- Delta:           {fmt_signed_pp(regime['delta_pp'])} ({flag})")
            lines.append("")

        sc = summary.get("surprise_reaction_correlation")
        if sc:
            lines.append("Surprise vs reaction")
            lines.append(f"- Pearson ρ: {sc['rho']:+.2f}")
            lines.append(f"- R²:        {sc['r_squared'] * 100:.0f}%")
            lines.append(f"- n:         {sc['n']}")
            lines.append("")

    lines.append(f"Take: {payload['take']}")

    if payload["tier_caveats"]:
        lines.append("")
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


# -------- Diagnostic error messages --------


def build_no_events_message(
    tickers: list[str],
    event_class: str,
    event_date: str | None,
    window: tuple[str, str] | None,
    period: str | None,
) -> str:
    """Compose an honest, per-ticker failure message from the diagnostics
    captured by each resolver. Replaces the legacy
    "No events matched the input criteria." string.
    """
    lines = ["No events to study.", "", "Per-ticker resolution:"]
    for t in tickers:
        diags = _RESOLVER_DIAGNOSTICS.get(t, {})
        # Pick the primary diag for the requested class. The earnings
        # resolver records under "earnings" + child stages.
        primary = diags.get(event_class) or {}
        reason = primary.get("failure_reason")
        if event_class == "earnings":
            edgar = diags.get("edgar_earnings") or {}
            benzinga = diags.get("benzinga_earnings") or {}
            edgar_reason = edgar.get("failure_reason")
            if edgar_reason == "cik_not_found":
                lines.append(
                    f"  {t}: CIK lookup failed (Massive + SEC ticker map "
                    f"both returned no CIK). Verify the ticker is currently "
                    f"listed and reporting to SEC."
                )
                continue
            raw_8k = edgar.get("raw_8k_count", 0)
            matched = edgar.get("matched_filter_count", 0)
            items_seen = edgar.get("observed_item_codes") or []
            after_date = primary.get("after_date_filter_count", 0)
            bz_n = benzinga.get("raw_row_count", 0)
            if bz_n == 0 and raw_8k > 0 and matched == 0:
                lines.append(
                    f"  {t}: Benzinga returned 0 rows; "
                    f"{raw_8k} raw 8-Ks found, 0 matched items "
                    f"{'/'.join(EDGAR_EARNINGS_ITEMS)} "
                    f"(observed item codes: {items_seen or 'none'}). "
                    f"Check whether {t} reports earnings via 10-Q only."
                )
                continue
            if bz_n == 0 and raw_8k == 0:
                lines.append(
                    f"  {t}: Benzinga returned 0 rows and no 8-Ks on file "
                    f"under CIK {edgar.get('cik', '?')}. Likely de-listed or "
                    f"non-reporting."
                )
                continue
            if matched > 0 and after_date == 0:
                w = (
                    f"--window {window[0]}..{window[1]}" if window
                    else (f"--event-date {event_date}" if event_date else "--period filter")
                )
                lines.append(
                    f"  {t}: {matched} 8-Ks matched filter, 0 within {w}. "
                    f"Try widening the window or use --period most_recent."
                )
                continue
            # Generic fallback
            lines.append(
                f"  {t}: {reason or 'no events from any source'} "
                f"(benzinga={bz_n}, edgar_matched={matched}, "
                f"after_date={after_date})"
            )
            continue
        if event_class == "dividend_changes":
            raw = primary.get("raw_action_count", 0)
            qual = primary.get("change_qualifying_count", 0)
            after = primary.get("after_date_filter_count", 0)
            if reason == "no_dividend_history":
                lines.append(
                    f"  {t}: 0 dividend records on file. Likely non-paying."
                )
                continue
            if reason == "no_changes_above_1pct":
                lines.append(
                    f"  {t}: {raw} dividend records, 0 changes >= 1% vs prior. "
                    f"Stable payer."
                )
                continue
            if reason == "all_filtered_by_date":
                w = (
                    f"--window {window[0]}..{window[1]}" if window
                    else (f"--event-date {event_date}" if event_date else "--period filter")
                )
                lines.append(
                    f"  {t}: {qual} dividend changes found, 0 within {w}."
                )
                continue
            lines.append(
                f"  {t}: {reason or 'no events'} "
                f"(raw={raw}, qualifying={qual}, after_date={after})"
            )
            continue
        if event_class == "large_volume_spike":
            n_days = primary.get("trading_day_count", 0)
            raw = primary.get("raw_spike_count", 0)
            after = primary.get("after_date_filter_count", 0)
            if reason == "no_daily_aggs":
                lines.append(
                    f"  {t}: 0 daily aggregates returned. Verify ticker is "
                    f"valid and within the date range."
                )
                continue
            if reason == "no_z_score_above_3":
                lines.append(
                    f"  {t}: {n_days} trading days scanned, 0 volume spikes "
                    f">3 sigma. Quiet name."
                )
                continue
            if reason == "all_filtered_by_date":
                w = (
                    f"--window {window[0]}..{window[1]}" if window
                    else (f"--event-date {event_date}" if event_date else "--period filter")
                )
                lines.append(
                    f"  {t}: {raw} spikes found, 0 within {w}."
                )
                continue
            lines.append(
                f"  {t}: {reason or 'no events'} "
                f"(days={n_days}, raw_spikes={raw}, after_date={after})"
            )
            continue
        # Unknown event class
        lines.append(f"  {t}: {reason or 'no events'}")
    lines.append("")
    lines.append("Pass --debug-resolver to dump full per-step diagnostics.")
    return "\n".join(lines)


# -------- Main pipeline --------


def run(
    ticker: str | None = None,
    tickers: Iterable[str] | str | None = None,
    event_class: str = "earnings",
    event_date: str | None = None,
    window: tuple[str, str] | str | None = None,
    period: str | None = None,
    with_base_rate: bool = False,
    base_rate_universe: Iterable[str] | str | None = None,
    debug_resolver: bool = False,
    client: MassiveClient | None = None,
) -> dict[str, Any]:
    """Compute event-study payload for one or more tickers.

    Accepts one of three input shapes:
      - ticker + event_date        → single mode
      - tickers + period='most_recent' → cross-section
      - tickers + window=(from,to) → aggregate
    """
    # Reset per-call module state so successive calls don't leak into each other.
    _reset_state(client=client)

    # Build an args-like namespace so the pre-existing body (below) doesn't need
    # to change shape. Kept intentionally close to the original CLI schema.
    if tickers is not None:
        if isinstance(tickers, str):
            tickers_str = tickers
        else:
            tickers_str = ",".join(tickers)
    else:
        tickers_str = None
    if isinstance(window, tuple) and len(window) == 2:
        window_str = f"{window[0]}..{window[1]}"
    else:
        window_str = window  # already string or None
    if base_rate_universe is not None and not isinstance(base_rate_universe, str):
        base_rate_universe = ",".join(base_rate_universe)

    args = SimpleNamespace(
        ticker=ticker,
        tickers=tickers_str,
        event_class=event_class,
        event_date=event_date,
        window=window_str,
        period=period,
        with_base_rate=with_base_rate,
        base_rate_universe=base_rate_universe,
        debug_resolver=debug_resolver,
    )

    if not args.ticker and not args.tickers:
        raise ValueError("Provide ticker= or tickers=")

    event_class = args.event_class
    if event_class not in RESOLVERS:
        raise ValueError(f"unknown event_class: {event_class}")

    # Determine mode
    tickers = args.tickers.split(",") if args.tickers else [args.ticker]
    tickers = [t.strip().upper() for t in tickers if t.strip()]

    if args.event_date:
        mode = "single" if len(tickers) == 1 else "cross_section"
        window_arg = None
    elif args.window:
        mode = "aggregate"
        a, b = args.window.split("..")
        window_arg = (a.strip(), b.strip())
    elif args.period:
        # cross-section using "most recent" semantics: pick the most recent
        # earnings per ticker
        mode = "cross_section" if len(tickers) > 1 else "single"
        window_arg = None
    else:
        raise ValueError(
            "Provide --event-date, --window, or --period."
        )

    # Resolve events
    all_events_by_ticker: dict[str, list[dict[str, Any]]] = {}
    chosen_events: list[dict[str, Any]] = []
    tiers_seen = set()
    for t in tickers:
        # Always pull the full history for the ticker (needed for per-subject
        # t_stat_vs_history regardless of mode)
        full = RESOLVERS[event_class](t, event_date=None, window=None)
        all_events_by_ticker[t] = full
        for e in full:
            tiers_seen.add(e.get("_tier", "A"))

        if mode == "single" and args.event_date:
            chosen = [e for e in full if e["event_date"] == args.event_date]
        elif args.period and args.period == "most_recent":
            chosen = [full[-1]] if full else []
        elif window_arg:
            chosen = [e for e in full if window_arg[0] <= e["event_date"] <= window_arg[1]]
        else:
            chosen = []
        chosen_events.extend(chosen)

    if not chosen_events:
        raise RuntimeError(
            build_no_events_message(
                tickers,
                event_class,
                event_date=args.event_date,
                window=window_arg,
                period=args.period,
            )
        )

    if getattr(args, "debug_resolver", False):
        print(
            "\n[debug-resolver] per-ticker diagnostics:\n"
            + json.dumps(_RESOLVER_DIAGNOSTICS, indent=2, default=str),
            file=sys.stderr,
        )

    tier = "A" if "A" in tiers_seen else "B"
    tier_caveats = []
    if tier == "B":
        tier_caveats.append(
            "Benzinga earnings unavailable; using SEC EDGAR 8-K item 2.02 as print-date proxy."
        )
        tier_caveats.append(
            "No surprise_eps_pct; cross-section drops surprise-vs-reaction correlation."
        )

    # Determine date span for aggregate pulls
    all_event_dates = sorted({e["event_date"] for evs in all_events_by_ticker.values() for e in evs})
    if not all_event_dates:
        raise RuntimeError("No events available for ticker history.")
    earliest = all_event_dates[0]
    latest = max(all_event_dates[-1], TODAY.isoformat())
    pull_from = (date.fromisoformat(earliest) - timedelta(days=30)).isoformat()
    pull_to = (
        min(date.fromisoformat(latest) + timedelta(days=15), TODAY)
    ).isoformat()

    # SPY pull
    spy_aggs = get_daily_aggs("SPY", pull_from, pull_to)

    # Per-ticker aggs + compute returns for ALL events (we need history for t-stat-vs-history)
    for t in tickers:
        if not all_events_by_ticker[t]:
            continue
        ticker_aggs = get_daily_aggs(t, pull_from, pull_to)
        for e in all_events_by_ticker[t]:
            res = compute_event_returns(e, ticker_aggs, spy_aggs)
            e["event_window_returns"] = res.get("event_window_returns")
            e["abnormal_returns"] = res.get("abnormal_returns")
            e["_t5_car"] = (
                e["abnormal_returns"].get("car_t5_pct")
                if e["abnormal_returns"] else None
            )

    # Build subject list (the chosen events with t-stat-vs-history attached)
    subjects = []
    for e in chosen_events:
        # Find the matching full-history entry (same ticker, same date)
        full_match = next(
            (x for x in all_events_by_ticker[e["ticker"]]
             if x["event_date"] == e["event_date"]),
            e
        )
        full_match["t_stat_vs_history"] = t_stat_vs_history(
            full_match, all_events_by_ticker[e["ticker"]]
        )
        subjects.append({
            "ticker": full_match["ticker"],
            "event_date": full_match["event_date"],
            "event_session": full_match["event_session"],
            "event_metadata": full_match["event_metadata"],
            "event_window_returns": full_match.get("event_window_returns"),
            "abnormal_returns": full_match.get("abnormal_returns"),
            "t_stat_vs_history": full_match.get("t_stat_vs_history"),
        })

    # Summary block (cross_section and aggregate)
    summary = None
    if mode in ("cross_section", "aggregate"):
        summary = build_summary(subjects, mode)

    # Take
    if mode == "single":
        take = generate_take_single(subjects[0])
    elif mode == "cross_section":
        take = generate_take_cross_section(summary, event_class)
    else:
        take = generate_take_aggregate(summary, event_class)

    # Window block
    window_block = None
    if mode == "cross_section":
        window_block = {
            "period_label": args.period or chosen_events[0]["event_date"],
            "from_date": min(s["event_date"] for s in subjects),
            "to_date": max(s["event_date"] for s in subjects),
        }
    elif mode == "aggregate" and window_arg:
        window_block = {
            "period_label": None,
            "from_date": window_arg[0],
            "to_date": window_arg[1],
        }

    # M10: universe base rate for single-name skills. Anchors the
    # single-ticker reaction in the same event class so the operator
    # can tell whether +5.2% drift is high or low. Heavy by default
    # (per-ticker resolver + aggs pull), so opt-in via --with-base-rate.
    universe_base_rate_block: dict[str, Any] | None = None
    if mode == "single":
        if getattr(args, "with_base_rate", False):
            universe_arg = getattr(args, "base_rate_universe", None)
            if universe_arg:
                br_universe = [
                    t.strip().upper() for t in universe_arg.split(",") if t.strip()
                ]
            else:
                br_universe = list(UNIVERSE_BASE_RATE_DEFAULT)
            # Exclude the subject ticker itself so the comparison is
            # against the rest of the universe, not partly itself.
            subject_ticker = subjects[0]["ticker"] if subjects else None
            br_universe = [t for t in br_universe if t != subject_ticker]
            print(
                f"  base-rate: pulling {event_class} history for "
                f"{len(br_universe)} universe tickers",
                file=sys.stderr,
            )
            universe_base_rate_block = compute_universe_base_rate(
                event_class, br_universe, pull_from, pull_to, spy_aggs
            )
            if universe_base_rate_block is None:
                universe_base_rate_block = {
                    "reason": "live_universe_pull_returned_no_events",
                }
        else:
            universe_base_rate_block = {
                "reason": "live_universe_pull_disabled",
                "hint": "pass --with-base-rate to opt in (heavy ~N*4 API calls)",
            }
            tier_caveats.append(
                "Universe base rate omitted (M10). Pass --with-base-rate "
                "to anchor the single-name metric against a mega-cap "
                "distribution."
            )

    payload = {
        "mode": mode,
        "tier": tier,
        "tier_caveats": tier_caveats,
        "event_class": event_class,
        "model": "spy",
        "take": take,
        "subjects": subjects,
        "summary": summary,
        "universe_base_rate": universe_base_rate_block,
        "window": window_block,
        "sources": list(_sources),
    }

    return payload


def render(payload: dict[str, Any]) -> str:
    """Dispatch to the mode-specific renderer."""
    mode = payload.get("mode")
    if mode == "single":
        return render_single(payload)
    if mode == "cross_section":
        return render_cross_section(payload)
    return render_aggregate(payload)


def _reset_state(client: MassiveClient | None = None) -> None:
    """Reset module-level per-run state so successive run() calls don't leak."""
    global _sources, _RESOLVER_DIAGNOSTICS, _cik_cache, _SEC_TICKER_MAP_CACHE
    global _aggs_cache, _benzinga_cache, _edgar_cache
    globals()["client"] = client or MassiveClient()
    _sources = []
    _RESOLVER_DIAGNOSTICS = {}
    _cik_cache = {}
    # SEC ticker map is HUGE (17K entries); keep it across calls as an
    # intentional exception — it changes rarely and re-pulling costs 500ms.
    if "_SEC_TICKER_MAP_CACHE" not in globals() or globals().get("_SEC_TICKER_MAP_CACHE") is None:
        globals()["_SEC_TICKER_MAP_CACHE"] = None
    if "_aggs_cache" in globals():
        _aggs_cache = {}
    if "_benzinga_cache" in globals():
        _benzinga_cache = {}
    if "_edgar_cache" in globals():
        _edgar_cache = {}


# CLI removed — see examples/run-event-study.py
